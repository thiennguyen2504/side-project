"""
scraper.py — Fetches articles from a Zendesk Help Center API
and converts them to clean Markdown files saved under data/articles/.

Uses the Zendesk Help Center REST API with pagination support.
Each article gets a slug-based filename and an "Article URL:" header line.
"""

import os
import re
import time
import logging
import hashlib
from pathlib import Path
from typing import Optional

import requests
from markdownify import markdownify as md

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_API_URL = "https://support.optisigns.com/api/v2/help_center/articles.json"
ARTICLES_DIR = Path("data/articles")
MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds

logger = logging.getLogger(__name__)


# ── Utility helpers ────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """
    Convert an article title to a lowercase, hyphen-separated slug
    safe for use as a filename.

    Example: "How to Add a YouTube Video?" → "how-to-add-a-youtube-video"
    """
    text = text.lower().strip()
    # Replace non-alphanumeric chars (except hyphens) with hyphens
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")[:80]  # cap at 80 chars to avoid FS limits


def request_with_retry(
    url: str,
    params: Optional[dict] = None,
    max_retries: int = MAX_RETRIES,
) -> requests.Response:
    """
    Perform a GET request with exponential-backoff retry (up to max_retries).

    Raises:
        requests.HTTPError: if all retries are exhausted.
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            wait = BACKOFF_BASE ** attempt
            if attempt < max_retries:
                logger.warning(
                    "Request to %s failed (attempt %d/%d): %s — retrying in %ds",
                    url, attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "Request to %s failed after %d attempts: %s",
                    url, max_retries, exc,
                )
                raise


def clean_html_to_markdown(html: str, article_url: str, title: str) -> str:
    """
    Convert raw article HTML to clean Markdown.

    Steps:
      1. markdownify converts HTML → raw MD.
      2. Post-process: strip leftover HTML tags, compress blank lines,
         remove nav/breadcrumb artifacts.
      3. Prepend "Article URL:" header line right after the title.

    Returns:
        A clean Markdown string.
    """
    # markdownify: strip= and convert= are mutually exclusive.
    # Use strip= to remove noise elements; markdownify naturally converts
    # all content tags (headings, lists, code, links, tables, etc.).
    raw_md = md(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "nav", "header", "footer", "aside",
               "form", "button", "iframe", "noscript", "meta"],
    )

    # Normalise Unicode whitespace: &nbsp; (\xa0), zero-width spaces,
    # soft hyphens, etc. that Zendesk HTML frequently contains.
    raw_md = raw_md.replace("\xa0", " ")          # non-breaking space
    raw_md = raw_md.replace("\u200b", "")          # zero-width space
    raw_md = raw_md.replace("\u00ad", "")          # soft hyphen
    raw_md = raw_md.replace("\u2019", "'")         # curly right apostrophe
    raw_md = raw_md.replace("\u2018", "'")         # curly left apostrophe
    raw_md = raw_md.replace("\u201c", '"')         # curly left double quote
    raw_md = raw_md.replace("\u201d", '"')         # curly right double quote

    # Remove any residual HTML tags
    raw_md = re.sub(r"<[^>]+>", "", raw_md)

    # Collapse 3+ consecutive blank lines to 2
    raw_md = re.sub(r"\n{3,}", "\n\n", raw_md)

    # Remove common Zendesk navigation artifacts that bleed through
    nav_patterns = [
        r"(?m)^(Home|Back|Next|Previous|Skip to main content|Was this article helpful\?.*)\s*$",
        r"(?m)^(Yes|No)\s*\d*\s*(out of|found this helpful).*$",
        r"(?m)^\d+ (comments?|people found this helpful).*$",
        r"(?m)^Search.*$",
        r"(?m)^Sign in\s*$",
        r"(?m)^Submit a request\s*$",
    ]
    for pat in nav_patterns:
        raw_md = re.sub(pat, "", raw_md)

    # Final cleanup of trailing whitespace per line
    lines = [line.rstrip() for line in raw_md.splitlines()]
    clean = "\n".join(lines).strip()

    # Build final document: title + article URL + content
    header = f"# {title}\n\nArticle URL: {article_url}\n\n"
    return header + clean


def fetch_articles(per_page: int = 100) -> list[dict]:
    """
    Fetch all articles from the Zendesk Help Center API using pagination.

    Returns:
        A list of article dicts, each containing at least:
        id, title, html_url, body (HTML string).
    """
    articles: list[dict] = []
    url: Optional[str] = BASE_API_URL
    params = {"per_page": per_page}
    page = 1

    while url:
        logger.info("Fetching page %d: %s", page, url)
        resp = request_with_retry(url, params=params if page == 1 else None)
        data = resp.json()
        batch = data.get("articles", [])
        articles.extend(batch)
        logger.info("  → Got %d articles (total so far: %d)", len(batch), len(articles))
        url = data.get("next_page")  # None when last page
        page += 1
        # Respect rate limits
        time.sleep(0.3)

    return articles


def save_article(article: dict) -> Optional[Path]:
    """
    Convert a single Zendesk article dict to a clean Markdown file and save it.

    Skips articles with empty body (draft/redirect articles).

    Returns:
        The Path of the saved file, or None if skipped.
    """
    title: str = article.get("title", "").strip()
    body: str = article.get("body", "") or ""
    html_url: str = article.get("html_url", "")

    if not body.strip():
        logger.warning("Skipping empty article: '%s' (%s)", title, html_url)
        return None

    if not title:
        logger.warning("Skipping article with no title: %s", html_url)
        return None

    slug = slugify(title)
    if not slug:
        # Fallback to article id if title produces empty slug
        slug = str(article.get("id", "unknown"))

    filename = ARTICLES_DIR / f"{slug}.md"

    # Handle filename collision (two different articles → same slug)
    if filename.exists():
        article_id = article.get("id", "")
        filename = ARTICLES_DIR / f"{slug}-{article_id}.md"

    markdown = clean_html_to_markdown(body, html_url, title)
    filename.write_text(markdown, encoding="utf-8")
    logger.debug("Saved: %s", filename)
    return filename


def scrape(min_articles: int = 30) -> list[Path]:
    """
    Main entry point: scrape ≥ min_articles from the Zendesk Help Center
    and write them as Markdown files to data/articles/.

    Returns:
        List of Paths to successfully saved Markdown files.
    """
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    articles = fetch_articles()

    if len(articles) < min_articles:
        logger.warning(
            "Only %d articles fetched; expected at least %d.",
            len(articles), min_articles,
        )

    saved: list[Path] = []
    for art in articles:
        path = save_article(art)
        if path:
            saved.append(path)

    logger.info("Scraping complete: %d/%d articles saved.", len(saved), len(articles))
    return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    paths = scrape()
    print(f"Saved {len(paths)} articles to {ARTICLES_DIR}")
