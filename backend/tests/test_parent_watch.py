"""`backend/parent_watch.py`: the backend leaves when the desktop shell does.

Real processes rather than mocks: the property is "closing the write end of stdin ends
this process", and only an actual pipe and an actual signal exercise it.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend import parent_watch

REPO = Path(__file__).resolve().parents[2]

# Stands in for uvicorn: starts the watch, reports whether it did and whether the
# variable is still there for its own children to inherit, then idles.
CHILD = """
import os, sys, time
from backend import parent_watch
started = parent_watch.start()
print(started, parent_watch.ENV in os.environ, flush=True)
time.sleep(60)
"""


def _spawn(
    env_value: str | None, *, code: str = CHILD, parent_pid: int | None = None
) -> subprocess.Popen[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in (parent_watch.ENV, parent_watch.PID_ENV)
    }
    if env_value is not None:
        env[parent_watch.ENV] = env_value
    # The shell passes its own PID; the Windows watch waits on it.
    env[parent_watch.PID_ENV] = str(parent_pid if parent_pid else os.getpid())
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM is not how Windows ends it")
def test_exits_when_the_parent_closes_its_pipe() -> None:
    child = _spawn("1")
    try:
        # Started, and consumed: a grandchild must not start a second watch.
        assert child.stdout is not None
        assert child.stdout.readline().split() == ["True", "False"]
        child.stdin.close()  # type: ignore[union-attr]
        # No handler installed here, so SIGTERM's default action ends it.
        assert child.wait(timeout=10) == -15
    finally:
        child.kill()


def test_does_nothing_unless_asked() -> None:
    child = _spawn(None)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().split() == ["False", "False"]
        child.stdin.close()  # type: ignore[union-attr]
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=1.5)
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(os.name != "nt", reason="the Windows watch waits on a process")
def test_exits_when_the_parent_process_ends_on_windows() -> None:
    """A stand-in shell, killed outright: the watch must notice with no pipe."""
    shell = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child = _spawn("1", parent_pid=shell.pid)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().split() == ["True", "False"]
        shell.kill()
        # No handler installed here: SIGINT's default raises KeyboardInterrupt.
        assert child.wait(timeout=10) != 0
    finally:
        shell.kill()
        child.kill()


# Starts the watch, then starts a subprocess the ordinary way (inheriting stdin), as
# the backend does while booting, and reports how long that took.
SPAWNS = """
import subprocess, sys, time
from backend import parent_watch
parent_watch.start()
time.sleep(0.3)  # let a stdin read, if there is one, block first
t = time.time()
try:
    subprocess.run([sys.executable, "-c", "pass"], timeout=8)
    print(f"{time.time() - t:.2f}", flush=True)
except subprocess.TimeoutExpired:
    print("hung", flush=True)
"""


def test_subprocess_still_starts_under_the_watch() -> None:
    """On Windows a thread blocked reading a synchronous stdin pipe made every
    subprocess that inherits stdin hang in CreateProcess, so the packaged backend
    never finished booting (desktop smoke, windows-latest). The watch must leave
    subprocess creation alone on every OS."""
    child = _spawn("1", code=SPAWNS)
    try:
        assert child.stdout is not None
        out = child.stdout.readline().strip()
        assert out != "hung"
        assert float(out) < 5
    finally:
        child.kill()
        child.wait()
