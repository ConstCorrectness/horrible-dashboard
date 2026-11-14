"""Stop this backend when the desktop shell that started it is gone.

The Electron shell spawns uvicorn in a process group of its own, so that one signal
reaches whatever `uv run` started under it — and so the backend outlives the shell
whenever the shell dies without running `will-quit`. Found on Linux: stopping
`pnpm dev:desktop` signalled Electron's whole process group, its GPU process and
zygote died in the same instant, Chromium aborted ("GPU process isn't usable.
Goodbye.") before it could stop the backend, and uvicorn kept :8000 — serving, and
holding the node's relay identity against the next launch, for hours.

So the shell hands the backend the write end of a pipe as stdin, and sets
`HORRIBLE_EXIT_WITH_PARENT=1`. It never writes to it. Read here until EOF, which
arrives only when the last write end closes: when the shell exits, **however** it
exits — a clean quit, a crash, SIGKILL. A `getppid()` poll would only cover POSIX,
and `PR_SET_PDEATHSIG` only Linux.

The variable is consumed rather than read, because children of this process (the
interp worker, a terminal's shell) inherit the environment, and some read stdin of
their own: a second watch in one of them would take that input or exit on its EOF.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

ENV = "HORRIBLE_EXIT_WITH_PARENT"
#: Lifespan shutdown gets this long once asked; the shell would normally give 3 s
#: before SIGKILL, but there is no shell left to do that.
_HARD_EXIT_AFTER_S = 15.0

log = logging.getLogger(__name__)


def _wait_for_parent_exit() -> None:
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        pass  # A closed or invalid stdin is the parent gone too.
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


def start() -> bool:
    """Start the watch if the launcher asked for one. Returns whether it did."""
    if os.environ.pop(ENV, None) != "1":
        return False
    threading.Thread(
        target=_wait_for_parent_exit, name="parent-watch", daemon=True
    ).start()
    return True
