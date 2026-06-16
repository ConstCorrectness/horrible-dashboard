"""Per-connection Python REPL sessions over the `repl` WS channel.

One `ReplManager` lives per `/ws` connection (like `TerminalManager`); its kernels
are dropped when the socket closes. A cell runs in a worker thread so a slow line
never stalls the event loop; its stdout/stderr stream live to the pane, and any
`dash.*` call relays a tool call to the *same* browser and blocks the worker thread
on the reply — reusing `conn.pending` (the future map the agent already uses,
keyed by a unique callId). UI mutations are **not** gated here: a REPL is the
user's own direct intent, like the terminal. See docs/modules/repl.md.

Channel protocol (`{channel:'repl', event, data}`):

| Direction     | event               | data                              |
| ------------- | ------------------- | --------------------------------- |
| client→server | `start`             | `{id}`                            |
| client→server | `exec`              | `{id, code}`                      |
| client→server | `close`             | `{id}`                            |
| server→client | `started`           | `{id, banner}`                    |
| server→client | `stdout` / `stderr` | `{id, data}`                      |
| server→client | `result`            | `{id, ok, repr?, error?}`         |
| server→client | `tool_call`         | `{id, callId, name, args}`        |
| client→server | `tool_result`       | `{id, callId, ok, result, error}` |
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import uuid
from typing import Any

from backend.modules.repl.kernel import ReplKernel
from backend.modules.repl.sdk import build_namespace

logger = logging.getLogger(__name__)

# A dash.* relay waits on a browser round-trip; matches the agent's tool timeout.
RELAY_TIMEOUT_S = 30.0

BANNER = f"horrible-dashboard Python {sys.version.split()[0]} — `dash` is your handle."


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "repl", "event": event, "data": data}


class ReplSession:
    def __init__(self, session_id: str, kernel: ReplKernel) -> None:
        self.id = session_id
        self.kernel = kernel
        # Serialize a session's cells: the namespace is shared mutable state.
        self.lock = asyncio.Lock()


class ReplManager:
    """Owns the REPL kernels for one WS connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.sessions: dict[str, ReplSession] = {}

    async def handle(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        data = msg.get("data") or {}
        if event == "start":
            await self._start(str(data.get("id", "")))
        elif event == "exec":
            # Detached: a cell can block on a relayed dash.* call whose tool_result
            # arrives on this same receive loop — awaiting inline would deadlock.
            # (The agent's `ask` is detached for the same reason.)
            asyncio.create_task(
                self._exec(str(data.get("id", "")), str(data.get("code", "")))
            )
        elif event == "close":
            self.sessions.pop(str(data.get("id", "")), None)
        elif event == "tool_result":
            self._resolve_tool(data)

    async def _start(self, session_id: str) -> None:
        if not session_id or session_id in self.sessions:
            await self._conn.send_json(
                _evt("error", {"id": session_id, "message": "bad or duplicate id"})
            )
            return
        loop = asyncio.get_running_loop()
        namespace = build_namespace(self._make_call(session_id, loop))
        self.sessions[session_id] = ReplSession(session_id, ReplKernel(namespace))
        await self._conn.send_json(
            _evt("started", {"id": session_id, "banner": BANNER})
        )

    async def _exec(self, session_id: str, code: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            await self._conn.send_json(
                _evt("error", {"id": session_id, "message": "unknown session"})
            )
            return
        loop = asyncio.get_running_loop()
        async with session.lock:
            stdout = _ChannelWriter(self, session_id, "stdout", loop)
            stderr = _ChannelWriter(self, session_id, "stderr", loop)
            result = await asyncio.to_thread(
                session.kernel.exec_cell, code, stdout, stderr
            )
            await self._conn.send_json(
                _evt(
                    "result",
                    {
                        "id": session_id,
                        "ok": result.ok,
                        "repr": result.value_repr,
                        "error": result.error,
                    },
                )
            )

    # --- dash.* relay: sync bridge run from the worker thread --------------

    def _make_call(self, session_id: str, loop: asyncio.AbstractEventLoop):
        """Build the synchronous `call(name, args)` the SDK uses. It hops to the
        event loop (where the socket lives), sends a tool_call, and blocks this
        worker thread until the browser's tool_result arrives."""

        def call(name: str, args: dict[str, Any]) -> Any:
            future = asyncio.run_coroutine_threadsafe(
                self._relay(session_id, name, args), loop
            )
            # A little over the loop-side timeout so the relay reports first.
            return future.result(timeout=RELAY_TIMEOUT_S + 5)

        return call

    async def _relay(self, session_id: str, name: str, args: dict[str, Any]) -> Any:
        call_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._conn.pending[call_id] = fut
        await self._conn.send_json(
            _evt(
                "tool_call",
                {"id": session_id, "callId": call_id, "name": name, "args": args},
            )
        )
        try:
            data = await asyncio.wait_for(fut, timeout=RELAY_TIMEOUT_S)
        except TimeoutError:
            self._conn.pending.pop(call_id, None)
            return {"error": "tool timed out"}
        if data.get("ok"):
            return data.get("result")
        return {"error": data.get("error", "tool failed")}

    def _resolve_tool(self, data: dict[str, Any]) -> None:
        fut = self._conn.pending.pop(str(data.get("callId", "")), None)
        if fut is not None and not fut.done():
            fut.set_result(data)

    def _emit_threadsafe(
        self, session_id: str, event: str, data: str, loop: asyncio.AbstractEventLoop
    ) -> None:
        asyncio.run_coroutine_threadsafe(
            self._conn.send_json(_evt(event, {"id": session_id, "data": data})), loop
        )

    async def close_all(self) -> None:
        """Drop every session — called when the WS connection closes."""
        self.sessions.clear()


class _ChannelWriter(io.TextIOBase):
    """A stdout/stderr sink that streams each write to the pane. Lives in the worker
    thread; schedules the send on the event loop."""

    def __init__(
        self,
        manager: ReplManager,
        session_id: str,
        event: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._manager = manager
        self._session_id = session_id
        self._event = event
        self._loop = loop

    def write(self, s: str) -> int:
        if s:
            self._manager._emit_threadsafe(self._session_id, self._event, s, self._loop)
        return len(s)

    def writable(self) -> bool:
        return True
