"""Cross-platform pseudo-terminal spawning.

The PTY always runs where the backend runs (browser and desktop are byte-for-byte
identical). We unify on two libraries that expose the same `PtyProcess.spawn` API:
`ptyprocess` on POSIX, `pywinpty` on Windows (ConPTY). Both yield an object with
`read`/`write`/`setwinsize`/`isalive`/`terminate`, captured by `PtyProcess` below.
See docs/modules/terminal.md.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class PtyProcess(Protocol):
    """The slice of the ptyprocess/pywinpty API the terminal manager uses. Both
    libraries' `PtyProcess` satisfy this; `manager.py` also accepts a fake."""

    def read(self, size: int = 1024) -> str: ...
    def write(self, data: str) -> int: ...
    def setwinsize(self, rows: int, cols: int) -> None: ...
    def isalive(self) -> bool: ...
    def terminate(self, force: bool = False) -> None: ...


def default_shell() -> str:
    """The backend host's default interactive shell."""
    if sys.platform == "win32":
        return "powershell.exe"
    return os.environ.get("SHELL", "/bin/bash")


def spawn_pty(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    rows: int = 24,
    cols: int = 80,
) -> PtyProcess:
    """Spawn a shell attached to a real PTY. Imported lazily so the platform lib is
    only required at runtime (tests use a fake backend)."""
    if sys.platform == "win32":
        from winpty import PtyProcess as _PtyProcess  # type: ignore[import-untyped]
    else:
        from ptyprocess import PtyProcessUnicode as _PtyProcess  # type: ignore[import-untyped]

    return _PtyProcess.spawn(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        dimensions=(rows, cols),
    )
