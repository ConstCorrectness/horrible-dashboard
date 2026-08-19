"""HTTP for the notification inbox.

The socket delivers what arrives while you are here; these three routes are what
a surface uses to find out what arrived while you were not. Without them the bell
starts empty on every reload, which is the same as not having an inbox — a toast
you missed is only harmless if something durable is still holding it.

Read and cleared state are **server-side** rather than per-browser for the same
reason: one notification reaches the shell toast, the bell, an in-game overlay and
an OS notification, and marking it answered has to mean answered everywhere. See
docs/modules/notifications.mdx.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.modules.notifications import service, store

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    """One inbox row.

    `model_config` allows the extra keys each category carries in `data` (an
    invite's room and map, a watch's id) to ride along rather than being filtered
    out — which is what a `response_model` would otherwise silently do, leaving
    the Join button with nothing to join.
    """

    model_config = {"extra": "allow"}

    id: str
    category: str
    kind: str
    title: str
    body: str
    person_id: str | None = None
    dedupe: str | None = None
    at: float
    read: bool


class ReadRequest(BaseModel):
    #: One notification, or every unread one when omitted.
    id: str | None = None


class ClearRequest(BaseModel):
    id: str | None = None
    #: Retires every surface showing this key at once — the invite case.
    dedupe: str | None = None


@router.get("/feed", response_model=list[NotificationOut])
async def get_feed(limit: int = store.FEED_LIMIT) -> list[dict]:
    """The live inbox, newest first. Expired rows are swept on the way past."""
    store.init_notifications_db()
    return store.feed(limit)


@router.post("/read")
async def mark_read(body: ReadRequest) -> dict[str, bool]:
    store.init_notifications_db()
    store.mark_read(body.id)
    return {"ok": True}


@router.post("/clear")
async def clear(body: ClearRequest) -> dict[str, bool]:
    """Retire a notification.

    By `dedupe` the retraction is broadcast, so the other surfaces drop it too
    rather than going stale — that broadcast is the whole difference between
    "dismissed here" and "answered".
    """
    store.init_notifications_db()
    if body.dedupe:
        await service.retract(body.dedupe)
    else:
        store.clear(body.id)
    return {"ok": True}
