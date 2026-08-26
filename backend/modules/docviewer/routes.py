"""HTTP surface for the documentation viewer (`/api/docviewer/*`).

Two kinds of route live here. The catalog routes are ordinary JSON. The one that
matters is `GET /pages/{page_id}/content`: it serves a captured archive's bytes under
the same CSP the artifact route uses, and it exists because **archives address each
other by page id, not artifact id** — see `store.py` for why that circularity is
unavoidable. A rewritten intra-set link resolves here.

Deleting a set deletes its artifacts and its library sources explicitly rather than
cascading from the catalog: both are shared stores, and a set is the only thing here
that owns anything in them.
"""

from __future__ import annotations

import html
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from backend.modules.artifacts import store as artifact_store
from backend.modules.artifacts.store import page_csp

# Underscore-private, but the established seam: `search` reaches for
# `_fetch_guarded` from this same module for the same reason. There is exactly one
# egress policy and it lives here.
from backend.modules.browser.fetch import UnsafeUrlError, _check_host_public
from backend.modules.docviewer import crawl, store
from backend.modules.docviewer.models import (
    CreateSetRequest,
    CrawlProgress,
    DocPage,
    DocSet,
    OkResponse,
    PagesResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    SetsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/docviewer", tags=["docviewer"])


def _set_or_404(set_id: str) -> dict:
    row = store.get_set(set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="doc set not found")
    return row


def _validate_seed(seed_url: str) -> str:
    """Same egress rule as the browser's own navigation: http(s), public host only.

    A doc-set crawl drives a real browser at a user-supplied URL, so it gets the
    identical check rather than a looser one of its own — a second, laxer validator
    is exactly where an SSRF hole appears.
    """
    url = (seed_url or "").strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail=f"unsupported scheme: {parts.scheme or '(none)'}"
        )
    try:
        _check_host_public(parts.hostname or "")
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return url


# ---- sets ------------------------------------------------------------------


@router.get("/sets", response_model=SetsResponse)
def list_sets() -> SetsResponse:
    return SetsResponse(sets=[DocSet(**s) for s in store.list_sets()])


@router.post("/sets", response_model=DocSet)
async def create_set(req: CreateSetRequest) -> DocSet:
    """Create a set and start crawling it. Returns immediately — progress arrives on
    the `docviewer` `/ws` channel.

    `async def` is load-bearing, not style: `start_crawl` calls
    `asyncio.create_task`, and FastAPI runs a plain `def` endpoint in a threadpool
    where there is no running loop to attach a task to.
    """
    seed = _validate_seed(req.seed_url)
    prefix = req.prefix or crawl.default_prefix(seed)
    title = req.title or urlsplit(seed).netloc or seed
    row = store.create_set(
        title=title,
        seed_url=seed,
        prefix=prefix,
        library=req.library or f"docs-{urlsplit(seed).netloc}",
        max_pages=req.max_pages,
    )
    crawl.start_crawl(row["id"], req.max_depth)
    return DocSet(**row)


@router.get("/sets/{set_id}", response_model=DocSet)
def get_set(set_id: str) -> DocSet:
    return DocSet(**_set_or_404(set_id))


@router.post("/sets/{set_id}/recrawl", response_model=CrawlProgress)
async def recrawl(set_id: str) -> CrawlProgress:
    # async for the same reason as `create_set`: `start_crawl` needs a running loop.
    row = _set_or_404(set_id)
    if not crawl.start_crawl(set_id):
        raise HTTPException(status_code=409, detail="a crawl is already running")
    return CrawlProgress(set_id=row["id"], status="crawling")


def _delete_library_source(source_id: str) -> None:
    """Drop a source *and its chunk rows*, the way the library's own delete route
    does. Deleting only the catalog row leaves the vectors behind, and an orphaned
    chunk keeps answering searches for a page that no longer exists."""
    from backend.modules.database.vectorstore import delete_document
    from backend.modules.library import store as library_store

    source = library_store.get_source(source_id)
    if source is None:
        return
    for chunk in library_store.chunk_docs_for(source):
        delete_document(f"{source_id}#{chunk['index']}")
    # A CLIP-only source has no chunk rows to iterate; sweeping `#0` explicitly is a
    # no-op when the loop already covered it. Same reasoning as `library/routes.py`.
    delete_document(f"{source_id}#0")
    library_store.delete_source(source_id)


@router.delete("/sets/{set_id}", response_model=OkResponse)
def delete_set(set_id: str) -> OkResponse:
    _set_or_404(set_id)
    if crawl.is_running(set_id):
        raise HTTPException(
            status_code=409, detail="stop the crawl before deleting the set"
        )
    for page in store.list_pages(set_id):
        if page["artifact_id"]:
            try:
                artifact_store.delete_artifact(page["artifact_id"])
            except Exception:  # noqa: BLE001 — a missing blob must not block the delete
                logger.info("docviewer: artifact %s already gone", page["artifact_id"])
        if page["source_id"]:
            try:
                _delete_library_source(page["source_id"])
            except Exception:  # noqa: BLE001
                logger.info("docviewer: source %s already gone", page["source_id"])
    store.delete_set(set_id)
    return OkResponse()


# ---- pages -----------------------------------------------------------------


@router.get("/sets/{set_id}/pages", response_model=PagesResponse)
def list_pages(set_id: str) -> PagesResponse:
    _set_or_404(set_id)
    return PagesResponse(pages=[DocPage(**p) for p in store.list_pages(set_id)])


@router.get("/pages/{page_id}", response_model=DocPage)
def get_page(page_id: str) -> DocPage:
    row = store.get_page(page_id)
    if row is None:
        raise HTTPException(status_code=404, detail="page not found")
    return DocPage(**row)


# A link can legitimately point at a page the crawl never reached — the cap was hit,
# robots.txt said no, or the fetch failed. It renders inside the archive frame, so it
# has to be HTML; the status stays 404 because it really is missing.
# Colours are literal here on purpose: this document is served into an opaque-origin
# frame with no access to the app's stylesheet, so a `var(--bg)` would resolve to
# nothing. It is deliberately close to the default theme and does not follow it.
_MISSING_PAGE_HTML = """<!DOCTYPE html>
<meta charset="utf-8">
<title>Not in this doc set</title>
<style>
  body {{ font: 14px/1.6 system-ui, sans-serif; margin: 0; padding: 3rem 2rem;
         color: #9aa0aa; background: #16181D; }}
  h1 {{ font-size: 0.75rem; letter-spacing: 0.14em; text-transform: uppercase;
        color: #e6e8ec; margin: 0 0 0.75rem; }}
  code {{ font-family: ui-monospace, monospace; color: #c9cdd6; }}
</style>
<h1>Not captured</h1>
<p>This page is inside the set's URL prefix, but the crawl never stored it —
the page cap was reached, robots.txt disallowed it, or the fetch failed.</p>
<p><code>{detail}</code></p>
"""


def _missing(detail: str) -> HTMLResponse:
    return HTMLResponse(
        # Escaped: `detail` is a crawled URL or a fetch error, i.e. remote text.
        # The sandbox would contain a script anyway; not injecting one is cheaper
        # than relying on that.
        _MISSING_PAGE_HTML.format(detail=html.escape(detail)),
        status_code=404,
        # `style-src` is spelled out because `default-src 'none'` would otherwise
        # block the inline <style> and render this as unstyled black-on-white.
        headers={
            "Content-Security-Policy": (
                "sandbox; default-src 'none'; style-src 'unsafe-inline'"
            )
        },
    )


# `response_model=None`: the return is a Response union, which FastAPI would
# otherwise try to turn into a pydantic field and refuse at import time.
@router.get("/pages/{page_id}/content", response_model=None)
def page_content(page_id: str) -> FileResponse | HTMLResponse:
    """Serve one captured archive.

    The CSP comes from `artifacts.store.page_csp`, so this route and the artifact
    byte route can never drift on the one decision that matters: a scripted archive
    is served `sandbox allow-scripts` — an opaque origin with no network — and an
    inert one is served with script denied outright.
    """
    row = store.get_page(page_id)
    if row is None:
        return _missing("no such page in any doc set")
    if row["status"] != "captured" or not row["artifact_id"]:
        return _missing(row["error"] or row["url"])
    artifact = artifact_store.get_artifact(row["artifact_id"])
    path = artifact_store.artifact_path(row["artifact_id"])
    if artifact is None or path is None or not path.is_file():
        return _missing("the stored archive is gone")
    return FileResponse(
        path,
        media_type=artifact["mime"],
        content_disposition_type="inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": page_csp(artifact),
        },
    )


# ---- search ----------------------------------------------------------------


@router.post("/sets/{set_id}/search", response_model=SearchResponse)
async def search_set(set_id: str, req: SearchRequest) -> SearchResponse:
    """Semantic search scoped to one set.

    The library holds the text, so this embeds the query and searches that library,
    then keeps only the hits whose source belongs to this set — a library can hold
    more than one set, and an unscoped result would send you to a page the sidebar
    you are looking at does not contain.
    """
    from backend.modules.database.embeddings import get_embedding
    from backend.modules.database.vectorstore import init_db, search_documents

    row = _set_or_404(set_id)
    init_db()
    embedding, _source = await get_embedding(req.query)
    # Over-fetch: hits are filtered to this set afterwards, so asking for exactly
    # `limit` would return fewer than asked for whenever a library holds two sets.
    rows = search_documents(row["library"], embedding, req.limit * 4)

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for hit in rows:
        meta = hit.get("metadata") or {}
        source_id = str(meta.get("source_id") or hit.get("id") or "")
        page = store.page_by_source(source_id) if source_id else None
        if page is None or page["set_id"] != set_id or page["id"] in seen:
            continue
        seen.add(page["id"])
        hits.append(
            SearchHit(
                page_id=page["id"],
                url=page["url"],
                title=page["title"],
                snippet=str(hit.get("text") or "")[:400],
                score=float(hit["score"]) if hit.get("score") is not None else None,
            )
        )
        if len(hits) >= req.limit:
            break
    return SearchResponse(hits=hits)
