"""Extract a web page's main article text + metadata (pure — no network).

Uses `trafilatura` (the same engine behind many read-it-later/clipper tools) for
main-content extraction, dropping nav/boilerplate. `trafilatura` is imported lazily
inside the extractor — like the kaggle provider's client — so a missing/broken
install can't break backend boot, and code paths that never extract don't import
it. A regex tag-strip is the last-resort fallback.

Fetching lives elsewhere on purpose: blog ingest goes through the browser module's
SSRF guard (`browser.fetch.fetch_readable`), which imports `extract_article` from
here. This module fetching for itself is exactly the unguarded URL sink that guard
was built to close.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t]+")


@dataclass
class Article:
    title: str
    author: str | None
    text: str
    url: str


def extract_article(html: str, url: str) -> Article:
    """Pure extraction (no network) — split out so it's unit-testable on raw HTML."""
    title: str | None = None
    author: str | None = None
    text = ""

    try:
        import trafilatura

        text = (
            trafilatura.extract(html, include_comments=False, include_tables=False)
            or ""
        )
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = meta.title or None
            author = meta.author or None
    except Exception:  # noqa: BLE001 — any extractor failure falls back below
        text = ""

    if not text:
        text = _strip_tags(html)
    if not title:
        title = _html_title(html) or url

    return Article(title=title, author=author, text=text, url=url)


def _html_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    return _WS_RE.sub(" ", m.group(1)).strip() if m else None


def _strip_tags(html: str) -> str:
    # Drop script/style blocks, then tags, then collapse whitespace.
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.I)
    text = _TAG_RE.sub(" ", html)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
