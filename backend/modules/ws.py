"""Shared `/ws` connection primitive.

Lives on its own (no telemetry/agent imports) so both the telemetry push task and
the agent orchestrator can depend on it without an import cycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

# Optional observer for outbound `/ws` frames (the telemetry module registers one
# for the observability panel). Kept as a plain callable set from outside so this
# module stays free of telemetry imports — the very cycle it exists to avoid.
_send_observer: Callable[[str, dict[str, Any]], None] | None = None
# Whether the observer above would actually record a given `channel`/`event`.
# Registered alongside it because the observer skips some frames by design, and a
# sender that wants to know "will anything see this?" must not have to keep its
# own copy of that list — see `is_observed`.
_observer_wants: Callable[[str, str], bool] | None = None
_active_connections: set[WsConnection] = set()


def register_connection(conn: WsConnection) -> None:
    """Register a new active `/ws` connection."""
    _active_connections.add(conn)


def unregister_connection(conn: WsConnection) -> None:
    """Remove an active `/ws` connection."""
    _active_connections.discard(conn)


async def broadcast_event(channel: str, event: str, data: dict[str, Any]) -> None:
    """Send an event to all active `/ws` connections."""
    message = {"channel": channel, "event": event, "data": data}
    for conn in list(_active_connections):
        try:
            await conn.send_json(message)
        except Exception:
            # A broken connection should be cleaned up by the main loop.
            pass


def is_observed(channel: str, event: str) -> bool:
    """Whether this particular frame would reach the observer.

    Read by senders that can produce a frame *without* building a dict — see
    `hassault.match._template_or_none`. A pre-serialised frame has nothing to
    hand an observer, so such a sender takes the plain path for frames that are
    watched and the fast path for frames that are not.

    Asking per frame rather than "is an observer registered at all" is the whole
    point: the app registers one unconditionally at import, so a global check is
    always `True` and would silently disable every fast path in the process.
    """
    if _send_observer is None:
        return False
    if _observer_wants is None:
        # An observer that did not say what it wants is assumed to want
        # everything. Guessing the other way would drop frames from the panel.
        return True
    return _observer_wants(channel, event)


def set_ws_send_observer(
    observer: Callable[[str, dict[str, Any]], None] | None,
    wants: Callable[[str, str], bool] | None = None,
) -> None:
    """Register (or clear) the observer invoked for every outbound frame.

    `wants` is the same observer's own answer to "would you record this?", used
    by `is_observed`. Optional so existing callers are unaffected.
    """
    global _send_observer, _observer_wants
    _send_observer = observer
    _observer_wants = wants if observer is not None else None


class WsConnection:
    """Wraps one `/ws` socket: serializes concurrent sends (the telemetry push
    task and the agent both write) and tracks outstanding tool-call futures keyed
    by callId."""

    def __init__(self, websocket: Any) -> None:
        self.ws = websocket
        self._send_lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Outstanding permission-approval prompts, keyed by approvalId, resolved
        # when the browser sends an `approval_response`.
        self.pending_approvals: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Dynamic agent tools the browser pushed for this connection (the
        # capability manifest): serialized AgentToolDecl/agent-command shapes,
        # never including handlers. Merged with the static LAYOUT_TOOLS each turn.
        self.agent_tools: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_json(data)
        observer = _send_observer
        if observer is not None:
            try:
                observer("out", data)
            except Exception:
                # Observation must never break the socket.
                pass

    async def send_text(self, text: str) -> None:
        """Send a frame that has already been serialised.

        For senders that build one payload and fan it out to many sockets, where
        `send_json` would re-run `json.dumps` per recipient over bytes that are
        identical — the match server's 20 Hz snapshot is the case this exists
        for. Takes the same lock as `send_json`, so the two interleave safely on
        one socket.

        **No observer call.** There is no dict to hand one, and inventing a
        `json.loads` here would cost more than the saving. Callers must check
        `is_observed(channel, event)` and fall back to `send_json` for a frame
        that something is watching, so nothing is ever silently unobserved.
        """
        async with self._send_lock:
            await self.ws.send_text(text)
