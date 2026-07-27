"""HTTP surface for the arXiv module (`/api/arxiv/*`).

Download rides the research module's guarded PDF pipeline — one egress policy,
no exceptions for "known" hosts — and files the paper into the library with the
paper's real title/authors, tagged `arxiv`.
"""

from __future__ import annotations

import dataclasses
import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from backend.modules.arxiv import client
from backend.modules.arxiv.models import (
    ArxivDownloadRequest,
    ArxivDownloadResponse,
    ArxivEntryModel,
    ArxivSearchResponse,
)
from backend.modules.artifacts.models import ArtifactModel
from backend.modules.browser.fetch import UnsafeUrlError
from backend.modules.library.models import SourceModel
from backend.modules.research import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/arxiv", tags=["arxiv"])


def _entry_model(entry: client.ArxivEntry) -> ArxivEntryModel:
    return ArxivEntryModel(**dataclasses.asdict(entry))


def _rate_limited(exc: client.ArxivRateLimited) -> HTTPException:
    """arXiv's 429 is ours too — pass the status and the wait through verbatim."""
    return HTTPException(
        status_code=429,
        detail=str(exc),
        headers={"Retry-After": str(max(1, int(exc.retry_after)))},
    )


@router.get("/search", response_model=ArxivSearchResponse)
async def search(
    query: str = Query(default=""),
    start: int = Query(default=0, ge=0),
    max_results: int = Query(default=20, ge=1, le=100),
    category: str | None = Query(default=None),
    sort: str = Query(default="relevance"),
) -> ArxivSearchResponse:
    try:
        total, entries = await client.search(
            query, start=start, max_results=max_results, category=category, sort=sort
        )
    except client.ArxivRateLimited as exc:
        raise _rate_limited(exc) from exc
    except client.ArxivError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"arXiv unreachable: {exc}"
        ) from exc
    return ArxivSearchResponse(
        query=query,
        total=total,
        start=start,
        entries=[_entry_model(e) for e in entries],
    )


@router.get("/paper/{arxiv_id:path}", response_model=ArxivEntryModel)
async def paper(arxiv_id: str) -> ArxivEntryModel:
    try:
        entry = await client.get_paper(arxiv_id)
    except client.ArxivRateLimited as exc:
        raise _rate_limited(exc) from exc
    except client.ArxivError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"arXiv unreachable: {exc}"
        ) from exc
    return _entry_model(entry)


@router.post("/download", response_model=ArxivDownloadResponse)
async def download(req: ArxivDownloadRequest) -> ArxivDownloadResponse:
    try:
        entry = await client.get_paper(req.arxiv_id)
    except client.ArxivRateLimited as exc:
        raise _rate_limited(exc) from exc
    except client.ArxivError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"arXiv unreachable: {exc}"
        ) from exc
    try:
        result = await service.save_pdf_url(
            entry.pdf_url,
            library=req.library,
            title=entry.title,
            tags=sorted({*req.tags, "arxiv"}),
            source_url=entry.abs_url,
            author=", ".join(entry.authors[:6]) or None,
        )
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"PDF fetch failed: {exc}") from exc
    return ArxivDownloadResponse(
        artifact=ArtifactModel(**result["artifact"]),
        source=SourceModel(**result["source"]),
        entry=_entry_model(entry),
    )
