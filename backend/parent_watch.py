"""Stop this backend when the desktop shell that started it is gone.

The Electron shell spawns uvicorn in a process group of its own, so that one signal
reaches whatever `uv run` started under it — and so the backend outlives the shell
whenever the shell dies without running `will-quit`. Found on Linux: stopping
`pnpm dev:desktop` signalled Electron's whole process group, its GPU process and
zygote died in the same instant, Chromium aborted ("GPU process isn't usable.
Goodbye.") before it could stop the backend, and uvicorn kept :8000 — serving, and
holding the node's relay identity against the next launch, for hours.

The shell sets `HORRIBLE_EXIT_WITH_PARENT=1`, and how the backend notices the shell
going differs by OS, because the one mechanism that is right on POSIX is a trap on
Windows:

- **POSIX: stdin EOF.** The shell hands the backend the write end of a pipe as stdin
  and never writes to it. Read here until EOF, which arrives only when the last write
  end closes: when the shell exits, **however** it exits — a clean quit, a crash,
  SIGKILL. A `getppid()` poll would miss a reparent race, and `PR_SET_PDEATHSIG` is
  Linux-only.
- **Windows: wait on the shell's process handle.** The shell passes its PID as
  `HORRIBLE_PARENT_PID`; the handle is opened at startup, while the shell is certainly
  alive (it just spawned us), and the wait ends the moment that process ends, however
  it ends. Never stdin: a thread blocked reading a *synchronous* pipe handle makes
  every later `subprocess` that inherits stdin hang in `CreateProcess`. The backend
  starts subprocesses while booting, so the packaged app sat "still starting" forever
  (desktop smoke on windows-latest, PR #29). `test_subprocess_still_starts_under_the_watch`
  pins it.

The variables are consumed rather than read, because children of this process (the
interp worker, a terminal's shell) inherit the environment, and some read stdin of
their own: a second watch in one of them would take that input or exit on its EOF.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections.abc import Callable

ENV = "HORRIBLE_EXIT_WITH_PARENT"
#: The shell's PID, for the Windows watch. Ignored on POSIX.
PID_ENV = "HORRIBLE_PARENT_PID"
#: Lifespan shutdown gets this long once asked; the shell would normally give 3 s
#: before SIGKILL, but there is no shell left to do that.
_HARD_EXIT_AFTER_S = 15.0

log = logging.getLogger(__name__)


def _shut_down() -> None:
    log.info("desktop shell is gone: shutting the backend down")
    # The same graceful path as the shell's own SIGTERM: uvicorn's handler, then
    # lifespan shutdown. Windows has no SIGTERM handler there; SIGINT is uvicorn's.
    signal.raise_signal(signal.SIGTERM if os.name != "nt" else signal.SIGINT)
    time.sleep(_HARD_EXIT_AFTER_S)
    log.warning(
        "lifespan shutdown overran %.0fs after the shell left: exiting",
        _HARD_EXIT_AFTER_S,
    )
    os._exit(1)


def _wait_for_stdin_eof() -> None:
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        pass  # A closed or invalid stdin is the parent gone too.
    _shut_down()


def _windows_waiter(pid: int) -> Callable[[], None]:
    """Open the shell's process now, and return the thread body that waits on it.

    Opened here, synchronously, rather than in the thread: the PID only names the
    shell while the shell is alive, and holding the handle keeps that true.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    synchronize, infinite = 0x00100000, 0xFFFFFFFF

    handle = kernel32.OpenProcess(synchronize, False, pid)

    def wait() -> None:
        # No handle means the shell is already gone (or the PID was never valid):
        # either way there is nobody left to stop this backend.
        if handle:
            kernel32.WaitForSingleObject(handle, infinite)
        _shut_down()

    return wait


def start() -> bool:
    """Start the watch if the launcher asked for one. Returns whether it did."""
    asked = os.environ.pop(ENV, None) == "1"
    pid_text = os.environ.pop(PID_ENV, None)
    if not asked:
        return False
    if os.name == "nt":
        try:
            pid = int(pid_text) if pid_text else os.getppid()
        except ValueError:
            log.warning("ignoring %s=%r: not a PID", PID_ENV, pid_text)
            pid = os.getppid()
        target = _windows_waiter(pid)
    else:
        target = _wait_for_stdin_eof
    threading.Thread(target=target, name="parent-watch", daemon=True).start()
    return True
