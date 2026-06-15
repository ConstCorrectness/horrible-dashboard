"""Shared `/ws` connection primitive.

Lives on its own (no telemetry/agent imports) so both the telemetry push task and
the agent orchestrator can depend on it without an import cycle.
"""

import asyncio
from typing import Any


class WsConnection:
    """Wraps one `/ws` socket: serializes concurrent sends (the telemetry push
    task and the agent both write) and tracks outstanding tool-call futures keyed
    by callId."""

    def __init__(self, websocket: Any) -> None:
        self.ws = websocket
        self._send_lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def send_json(self, data: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.ws.send_json(data)
