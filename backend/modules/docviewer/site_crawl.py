"""The recipe crawl: how the knowledge base imports a whole site.

Same engine and the same guarantees as the original doc-set crawl in `crawl.py` —
headless Chromium behind the browser's egress guard, robots.txt and per-host delay,
archives whose intra-set links point at sibling archives — with what a site import
needs on top:

- **Start ≠ scope.** `recipe.start_urls` may sit outside the scope they lead into;
  `index_pages` are navigated for their links and never kept.
- **Frontiers.** A publisher's `sitemap.xml` names every page up front, which is the
  only complete list for a site that renders its navigation client-side.
- **Resume.** Pages already captured are skipped (unless `force`), and their links
  recovered from the archive's `data-orig-href`s, so a recrawl after an interruption
  picks up where it stopped instead of starting over.
- **Canonical dedupe.** A page that declares the canonical URL of one already
  captured is a duplicate path to the same content (aman.ai serves every primer at
  two paths) and is dropped before anything is stored.
- **Batched ingest.** Text is prepared per page but written in batches of pages —
  one table write per batch, not per page (`library.ingest.commit_prepared`).
- **Content-Signal.** A site whose robots.txt says `ai-input=no` is archived for
  reading but its text is not embedded for the agent.
- **Catalog.** With `kb_root_item_id` set, every captured page is filed into the
  knowledge base (`kb.sitesync`).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from backend.modules.artifacts.store import artifact_path, store_bytes
from backend.modules.docviewer import store
from backend.modules.docviewer.broadcast import (
    publish_page,
    publish_progress,
    publish_set,
)
from backend.modules.docviewer.crawl import (
    _NAV_SETTLE_MS,
    _PAGE_TIMEOUT_S,
    _links,
    rewrite_links,
)
from backend.modules.docviewer.models import TOTAL_SET_BYTES_CAP
from backend.modules.docviewer.recipe import Recipe
from backend.modules.docviewer.sitemap import sitemap_urls
from backend.modules.search.canonical import canonical_url, host_of
from backend.modules.search.crawl.robots import HostLimiter, RobotsCache
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

# Pages prepared before one batched write. Bigger batches mean fewer table writes
# but more re-work if the process dies mid-batch; eight pages of a long primer is
# already a couple of thousand rows.
INGEST_BATCH_PAGES = 8

# MathJax 3/4 renders TeX into <mjx-container> and keeps the source only in its own
# JS state. Stamp it back onto the element before capture, so text extraction can
# recover the TeX (`extract._math_to_tex` reads `data-tex`). MathJax 2 keeps its
# <script type="math/tex"> in the DOM and needs nothing.
_STAMP_TEX = """
() => {
  let n = 0;
  try {
    const doc = window.MathJax && window.MathJax.startup && window.MathJax.startup.document;
    if (doc && doc.math) {
      for (const item of doc.math) {
        const node = item.typesetRoot;
        if (node && node.setAttribute && typeof item.math === 'string') {
          node.setAttribute('data-tex', item.math);
          if (item.display) node.setAttribute('display', 'true');
          n++;
        }
      }
    }
  } catch (e) {}
  return n;
}
"""
_OUTER_HTML = "() => document.documentElement.outerHTML"

# Typesetting is asynchronous and slow: aman.ai's MathJax 4 loads from a CDN after
# `domcontentloaded` and finishes ~3-9 s later, so capturing after the usual settle
# archives raw `\(…\)` source instead of rendered equations. Wait for the
# library's own completion promise (v3/v4) or queue (v2), bounded; a page without
# MathJax returns at once.
_WAIT_MATH = r"""
async () => {
  const deadline = Date.now() + 12000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const text = document.body ? document.body.innerText : '';
  const hasTex = text.includes('\\(') || text.includes('\\[') || text.includes('$$');
  while (Date.now() < deadline) {
    const MJ = window.MathJax;
    if (MJ && MJ.startup && MJ.startup.promise) {
      await Promise.race([MJ.startup.promise, sleep(deadline - Date.now())]);
      return 'mathjax3';
    }
    if (MJ && MJ.Hub && MJ.Hub.Queue) {
      await Promise.race([new Promise((r) => MJ.Hub.Queue(r)), sleep(deadline - Date.now())]);
      return 'mathjax2';
    }
    if (!hasTex) return 'none';
    await sleep(250);
  }
  return 'timeout';
}
"""


class _Batch:
    """Prepared pages waiting for one write."""

    def __init__(self) -> None:
        self.items: list[tuple[Any, str, str | None]] = []  # (prepared, page, item)

    def __len__(self) -> int:
        return len(self.items)

    def flush(self) -> None:
        if not self.items:
            return
        from backend.modules.kb.sitesync import attach_source
        from backend.modules.library import store as library_store
        from backend.modules.library.ingest import commit_prepared

        batch, self.items = self.items, []
        commit_prepared([p for p, _page, _item in batch])
        for prepared, page_key, item_id in batch:
            # Read back: `commit_prepared` records failure on the row, not by raising.
            row = library_store.get_source(prepared.source_id) or {}
            ok = row.get("status") == "ready" and row.get("chunk_count")
            store.set_page_source(page_key, prepared.source_id if ok else None)
            attach_source(item_id, prepared.source_id)
            publish_page(store.get_page(page_key) or {})


async def _content_signal(robots: Any, url: str) -> str | None:
    getter = getattr(robots, "content_signal", None)
    if getter is None:
        return None
    try:
        return await getter(url)
    except Exception:  # noqa: BLE001 — no signal is not a reason to stop
        return None


async def _sitemap_frontier(robots: Any, recipe: Recipe) -> list[str]:
    candidates: list[str] = []
    if recipe.sitemap_url:
        candidates.append(recipe.sitemap_url)
    elif recipe.start_urls:
        getter = getattr(robots, "sitemaps", None)
        if getter is not None:
            try:
                candidates.extend(await getter(recipe.start_urls[0]))
            except Exception:  # noqa: BLE001
                pass
        parts = urlsplit(recipe.start_urls[0])
        candidates.append(f"{parts.scheme}://{parts.netloc}/sitemap.xml")
    for candidate in candidates:
        urls = await sitemap_urls(candidate, recipe.keeps)
        if urls:
            return urls
    return []


def _archived_links(artifact_id: str | None) -> list[str]:
    """Original URLs of the in-set links a stored archive holds."""
    if not artifact_id:
        return []
    path = artifact_path(artifact_id)
    if path is None or not path.is_file():
        return []
    import lxml.html

    try:
        doc = lxml.html.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    return [str(h) for h in doc.xpath("//a/@data-orig-href")]


def _declared_canonical(html: str, base_url: str) -> str | None:
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return None
    for href in doc.xpath('//link[@rel="canonical"]/@href'):
        href = str(href).strip()
        if href:
            return urldefrag(urljoin(base_url, href))[0]
    return None


async def _visit(session: Any, url: str) -> None:
    await asyncio.wait_for(
        session.submit("navigate", {"url": url}), timeout=_PAGE_TIMEOUT_S
    )
    await asyncio.wait_for(
        session.submit("wait", {"ms": _NAV_SETTLE_MS}), timeout=_PAGE_TIMEOUT_S
    )


async def _eval(session: Any, js: str) -> Any:
    try:
        out = await asyncio.wait_for(
            session.submit("eval", {"js": js}), timeout=_PAGE_TIMEOUT_S
        )
    except Exception:  # noqa: BLE001 — an engine without eval just skips the extra
        return None
    return (out or {}).get("result") if isinstance(out, dict) else None


async def run_recipe_crawl(set_id: str, max_depth: int, *, force: bool = False) -> None:
    from backend.modules.browser.session import browser_manager

    doc_set = store.get_set(set_id)
    if doc_set is None or not doc_set.get("recipe"):
        return
    recipe = Recipe.from_dict(doc_set["recipe"])
    library = doc_set["library"]
    max_pages = int(doc_set["max_pages"])
    configured = get_value("docviewer.crawlDelay", 1.0)
    delay = 1.0 if configured is None else float(configured)
    robots = RobotsCache()
    limiter = HostLimiter(min_interval_s=delay)
    session_key = f"docviewer:{set_id}"

    signal = await _content_signal(robots, recipe.start_urls[0])
    from backend.modules.search.crawl.robots import allows_ai_input

    embed_text = recipe.structured and allows_ai_input(signal)
    embed_note: str | None = None
    if recipe.structured and not embed_text:
        embed_note = (
            f"the site's robots.txt says {signal!r}: pages are archived for reading, "
            "but their text is not indexed for the agent"
        )

    existing = {p["id"]: p for p in store.list_pages(set_id)}
    have: dict[str, dict[str, Any]] = {}
    if not force:
        have = {
            canonical_url(p["url"]): p
            for p in existing.values()
            if p["status"] == "captured"
        }
    captured = len(have)
    failed = 0
    total_bytes = sum(p["bytes"] for p in existing.values())
    ordinal = max((p["ordinal"] for p in existing.values()), default=0)

    seen: set[str] = set()
    seen_canonical: set[str] = {canonical_url(p["url"]) for p in have.values()}
    frontier: deque[tuple[str, str | None, int]] = deque(
        (u, None, 0) for u in recipe.start_urls
    )

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

    if recipe.frontier in ("sitemap", "auto"):
        listed = await _sitemap_frontier(robots, recipe)
        if listed:
            # Listed pages sit at depth 1 under the start, whatever their path
            # depth — the sitemap *is* the discovery, so link depth is moot.
            frontier.extend((u, None, 1) for u in listed)
        elif recipe.frontier == "sitemap":
            logger.info("docviewer: no usable sitemap for %s; walking links", set_id)

    batch = _Batch()
    session = None
    error: str | None = None
    stopped = False
    try:
        session = await browser_manager.open_headless(session_key, profile="docviewer")
        while frontier and captured < max_pages:
            url, link_parent, depth = frontier.popleft()
            url = urldefrag(url)[0]
            key = canonical_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            if not recipe.in_scope(url) or depth > max_depth:
                continue

            index = recipe.is_index(url)
            if not index and key in have:
                # Resume: already captured. Recover its links from the archive so
                # whatever only it linked to is still reached.
                if depth < max_depth:
                    for target in _archived_links(have[key]["artifact_id"]):
                        frontier.append((target, have[key]["id"], depth + 1))
                continue
            if not await robots.allowed(url):
                logger.info("docviewer: robots.txt disallows %s", url)
                continue
            if total_bytes >= TOTAL_SET_BYTES_CAP:
                logger.info("docviewer: set %s hit the byte cap", set_id)
                break

            host = host_of(url)
            limiter.note_crawl_delay(host, await robots.crawl_delay(url))
            await limiter.acquire(host)
            try:
                if index:
                    progress(url)
                    await _visit(session, url)
                    html = await _eval(session, _OUTER_HTML) or ""
                    if depth < max_depth:
                        for target, _frag in _links(str(html), url):
                            frontier.append((target, None, depth + 1))
                    continue

                # Where this page hangs in the tree.
                if recipe.hierarchy == "url_path":
                    parent_url = recipe.path_parent(url)
                    parent_id = (
                        store.page_id(set_id, canonical_url(parent_url))
                        if parent_url
                        else None
                    )
                elif recipe.hierarchy == "links":
                    parent_id = link_parent
                else:
                    parent_id = None
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
                try:
                    result = await _capture(session, url, recipe.keep_scripts)
                except Exception as exc:  # noqa: BLE001 — one bad page ≠ a bad crawl
                    logger.info("docviewer: capture failed for %s: %s", url, exc)
                    store.mark_failed(row["id"], str(exc))
                    failed += 1
                    publish_page(store.get_page(row["id"]) or {})
                    continue
            finally:
                limiter.release(host)

            html = str(result.get("html") or "")
            final_url = urldefrag(str(result.get("url") or url))[0]
            title = recipe.clean_title(str(result.get("title") or url))
            declared = _declared_canonical(html, final_url)
            identity = canonical_url(declared or final_url)
            if identity in seen_canonical:
                store.mark_failed(row["id"], f"duplicate of {declared or final_url}")
                publish_page(store.get_page(row["id"]) or {})
                continue
            seen_canonical.add(identity)
            seen.add(canonical_url(final_url))

            links = _links(html, final_url)
            rewritten = rewrite_links(
                html, final_url, set_id, doc_set["prefix"], keep=recipe.keeps
            )
            data = rewritten.encode("utf-8")
            artifact = store_bytes(
                data,
                kind="page",
                mime="text/html",
                filename=f"{row['id']}.html",
                origin_url=final_url,
                meta={
                    "title": title,
                    "engine": "chromium",
                    "scripts": recipe.keep_scripts,
                },
            )
            store.mark_captured(
                row["id"],
                title=title,
                artifact_id=artifact["id"],
                source_id=None,
                size=len(data),
            )
            captured += 1
            total_bytes += len(data)

            item_id: str | None = None
            if recipe.kb_root_item_id:
                from backend.modules.kb.sitesync import file_page

                item_id = file_page(
                    root_item_id=recipe.kb_root_item_id,
                    docset_id=set_id,
                    docpage_id=row["id"],
                    parent_docpage_id=parent_id,
                    url=final_url,
                    declared_canonical=declared,
                    title=title,
                    kind=recipe.kind_for(final_url),
                    ordinal=ordinal,
                    is_root=recipe.is_root(final_url),
                    library=library,
                    artifact_id=artifact["id"],
                    tags=recipe.tags,
                    content_signal=signal,
                    profile_id=recipe.profile_id,
                )

            previous = existing.get(row["id"])
            if previous is not None and previous.get("source_id"):
                # A forced recapture replaces the page's text; drop the old source
                # so its chunks don't answer searches twice.
                _drop_source(previous["source_id"])
            if embed_text:
                prepared, prep_error = await _prepare(
                    library=library,
                    url=final_url,
                    title=title,
                    artifact_id=artifact["id"],
                    recipe=recipe,
                )
                if prepared is not None:
                    batch.items.append((prepared, row["id"], item_id))
                elif _embedder_down(prep_error):
                    # Every page would fail the same way. Keep archiving, stop
                    # embedding, and say so on the set.
                    embed_text = False
                    embed_note = (
                        "the embedding provider is unavailable: pages are archived "
                        "but not indexed — re-index once it is back"
                    )
                if prepared is None and item_id:
                    # Show *why* on the item instead of an eternal "queued".
                    _note_unindexed(item_id, prep_error or embed_note)
            elif recipe.structured and item_id:
                _note_unindexed(item_id, embed_note)
            # Outside the branches above: this used to sit inside the `elif`, so a
            # crawl that *was* embedding never flushed until it ended — every page's
            # chunks held in memory, nothing searchable until the last page.
            if len(batch) >= INGEST_BATCH_PAGES:
                await asyncio.to_thread(batch.flush)

            publish_page(store.get_page(row["id"]) or {})
            store.refresh_page_count(set_id)
            if depth < max_depth:
                parent_for_links = row["id"]
                for target, _frag in links:
                    if canonical_url(target) not in seen and recipe.in_scope(target):
                        frontier.append((target, parent_for_links, depth + 1))
            progress(url)
    except asyncio.CancelledError:
        stopped = True
    except Exception as exc:  # noqa: BLE001 — record it; the set stays browsable
        logger.exception("docviewer: recipe crawl of %s failed", set_id)
        error = str(exc)
    finally:
        try:
            await asyncio.to_thread(batch.flush)
        except Exception:  # noqa: BLE001
            logger.exception("docviewer: final ingest flush for %s failed", set_id)
        if session is not None:
            browser_manager.close_headless(session_key)

    if recipe.kb_root_item_id:
        from backend.modules.kb.sitesync import relink

        try:
            relink(set_id, recipe.kb_root_item_id)
        except Exception:  # noqa: BLE001
            logger.exception("docviewer: relinking %s failed", set_id)
    try:
        from backend.modules.database.vectorstore import ensure_ann_index

        await asyncio.to_thread(ensure_ann_index, library)
    except Exception:  # noqa: BLE001 — an index is an optimisation
        logger.exception("docviewer: ANN index for %s failed", library)

    store.refresh_page_count(set_id)
    if stopped:
        error = "stopped — recrawl to resume"
    note = error or embed_note
    status = "failed" if error and captured == 0 else "ready"
    store.set_status(set_id, status, error=note)
    publish_set(store.get_set(set_id) or {})
    publish_progress(
        {
            "set_id": set_id,
            "status": status,
            "captured": captured,
            "failed": failed,
            "queued": 0,
            "current_url": None,
            "error": note,
        }
    )
    if recipe.kb_root_item_id:
        from backend.modules.kb.service import emit

        emit(recipe.kb_root_item_id)
    if stopped:
        raise asyncio.CancelledError


async def _capture(session: Any, url: str, keep_scripts: bool) -> dict[str, Any]:
    await _visit(session, url)
    await _eval(session, _WAIT_MATH)
    await _eval(session, _STAMP_TEX)
    return await asyncio.wait_for(
        session.submit(
            "capture",
            {"keep_scripts": keep_scripts, "store": False, "shrink_images": True},
        ),
        timeout=_PAGE_TIMEOUT_S,
    )


async def run_reindex(set_id: str) -> None:
    """Re-extract and re-embed every captured page from its stored archive.

    For when extraction or chunking improved, or the embedder was down during the
    import. The archives are the source of truth, so nothing is crawled again: no
    browser, no page fetches — only robots.txt, for its Content-Signal. A page that
    never got a source (its ingest failed) gets one now.
    """
    from backend.modules.kb.sitesync import _item_for_docpage
    from backend.modules.search.crawl.robots import allows_ai_input

    doc_set = store.get_set(set_id)
    if doc_set is None or not doc_set.get("recipe"):
        return
    recipe = Recipe.from_dict(doc_set["recipe"])
    library = doc_set["library"]
    pages = [
        p
        for p in store.list_pages(set_id)
        if p["status"] == "captured" and p.get("artifact_id")
    ]

    def progress(done: int, status: str, error: str | None = None) -> None:
        publish_progress(
            {
                "set_id": set_id,
                "status": status,
                "captured": done,
                "failed": 0,
                "queued": len(pages) - done,
                "current_url": None,
                "error": error,
            }
        )

    error: str | None = None
    signal = await _content_signal(RobotsCache(), recipe.start_urls[0])
    if not recipe.structured or not allows_ai_input(signal):
        error = f"not re-indexed: the site's robots.txt says {signal!r}"
        pages = []

    batch = _Batch()
    done = 0
    try:
        for page in pages:
            item_id = _item_for_docpage(set_id, page["id"])
            prepared, prep_error = await _prepare(
                library=library,
                url=page["url"],
                title=page["title"],
                artifact_id=page["artifact_id"],
                recipe=recipe,
                source_id=page.get("source_id"),
            )
            if prepared is not None:
                batch.items.append((prepared, page["id"], item_id))
            elif _embedder_down(prep_error):
                error = "the embedding provider is unavailable — start it and re-index"
                break
            elif item_id:
                _note_unindexed(item_id, prep_error)
            if len(batch) >= INGEST_BATCH_PAGES:
                await asyncio.to_thread(batch.flush)
            done += 1
            progress(done, "indexing")
    finally:
        # `flush` re-links each committed page and its item to the source.
        await asyncio.to_thread(batch.flush)

    store.set_status(set_id, "ready", error=error)
    publish_set(store.get_set(set_id) or {})
    progress(done, "ready", error)


def _note_unindexed(item_id: str, reason: str | None) -> None:
    from backend.modules.kb import store as kb_store

    try:
        kb_store.update_item(item_id, index_error=reason or "not indexed")
    except Exception:  # noqa: BLE001
        logger.exception("docviewer: could not note unindexed item %s", item_id)


def _drop_source(source_id: str) -> None:
    from backend.modules.library import store as library_store
    from backend.modules.library.routes import delete_source_chunks

    source = library_store.get_source(source_id)
    if source is not None:
        delete_source_chunks(source)
        library_store.delete_source(source_id)


def _embedder_down(error: str | None) -> bool:
    return bool(error) and "embedding provider is unavailable" in str(error)


async def _prepare(
    *,
    library: str,
    url: str,
    title: str,
    artifact_id: str,
    recipe: Recipe,
    source_id: str | None = None,
) -> tuple[Any, str | None]:
    """Extract/chunk/embed a page's archive into its library source (no write).

    Creates the source unless `source_id` names an existing one to re-index — its
    old chunks keep answering until `commit_prepared` replaces them.

    Returns `(prepared, None)`, or `(None, error)` with the error read back off the
    source row — `prepare_source` records failure there rather than raising.
    """
    from backend.modules.kb.service import chunk_size
    from backend.modules.library import store as library_store
    from backend.modules.library.ingest import prepare_source
    from backend.modules.library.models import IngestRequest

    source = (library_store.get_source(source_id) if source_id else None) or (
        library_store.create_source(
            library=library,
            type="page",
            title=title,
            url=url,
            author=None,
            tags=["kb", *recipe.tags],
            artifact_id=artifact_id,
        )
    )
    req = IngestRequest(
        type="page",
        library=library,
        url=url,
        title=title,
        artifact_id=artifact_id,
        tags=["kb", *recipe.tags],
        structured=True,
        chunk_size=chunk_size(),
        content_selector=recipe.content_selector,
        strict_embeddings=True,
    )
    prepared = await prepare_source(source["id"], req)
    if prepared is not None:
        return prepared, None
    return None, (library_store.get_source(source["id"]) or {}).get("error")
