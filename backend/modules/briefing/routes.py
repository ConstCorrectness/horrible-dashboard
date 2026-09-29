"""HTTP surface for the AI briefing (`/api/briefing/*`).

Two routes rather than one, so a slow or failing source never holds up the
other: the Spotlight renders each section as its own answer arrives.
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, HTTPException, Query

from backend.modules.briefing import client
from backend.modules.briefing.models import (
    NewsResponse,
    PaperModel,
    PapersResponse,
    StoryModel,
)

router = APIRouter(prefix="/briefing", tags=["briefing"])


@router.get("/papers", response_model=PapersResponse)
async def papers(refresh: bool = Query(default=False)) -> PapersResponse:
    try:
        items, fetched_at, stale = await client.top_papers(refresh=refresh)
    except client.BriefingSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return PapersResponse(
        papers=[PaperModel(**dataclasses.asdict(p)) for p in items],
        fetched_at=fetched_at,
        stale=stale,
    )


@router.get("/news", response_model=NewsResponse)
async def news(refresh: bool = Query(default=False)) -> NewsResponse:
    try:
        items, fetched_at, stale = await client.ai_news(refresh=refresh)
    except client.BriefingSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return NewsResponse(
        stories=[StoryModel(**dataclasses.asdict(s)) for s in items],
        fetched_at=fetched_at,
        stale=stale,
    )
