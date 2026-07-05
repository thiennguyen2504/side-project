"""
uploader.py — Manages the Gemini File Search Store (corpus) for the support bot.

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
STORE_DISPLAY_NAME = "kb-sync-support-docs"
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


# ── Store index ────────────────────────────────────────────────────────────────

def parse_display_name(display_name: str) -> tuple[str, str]:
    """
    Parse a {slug}__{hash8}.md display_name back into its components.
    
    Returns:
        tuple of (slug, hash8)
        
    Raises:
        ValueError if the display_name doesn't match the expected format.
    """
    if not display_name or not display_name.endswith(".md"):
        raise ValueError("Must end with .md")
    base_name = display_name[:-3]
    if "__" not in base_name:
        raise ValueError("Must contain __ separator")
    return base_name.rsplit("__", 1)


def get_store_index(client: genai.Client, store_name: str) -> dict[str, dict]:
    """
    List all documents currently in the File Search Store.
    Parse each document's display_name back into (slug, hash8).

    Expected display_name format: {slug}__{hash8}.md

    Returns:
        Dict mapping slug → {"hash8": str, "document_name": str}
    """
    index = {}

    pager = _with_retry(
        client.file_search_stores.documents.list,
        parent=store_name,
        label="list_documents",
    )

    for doc in pager:
        display_name = getattr(doc, "display_name", "")
        try:
            slug, hash8 = parse_display_name(display_name)
        except ValueError as exc:
            logger.debug("Skipping document with unexpected display_name '%s': %s", display_name, exc)
            continue

        index[slug] = {
            "hash8": hash8,
            "document_name": doc.name,
        }

    return index


# ── Document upload ────────────────────────────────────────────────────────────

def upload_document(
    client: genai.Client,
    store_name: str,
    file_path: Path,
    display_name: Optional[str] = None,
) -> Optional["DocRef"]:
    """
    Upload a single Markdown file to the File Search Store.

    Args:
        client: Authenticated Gemini client.
        store_name: Fully-qualified store resource name.
        file_path: Path to the local .md file.
        display_name: Custom display name for the store. Defaults to file_path.name.

    Returns:
        A DocRef(name=document_resource_name), or None on failure.
    """
    if display_name is None:
        display_name = file_path.name
    try:
        content = file_path.read_bytes()
        file_obj = io.BytesIO(content)

        operation = _with_retry(
            client.file_search_stores.upload_to_file_search_store,
            file_search_store_name=store_name,
            file=file_obj,
            config=types.UploadToFileSearchStoreConfig(
                mime_type="text/plain",
                display_name=display_name,
            ),
            label=f"upload:{display_name}",
        )
        # upload_to_file_search_store is synchronous — response is already populated.
        # Structure: operation.response.document_name = 'fileSearchStores/.../documents/...'
        response = getattr(operation, "response", None)
        doc_name = getattr(response, "document_name", None)
        if not doc_name:
            logger.warning("Upload returned no document_name for '%s'", display_name)
            return None
        logger.debug("Uploaded: %s → %s", display_name, doc_name)
        return DocRef(name=doc_name)
    except Exception as exc:
        logger.error("Failed to upload '%s': %s", display_name, exc)
        return None



# ── Simple document reference ──────────────────────────────────────────────────

class DocRef:
    """Lightweight reference to an uploaded document, compatible with main.py's doc.name usage."""
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"DocRef(name={self.name!r})"


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
    (Note: Typically driven by main.py's diff logic now, so might be unused
    or used for initial bulk upload without hashes.)

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

SYSTEM_PROMPT = """\
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
"""


def ask_bot(
    client: genai.Client,
    store_name: str,
    question: str,
) -> str:
    """
    Send a question to the support bot grounded on the File Search Store.

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
