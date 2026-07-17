"""The Google connector's own routes — currently just the Drive→library sync trigger.

Replaces `POST /api/integrations/google/sync` from the deleted integrations module.
Kept separate from the generic `/api/connectors` router, which stays connector-agnostic.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.connectors import store
from backend.modules.connectors.providers import google_sync

router = APIRouter(prefix="/connectors/google", tags=["connectors"])


class SyncRequest(BaseModel):
    """`library` defaults to the `connectors.google.driveLibrary` setting, then
    `google_drive`. `full` forces a complete re-crawl instead of reading only what
    changed since the last run."""

    library: str | None = None
    full: bool = False


class SyncResponse(BaseModel):
    task_id: str
    library: str
    full: bool


class SyncStatusResponse(BaseModel):
    library: str
    # None until the first run completes — i.e. "never synced".
    synced: bool = False
    files: int = 0


@router.post("/sync", response_model=SyncResponse)
def trigger_sync(body: SyncRequest | None = None) -> SyncResponse:
    """Queue a Drive sync. Returns immediately — it runs on the task queue."""
    body = body or SyncRequest()
    if not store.is_connected("google"):
        raise HTTPException(
            status_code=409,
            detail="Google isn't connected — connect it from the home page first.",
        )
    library = google_sync.target_library(body.model_dump(exclude_none=True))
    task_id = google_sync.enqueue_sync(library, full=body.full)
    return SyncResponse(task_id=task_id, library=library, full=body.full)


@router.get("/sync", response_model=SyncStatusResponse)
def sync_status(library: str | None = None) -> SyncStatusResponse:
    target = google_sync.target_library({"library": library} if library else {})
    return SyncStatusResponse(
        library=target,
        synced=google_sync.get_start_page_token(target) is not None,
        files=google_sync.synced_file_count(target),
    )
