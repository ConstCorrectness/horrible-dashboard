"""ArXiv Atom API client — search, paper detail, and PDF location.

`https://export.arxiv.org/api/query` is keyless but asks for etiquette: one
request per ~3 seconds per client. A module-level lock + monotonic timestamp
enforce the spacing process-wide, and a small TTL cache absorbs repeated
queries (the panel's search-as-you-refine and an agent retrying are the same
query many times in a row).

Parsing is stdlib `xml.etree.ElementTree` — the Atom namespaces are stable and
a dependency would buy nothing. IDs are validated against the two arXiv id
grammars (new `2107.03374v2` / old `cs/0112017`) before any URL is built from
them, so a hostile "id" can't smuggle path segments into the request.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

API_URL = "https://export.arxiv.org/api/query"
_UA = "horrible-dashboard/0.1 (research module; mailto:unset)"
_MIN_INTERVAL_S = 3.0
_CACHE_TTL_S = 600.0
_CACHE_MAX = 100
_TIMEOUT = 20.0

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# New-style (2107.03374, optional vN) or old-style (cs/0112017, archive[.sub]/YYMMNNN).
_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}(v\d+)?|[a-z-]+(\.[A-Z]{2})?/\d{7}(v\d+)?)$")

_lock = asyncio.Lock()
_last_request = 0.0
_cache: dict[str, tuple[float, Any]] = {}


class ArxivError(RuntimeError):
    """A request to the arXiv API failed or returned something unparseable."""


def valid_id(arxiv_id: str) -> bool:
    return bool(_ID_RE.match(arxiv_id))


@dataclass
class ArxivEntry:
    id: str
    title: str
    summary: str
    authors: list[str] = field(default_factory=list)
    published: str = ""
    updated: str = ""
    categories: list[str] = field(default_factory=list)
    pdf_url: str = ""
    abs_url: str = ""
    comment: str | None = None
    doi: str | None = None


def _text(el: ElementTree.Element | None) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def _parse_entry(entry: ElementTree.Element) -> ArxivEntry:
    raw_id = _text(entry.find("atom:id", _NS))  # http://arxiv.org/abs/2107.03374v2
    arxiv_id = raw_id.rsplit("/abs/", 1)[-1]
    pdf_url = ""
    abs_url = raw_id
    for link in entry.findall("atom:link", _NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href") or ""
        elif link.get("rel") == "alternate":
            abs_url = link.get("href") or abs_url
    return ArxivEntry(
        id=arxiv_id,
        title=_text(entry.find("atom:title", _NS)),
        summary=_text(entry.find("atom:summary", _NS)),
        authors=[
            _text(a.find("atom:name", _NS)) for a in entry.findall("atom:author", _NS)
        ],
        published=_text(entry.find("atom:published", _NS)),
        updated=_text(entry.find("atom:updated", _NS)),
        categories=[c.get("term") or "" for c in entry.findall("atom:category", _NS)],
        pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        abs_url=abs_url,
        comment=_text(entry.find("arxiv:comment", _NS)) or None,
        doi=_text(entry.find("arxiv:doi", _NS)) or None,
    )


def parse_feed(xml_text: str) -> tuple[int, list[ArxivEntry]]:
    """`(total_results, entries)` from an Atom feed. Pure — fixture-testable."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ArxivError(f"unparseable arXiv response: {exc}") from exc
    total = int(_text(root.find("opensearch:totalResults", _NS)) or 0)
    entries = [_parse_entry(e) for e in root.findall("atom:entry", _NS)]
    return total, entries


async def _throttled_get(url: str) -> str:
    """One API request, honoring the 3-second etiquette process-wide."""
    global _last_request
    async with _lock:
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        finally:
            _last_request = time.monotonic()


def _cached(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    stamp, value = hit
    if time.monotonic() - stamp > _CACHE_TTL_S:
        del _cache[key]
        return None
    return value


def _remember(key: str, value: Any) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Drop the oldest entry; a real LRU is overkill for 100 slots.
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]
    _cache[key] = (time.monotonic(), value)


async def search(
    query: str,
    *,
    start: int = 0,
    max_results: int = 20,
    category: str | None = None,
    sort: str = "relevance",
) -> tuple[int, list[ArxivEntry]]:
    """Search arXiv. `sort` is `relevance` | `lastUpdatedDate` | `submittedDate`."""
    search_query = f"all:{query}" if query else ""
    if category:
        prefix = f"cat:{category}"
        search_query = f"{prefix} AND ({search_query})" if search_query else prefix
    if not search_query:
        raise ArxivError("empty query")
    params = urlencode(
        {
            "search_query": search_query,
            "start": max(0, start),
            "max_results": min(max(1, max_results), 100),
            "sortBy": sort
            if sort in ("relevance", "lastUpdatedDate", "submittedDate")
            else "relevance",
            "sortOrder": "descending",
        }
    )
    url = f"{API_URL}?{params}"
    cached = _cached(url)
    if cached is not None:
        return cached
    result = parse_feed(await _throttled_get(url))
    _remember(url, result)
    return result


async def get_paper(arxiv_id: str) -> ArxivEntry:
    if not valid_id(arxiv_id):
        raise ArxivError(f"not an arXiv id: {arxiv_id!r}")
    url = f"{API_URL}?{urlencode({'id_list': arxiv_id})}"
    cached = _cached(url)
    if cached is None:
        cached = parse_feed(await _throttled_get(url))
        _remember(url, cached)
    _total, entries = cached
    if not entries or not entries[0].id:
        raise ArxivError(f"arXiv has no paper {arxiv_id!r}")
    return entries[0]
