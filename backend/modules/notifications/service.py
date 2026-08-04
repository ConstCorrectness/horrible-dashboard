"""Delivering a notification, and firing the watches that ask for one.

One function — `notify()` — is the single door every proactive message goes
through, and the mute check lives inside it. That placement is the design: a rule
enforced at the producer means a muted notification is never sent, never crosses
the socket, and never reaches whatever the browser would have done with it. Filter
it in the browser instead and "muted" still buzzes a phone.

The transport is `ws.broadcast_event`, which needs no chat turn and no open pane —
already the way `hassault` pushes invites. It lands on a `notifications` channel
the frontend subscribes to at boot.

The watch evaluator hangs off the roster's `presence` event, which exists precisely
because there was no "came online" signal before this. See docs/modules/notifications.mdx.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.notifications import store
from backend.modules.ws import broadcast_event

logger = logging.getLogger(__name__)

CHANNEL = "notifications"


async def notify(
    category: str,
    title: str,
    body: str,
    *,
    person_id: str | None = None,
    kind: str = "info",
    data: dict[str, Any] | None = None,
) -> bool:
    """Deliver one notification unless a rule says otherwise. Returns whether it went.

    `person_id` is who it is *about*, and it is what a per-person mute matches on —
    so passing it is not optional decoration: without it, "mute Andrew" cannot
    apply to a notification that is about Andrew.
    """
    if store.is_muted(category, person_id):
        logger.debug("notification suppressed by a mute: %s/%s", category, person_id)
        return False
    await broadcast_event(
        CHANNEL,
        "notify",
        {
            "category": category,
            "kind": kind,
            "title": title,
            "body": body,
            "person_id": person_id,
            **(data or {}),
        },
    )
    return True


async def on_presence(event: dict[str, Any]) -> None:
    """A friend came online or went offline — fire any watch that was waiting.

    Only watches fire here. Presence is *not* notified about by default: a toast
    every time any friend's laptop connects is the notification everyone turns off
    first, and turning it into an opt-in ("tell me when Andrew logs in") is the
    whole point of a watch.
    """
    person_id = str(event.get("person_id") or "")
    online = bool(event.get("online"))
    name = str(event.get("display_name") or person_id)

    for watch in store.list_watches("presence"):
        if watch["subject"] and watch["subject"] != person_id:
            continue
        # `{"online": true}` waits for an arrival, `false` for a departure.
        if bool(watch["predicate"].get("online", True)) != online:
            continue
        sent = await notify(
            "watch",
            f"{watch['label']} is {'online' if online else 'offline'}",
            watch["note"] or f"You asked to be told when {watch['label']} logged in.",
            person_id=person_id,
            kind="info",
            data={"watch_id": watch["id"]},
        )
        # Marked fired whether or not it was delivered: a one-shot the user muted
        # is one they chose not to hear, not one still owed to them.
        if watch["one_shot"]:
            store.mark_fired(watch["id"])
        logger.info("watch %s fired for %s (delivered=%s)", watch["id"], name, sent)


def register() -> None:
    """Subscribe to the roster's presence events. Called once at network startup."""
    import asyncio

    from backend.modules.social import roster

    store.init_notifications_db()

    def _on_social(event: str, data: dict[str, Any]) -> None:
        if event != "presence":
            return
        # Detached: this runs on the roster's synchronous emit, which is itself
        # called from the hub's receive loop.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(on_presence(data))

    roster.subscribe(_on_social)
