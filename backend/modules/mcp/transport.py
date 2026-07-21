"""Cross-platform stdio transport for MCP client sessions.

This is a drop-in replacement for `mcp.client.stdio.stdio_client`, and it exists for
one reason: **the SDK's version cannot spawn a process under our documented dev
command.**

`stdio_client` spawns via `anyio.open_process`, which on the asyncio backend is
`asyncio.create_subprocess_exec`. That API is implemented only on Windows'
`ProactorEventLoop`, and uvicorn runs the app on the `SelectorEventLoop` whenever
`--reload` is on (its loop factory returns Selector when `use_subprocess=True`) — which
`pnpm dev` always passes. The result is a bare `NotImplementedError` at spawn time, so
every stdio MCP server would silently fail to start for anyone developing on Windows.

The fix is the same one the LSP pipe and the terminal PTY already use (see
`backend/modules/lsp/manager.py`): spawn with blocking `subprocess.Popen` offloaded to a
worker thread, pump stdout on a daemon thread, and hand sends back to the event loop
with `run_coroutine_threadsafe`. That is loop-agnostic — it works on Proactor, Selector,
and uvloop alike, on every OS.

`ClientSession` only ever sees a pair of anyio memory object streams, so it neither
knows nor cares which transport produced them. MCP's stdio framing is newline-delimited
JSON (not LSP's `Content-Length` headers), so the reader splits on `\\n`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

# How long a terminated server gets to exit on its own before it is killed outright.
_TERMINATE_GRACE_S = 5.0

# Windows resolves `npx`/`npm`/`node` to `.cmd` shims that `CreateProcess` will not run
# without the extension. `shutil.which` finds them via PATHEXT; the SDK does the same
# dance in `_get_executable_command`, and omitting it makes every `npx`-launched MCP
# server (which is most of them) fail with FileNotFoundError on Windows only.
_WINDOWS_SHIM_EXTS = (".cmd", ".bat", ".exe")


def resolve_command(command: str) -> str | None:
    """The executable to actually spawn for `command`, or None if it isn't on PATH.

    Returning None rather than raising lets the caller report "server X isn't
    installed" as a status, instead of a stack trace on an unremarkable condition.
    """
    if found := shutil.which(command):
        return found
    if sys.platform == "win32":
        for ext in _WINDOWS_SHIM_EXTS:
            if found := shutil.which(command + ext):
                return found
    return None


def _creation_flags() -> int:
    """Keep spawned servers from flashing a console window on Windows.

    Without this every `npx` server pops a visible cmd window on the user's desktop,
    which is unacceptable for something the agent starts in the background.
    """
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


class StdioProcess:
    """A spawned MCP server process plus the threads pumping its stdio.

    Held by `popen_stdio_client`; not part of the public surface.
    """

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        loop: asyncio.AbstractEventLoop,
        writer: MemoryObjectSendStream[SessionMessage | Exception],
    ) -> None:
        self.proc = proc
        self.loop = loop
        self._writer = writer
        self._closed = threading.Event()
        self.reader_thread: threading.Thread | None = None
        # Serializes stdin writes so two concurrent tool calls can't interleave
        # halves of two JSON lines into the same pipe.
        self.write_lock = asyncio.Lock()

    def start_reader(self, name: str) -> None:
        self.reader_thread = threading.Thread(
            target=self._read_loop, name=f"mcp-{name}", daemon=True
        )
        self.reader_thread.start()

    def _read_loop(self) -> None:
        """Pump the server's stdout into the read stream. Runs on a daemon thread.

        Every send is bounced back onto the event loop — anyio memory streams are not
        thread-safe, and this thread is deliberately outside the loop.
        """
        stdout = self.proc.stdout
        if stdout is None:
            return
        try:
            for raw in stdout:
                if self._closed.is_set():
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = types.JSONRPCMessage.model_validate_json(line)
                except ValueError as exc:
                    # A malformed line is the server's bug, not a reason to tear the
                    # session down — forward it so ClientSession can surface it.
                    logger.debug("mcp: unparseable line from server: %s", exc)
                    self._send(exc)
                    continue
                self._send(SessionMessage(message))
        except (OSError, ValueError) as exc:
            logger.debug("mcp: stdout reader stopped: %s", exc)
        finally:
            self._close_writer()

    def _send(self, item: SessionMessage | Exception) -> None:
        if self._closed.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._writer.send(item), self.loop)
        except RuntimeError:
            # Loop already closed (shutdown race) — nothing left to deliver to.
            pass

    def _close_writer(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            asyncio.run_coroutine_threadsafe(self._writer.aclose(), self.loop)
        except RuntimeError:
            pass

    def write(self, payload: bytes) -> None:
        """Blocking stdin write — always called via `asyncio.to_thread`."""
        stdin = self.proc.stdin
        if stdin is None:
            return
        stdin.write(payload)
        stdin.flush()

    def terminate(self) -> None:
        """Stop the reader and end the process, escalating to kill if it lingers."""
        self._closed.set()
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            logger.warning("mcp: server ignored terminate, killing")
            self.proc.kill()
        except OSError as exc:
            logger.debug("mcp: terminate failed: %s", exc)


@asynccontextmanager
async def popen_stdio_client(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> AsyncIterator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ]
]:
    """Spawn an MCP server over stdio and yield `(read_stream, write_stream)`.

    Signature-compatible with what `ClientSession` expects, so callers can swap this
    for the SDK's `stdio_client` without further change.

    Raises `FileNotFoundError` if the command isn't on PATH and `OSError` if the spawn
    itself fails — both are reported as server status rather than crashing the app.
    """
    executable = resolve_command(command)
    if executable is None:
        raise FileNotFoundError(
            f"MCP server command {command!r} is not on PATH. "
            "Install it, or give an absolute path in the server config."
        )

    read_writer, read_stream = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)

    # Inherit the parent environment so servers find node/python/PATH, with the
    # config's own vars layered on top.
    child_env = {**os.environ, **(env or {})}

    proc = await asyncio.to_thread(
        subprocess.Popen,
        [executable, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Server logs go to stderr by convention; we don't interleave them into the
        # protocol stream, and inheriting would spam the backend console.
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        env=child_env,
        bufsize=0,
        creationflags=_creation_flags(),
    )

    handle = StdioProcess(proc, asyncio.get_running_loop(), read_writer)
    handle.start_reader(command)

    async def pump_writes() -> None:
        """Forward outbound messages to the server's stdin, one at a time."""
        try:
            async with write_reader:
                async for message in write_reader:
                    line = (
                        message.message.model_dump_json(
                            by_alias=True, exclude_none=True
                        )
                        + "\n"
                    ).encode("utf-8")
                    async with handle.write_lock:
                        try:
                            await asyncio.to_thread(handle.write, line)
                        except (OSError, ValueError) as exc:
                            logger.debug("mcp: stdin write failed: %s", exc)
                            return
        except anyio.ClosedResourceError:
            pass

    writer_task = asyncio.create_task(pump_writes(), name=f"mcp-writer-{command}")
    try:
        yield read_stream, write_stream
    finally:
        writer_task.cancel()
        # Closing stdin is what tells a well-behaved server to exit; terminate is the
        # backstop for one that doesn't.
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        await asyncio.to_thread(handle.terminate)
        await read_stream.aclose()
        await write_stream.aclose()


def describe_target(command: str, args: list[str]) -> dict[str, Any]:
    """What this node would actually run for a stdio config, for the UI's
    "why won't this connect" view. Resolution is the usual failure, so show it."""
    resolved = resolve_command(command)
    return {
        "command": command,
        "resolved": resolved,
        "available": resolved is not None,
        "argv": [resolved or command, *args],
    }
