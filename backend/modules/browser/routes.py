"""HTTP surface for the embedded browser (`/api/browser/*`).

- `GET /read` — reader mode: SSRF-safe server-side fetch + main-content extraction,
  so a page that refuses iframing can still be read inline.
- `GET/POST/DELETE /history` and `/bookmarks` — the server-side catalog backing the
  panel's history dropdown and bookmarks strip.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query

from backend.modules.browser import store
from backend.modules.browser.fetch import UnsafeUrlError, fetch_readable
from backend.modules.browser.models import (
    AddBookmarkRequest,
    Bookmark,
    BookmarksResponse,
    EngineStatus,
    HistoryEntry,
    HistoryListResponse,
    OkResponse,
    ReaderResponse,
    RecordHistoryRequest,
)
from backend.modules.browser.session import server_browser_enabled

router = APIRouter(prefix="/browser", tags=["browser"])


@router.get("/engine", response_model=EngineStatus)
def engine_status() -> EngineStatus:
    """Whether the real backend browser engine is available (gated + installed).

    The panel calls this to decide `auto` mode: full engine when on, iframe otherwise.
    Reports installed=False if Playwright isn't importable so the UI can hint the extra.
    """
    installed = True
    try:
        import playwright  # noqa: F401
    except Exception:  # noqa: BLE001
        installed = False
    return EngineStatus(enabled=server_browser_enabled(), installed=installed)


@router.get("/read", response_model=ReaderResponse)
async def read(url: str = Query(..., description="page URL to read")) -> ReaderResponse:
    """Fetch and extract the readable article for `url` (SSRF-guarded)."""
    try:
        article = await fetch_readable(url)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"unsafe URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
    return ReaderResponse(
        url=article.url,
        title=article.title,
        author=article.author,
        text=article.text,
    )


@router.get("/history", response_model=HistoryListResponse)
def get_history(limit: int = 100) -> HistoryListResponse:
    return HistoryListResponse(
        entries=[HistoryEntry(**e) for e in store.list_history(limit)]
    )


@router.post("/history", response_model=HistoryEntry)
def add_history(req: RecordHistoryRequest) -> HistoryEntry:
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    return HistoryEntry(**store.record_visit(req.url, req.title or req.url))


@router.delete("/history", response_model=OkResponse)
def delete_history() -> OkResponse:
    store.clear_history()
    return OkResponse()


@router.get("/bookmarks", response_model=BookmarksResponse)
def get_bookmarks() -> BookmarksResponse:
    return BookmarksResponse(bookmarks=[Bookmark(**b) for b in store.list_bookmarks()])


@router.post("/bookmarks", response_model=Bookmark)
def create_bookmark(req: AddBookmarkRequest) -> Bookmark:
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    return Bookmark(**store.add_bookmark(req.url, req.title or req.url, req.tags))


@router.delete("/bookmarks/{bookmark_id}", response_model=OkResponse)
def delete_bookmark(bookmark_id: str) -> OkResponse:
    if not store.delete_bookmark(bookmark_id):
        raise HTTPException(status_code=404, detail="bookmark not found")
    return OkResponse()
