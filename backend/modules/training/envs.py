"""Per-project uv venvs.

Each training project owns `.venv/` inside its directory, created and populated
with `uv` so torch-sized dependencies never touch the backend env. Every spawn is
blocking `subprocess.Popen` pumped on a daemon thread (the LSP manager's Windows-
safe pattern — `asyncio.create_subprocess_exec` breaks on the SelectorEventLoop
uvicorn uses under `--reload`); stdout/stderr lines stream to a progress callback
that the routes fan out over `/ws` as `env_progress` events.

The bootstrap installs `ipykernel` (kernel side of the Jupyter protocol; the
manager side, jupyter_client, lives only in the backend env) and the local
`horrible-train` helper package (metrics/frames/graph emission).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from backend.modules.training.models import ProjectModel
from backend.modules.training.providers.base import ProviderError

ProgressLine = Callable[[str], None]

HELPER_DIR = Path(__file__).resolve().parent / "helper"

# Serialize venv mutations per project so a dep install can't race the bootstrap.
_project_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(project_id: str) -> threading.Lock:
    with _locks_guard:
        return _project_locks.setdefault(project_id, threading.Lock())


def venv_dir(project: ProjectModel) -> Path:
    return Path(project.root) / ".venv"


def python_path(project: ProjectModel) -> Path:
    if sys.platform == "win32":
        return venv_dir(project) / "Scripts" / "python.exe"
    return venv_dir(project) / "bin" / "python"


def venv_ready(project: ProjectModel) -> bool:
    return python_path(project).is_file()


def _uv() -> str:
    exe = shutil.which("uv")
    if exe is None:
        raise ProviderError("uv not found on PATH — install uv to manage venvs")
    return exe


def _run(cmd: list[str], cwd: str, progress: ProgressLine) -> None:
    """Run a uv command to completion, streaming merged output lines. Blocking —
    call from a daemon thread or via asyncio.to_thread, never the event loop."""
    progress("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        stripped = line.rstrip()
        if stripped:
            progress(stripped)
    code = proc.wait()
    if code != 0:
        raise ProviderError(f"{cmd[0]} exited with {code}: {' '.join(cmd[1:3])}…")


def create(project: ProjectModel, progress: ProgressLine) -> None:
    """`uv venv .venv --python <ver>` in the project root (idempotent)."""
    with _lock_for(project.id):
        if venv_ready(project):
            progress(".venv already exists")
            return
        _run(
            [_uv(), "venv", ".venv", "--python", project.python],
            project.root,
            progress,
        )


def install(project: ProjectModel, packages: list[str], progress: ProgressLine) -> None:
    """`uv pip install` into the project venv."""
    if not packages:
        return
    with _lock_for(project.id):
        if not venv_ready(project):
            raise ProviderError("project venv missing — create it first")
        _run(
            [_uv(), "pip", "install", "--python", str(python_path(project)), *packages],
            project.root,
            progress,
        )


def bootstrap(
    project: ProjectModel, requirements: list[str], progress: ProgressLine
) -> None:
    """Create the venv and install the kernel + helper + provider requirements."""
    create(project, progress)
    install(project, ["ipykernel", str(HELPER_DIR), *requirements], progress)
