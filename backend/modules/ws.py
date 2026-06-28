"""Shared `/ws` connection primitive.

Lives on its own (no telemetry/agent imports) so both the telemetry push task and
the agent orchestrator can depend on it without an import cycle.
"""

import asyncio
from collections.abc import Callable
from typing import Any

# Optional observer for outbound `/ws` frames (the telemetry module registers one
# for the observability panel). Kept as a plain callable set from outside so this
# module stays free of telemetry imports — the very cycle it exists to avoid.
_send_observer: Callable[[str, dict[str, Any]], None] | None = None


def set_ws_send_observer(
    observer: Callable[[str, dict[str, Any]], None] | None,
) -> None:
    """Register (or clear) the observer invoked for every outbound frame."""
    global _send_observer
    _send_observer = observer


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
