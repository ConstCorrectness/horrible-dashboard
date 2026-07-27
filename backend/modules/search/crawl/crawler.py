"""The focused crawler: a bounded BFS over a seed's own domain, into `webindex`.

Deliberately small. This is not a web crawler in the Common Crawl sense — the honest
economics say a general index loses to a search API below roughly 60M queries/month.
What it *is* good at is the thing search APIs are worst at: a handful of sites you
read constantly (framework docs, a few ML blogs, an API reference), indexed in full,
searchable instantly, free per query, and current for the sites that matter to you.

**No JavaScript rendering.** The `browser-engine` extra could drive Playwright here,
but a rendered crawl is roughly 100× the cost per page, and at 200 pages a seed the
trade is obviously wrong. JS-only docs sites simply don't index. That's a known,
accepted gap — please don't "fix" it by wiring in Chromium.

**The two costs that actually bite**, both handled here:

- `upsert_documents` is a whole-table `merge_insert`, ~1.5s per call regardless of
  batch size. A docs page is 3–10 chunks, so flushing per page would spend five
  minutes in Arrow to index two hundred pages. The buffer therefore spans **pages**,
  not chunks within a page.
- Embedding is the other half, and `crawl_pages`'s content hash is what avoids it: an
  unchanged page costs a conditional GET and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from backend.modules.search.canonical import canonical_url, host_of
from backend.modules.search.crawl import index as webindex
from backend.modules.search.crawl import store
from backend.modules.search.crawl.robots import USER_AGENT, HostLimiter, RobotsCache

logger = logging.getLogger(__name__)

_MAX_PAGE_BYTES = 3_000_000
_FETCH_TIMEOUT_S = 20.0
# Below this, "extracted text" is a nav bar or a cookie banner, not a document.
_MIN_TEXT_CHARS = 200

ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class CrawlStats:
    seed_id: str
    fetched: int = 0
    indexed: int = 0
    unchanged: int = 0
    not_modified: int = 0
    skipped: int = 0
    errors: int = 0
    chunks: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "fetched": self.fetched,
            "indexed": self.indexed,
            "unchanged": self.unchanged,
            "not_modified": self.not_modified,
            "skipped": self.skipped,
            "errors": self.errors,
            "chunks": self.chunks,
            "notes": self.notes,
        }


class _LinkParser(HTMLParser):
    """Collect `<a href>` values. stdlib rather than a parser dependency — we need
    hrefs, and trafilatura is already doing the content extraction."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute http(s) links from a page, fragments dropped. Pure — testable."""
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — malformed HTML is the normal case
        pass
    out: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        href = href.strip()
        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:", "data:")
        ):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        clean = absolute.split("#", 1)[0]
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def in_scope(url: str, spec: dict[str, Any]) -> bool:
    """Whether a URL belongs to this seed. Pure — this is the rule worth testing.

    Host match allows subdomains of an allowed domain (`docs.example.com` under
    `example.com`) but not suffix collisions (`notexample.com`).
    """
    host = host_of(url)
    if not host:
        return False
    allowed = [d.lower().removeprefix("www.") for d in spec.get("allow_domains") or []]
    if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
        return False

    path = urlsplit(url).path or "/"
    allow_patterns = spec.get("allow_patterns") or []
    if allow_patterns and not any(re.search(p, path) for p in allow_patterns):
        return False
    return not any(re.search(p, url) for p in spec.get("deny_patterns") or [])


def content_hash(text: str) -> str:
    """Hash of the *extracted* text, not the raw HTML.

    Deliberate: docs sites embed build ids, CSRF tokens and rotating banner copy in
    the markup, so hashing HTML would report a change on every crawl and defeat the
    whole skip path. The extracted article is what we actually index, so it's what
    "changed" should mean.
    """
    return hashlib.sha256(" ".join(text.split()).encode("utf-8", "replace")).hexdigest()


async def crawl_seed(
    seed_id: str,
    *,
    force: bool = False,
    on_progress: ProgressFn | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> CrawlStats:
    """Crawl one seed into `webindex`. Returns what happened.

    Never raises for ordinary web trouble — a dead host, a 500, an unparseable page
    are all recorded and stepped over. It *does* stop early when the embedding
    provider is unavailable, because continuing would write hash-fallback vectors
    that permanently pin the collection's width.
    """
    from backend.modules.database.vectorstore import init_db

    seed = store.get_seed(seed_id)
    if seed is None:
        raise ValueError(f"unknown crawl seed {seed_id!r}")
    spec = seed["config"]
    stats = CrawlStats(seed_id=seed_id)

    init_db()
    robots = RobotsCache()
    limiter = HostLimiter(_crawl_delay_setting())

    max_depth = int(spec.get("max_depth") or 2)
    max_pages = int(spec.get("max_pages") or 200)
    tags = list(spec.get("tags") or [])

    frontier: deque[tuple[str, int]] = deque(
        (str(u), 0) for u in spec.get("start_urls") or []
    )
    seen: set[str] = {canonical_url(u) for u, _d in frontier}

    # Re-check everything already indexed, not just what this pass can rediscover.
    # On a re-crawl the start page answers 304, which means no HTML and therefore no
    # links — so a frontier built purely from discovery would check one URL and stop,
    # and a changed article would never be seen again. Known pages cost a conditional
    # request each and almost always come back 304.
    for url in store.known_urls(seed_id):
        key = canonical_url(url)
        if key not in seen:
            seen.add(key)
            frontier.append((url, max_depth))
    pending: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)

    def progress() -> None:
        if on_progress:
            on_progress(stats.as_dict())

    while frontier and stats.fetched < max_pages:
        if is_cancelled and is_cancelled():
            stats.notes.append("cancelled")
            break

        url, depth = frontier.popleft()
        canonical = canonical_url(url)

        if not await robots.allowed(url):
            stats.skipped += 1
            continue
        limiter.note_crawl_delay(urlsplit(url).netloc, await robots.crawl_delay(url))

        known = store.get_page(canonical, seed_id)
        conditional: dict[str, str] = {}
        if known and not force:
            if known.get("etag"):
                conditional["If-None-Match"] = str(known["etag"])
            if known.get("last_modified"):
                conditional["If-Modified-Since"] = str(known["last_modified"])

        host = urlsplit(url).netloc
        await limiter.acquire(host)
        try:
            outcome = await _fetch(url, conditional)
        finally:
            limiter.release(host)

        if outcome.get("gone"):
            # 404/410: forget it rather than leaving a phantom in the index.
            webindex.forget_page(canonical)
            store.drop_page(canonical, seed_id)
            stats.skipped += 1
            progress()
            continue
        if error := outcome.get("error"):
            stats.errors += 1
            store.record_page(
                canonical, seed_id, content_hash="", status="error", error=str(error)
            )
            progress()
            continue

        stats.fetched += 1

        # Level 1: the server said nothing changed. No body, no extraction, no embed.
        if outcome.get("not_modified"):
            stats.not_modified += 1
            store.touch_page(canonical, seed_id)
            progress()
            continue

        html = str(outcome["html"])
        final_url = str(outcome["final_url"])

        if depth < max_depth:
            for link in extract_links(html, final_url):
                key = canonical_url(link)
                if key in seen or not in_scope(link, spec):
                    continue
                seen.add(key)
                frontier.append((link, depth + 1))

        from backend.modules.library.extract import extract_article

        article = extract_article(html, final_url)
        text = (article.text or "").strip()
        if len(text) < _MIN_TEXT_CHARS:
            stats.skipped += 1
            store.record_page(
                canonical,
                seed_id,
                title=article.title,
                content_hash="",
                status="error",
                error="no extractable content",
            )
            progress()
            continue

        digest = content_hash(text)

        # Level 2: body came back but says the same thing. Skip the expensive half.
        if known and not force and known.get("content_hash") == digest:
            stats.unchanged += 1
            store.touch_page(canonical, seed_id)
            progress()
            continue

        # Level 3: genuinely new or changed — replace, don't duplicate.
        webindex.forget_page(canonical)
        chunks = _chunk(text)
        for i, chunk in enumerate(chunks):
            pending.append(
                (
                    webindex.doc_id(canonical, i),
                    chunk,
                    {
                        "url": final_url,
                        "title": article.title or canonical,
                        "seed_id": seed_id,
                        "tags": tags,
                        "chunk_index": i,
                        "crawled_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                    },
                )
            )
        store.record_page(
            canonical,
            seed_id,
            title=article.title,
            etag=outcome.get("etag"),
            last_modified=outcome.get("last_modified"),
            content_hash=digest,
            chunk_count=len(chunks),
            status="ok",
            indexed=True,
        )
        stats.indexed += 1
        stats.chunks += len(chunks)

        # The buffer spans pages on purpose — see the module docstring.
        if len(pending) >= webindex.EMBED_CHUNK:
            if note := await _flush(pending):
                stats.notes.append(note)
                stats.errors += 1
                break
        progress()

    if pending:
        if note := await _flush(pending):
            stats.notes.append(note)
            stats.errors += 1

    progress()
    return stats


def _crawl_delay_setting() -> float:
    from backend.modules.settings.routes import get_value

    try:
        return max(0.0, float(get_value("search.crawlDelaySeconds", 1.0) or 1.0))
    except (TypeError, ValueError):
        return 1.0


def _chunk(text: str) -> list[str]:
    from backend.modules.library.chunking import chunk_text
    from backend.modules.settings.routes import get_value

    return chunk_text(text, size=int(get_value("library.chunkSize", 1000) or 1000))


async def _fetch(url: str, conditional: dict[str, str]) -> dict[str, Any]:
    """One guarded, conditional GET. Returns an outcome dict, never raises."""
    import httpx

    from backend.modules.browser.fetch import UnsafeUrlError, _fetch_guarded

    try:
        final_url, resp = await asyncio.wait_for(
            _fetch_guarded(
                url,
                accept=("html", "xml", "text"),
                max_bytes=_MAX_PAGE_BYTES,
                user_agent=USER_AGENT,
                headers=conditional or None,
            ),
            timeout=_FETCH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return {"error": "timed out"}
    except UnsafeUrlError as exc:
        return {"error": f"blocked: {exc}"}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return {"gone": True}
        return {"error": f"HTTP {exc.response.status_code}"}
    except httpx.HTTPError as exc:
        return {"error": str(exc)}

    if resp.status_code == 304:
        return {"not_modified": True, "final_url": final_url}
    return {
        "html": resp.text,
        "final_url": final_url,
        "etag": resp.headers.get("etag"),
        "last_modified": resp.headers.get("last-modified"),
    }


async def _flush(pending: list[tuple[str, str, dict[str, Any]]]) -> str | None:
    """Embed and write one buffer. Returns an error note, or None on success.

    Refuses to write hash-fallback vectors: a collection's vector width is fixed at
    creation, so one crawl run started while the embedder was down would pin
    `webindex` to 384-dim noise permanently.
    """
    from backend.modules.database.embeddings import get_embeddings
    from backend.modules.database.vectorstore import upsert_documents

    batch = list(pending)
    pending.clear()
    if not batch:
        return None

    try:
        vectors, method = await get_embeddings([text for _id, text, _meta in batch])
    except Exception as exc:  # noqa: BLE001
        return f"embedding failed: {exc}"

    if webindex.is_fallback(method):
        return (
            "the embedding provider is unavailable — stopping rather than writing "
            "hash-fallback vectors, which would permanently pin the index"
        )
    if len(vectors) != len(batch):
        return "embedding returned the wrong number of vectors"

    dim = len(vectors[0])
    if drift := webindex.drift_error(method, dim):
        return drift

    rows = [
        (doc_id, text, {**meta, "embed_model": method}, vector)
        for (doc_id, text, meta), vector in zip(batch, vectors)
    ]
    try:
        await asyncio.to_thread(upsert_documents, webindex.COLLECTION, rows)
    except Exception as exc:  # noqa: BLE001
        return f"index write failed: {exc}"

    webindex.note_build(method, dim, webindex.status().get("docs", 0))
    return None
