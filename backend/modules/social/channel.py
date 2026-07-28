"""The `/ws` `social` channel: live roster and presence for the Friends panel.

Presence changes are pushed rather than polled — a friend's machine connecting or
dropping is a fabric event, and the panel should reflect it immediately.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.modules.social import roster


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "social", "event": event, "data": data}


def subscribe_social_conn(conn: Any) -> Any:
    """Fan roster/presence events out to one browser connection. Returns the
    unsubscribe handle the `/ws` loop calls on disconnect."""

    def cb(event: str, data: dict[str, Any]) -> None:
        asyncio.ensure_future(conn.send_json(_evt(event, data)))

    return roster.subscribe(cb)


async def handle_social_message(conn: Any, msg: dict[str, Any]) -> None:
    """Route an inbound `social`-channel message from the browser.

    The mutating events are dispatched with `create_task` rather than awaited:
    each one dials or messages a peer, and a handler that blocks this receive loop
    would stall every other channel on the same socket.
    """
    event = msg.get("event")
    data = msg.get("data") or {}
    if event == "roster":
        await conn.send_json(_evt("roster", roster.snapshot().model_dump()))
    elif event == "add":
        asyncio.create_task(
            roster.add_friend(
                str(data.get("code") or ""),
                data.get("address"),
                data.get("note"),
            )
        )
    elif event == "respond":
        asyncio.create_task(
            roster.respond(str(data.get("personId") or ""), bool(data.get("accept")))
        )
    elif event == "remove":
        asyncio.create_task(roster.remove(str(data.get("personId") or "")))
    elif event == "block":
        asyncio.create_task(roster.block(str(data.get("personId") or "")))
