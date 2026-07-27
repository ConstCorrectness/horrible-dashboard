"""ArXiv Atom API client — search, paper detail, and PDF location.

`https://export.arxiv.org/api/query` is keyless but asks for etiquette: one
request per ~3 seconds per client. A module-level lock + monotonic timestamp
enforce the spacing process-wide, and a small TTL cache absorbs repeated
queries (the panel's search-as-you-refine and an agent retrying are the same
query many times in a row).

Spacing alone isn't enough: arXiv also rate-limits over longer windows and
answers **429** (or a 503 while it sheds load), which the throttle can't
predict. Those two statuses are retried with a backoff that honors
`Retry-After`, and the resulting cooldown is process-wide too — otherwise a
research run fanning out N queries burns its whole retry budget N times over.
A rate-limited lookup falls back to a *stale* cache entry when there is one,
because a ten-minute-old abstract beats an error.

Parsing is stdlib `xml.etree.ElementTree` — the Atom namespaces are stable and
a dependency would buy nothing. IDs are validated against the two arXiv id
grammars (new `2107.03374v2` / old `cs/0112017`) before any URL is built from
them, so a hostile "id" can't smuggle path segments into the request.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"
_UA = "horrible-dashboard/0.1 (research module; mailto:unset)"
_MIN_INTERVAL_S = 3.0
_CACHE_TTL_S = 600.0
_CACHE_MAX = 100
_TIMEOUT = 20.0

_RETRY_STATUSES = frozenset({429, 503})
_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_AFTER_S = 10.0
# Past this, waiting out the cooldown in-request would just hold a request
# handler open; fail fast and let the caller decide.
_MAX_BLOCKING_WAIT_S = 30.0

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# New-style (2107.03374, optional vN) or old-style (cs/0112017, archive[.sub]/YYMMNNN).
_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}(v\d+)?|[a-z-]+(\.[A-Z]{2})?/\d{7}(v\d+)?)$")

_lock = asyncio.Lock()
_last_request = 0.0
_penalty_until = 0.0  # monotonic deadline set by a 429/503
_cache: dict[str, tuple[float, Any]] = {}


class ArxivError(RuntimeError):
    """A request to the arXiv API failed or returned something unparseable."""


class ArxivRateLimited(ArxivError):
    """arXiv answered 429/503 — back off for `retry_after` seconds."""

    def __init__(self, retry_after: float, message: str = "") -> None:
        self.retry_after = max(1.0, retry_after)
        super().__init__(
            message
            or f"arXiv is rate-limiting this node; retry in {self.retry_after:.0f}s"
        )


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


def _retry_after_seconds(resp: httpx.Response, fallback: float) -> float:
    """`Retry-After` as seconds — the header is either a delay or an HTTP date."""
    raw = (resp.headers.get("retry-after") or "").strip()
    if not raw:
        return fallback
    try:
        return max(1.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return fallback
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(1.0, (when - datetime.now(timezone.utc)).total_seconds())


async def _throttled_get(url: str) -> str:
    """One API request, honoring the 3-second etiquette and 429 backoff process-wide.

    Retries are taken while holding the lock: everyone else is meant to be
    waiting out the same cooldown anyway.
    """
    global _last_request, _penalty_until
    async with _lock:
        penalty = _penalty_until - time.monotonic()
        if penalty > _MAX_BLOCKING_WAIT_S:
            raise ArxivRateLimited(penalty)
        wait = max(_MIN_INTERVAL_S - (time.monotonic() - _last_request), penalty)
        limited: ArxivRateLimited | None = None
        for attempt in range(_MAX_ATTEMPTS):
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(
                    timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code in _RETRY_STATUSES:
                        retry_after = _retry_after_seconds(
                            resp, _DEFAULT_RETRY_AFTER_S * (2**attempt)
                        )
                        _penalty_until = time.monotonic() + retry_after
                        limited = ArxivRateLimited(retry_after)
                        logger.warning(
                            "arXiv %s on attempt %d/%d; backing off %.0fs",
                            resp.status_code,
                            attempt + 1,
                            _MAX_ATTEMPTS,
                            retry_after,
                        )
                        wait = min(retry_after, _MAX_BLOCKING_WAIT_S)
                        continue
                    resp.raise_for_status()
                    _penalty_until = 0.0
                    return resp.text
            finally:
                _last_request = time.monotonic()
        raise limited or ArxivError("arXiv request failed")


def _cached(key: str, *, max_age: float | None = _CACHE_TTL_S) -> Any | None:
    """Cached value for `key`. `max_age=None` accepts an expired (stale) entry."""
    hit = _cache.get(key)
    if hit is None:
        return None
    stamp, value = hit
    if max_age is not None and time.monotonic() - stamp > max_age:
        return None
    return value


def _remember(key: str, value: Any) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Drop the oldest entry; a real LRU is overkill for 100 slots.
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]
    _cache[key] = (time.monotonic(), value)


async def _feed(url: str) -> tuple[int, list[ArxivEntry]]:
    """Parsed feed for `url` — cached, and stale-on-rate-limit rather than error."""
    hit = _cached(url)
    if hit is not None:
        return hit
    try:
        result = parse_feed(await _throttled_get(url))
    except ArxivRateLimited:
        stale = _cached(url, max_age=None)
        if stale is None:
            raise
        logger.warning("arXiv rate-limited; serving stale cache for %s", url)
        return stale
    _remember(url, result)
    return result


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
    return await _feed(f"{API_URL}?{params}")


async def get_paper(arxiv_id: str) -> ArxivEntry:
    if not valid_id(arxiv_id):
        raise ArxivError(f"not an arXiv id: {arxiv_id!r}")
    url = f"{API_URL}?{urlencode({'id_list': arxiv_id})}"
    _total, entries = await _feed(url)
    if not entries or not entries[0].id:
        raise ArxivError(f"arXiv has no paper {arxiv_id!r}")
    return entries[0]
