"""Per-connection Language Server sessions over the `lsp` WS channel.

The backend is a **dumb transport**: it spawns the right language server for a
language, then pipes JSON-RPC both ways between the browser and the server's stdio
(translating LSP's `Content-Length` framing ↔ discrete WS messages). It does **not**
parse LSP semantics — the frontend is the LSP client (see editor/lsp.ts), so every
capability (diagnostics, completion, hover, …) flows through this one pipe with no
backend change. One `LspManager` lives per `/ws` connection; its servers are killed
when the socket closes. The server command is chosen from a fixed registry by
languageId (never from client input), so the channel can't be used to run arbitrary
processes. See docs/modules/editor.md.

Channel protocol (`{channel:'lsp', event, data}`):

| Direction     | event     | data                                  |
| ------------- | --------- | ------------------------------------- |
| client→server | `start`   | `{sessionId, languageId, root?}`      |
| client→server | `rpc`     | `{sessionId, payload}` (JSON-RPC msg) |
| client→server | `stop`    | `{sessionId}`                         |
| server→client | `started` | `{sessionId}`                         |
| server→client | `rpc`     | `{sessionId, payload}`                |
| server→client | `exit`    | `{sessionId, code}`                   |
| server→client | `error`   | `{sessionId?, message}`               |
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any

logger = logging.getLogger(__name__)

# languageId → server command. Only these (resolvable on PATH) are ever spawned;
# the client can't supply a command, so this is not an arbitrary-exec surface.
LSP_SERVERS: dict[str, list[str]] = {
    "python": ["pylsp"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "typescriptreact": ["typescript-language-server", "--stdio"],
    "javascriptreact": ["typescript-language-server", "--stdio"],
    "rust": ["rust-analyzer"],
}


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "lsp", "event": event, "data": data}


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


class LspSession:
    def __init__(self, session_id: str, proc: asyncio.subprocess.Process) -> None:
        self.id = session_id
        self.proc = proc
        self.reader: asyncio.Task[None] | None = None


class LspManager:
    """Owns the language-server subprocesses for one WS connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.sessions: dict[str, LspSession] = {}

    async def handle(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        data = msg.get("data") or {}
        if event == "start":
            await self._start(data)
        elif event == "rpc":
            await self._rpc(data)
        elif event == "stop":
            await self._stop(str(data.get("sessionId", "")))

    async def _start(self, data: dict[str, Any]) -> None:
        sid = str(data.get("sessionId", ""))
        language = str(data.get("languageId", ""))
        if not sid or sid in self.sessions:
            await self._error(sid, "bad or duplicate sessionId")
            return
        cmd = LSP_SERVERS.get(language)
        if not cmd or shutil.which(cmd[0]) is None:
            # No server for this language — the editor degrades to no diagnostics.
            await self._error(sid, f"no language server for {language!r}")
            return
        root = data.get("root")
        cwd = root if isinstance(root, str) and os.path.isdir(root) else None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=cwd,
            )
        except OSError as exc:
            logger.warning("lsp spawn failed (%s): %s", language, exc)
            await self._error(sid, f"spawn failed: {exc}")
            return
        session = LspSession(sid, proc)
        self.sessions[sid] = session
        session.reader = asyncio.create_task(self._read_loop(session))
        await self._conn.send_json(_evt("started", {"sessionId": sid}))

    async def _rpc(self, data: dict[str, Any]) -> None:
        session = self.sessions.get(str(data.get("sessionId", "")))
        payload = data.get("payload")
        if (
            session is None
            or not isinstance(payload, dict)
            or session.proc.stdin is None
        ):
            return
        try:
            session.proc.stdin.write(_frame(payload))
            await session.proc.stdin.drain()
        except (OSError, ConnectionError) as exc:
            logger.debug("lsp stdin write failed: %s", exc)

    async def _read_loop(self, session: LspSession) -> None:
        """Parse Content-Length-framed JSON-RPC from the server's stdout and relay
        each message to the browser, until the server exits."""
        stdout = session.proc.stdout
        assert stdout is not None
        try:
            while True:
                length = 0
                while True:
                    line = await stdout.readline()
                    if not line:
                        raise asyncio.IncompleteReadError(line, None)
                    stripped = line.strip()
                    if not stripped:
                        break  # blank line ends the header block
                    key, _, value = stripped.partition(b":")
                    if key.strip().lower() == b"content-length":
                        length = int(value.strip() or b"0")
                if length <= 0:
                    continue
                body = await stdout.readexactly(length)
                try:
                    payload = json.loads(body)
                except ValueError:
                    continue
                await self._conn.send_json(
                    _evt("rpc", {"sessionId": session.id, "payload": payload})
                )
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception as exc:  # noqa: BLE001 — keep one bad server off the WS loop
            logger.debug("lsp read loop ended: %s", exc)
        finally:
            self.sessions.pop(session.id, None)
            code = session.proc.returncode
            try:
                await self._conn.send_json(
                    _evt("exit", {"sessionId": session.id, "code": code})
                )
            except Exception:  # noqa: BLE001 — socket may already be closed
                pass

    async def _stop(self, sid: str) -> None:
        session = self.sessions.pop(sid, None)
        if session is not None:
            self._terminate(session)

    async def _error(self, sid: str, message: str) -> None:
        await self._conn.send_json(
            _evt("error", {"sessionId": sid, "message": message})
        )

    def _terminate(self, session: LspSession) -> None:
        if session.reader is not None:
            session.reader.cancel()
        if session.proc.returncode is None:
            try:
                session.proc.kill()
            except (OSError, ProcessLookupError) as exc:
                logger.debug("lsp kill failed: %s", exc)

    async def close_all(self) -> None:
        """Kill every language server — called when the WS connection closes."""
        for session in list(self.sessions.values()):
            self._terminate(session)
        self.sessions.clear()
