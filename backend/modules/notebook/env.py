"""The kernel interpreter for generic notebooks.

Unlike training (one venv per project), the notebook module uses a single managed
uv venv under the data dir, populated with `ipykernel` (kernel side of the Jupyter
protocol), `ipywidgets` (interactive widgets), and `anywidget` (custom `_esm`
front-ends). A `notebook.python` setting can override it with an existing
interpreter (which must have ipykernel + ipywidgets).

Every spawn is blocking `subprocess.Popen` pumped on the calling thread — never
`asyncio.create_subprocess_exec`, which breaks on the SelectorEventLoop uvicorn
uses under `--reload` (the LSP manager's Windows-safe pattern).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from backend.modules.settings.routes import get_value
from backend import paths

ProgressLine = Callable[[str], None]

# One managed venv, serialized so a lazy bootstrap can't race a second open.
_bootstrap_lock = threading.Lock()


def _data_dir() -> Path:
    return paths.data_dir()


def managed_venv_dir() -> Path:
    return _data_dir() / "notebook-venv"


def _venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _uv() -> str:
    exe = shutil.which("uv")
    if exe is None:
        raise RuntimeError(
            "uv not found on PATH — install uv to manage the notebook venv"
        )
    return exe


def _run(cmd: list[str], progress: ProgressLine | None) -> None:
    """Run a uv command to completion, streaming merged output lines. Blocking."""
    if progress:
        progress("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        stripped = line.rstrip()
        if stripped and progress:
            progress(stripped)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{cmd[0]} exited with {code}: {' '.join(cmd[1:3])}…")


def python_ready() -> bool:
    """True if a usable interpreter is already available (override or bootstrapped)."""
    override = str(get_value("notebook.python", "")).strip()
    if override:
        return Path(override).is_file()
    return _venv_python(managed_venv_dir()).is_file()


def ensure_python(progress: ProgressLine | None = None) -> str:
    """Resolve the kernel interpreter, bootstrapping the managed venv on first use.
    Blocking — call from a daemon thread / `asyncio.to_thread`, never the loop."""
    override = str(get_value("notebook.python", "")).strip()
    if override:
        if not Path(override).is_file():
            raise RuntimeError(f"notebook.python does not exist: {override}")
        return override

    venv = managed_venv_dir()
    py = _venv_python(venv)
    if py.is_file():
        return str(py)

    with _bootstrap_lock:
        if py.is_file():  # another thread won the race
            return str(py)
        python_ver = str(get_value("notebook.python.version", "3.12"))
        _run([_uv(), "venv", str(venv), "--python", python_ver], progress)
        _run(
            [
                _uv(),
                "pip",
                "install",
                "--python",
                str(py),
                "ipykernel",
                "ipywidgets",
                "anywidget",  # custom front-ends (_esm/_css) — see AnyWidgetView
            ],
            progress,
        )
    return str(py)
