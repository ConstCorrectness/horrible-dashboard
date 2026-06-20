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

**Why a thread, not `asyncio.create_subprocess_exec`:** on Windows the asyncio
subprocess API only works on the `ProactorEventLoop`, and uvicorn runs the app on the
`SelectorEventLoop` whenever `--reload`/`--workers>1` is on (its loop factory returns
Selector when `use_subprocess=True`). Under the asyncio API that fails with
`NotImplementedError`, so the server silently never spawned under the documented dev
command. Spawning with blocking `subprocess.Popen` and pumping stdio on a daemon
thread (sends scheduled back onto the loop with `run_coroutine_threadsafe`) is
loop-agnostic — the server spawns automatically regardless of which loop uvicorn
picked. Same reasoning applies to the terminal PTY.

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
import subprocess
import threading
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

# Cap a single LSP message; a server going haywire shouldn't let us allocate without
# bound. Real payloads (even big completion lists) sit well under this.
_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# Don't let a reader thread block forever relaying to a dead socket.
_SEND_TIMEOUT_S = 5.0


def _evt(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"channel": "lsp", "event": event, "data": data}


def _frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


class LspSession:
    def __init__(
        self,
        session_id: str,
        proc: subprocess.Popen[bytes],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.id = session_id
        self.proc = proc
        self.loop = loop
        self.reader: threading.Thread | None = None
        # Serializes stdin writes (each offloaded to a thread) so concurrent rpcs
        # can't interleave partial frames.
        self.write_lock = asyncio.Lock()
        # Set on terminate so a late read-loop send (e.g. the exit event) stays quiet
        # once we've torn the session down deliberately.
        self.closing = False


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
        exe = shutil.which(cmd[0]) if cmd else None
        if not cmd or exe is None:
            # No server for this language — the editor degrades to no diagnostics.
            await self._error(sid, f"no language server for {language!r}")
            return
        root = data.get("root")
        cwd = root if isinstance(root, str) and os.path.isdir(root) else None
        try:
            # Blocking spawn, offloaded so it never stalls the event loop. Loop-
            # agnostic on purpose (see module docstring) — works under --reload.
            proc = await asyncio.to_thread(
                subprocess.Popen,
                [exe, *cmd[1:]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd,
            )
        except OSError as exc:
            logger.warning("lsp spawn failed (%s): %s", language, exc)
            await self._error(sid, f"spawn failed: {exc}")
            return
        session = LspSession(sid, proc, asyncio.get_running_loop())
        self.sessions[sid] = session
        session.reader = threading.Thread(
            target=self._read_loop, args=(session,), name=f"lsp-{sid}", daemon=True
        )
        session.reader.start()
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
        frame = _frame(payload)
        # Offload the blocking write, serialized so frames never interleave.
        async with session.write_lock:
            try:
                await asyncio.to_thread(self._write, session, frame)
            except (OSError, ValueError) as exc:
                logger.debug("lsp stdin write failed: %s", exc)

    @staticmethod
    def _write(session: LspSession, frame: bytes) -> None:
        stdin = session.proc.stdin
        if stdin is None:
            return
        stdin.write(frame)
        stdin.flush()

    def _send(self, session: LspSession, msg: dict[str, Any]) -> bool:
        """Relay a message to the browser from the reader thread, blocking until the
        loop has sent it (preserves order + applies backpressure). Returns False if
        the socket is gone, so the reader can stop."""
        if session.closing:
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._conn.send_json(msg), session.loop
            )
            future.result(timeout=_SEND_TIMEOUT_S)
            return True
        except Exception:  # noqa: BLE001 — loop stopped / socket closed / timed out
            return False

    def _read_loop(self, session: LspSession) -> None:
        """Parse Content-Length-framed JSON-RPC from the server's stdout (blocking, on
        a daemon thread) and relay each message to the browser until the server exits
        or the socket closes."""
        stdout = session.proc.stdout
        assert stdout is not None
        try:
            while True:
                length = 0
                while True:
                    line = stdout.readline()
                    if not line:
                        raise EOFError
                    stripped = line.strip()
                    if not stripped:
                        break  # blank line ends the header block
                    key, _, value = stripped.partition(b":")
                    if key.strip().lower() == b"content-length":
                        length = int(value.strip() or b"0")
                if length <= 0 or length > _MAX_MESSAGE_BYTES:
                    continue
                body = self._read_exact(stdout, length)
                try:
                    payload = json.loads(body)
                except ValueError:
                    continue
                if not self._send(
                    session, _evt("rpc", {"sessionId": session.id, "payload": payload})
                ):
                    return
        except (EOFError, ValueError, OSError):
            pass
        except Exception as exc:  # noqa: BLE001 — keep one bad server off the WS loop
            logger.debug("lsp read loop ended: %s", exc)
        finally:
            self.sessions.pop(session.id, None)
            self._send(
                session,
                _evt("exit", {"sessionId": session.id, "code": session.proc.poll()}),
            )

    @staticmethod
    def _read_exact(stream: Any, length: int) -> bytes:
        """Read exactly `length` bytes (a pipe read can return short); EOF before then
        means the server died mid-message."""
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = stream.read(remaining)
            if not chunk:
                raise EOFError
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    async def _stop(self, sid: str) -> None:
        session = self.sessions.pop(sid, None)
        if session is not None:
            self._terminate(session)

    async def _error(self, sid: str, message: str) -> None:
        await self._conn.send_json(
            _evt("error", {"sessionId": sid, "message": message})
        )

    def _terminate(self, session: LspSession) -> None:
        session.closing = True
        if session.proc.poll() is None:
            try:
                session.proc.kill()
            except OSError as exc:
                logger.debug("lsp kill failed: %s", exc)
        # Close stdin so a server blocked reading it unblocks; killing it EOFs stdout,
        # which lets the daemon reader thread fall out of its blocking read and exit.
        try:
            if session.proc.stdin is not None:
                session.proc.stdin.close()
        except OSError:
            pass

    async def close_all(self) -> None:
        """Kill every language server — called when the WS connection closes."""
        for session in list(self.sessions.values()):
            self._terminate(session)
        self.sessions.clear()
