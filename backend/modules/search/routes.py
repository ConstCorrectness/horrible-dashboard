"""HTTP surface for search and the focused crawl: `/api/search/*`.

Read paths (`/query`, `/read`, `/index`) are POSTs even though they're read-only —
queries carry user text, and a query string is the one place it must not go: URLs
land in logs, in referrers, and in the I/O event stream's `target` field.

Crawl *control* lives here rather than on the `crawl` `/ws` channel, which is
push-only. That keeps the channel dispatch chain untouched and means a plugin's
unknown-channel handling is unaffected.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.modules.search import credentials, pipeline
from backend.modules.search.base import (
    SearchProviderError,
    all_providers,
    auto_provider_ids,
    resolve_providers,
)
from backend.modules.search.canonical import host_of
from backend.modules.search.crawl import index as webindex
from backend.modules.search.crawl import store as crawl_store
from backend.modules.search.models import (
    CrawlQueued,
    CrawlRequest,
    CrawlStatusResponse,
    IndexStatus,
    ProviderModel,
    ProvidersResponse,
    ReadRequest,
    ReadResponse,
    SearchHitModel,
    SearchRequest,
    SearchResponse,
    SeedModel,
    SeedsResponse,
    SeedUpsertRequest,
)
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


def _selected_providers(requested: list[str] | None) -> list[str] | None:
    """Request override → the `search.provider` setting → auto fan-out."""
    if requested:
        return requested
    setting = str(get_value("search.provider", "auto") or "auto")
    if setting and setting != "auto":
        return [setting]
    configured = str(get_value("search.fanoutProviders", "") or "").strip()
    if configured:
        return [p.strip() for p in configured.split(",") if p.strip()]
    return None


@router.post("/query", response_model=SearchResponse)
async def query(req: SearchRequest) -> SearchResponse:
    run = pipeline.deep_search if req.depth == "deep" else pipeline.quick_search
    kwargs = {
        "limit": req.limit,
        "providers": _selected_providers(req.providers),
        "site": req.site,
        "freshness": req.freshness,
        "use_cache": req.use_cache,
    }
    answer = await run(req.query, **kwargs)  # type: ignore[arg-type]
    return _to_response(answer)


def _to_response(answer: pipeline.SearchAnswer) -> SearchResponse:
    return SearchResponse(
        query=answer.query,
        hits=[SearchHitModel(**hit.to_dict()) for hit in answer.hits],
        rewrites=answer.rewrites,
        providers_used=answer.providers_used,
        notes=answer.notes,
        cached=answer.cached,
        elapsed_ms=answer.elapsed_ms,
    )


@router.get("/providers", response_model=ProvidersResponse)
def providers() -> ProvidersResponse:
    """Which providers exist, which can run, and why the rest can't.

    Safe to hand to the browser in full: it reports whether a key is *present*, never
    what it is.
    """
    ready, notes = resolve_providers(None)
    ready_ids = {p.id for p in ready}
    reasons = {note.split(":", 1)[0]: note for note in notes if ":" in note}

    rows = [
        ProviderModel(
            id=p.id,
            label=p.label,
            needs_key=p.needs_key,
            configured=p.id in ready_ids,
            reason="" if p.id in ready_ids else reasons.get(p.id, ""),
        )
        for p in all_providers()
    ]
    return ProvidersResponse(
        providers=rows,
        selected=str(get_value("search.provider", "auto") or "auto"),
        active=auto_provider_ids(),
    )


@router.post("/read", response_model=ReadResponse)
async def read(req: ReadRequest) -> ReadResponse:
    try:
        page = await pipeline.fetch_page(req.url, use_cache=req.use_cache)
    except Exception as exc:  # noqa: BLE001 — a bad URL is a 400, not a 500
        raise HTTPException(status_code=400, detail=f"couldn't read {req.url}: {exc}")
    return ReadResponse(**page)


@router.post("/index/query", response_model=SearchResponse)
async def index_query(req: SearchRequest) -> SearchResponse:
    """Search only the local crawl index — no metered calls, no network."""
    from backend.modules.search.providers.crawl import search_index

    try:
        results = await search_index(req.query, limit=req.limit, site=req.site)
    except SearchProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return SearchResponse(
        query=req.query,
        hits=[
            SearchHitModel(
                url=r.url,
                title=r.title,
                snippet=r.snippet,
                score=r.score or 0.0,
                providers=["crawl"],
                published=r.published,
                host=host_of(r.url),
            )
            for r in results
        ],
        providers_used=["crawl"] if results else [],
    )


# --- crawl seeds ------------------------------------------------------------


@router.get("/seeds", response_model=SeedsResponse)
def seeds() -> SeedsResponse:
    crawl_store.init_crawl_db()
    return SeedsResponse(seeds=[SeedModel(**s) for s in crawl_store.list_seeds()])


@router.post("/seeds", response_model=SeedModel)
def upsert_seed(req: SeedUpsertRequest) -> SeedModel:
    crawl_store.init_crawl_db()
    if not req.start_urls:
        raise HTTPException(
            status_code=400, detail="a seed needs at least one start URL"
        )
    existing = crawl_store.get_seed(req.id)
    if existing and existing["builtin"]:
        raise HTTPException(
            status_code=409,
            detail=f"{req.id} is a built-in seed; copy it under a new id to customize it",
        )
    spec = req.model_dump()
    spec["label"] = req.label or req.id
    return SeedModel(**crawl_store.upsert_seed(spec))


@router.delete("/seeds/{seed_id}")
def delete_seed(seed_id: str) -> dict[str, str]:
    crawl_store.init_crawl_db()
    outcome = crawl_store.delete_seed(seed_id)
    if outcome == "missing":
        raise HTTPException(status_code=404, detail=f"unknown seed {seed_id}")
    # A built-in comes back on the next start, so it is disabled rather than deleted.
    return {"seed_id": seed_id, "outcome": outcome}


@router.post("/crawl", response_model=CrawlQueued)
def crawl(req: CrawlRequest) -> CrawlQueued:
    """Enqueue one seed, or every seed that's due.

    One task per seed rather than one task looping over all of them — the queue
    worker is serial, so a single job would hold it for the entire batch and starve
    every ingest behind it. Same reason `library/reindex-clip` fans out.
    """
    from backend.modules.tasks.queue import enqueue_task

    crawl_store.init_crawl_db()
    if req.seed_id:
        if crawl_store.get_seed(req.seed_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown seed {req.seed_id}")
        targets = [req.seed_id]
    else:
        targets = [s["id"] for s in crawl_store.due_seeds()]

    for seed_id in targets:
        enqueue_task(
            task_type="crawl_seed", payload={"seed_id": seed_id, "force": req.force}
        )
    return CrawlQueued(queued=targets)


@router.get("/crawl/status", response_model=CrawlStatusResponse)
def crawl_status() -> CrawlStatusResponse:
    crawl_store.init_crawl_db()
    return CrawlStatusResponse(
        seeds=[SeedModel(**s) for s in crawl_store.list_seeds()],
        index=IndexStatus(**webindex.status()),
    )


@router.post("/cache/clear")
def clear_cache() -> dict[str, int]:
    from backend.modules.search import cache

    return cache.clear()


@router.get("/keys")
def key_status() -> dict[str, bool]:
    """Which providers have a key. Booleans only — never the values."""
    return {p: bool(credentials.get_key(p)) for p in credentials.KEYED_PROVIDERS}
