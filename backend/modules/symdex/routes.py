"""HTTP surface for the symdex index: reindex kick, status, and search."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from backend.modules.symdex.index import symdex_index
from backend.modules.symdex.models import (
    ReindexRequest,
    SearchResponse,
    SymdexStatus,
)

router = APIRouter(prefix="/symdex", tags=["symdex"])


@router.post("/reindex")
async def reindex(body: ReindexRequest) -> dict[str, object]:
    """Kick a rebuild of the given kinds, detached — progress streams on the
    `symdex` /ws channel; poll /status for the outcome."""
    if symdex_index.building:
        return {"started": False, "reason": "already building"}
    asyncio.create_task(symdex_index.reindex(list(body.kinds)))
    return {"started": True, "kinds": list(body.kinds)}


@router.get("/status", response_model=SymdexStatus)
async def status() -> SymdexStatus:
    return SymdexStatus(**symdex_index.status())


@router.get("/search", response_model=SearchResponse)
async def search(q: str, kind: str = "", limit: int = 8) -> SearchResponse:
    result = await symdex_index.search(q, kind or None, max(1, min(limit, 50)))
    return SearchResponse(**result)
