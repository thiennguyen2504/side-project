"""
uploader.py — Manages the Gemini File Search Store (corpus) for OptiBot.

Responsibilities:
  - Create or retrieve an existing File Search Store (no duplicates per run).
  - Upload Markdown files to the store via upload_to_file_search_store.
  - Delete individual documents from the store (used on update, avoids duplication).
  - Provide ask_bot() for question-answering grounded on the store.

Uses exponential-backoff retry for every Gemini API call.
Never logs or exposes the API key.
"""

import io
import os
import time
import logging
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

# ── Constants ──────────────────────────────────────────────────────────────────
STORE_DISPLAY_NAME = "optibot-support-docs"
MODEL_ID = "gemini-3.1-flash-lite"
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds (exponential: 2, 4, 8)

logger = logging.getLogger(__name__)


# ── Client factory ─────────────────────────────────────────────────────────────

def get_client() -> genai.Client:
    """
    Create and return an authenticated Gemini API client.

    Reads GEMINI_API_KEY from environment — never hardcodes secrets.

    Raises:
        EnvironmentError: if GEMINI_API_KEY is not set.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    return genai.Client(api_key=api_key)


# ── Retry helper ───────────────────────────────────────────────────────────────

def _with_retry(fn, *args, max_retries: int = MAX_RETRIES, label: str = "", **kwargs):
    """
    Call fn(*args, **kwargs) with exponential-backoff retry.

    Retries on any exception up to max_retries times, then re-raises.

    Args:
        label: Human-readable name for this operation (used in log messages).
    """
    name = label or getattr(fn, "__name__", str(fn))
    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            wait = BACKOFF_BASE ** attempt
            if attempt < max_retries:
                logger.warning(
                    "[%s] Attempt %d/%d failed: %s — retrying in %ds",
                    name, attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "[%s] All %d attempts failed: %s",
                    name, max_retries, exc,
                )
                raise


# ── Store management ───────────────────────────────────────────────────────────

def get_or_create_store(client: genai.Client) -> types.FileSearchStore:
    """
    Return the existing File Search Store named STORE_DISPLAY_NAME,
    or create a new one if none exists.

    Prevents creating a duplicate store on every run.

    Returns:
        The FileSearchStore object (has .name and .display_name).
    """
    # Iterate over existing stores looking for a name match
    stores_pager = _with_retry(
        client.file_search_stores.list,
        label="list_stores",
    )
    for store in stores_pager:
        if store.display_name == STORE_DISPLAY_NAME:
            logger.info("Reusing existing File Search Store: %s", store.name)
            return store

    # None found → create
    logger.info("Creating new File Search Store: '%s'", STORE_DISPLAY_NAME)
    store = _with_retry(
        client.file_search_stores.create,
        config=types.CreateFileSearchStoreConfig(display_name=STORE_DISPLAY_NAME),
        label="create_store",
    )
    logger.info("Created store: %s", store.name)
    return store


# ── Document upload ────────────────────────────────────────────────────────────

def upload_document(
    client: genai.Client,
    store_name: str,
    file_path: Path,
) -> Optional[types.Document]:
    """
    Upload a single Markdown file to the File Search Store.

    Uses upload_to_file_search_store which accepts a file-like object
    and handles chunking + embedding automatically.

    Args:
        client: Authenticated Gemini client.
        store_name: Fully-qualified store resource name (e.g. 'fileSearchStores/abc123').
        file_path: Path to the local .md file.

    Returns:
        The Document object with .name set, or None on failure.
    """
    try:
        content = file_path.read_bytes()
        file_obj = io.BytesIO(content)

        result = _with_retry(
            client.file_search_stores.upload_to_file_search_store,
            name=store_name,
            file=file_obj,
            config=types.UploadToFileSearchStoreConfig(
                mime_type="text/plain",
                display_name=file_path.name,
            ),
            label=f"upload:{file_path.name}",
        )
        # upload_to_file_search_store returns an Operation; resolve it
        doc = result if isinstance(result, types.Document) else _resolve_operation(result)
        logger.debug("Uploaded: %s → %s", file_path.name, doc.name if doc else "?")
        return doc
    except Exception as exc:
        logger.error("Failed to upload '%s': %s", file_path.name, exc)
        return None


def _resolve_operation(operation) -> Optional[types.Document]:
    """
    Wait for a long-running upload operation to complete and return its result.

    Some SDK versions return an Operation object that must be polled.
    """
    # If it already looks like a Document, return it
    if hasattr(operation, "name") and hasattr(operation, "state"):
        return operation  # It's a Document with state
    if hasattr(operation, "result"):
        # Long-running operation — poll until done
        max_wait = 120  # seconds
        elapsed = 0
        while elapsed < max_wait:
            if getattr(operation, "done", False):
                return operation.result()
            time.sleep(2)
            elapsed += 2
        logger.warning("Upload operation did not complete within %ds", max_wait)
    return operation  # Return as-is; name extraction will work if it has .name


# ── Document deletion ──────────────────────────────────────────────────────────

def delete_document(
    client: genai.Client,
    document_name: str,
) -> bool:
    """
    Delete a document from the File Search Store by its resource name.

    Used before re-uploading a changed article to prevent duplicate content.

    Args:
        document_name: Fully-qualified document resource name stored in state.json.

    Returns:
        True on success, False on failure (non-fatal).
    """
    try:
        _with_retry(
            client.file_search_stores.documents.delete,
            name=document_name,
            label=f"delete:{document_name}",
        )
        logger.debug("Deleted document: %s", document_name)
        return True
    except Exception as exc:
        logger.error("Failed to delete document '%s': %s", document_name, exc)
        return False


# ── Batch upload ───────────────────────────────────────────────────────────────

def upload_all(
    client: genai.Client,
    store_name: str,
    file_paths: list[Path],
) -> dict[str, str]:
    """
    Upload a list of Markdown files to the store, logging progress.

    Args:
        client: Authenticated Gemini client.
        store_name: Fully-qualified store resource name.
        file_paths: List of local .md file Paths to upload.

    Returns:
        Dict mapping filename → document resource name for successful uploads.
    """
    total = len(file_paths)
    uploaded: dict[str, str] = {}
    failed = 0

    for i, path in enumerate(file_paths, start=1):
        doc = upload_document(client, store_name, path)
        if doc and hasattr(doc, "name"):
            uploaded[path.name] = doc.name
            logger.info("[%d/%d] ✓ Uploaded: %s", i, total, path.name)
        else:
            failed += 1
            logger.warning("[%d/%d] ✗ Failed:   %s", i, total, path.name)

    logger.info(
        "Upload summary: %d/%d files uploaded successfully (%d failed).",
        len(uploaded), total, failed,
    )
    return uploaded


# ── Bot Q&A ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are OptiBot, a customer support assistant for OptiSigns.

Rules:
1. Answer ONLY based on the information found in the provided support documents.
2. If the information needed to answer the question is not in the documents, say:
   "I couldn't find this information in the OptiSigns support documentation."
3. Do NOT make up or infer information not present in the documents.
4. Always cite the relevant Article URL(s) at the end of your answer under a
   "Sources:" section, using the exact Article URL lines from the documents.
5. Be concise, friendly, and professional.
"""


def ask_bot(
    client: genai.Client,
    store_name: str,
    question: str,
) -> str:
    """
    Send a question to OptiBot grounded on the File Search Store.

    Args:
        client: Authenticated Gemini client.
        store_name: Fully-qualified File Search Store resource name.
        question: The user's question string.

    Returns:
        The model's text response (always a string).
    """
    response = _with_retry(
        client.models.generate_content,
        model=MODEL_ID,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[store_name],
                    )
                )
            ],
        ),
        label="generate_content",
    )
    return response.text or ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = get_client()
    store = get_or_create_store(client)
    logger.info("Store ready: %s", store.name)
    # Smoke-test query
    answer = ask_bot(client, store.name, "How do I add a YouTube video?")
    print("\n=== Bot Response ===")
    print(answer)
