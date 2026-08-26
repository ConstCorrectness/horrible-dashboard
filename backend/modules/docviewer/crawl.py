"""The doc-set crawl: a bounded walk under one URL prefix, capturing each page.

This is deliberately *not* the search module's focused crawler. That one indexes text
into `webindex` and refuses Chromium on purpose — at 200 pages a seed, a rendered
crawl is the wrong trade for retrieval. Here the rendered page **is** the product:
the whole point of a doc set is that you can read it offline with its CSS and its
JavaScript, so every page goes through a real headless browser. The two crawlers
share politeness (`robots.py`) and URL identity (`canonical.py`) and nothing else;
`search/crawl/crawler.py` is not touched.

Three things worth knowing before changing anything here:

- **Links are rewritten during capture, not after.** A page's archive addresses its
  siblings by `store.page_id`, which is derived from the URL and so is known before
  the target has been fetched. Using artifact ids instead is impossible, not merely
  awkward: the artifact store is content-addressed, so two pages that link to each
  other would each need the other's bytes to compute their own.
- **A crawl runs detached, under a semaphore, off the shared task queue.** That queue
  is serial, and a 200-page crawl parked in it would hold up every library ingest
  behind it for ten minutes. The semaphore is 1 because two crawls means two
  Chromiums competing for the same cores.
- **Ingest never raises.** `ingest_source` records failure on the source row rather
  than throwing, so a page is only reported captured after the row is read back and
  found to have chunks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from backend.modules.artifacts.store import store_bytes
from backend.modules.docviewer import store
from backend.modules.docviewer.broadcast import (
    publish_page,
    publish_progress,
    publish_set,
)
from backend.modules.docviewer.models import (
    DEFAULT_MAX_DEPTH,
    TOTAL_SET_BYTES_CAP,
)
from backend.modules.search.canonical import canonical_url, host_of
from backend.modules.search.crawl.robots import HostLimiter, RobotsCache
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

# One crawl at a time, process-wide. Each holds a Chromium; two would halve both.
_crawl_slot = asyncio.Semaphore(1)
_running: dict[str, asyncio.Task[None]] = {}

_NAV_SETTLE_MS = 400
_PAGE_TIMEOUT_S = 90.0


def default_prefix(seed_url: str) -> str:
    """The seed's own directory — what "these docs" almost always means.

    `https://x.dev/docs/start` bounds the set to `https://x.dev/docs/`, so the crawl
    picks up siblings without wandering into the marketing site. A seed that is
    already a directory keeps its whole path.
    """
    parts = urlsplit(urldefrag(seed_url)[0])
    path = parts.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def _in_scope(url: str, prefix: str) -> bool:
    """Scope is compared on canonical forms, so `www.` and `http` don't fork it."""
    return canonical_url(url).startswith(canonical_url(prefix).rstrip("/"))


def _links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Every `<a href>` in the built page, as (absolute_url, fragment)."""
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001 — a page we can't parse simply has no links
        return []
    out: list[tuple[str, str]] = []
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        target, fragment = urldefrag(absolute)
        out.append((target, fragment))
    return out


def rewrite_links(html: str, base_url: str, set_id: str, prefix: str) -> str:
    """Point in-scope links at their sibling archives; leave the rest alone.

    An out-of-scope link keeps its absolute URL, which under the archive's CSP is
    simply inert — the sandbox has no `allow-top-navigation`, so clicking it does
    nothing. That is the intended behaviour: the way out of the archive is the pane's
    "live" button, not a link that silently navigates the frame to the open web.
    """
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return html
    changed = False
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        target, fragment = urldefrag(absolute)
        if not _in_scope(target, prefix):
            a.set("href", absolute)  # absolute, and inert under the CSP
            changed = True
            continue
        pid = store.page_id(set_id, canonical_url(target))
        rewritten = f"/api/docviewer/pages/{pid}/content"
        if fragment:
            rewritten += f"#{fragment}"
        a.set("href", rewritten)
        changed = True
    if not changed:
        return html
    return lxml.html.tostring(doc, encoding="unicode", doctype="<!DOCTYPE html>")


async def _ingest_page(
    *,
    library: str,
    url: str,
    title: str,
    artifact_id: str,
) -> str | None:
    """File a captured page into the library and return its source id.

    Returns None when the source came back with no chunks. `ingest_source` never
    raises, so awaiting it proves only that it ran — the row has to be read back or a
    page with zero chunks gets reported as searchable when it is not.
    """
    from backend.modules.library import store as library_store
    from backend.modules.library.ingest import ingest_source
    from backend.modules.library.models import IngestRequest

    source = library_store.create_source(
        library=library,
        type="page",
        title=title,
        url=url,
        author=None,
        tags=["docviewer"],
        artifact_id=artifact_id,
    )
    req = IngestRequest(
        type="page",
        library=library,
        url=url,
        title=title,
        artifact_id=artifact_id,
        tags=["docviewer"],
    )
    await ingest_source(source["id"], req)
    row = library_store.get_source(source["id"])
    if row is None or row["status"] != "ready" or not row["chunk_count"]:
        return None
    return source["id"]


async def _capture_one(
    session: Any,
    *,
    doc_set: dict[str, Any],
    url: str,
    page_key: str,
) -> tuple[str, int, list[tuple[str, str]]]:
    """Navigate, capture, rewrite, store. Returns (title, size, links)."""
    await asyncio.wait_for(
        session.submit("navigate", {"url": url}), timeout=_PAGE_TIMEOUT_S
    )
    # Docs sites hydrate after `domcontentloaded`; capturing immediately catches the
    # pre-hydration DOM, which for a JS-rendered site is an empty shell.
    await asyncio.wait_for(
        session.submit("wait", {"ms": _NAV_SETTLE_MS}), timeout=_PAGE_TIMEOUT_S
    )
    result = await asyncio.wait_for(
        session.submit("capture", {"keep_scripts": True, "store": False}),
        timeout=_PAGE_TIMEOUT_S,
    )
    html = str(result.get("html") or "")
    final_url = str(result.get("url") or url)
    title = str(result.get("title") or url)
    links = _links(html, final_url)
    rewritten = rewrite_links(html, final_url, doc_set["id"], doc_set["prefix"])
    data = rewritten.encode("utf-8")
    artifact = store_bytes(
        data,
        kind="page",
        mime="text/html",
        filename=f"{page_key}.html",
        origin_url=final_url,
        # `scripts` is what unlocks `sandbox allow-scripts` on the byte route. Without
        # it the archive is served inert and the page's tabs and menus do nothing.
        meta={"title": title, "engine": "chromium", "scripts": True},
    )
    source_id = await _ingest_page(
        library=doc_set["library"],
        url=final_url,
        title=title,
        artifact_id=artifact["id"],
    )
    store.mark_captured(
        page_key,
        title=title,
        artifact_id=artifact["id"],
        source_id=source_id,
        size=len(data),
    )
    return title, len(data), links


async def _run_crawl(set_id: str, max_depth: int) -> None:
    from backend.modules.browser.session import browser_manager

    doc_set = store.get_set(set_id)
    if doc_set is None:
        return
    prefix = doc_set["prefix"]
    max_pages = int(doc_set["max_pages"])
    delay = float(get_value("docviewer.crawlDelay", 1.0) or 1.0)

    robots = RobotsCache()
    limiter = HostLimiter(min_interval_s=delay)
    session_key = f"docviewer:{set_id}"

    seen: set[str] = set()
    frontier: list[tuple[str, str | None, int]] = [(doc_set["seed_url"], None, 0)]
    captured = 0
    failed = 0
    total_bytes = 0
    ordinal = 0

    store.set_status(set_id, "crawling")
    publish_set(store.get_set(set_id) or {})

    def progress(current: str | None) -> None:
        publish_progress(
            {
                "set_id": set_id,
                "status": "crawling",
                "captured": captured,
                "failed": failed,
                "queued": len(frontier),
                "current_url": current,
            }
        )

    session = None
    error: str | None = None
    try:
        session = await browser_manager.open_headless(session_key, profile="docviewer")
        while frontier and captured < max_pages:
            url, parent_id, depth = frontier.pop(0)
            key = canonical_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            if not _in_scope(url, prefix) or depth > max_depth:
                continue
            if not await robots.allowed(url):
                logger.info("docviewer: robots.txt disallows %s", url)
                continue
            if total_bytes >= TOTAL_SET_BYTES_CAP:
                logger.info("docviewer: set %s hit the byte cap", set_id)
                break

            ordinal += 1
            row = store.upsert_page(
                set_id=set_id,
                url=url,
                canonical=key,
                title=url,
                status="pending",
                parent_id=parent_id,
                depth=depth,
                ordinal=ordinal,
            )
            progress(url)

            host = host_of(url)
            limiter.note_crawl_delay(host, await robots.crawl_delay(url))
            await limiter.acquire(host)
            try:
                _title, size, links = await _capture_one(
                    session, doc_set=doc_set, url=url, page_key=row["id"]
                )
            except Exception as exc:  # noqa: BLE001 — one bad page is not a bad crawl
                logger.info("docviewer: capture failed for %s: %s", url, exc)
                store.mark_failed(row["id"], str(exc))
                failed += 1
                publish_page(store.get_page(row["id"]) or {})
                continue
            finally:
                limiter.release(host)

            captured += 1
            total_bytes += size
            publish_page(store.get_page(row["id"]) or {})
            store.refresh_page_count(set_id)

            if depth < max_depth:
                for target, _fragment in links:
                    if canonical_url(target) in seen or not _in_scope(target, prefix):
                        continue
                    frontier.append((target, row["id"], depth + 1))
            progress(url)
    except Exception as exc:  # noqa: BLE001 — record it; the set stays browsable
        logger.exception("docviewer: crawl of %s failed", set_id)
        error = str(exc)
    finally:
        if session is not None:
            browser_manager.close_headless(session_key)

    store.refresh_page_count(set_id)
    status = "failed" if error and captured == 0 else "ready"
    store.set_status(set_id, status, error=error)
    publish_set(store.get_set(set_id) or {})
    publish_progress(
        {
            "set_id": set_id,
            "status": status,
            "captured": captured,
            "failed": failed,
            "queued": 0,
            "current_url": None,
            "error": error,
        }
    )


async def _guarded(set_id: str, max_depth: int) -> None:
    async with _crawl_slot:
        try:
            await _run_crawl(set_id, max_depth)
        finally:
            _running.pop(set_id, None)


def start_crawl(set_id: str, max_depth: int = DEFAULT_MAX_DEPTH) -> bool:
    """Kick a crawl off detached. False if one is already running for this set."""
    existing = _running.get(set_id)
    if existing is not None and not existing.done():
        return False
    _running[set_id] = asyncio.create_task(_guarded(set_id, max_depth))
    return True


def is_running(set_id: str) -> bool:
    task = _running.get(set_id)
    return task is not None and not task.done()
