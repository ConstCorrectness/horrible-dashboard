"""HTTP surface: `/api/discover/*`.

Upstream failures are **answers, not errors**: a list call returns 200 with a
`status` (`needs_connect`, `rate_limited`, `degraded`, `error`) and, when one exists,
the last good page marked `stale`. The pane renders each differently — "connect
Kaggle", "resets in 41s", "showing cached results" — and none of them is a red 502.

Filters travel as `f.<id>=<value>` query parameters, so a filter set is visible and
cacheable in the URL and a new filter needs no route change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.modules.discover.cache import Cache
from backend.modules.discover.models import (
    DiscoverDetail,
    DiscoverItem,
    DiscoverPage,
    SourceSpec,
    SourcesResponse,
)
from backend.modules.discover.sources import SOURCES
from backend.modules.discover.sources import base as source_base
from backend.modules.discover.sources import skills as skills_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discover", tags=["discover"])

#: An unsearched feed moves slowly; a search is re-run as it's refined, so it's
#: cached briefly; a detail page (README, card) changes least of all.
TTL_FEED_S = 600.0
TTL_SEARCH_S = 120.0
TTL_DETAIL_S = 600.0

_cache = Cache()
#: Rows seen in recent list calls, so a source without a cheap detail endpoint can
#: render a detail view from the row the user just clicked.
_items: OrderedDict[str, DiscoverItem] = OrderedDict()
_ITEMS_MAX = 3000
#: Per-source "don't call upstream before" deadlines, set by a rate limit. While one
#: is active, requests are answered from cache or with the limit, never by spending
#: another call that would only be refused.
_cooldown: dict[str, float] = {}


def clear_state() -> None:
    _cache.clear()
    _items.clear()
    _cooldown.clear()


def _remember(items: list[DiscoverItem]) -> None:
    for item in items:
        key = f"{item.source}:{item.kind}:{item.id}"
        _items[key] = item
        _items.move_to_end(key)
    while len(_items) > _ITEMS_MAX:
        _items.popitem(last=False)


def _source(source_id: str) -> source_base.DiscoverSource:
    source = SOURCES.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"unknown source {source_id!r}")
    return source


def _filters(request: Request) -> dict[str, str]:
    return {
        key[2:]: value
        for key, value in request.query_params.items()
        if key.startswith("f.") and len(key) > 2
    }


@router.get("/sources", response_model=SourcesResponse)
async def sources() -> SourcesResponse:
    """Every source's kinds, sorts, filters and connection state."""

    async def one(source: source_base.DiscoverSource) -> SourceSpec | None:
        try:
            return await source.spec()
        except Exception:  # noqa: BLE001 — one broken source must not hide the rest
            logger.exception("discover: spec failed for %s", source.id)
            return None

    specs = await asyncio.gather(*(one(s) for s in SOURCES.values()))
    return SourcesResponse(sources=[s for s in specs if s is not None])


@router.get("/{source_id}/list", response_model=DiscoverPage)
async def list_items(
    source_id: str,
    request: Request,
    kind: str = Query(..., min_length=1),
    q: str = "",
    sort: str = "",
    cursor: str | None = None,
    fresh: bool = False,
) -> DiscoverPage:
    """A default feed (no `q`) or a search, one page at a time."""
    source = _source(source_id)
    query = source_base.Query(
        kind=kind,
        q=q.strip(),
        sort=sort,
        filters=_filters(request),
        cursor=cursor or None,
    )
    key = "list:" + json.dumps(
        [source_id, kind, query.q, sort, sorted(query.filters.items()), query.cursor]
    )
    ttl = TTL_SEARCH_S if query.q else TTL_FEED_S

    def page(
        result: source_base.SourceResult, at: float, *, stale: bool = False
    ) -> DiscoverPage:
        return DiscoverPage(
            source=source_id,
            kind=kind,
            items=result.items,
            cursor_next=result.cursor_next,
            total=result.total,
            feed_label=result.feed_label,
            fetched_at=at,
            stale=stale,
            status=result.status,
            message=result.message,
        )

    def unavailable(exc: source_base.SourceUnavailable) -> DiscoverPage:
        if (last := _cache.last_good(key)) is not None:
            served = page(last[1], last[0], stale=True)
            served.status = exc.status
            served.message = str(exc)
            served.retry_after = exc.retry_after
            return served
        return DiscoverPage(
            source=source_id,
            kind=kind,
            fetched_at=time.time(),
            status=exc.status,
            message=str(exc),
            retry_after=exc.retry_after,
        )

    until = _cooldown.get(source_id, 0.0)
    if until > time.time() and _cache.get_fresh(key, ttl) is None:
        return unavailable(
            source_base.SourceUnavailable(
                "Rate limit still in effect.",
                status="rate_limited",
                retry_after=until - time.time(),
            )
        )

    try:
        at, result = await _cache.fetch(
            key, ttl, lambda: source.list(query), fresh=fresh
        )
    except source_base.SourceUnavailable as exc:
        if exc.status == "rate_limited":
            _cooldown[source_id] = time.time() + (exc.retry_after or 60.0)
        return unavailable(exc)
    except Exception as exc:  # noqa: BLE001 — an adapter bug is reported, not a 500
        logger.exception("discover: %s list failed", source_id)
        return unavailable(
            source_base.SourceUnavailable(f"{type(exc).__name__}: {exc}")
        )
    _remember(result.items)
    return page(result, at)


_DETAIL_STATUS = {
    "needs_connect": 409,
    "rate_limited": 429,
    "degraded": 404,
    "error": 502,
}


@router.get("/{source_id}/item", response_model=DiscoverDetail)
async def item_detail(
    source_id: str,
    kind: str = Query(..., min_length=1),
    id: str = Query(..., min_length=1),
    fresh: bool = False,
) -> DiscoverDetail:
    """One item's card: README / abstract / SKILL.md, facts, links, files."""
    source = _source(source_id)
    known = _items.get(f"{source_id}:{kind}:{id}")
    key = "item:" + json.dumps([source_id, kind, id])
    try:
        _at, detail = await _cache.fetch(
            key, TTL_DETAIL_S, lambda: source.detail(kind, id, known), fresh=fresh
        )
    except source_base.SourceUnavailable as exc:
        raise HTTPException(
            status_code=_DETAIL_STATUS.get(exc.status, 502), detail=str(exc)
        ) from exc
    return detail


class InstallSkillRequest(BaseModel):
    id: str


class InstallSkillResponse(BaseModel):
    name: str
    files: int


@router.post("/skills/install", response_model=InstallSkillResponse)
async def install_skill(req: InstallSkillRequest) -> InstallSkillResponse:
    """Copy a listed skill's whole directory into the user skills folder."""
    try:
        name, files = await skills_source.install(req.id)
    except source_base.SourceUnavailable as exc:
        raise HTTPException(
            status_code=_DETAIL_STATUS.get(exc.status, 400), detail=str(exc)
        ) from exc
    # The list's "installed" badges are now wrong; drop cached skill pages.
    _cache.clear()
    return InstallSkillResponse(name=name, files=files)
