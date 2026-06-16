"""Per-connection terminal sessions over the `terminal` WS channel.

One `TerminalManager` lives per `/ws` connection; its sessions are killed when the
socket closes (v1 — resume-on-reconnect is a later enhancement). The manager pumps
each PTY's output to the browser and relays input/resize/kill back. The spawn
factory is injectable so the lifecycle is testable with a fake PTY. See
docs/modules/terminal.md.

Channel protocol (`{channel:'terminal', event, data}`):

| Direction     | event     | data                          |
| ------------- | --------- | ----------------------------- |
| client→server | `start`   | `{id, cols, rows, cwd?}`      |
| client→server | `input`   | `{id, data}`                  |
| client→server | `resize`  | `{id, cols, rows}`            |
| client→server | `kill`    | `{id}`                        |
| server→client | `started` | `{id}`                        |
| server→client | `output`  | `{id, data}`                  |
| server→client | `exit`    | `{id}`                        |
| server→client | `error`   | `{id?, message}`              |
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.modules.terminal.pty import PtyProcess, default_shell, spawn_pty

logger = logging.getLogger(__name__)

SpawnFn = Callable[..., PtyProcess]


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "terminal", "event": event, "data": data}


class TerminalSession:
    def __init__(self, session_id: str, proc: PtyProcess) -> None:
        self.id = session_id
        self.proc = proc
        self.task: asyncio.Task[None] | None = None


class TerminalManager:
    """Owns the PTY sessions for one WS connection."""

    def __init__(self, conn: Any, spawn: SpawnFn = spawn_pty) -> None:
        self._conn = conn
        self._spawn = spawn
        self.sessions: dict[str, TerminalSession] = {}

    async def handle(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        data = msg.get("data") or {}
        if event == "start":
            await self._start(data)
        elif event == "input":
            self._input(data)
        elif event == "resize":
            self._resize(data)
        elif event == "kill":
            await self._kill(str(data.get("id", "")))

    async def _start(self, data: dict[str, Any]) -> None:
        session_id = str(data.get("id", ""))
        if not session_id or session_id in self.sessions:
            await self._conn.send_json(
                _evt("error", {"id": session_id, "message": "bad or duplicate id"})
            )
            return
        rows = int(data.get("rows", 24))
        cols = int(data.get("cols", 80))
        cwd = data.get("cwd")
        try:
            proc = self._spawn(
                [default_shell()], cwd=cwd, env=None, rows=rows, cols=cols
            )
        except Exception as exc:  # noqa: BLE001 — surface any spawn failure to the UI
            logger.warning("pty spawn failed: %s", exc)
            await self._conn.send_json(
                _evt("error", {"id": session_id, "message": f"spawn failed: {exc}"})
            )
            return
        session = TerminalSession(session_id, proc)
        self.sessions[session_id] = session
        session.task = asyncio.create_task(self._pump(session))
        await self._conn.send_json(_evt("started", {"id": session_id}))

    def _input(self, data: dict[str, Any]) -> None:
        session = self.sessions.get(str(data.get("id", "")))
        if session is not None:
            try:
                session.proc.write(str(data.get("data", "")))
            except Exception as exc:  # noqa: BLE001 — writing to a closed PTY (EOFError on Windows) must not crash the WS loop
                logger.debug("pty write failed: %s", exc)

    def _resize(self, data: dict[str, Any]) -> None:
        session = self.sessions.get(str(data.get("id", "")))
        if session is not None:
            try:
                session.proc.setwinsize(
                    int(data.get("rows", 24)), int(data.get("cols", 80))
                )
            except Exception as exc:  # noqa: BLE001 — resizing a closed PTY must not crash the WS loop
                logger.debug("pty resize failed: %s", exc)

    async def _kill(self, session_id: str) -> None:
        # Terminating the PTY makes the blocked read return EOF, so `_pump`'s
        # finally emits `exit` and removes the session — no task cancellation race.
        session = self.sessions.get(session_id)
        if session is not None:
            self._terminate(session)

    async def _pump(self, session: TerminalSession) -> None:
        """Stream PTY output to the browser until EOF/close. The blocking read runs
        in a thread so it never stalls the event loop."""
        try:
            while True:
                data = await asyncio.to_thread(session.proc.read, 65536)
                if not data:
                    break
                await self._conn.send_json(
                    _evt("output", {"id": session.id, "data": data})
                )
        except (EOFError, OSError):
            pass  # PTY closed (process exited)
        finally:
            self.sessions.pop(session.id, None)
            try:
                await self._conn.send_json(_evt("exit", {"id": session.id}))
            except Exception:  # noqa: BLE001 — socket may already be closed
                pass

    def _terminate(self, session: TerminalSession) -> None:
        try:
            session.proc.terminate(force=True)
        except OSError as exc:
            logger.debug("pty terminate failed: %s", exc)

    async def close_all(self) -> None:
        """Kill every session — called when the WS connection closes. Each PTY's
        `_pump` ends on the resulting EOF and clears its own entry."""
        for session in list(self.sessions.values()):
            self._terminate(session)
        self.sessions.clear()
