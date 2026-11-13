"""Run one uvicorn app for `scripts/dev.mjs`, and stop when it says so.

    python scripts/dev_backend.py backend.app:app 127.0.0.1 8000 <stop-file> <parent-pid>

This stands in for `uvicorn --reload`. On Windows that reloader restarts its
worker with a console Ctrl+C, and a console Ctrl+C reaches every process on the
console: `dev.mjs`, Vite and the game server all took it as "stop", so saving a
backend file ended the whole dev stack. The reloader also imports the app in its
own process before the worker does, which doubled the time to a bound port and
let the parent take a module's single-process claim away from the worker.

So `dev.mjs` watches the files and restarts this process instead, and asks for
that through something only this process is looking at:

- when the stop file appears, shut down normally, so the app's lifespan teardown
  runs (kernels, MCP servers and llama-server are child processes that a hard
  kill would strand);
- when the parent process is gone, leave with it rather than staying behind
  holding the port.

A file and a poll, not stdin: a thread blocked reading a stdin pipe on Windows
holds that pipe's lock, and anything else that touches the handle waits behind
it — including every child process this app starts, which inherits it. The first
version of this script did that and hung before printing a line.
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

#: How often the stop file and the parent are checked. A restart waits at most
#: this long before it begins.
POLL_SECONDS = 0.25

#: A shutdown still running after this long gets its thread stacks printed. Kept
#: under STOP_GRACE_MS in dev.mjs, which is when the process is killed.
SLOW_SHUTDOWN_SECONDS = 5


def _alive(pid: int) -> bool:
    """Whether process `pid` is still running."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    # Not `os.kill(pid, 0)`: on Windows that is TerminateProcess.
    import ctypes

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: dev_backend.py <module:app> <host> <port> <stop-file> <parent-pid>"
        )
    app, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    stop_file, parent = Path(sys.argv[4]), int(sys.argv[5])

    # `python scripts/…` puts scripts/ on the path, not the repo root the app
    # lives under. The uvicorn command line added the working directory itself.
    sys.path.insert(0, os.getcwd())

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            # A browser tab holds a WebSocket open for as long as it lives, and
            # an unbounded graceful shutdown would wait on it for just as long.
            timeout_graceful_shutdown=2,
        )
    )

    def wait_for_stop() -> None:
        while not stop_file.exists() and _alive(parent):
            time.sleep(POLL_SECONDS)
        # Taken now: the listening sockets are the way to the loop, and they are
        # gone once shutdown starts.
        loop = server.servers[0].get_loop() if server.servers else None
        server.should_exit = True

        # `dev.mjs` kills a server that has not left within its grace period, and
        # a kill says nothing about why. If shutdown is still going shortly before
        # that, print what the loop is waiting on, so a teardown that hangs names
        # itself. Tasks rather than threads: a slow shutdown is almost always an
        # idle loop awaiting something, and every thread then just reads "waiting".
        time.sleep(SLOW_SHUTDOWN_SECONDS)
        print(
            f"dev_backend: still shutting down after {SLOW_SHUTDOWN_SECONDS}s. Pending tasks:",
            file=sys.stderr,
            flush=True,
        )
        if loop is None:
            faulthandler.dump_traceback(file=sys.stderr)
            return
        for task in asyncio.all_tasks(loop):
            task.print_stack(limit=8, file=sys.stderr)

    threading.Thread(target=wait_for_stop, name="dev-stop", daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
