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


def _spawn(env_value: str | None) -> subprocess.Popen[str]:
    env = {k: v for k, v in os.environ.items() if k != parent_watch.ENV}
    if env_value is not None:
        env[parent_watch.ENV] = env_value
    return subprocess.Popen(
        [sys.executable, "-c", CHILD],
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
