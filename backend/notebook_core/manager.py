"""Process-global registry of kernel sessions and the shared `/ws` event handling.

Kernels are process-global: they keep running when a pane (or the whole tab)
closes; a socket close only unsubscribes. The six lifecycle events
(`run_cell`/`run_all`/`cells`/`interrupt`/`restart`/`shutdown`) are handled here
for every consumer; `open` and session construction are the seams a subclass fills
(`training` resolves a project venv; `notebook` resolves a file + a managed venv).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from backend.notebook_core.config import SessionConfig
from backend.notebook_core.session import KernelSession

logger = logging.getLogger(__name__)

# Events that require an existing session (used to decide when to reply "unknown
# session" rather than silently dropping an event for a not-yet-opened key).
SESSION_EVENTS = (
    "run_cell",
    "run_all",
    "cells",
    "set_mode",
    "comm_msg",
    "interrupt",
    "restart",
    "shutdown",
)


class KernelSessionManager:
    """Base manager. Subclasses set `channel`, `SessionCls`, and the open seams."""

    channel = "notebook"
    SessionCls: type[KernelSession] = KernelSession

    def __init__(self) -> None:
        self.sessions: dict[str, KernelSession] = {}
        self._open_lock = asyncio.Lock()
        self._captured_loop: asyncio.AbstractEventLoop | None = None

    def _evt(self, event: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"channel": self.channel, "event": event, "data": data}

    # --- ws dispatch ---------------------------------------------------------

    async def handle(self, conn: Any, msg: dict[str, Any]) -> None:
        self._captured_loop = asyncio.get_running_loop()
        event = str(msg.get("event", ""))
        data = msg.get("data") or {}
        if event == "open":
            # Detached: a cold kernel takes seconds to boot; never stall the
            # receive loop (see ws-handler conventions).
            asyncio.create_task(self._open(conn, data))
            return
        if await self._handle_extra(conn, event, data):
            return
        session = self.sessions.get(str(data.get("sessionKey", "")))
        if session is None:
            if event in SESSION_EVENTS:
                await conn.send_json(
                    self._evt(
                        "error",
                        {"message": f"unknown session: {data.get('sessionKey')}"},
                    )
                )
            return
        await self._handle_session_event(conn, session, event, data)

    async def _handle_extra(self, conn: Any, event: str, data: dict[str, Any]) -> bool:
        """Subclass hook for module-specific events (e.g. training `watch_run`).
        Return True if the event was handled here."""
        return False

    async def _handle_session_event(
        self, conn: Any, session: KernelSession, event: str, data: dict[str, Any]
    ) -> None:
        if event == "run_cell":
            cell_id = str(data.get("cellId", ""))
            ran = (
                session.reactive_enqueue(cell_id)
                if session.mode == "reactive"
                else session.enqueue(cell_id)
            )
            if not ran:
                await conn.send_json(
                    self._evt(
                        "error",
                        {
                            "sessionKey": session.key,
                            "message": f"no code cell {data.get('cellId')}",
                        },
                    )
                )
        elif event == "run_all":
            session.enqueue_all()
        elif event == "set_mode":
            await asyncio.to_thread(session.set_mode, str(data.get("mode", "")))
        elif event == "comm_msg":
            buffers = [base64.b64decode(b) for b in (data.get("buffers") or [])]
            session.send_comm(
                str(data.get("commId", "")), data.get("data") or {}, buffers
            )
        elif event == "cells":
            ops = list(data.get("ops") or [])
            try:
                await asyncio.to_thread(session.apply_ops, ops)
            except ValueError as exc:
                await conn.send_json(
                    self._evt("error", {"sessionKey": session.key, "message": str(exc)})
                )
                return
            # Reactive graph refresh (+ stale-def cascade when a cell was deleted).
            had_delete = any(op.get("op") == "delete" for op in ops)
            await asyncio.to_thread(session.on_cells_changed, had_delete)
            # Everyone else re-syncs; the sender already applied optimistically.
            payload = self._evt(
                "cells_changed",
                {"sessionKey": session.key, "notebook": session.notebook_model()},
            )
            for sub in list(session.subscribers):
                if sub is not conn:
                    await sub.send_json(payload)
        elif event == "interrupt":
            await asyncio.to_thread(session.interrupt)
        elif event == "restart":
            asyncio.create_task(asyncio.to_thread(session.restart))
        elif event == "shutdown":
            self.sessions.pop(session.key, None)
            asyncio.create_task(asyncio.to_thread(session.shutdown))

    # --- open (generic; training overrides for its venv/error handling) ------

    async def _open(self, conn: Any, data: dict[str, Any]) -> None:
        try:
            key = self._session_key(data)
        except Exception as exc:  # noqa: BLE001 — bad open request
            await conn.send_json(self._evt("error", {"message": str(exc)}))
            return
        try:
            async with self._open_lock:
                session = self.sessions.get(key)
                if session is None:
                    session = await asyncio.to_thread(self._create_session, data, key)
                    self.sessions[key] = session
        except Exception as exc:  # noqa: BLE001 — surfaced to the pane
            logger.exception("kernel open failed for %s", key)
            await conn.send_json(
                self._evt("error", {"sessionKey": key, "message": str(exc)})
            )
            return
        session.subscribers.add(conn)
        await conn.send_json(
            self._evt(
                "opened",
                {
                    "sessionKey": session.key,
                    "notebook": session.notebook_model(),
                    "kernel": session.status,
                    "mode": session.mode,
                    # Live widget comms so a pane attaching to an already-running
                    # kernel re-hydrates its widgets (reattach-resync).
                    "comms": session.comms_snapshot(),
                    **self._opened_extra(session, data),
                },
            )
        )
        # Seed the reactive graph so the pane shows edges/diagnostics immediately.
        if session.mode == "reactive":
            await asyncio.to_thread(session.rebuild_graph)

    def _session_key(self, data: dict[str, Any]) -> str:
        raise NotImplementedError

    def _create_session(self, data: dict[str, Any], key: str) -> KernelSession:
        """Build (and start) a session for `key`. Blocking — runs on a thread."""
        config = self._build_config(data, key)
        session = self.SessionCls(config, self._loop())
        session.start()
        return session

    def _build_config(self, data: dict[str, Any], key: str) -> SessionConfig:
        raise NotImplementedError

    def _opened_extra(
        self, session: KernelSession, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Extra fields to include in the `opened` payload (subclass hook)."""
        return {}

    # --- shared bookkeeping --------------------------------------------------

    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = self._captured_loop
        if loop is None or loop.is_closed():
            raise RuntimeError("no event loop available for kernel fanout")
        return loop

    def detach(self, conn: Any) -> None:
        """Socket closed: unsubscribe everywhere. Kernels keep running."""
        for session in self.sessions.values():
            session.subscribers.discard(conn)

    def session_for(self, key: str) -> KernelSession | None:
        return self.sessions.get(key)

    async def shutdown_all(self) -> None:
        for key in list(self.sessions):
            session = self.sessions.pop(key)
            await asyncio.to_thread(session.shutdown)
