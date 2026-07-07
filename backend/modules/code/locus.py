"""Backend half of the **code-locus bus** — the shared "what code am I looking at"
cursor (path + optional range/symbol) every coding pane publishes to and follows.

Browsers mirror their locus here over the `code` `/ws` channel so `dash.code` and
the agent can read the live locus; the backend (dash) can set it and browsers
follow. Mirrors the library broadcaster's recorder/stream split (push-only fan-out;
the shared `/ws` receive loop owns inbound reads). Self-echo is suppressed on the
frontend by an `origin` client id — the backend just stores and re-fans. See
docs/modules/code.mdx.
"""

from __future__ import annotations

import asyncio
from typing import Any

# The latest locus any browser (or dash) reported. Read by `dash.code.locus()`.
_current_locus: dict[str, Any] = {}


class CodeBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


code_events = CodeBroadcaster()


def current_locus() -> dict[str, Any]:
    """A copy of the most recent locus (empty dict if none reported yet)."""
    return dict(_current_locus)


def set_locus_from_backend(locus: dict[str, Any]) -> dict[str, Any]:
    """Set the locus from the backend (dash/agent) and push it to browsers to follow.
    Tagged `source='dash'` with no client `origin`, so every browser applies it."""
    locus = {**locus, "source": locus.get("source", "dash")}
    _current_locus.clear()
    _current_locus.update(locus)
    code_events.publish({"event": "locus", "data": locus})
    return locus


async def handle_code_message(conn: Any, msg: dict[str, Any]) -> None:
    """Inbound `code` channel: a browser published its locus. Store it (so dash and
    the agent can read the live locus) and re-fan to browsers so panes stay in sync
    across windows. The frontend `origin` tag lets the sender ignore its own echo."""
    if msg.get("event") != "locus":
        return
    data = msg.get("data")
    if not isinstance(data, dict):
        return
    _current_locus.clear()
    _current_locus.update(data)
    code_events.publish({"event": "locus", "data": data})


async def push_code_events(conn: Any) -> None:
    queue = code_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "code", "event": event["event"], "data": event["data"]}
            )
    finally:
        code_events.unsubscribe(queue)
