"""
main.py — Daily job entrypoint for OptiBot.

Pipeline:
  1. Scrape articles from the Zendesk Help Center.
  2. Compute SHA-256 content hashes (normalised whitespace).
  3. Compare against state.json (previous run).
  4. Upload NEW articles, delete+re-upload CHANGED articles, skip UNCHANGED.
  5. Write updated state.json.
  6. Log summary: added=X, updated=Y, skipped=Z.

Exit code is always 0 (partial upload failures are logged, not fatal).
"""

# Load .env file first — must happen before any other import that reads env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed (e.g. in Docker where env vars are passed directly)

import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scraper
import uploader

# ── Paths ──────────────────────────────────────────────────────────────────────
STATE_FILE = Path("state.json")
LOGS_DIR = Path("logs")
ARTICLES_DIR = Path("data/articles")

# ── Logging setup ──────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    """
    Configure root logger to write to both stdout and a timestamped log file.

    Uses UTF-8 for both handlers to avoid Windows charmap issues.

    Returns:
        The root logger.
    """
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_file = LOGS_DIR / f"run_{timestamp}.log"

    fmt = "%(asctime)s %(levelname)-8s %(message)s"

    # UTF-8 stdout wrapper — avoids UnicodeEncodeError on Windows cp1252 console
    stdout_handler = logging.StreamHandler(
        stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
    )
    stdout_handler.setFormatter(logging.Formatter(fmt))

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler])

    logger = logging.getLogger(__name__)
    logger.info("Log file: %s", log_file)
    return logger


# ── Content hash ───────────────────────────────────────────────────────────────

def compute_hash(content: str) -> str:
    """
    Compute a SHA-256 hash of the given content after normalising whitespace.

    Normalisation rules (order matters):
      1. Strip leading/trailing whitespace from each line.
      2. Collapse runs of whitespace within lines to a single space.
      3. Strip leading/trailing blank lines from the whole document.
      4. Collapse 2+ consecutive blank lines to exactly one blank line.

    This ensures that cosmetic whitespace changes (trailing spaces, extra
    blank lines) do NOT produce a different hash, while real content
    changes always do.

    Returns:
        64-character lowercase hex digest.
    """
    # Normalise per-line whitespace
    lines = [re.sub(r"\s+", " ", line).strip() for line in content.splitlines()]
    # Remove leading/trailing empty lines
    text = "\n".join(lines).strip()
    # Collapse 2+ blank lines → one blank line
    text = re.sub(r"\n{2,}", "\n\n", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── State management ───────────────────────────────────────────────────────────

def load_state() -> dict[str, Any]:
    """
    Load the previous run state from state.json.

    State schema:
      {
        "<filename.md>": {
          "hash": "<sha256>",
          "document_name": "<gemini-resource-name>",
          "updated_at": "<iso8601>"
        },
        ...
      }

    Returns:
        Dict of previous article states, or empty dict if no state exists.
    """
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.getLogger(__name__).warning("Corrupted state.json — starting fresh.")
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Persist the current run state to state.json (pretty-printed)."""
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Delta logic ────────────────────────────────────────────────────────────────

def compute_delta(
    article_paths: list[Path],
    prev_state: dict[str, Any],
) -> tuple[list[Path], list[Path], list[Path]]:
    """
    Classify each article file as new, changed, or unchanged.

    Args:
        article_paths: All .md files found after scraping.
        prev_state: State from the previous run.

    Returns:
        Tuple of (new_files, changed_files, skipped_files).
    """
    new_files: list[Path] = []
    changed_files: list[Path] = []
    skipped_files: list[Path] = []

    for path in article_paths:
        content = path.read_text(encoding="utf-8")
        current_hash = compute_hash(content)
        name = path.name

        if name not in prev_state:
            new_files.append(path)
        elif prev_state[name]["hash"] != current_hash:
            changed_files.append(path)
        else:
            skipped_files.append(path)

    return new_files, changed_files, skipped_files


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run() -> None:
    """
    Execute the full scrape → diff → upload daily job.

    Always exits with code 0; partial failures are logged.
    """
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("OptiBot daily sync job started")
    logger.info("=" * 60)

    # ── Step 1: Scrape ────────────────────────────────────────────
    try:
        article_paths = scraper.scrape(min_articles=30)
        logger.info("Scraper produced %d article files.", len(article_paths))
    except Exception as exc:
        logger.error("Scraping failed completely: %s", exc)
        logger.info("Job finished with errors. exit=0")
        sys.exit(0)

    if not article_paths:
        logger.warning("No articles found. Nothing to upload.")
        sys.exit(0)

    # ── Step 2: Load previous state & compute delta ───────────────
    prev_state = load_state()
    new_files, changed_files, skipped_files = compute_delta(article_paths, prev_state)

    logger.info(
        "Delta: added=%d, updated=%d, skipped=%d",
        len(new_files), len(changed_files), len(skipped_files),
    )

    if not new_files and not changed_files:
        logger.info("No changes detected — nothing to upload.")
        print(f"added=0, updated=0, skipped={len(skipped_files)}")
        sys.exit(0)

    # ── Step 3: Connect to Gemini ─────────────────────────────────
    try:
        client = uploader.get_client()
        store = uploader.get_or_create_store(client)
    except Exception as exc:
        logger.error("Cannot connect to Gemini: %s", exc)
        sys.exit(0)

    # ── Step 4: Process new articles ──────────────────────────────
    added = 0
    updated = 0
    failed = 0
    new_state: dict[str, Any] = {k: v for k, v in prev_state.items()}

    for path in new_files:
        doc = uploader.upload_document(client, store.name, path)
        if doc:
            content = path.read_text(encoding="utf-8")
            new_state[path.name] = {
                "hash": compute_hash(content),
                "document_name": doc.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            added += 1
            logger.info("ADDED: %s", path.name)
        else:
            failed += 1

    # ── Step 5: Process changed articles ─────────────────────────
    for path in changed_files:
        old_doc_name = prev_state.get(path.name, {}).get("document_name")

        # Delete stale document first (avoids duplicate content in search)
        if old_doc_name:
            deleted = uploader.delete_document(client, old_doc_name)
            if not deleted:
                logger.warning(
                    "Could not delete old doc '%s' — uploading new version anyway.",
                    old_doc_name,
                )

        # Upload fresh version
        doc = uploader.upload_document(client, store.name, path)
        if doc:
            content = path.read_text(encoding="utf-8")
            new_state[path.name] = {
                "hash": compute_hash(content),
                "document_name": doc.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            updated += 1
            logger.info("UPDATED: %s", path.name)
        else:
            # Keep old state entry intact so we retry next run
            failed += 1

    # ── Step 6: Persist state ─────────────────────────────────────
    save_state(new_state)

    # ── Step 7: Summary log ───────────────────────────────────────
    summary = f"added={added}, updated={updated}, skipped={len(skipped_files)}"
    if failed:
        summary += f", failed={failed}"

    logger.info("=" * 60)
    logger.info("Job complete: %s", summary)
    logger.info("=" * 60)

    # Also print to stdout (for Render Cron Job log visibility)
    print(summary)

    # Run quick smoke-test query to validate the store is functional
    try:
        logger.info("Running smoke-test query: 'How do I add a YouTube video?'")
        answer = uploader.ask_bot(client, store.name, "How do I add a YouTube video?")
        logger.info("Bot response preview: %s", answer[:300])
    except Exception as exc:
        logger.warning("Smoke-test query failed (non-fatal): %s", exc)


if __name__ == "__main__":
    run()
    sys.exit(0)
