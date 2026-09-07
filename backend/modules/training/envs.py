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
from typing import Any

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


def venv_exists(project: ProjectModel) -> bool:
    """True if the python executable physically exists in the project venv on disk."""
    return python_path(project).is_file()


def venv_ready(project: ProjectModel) -> bool:
    """True if the project venv bootstrap has completed and the python executable exists."""
    return project.venv_ready and venv_exists(project)


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
        if venv_exists(project):
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
        if not venv_exists(project):
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


def torch_index_url(profile: Any) -> tuple[str, str]:
    """(index URL, why) for `torch` on this machine — or ("", why) for the default.

    The step that was missing, and the reason the recipe form has spent its whole
    life reporting "trl and peft are not installed": `bootstrap` installs
    `ipykernel` and the helper and nothing else, so there has never been a moment
    where the ML stack arrived in a project venv.

    Getting `torch` itself right needs the **card and the OS together**, the same
    rule `hardware._variant_for` follows for llama.cpp builds:

    - **CUDA**: PyPI's default `torch` wheel is already a CUDA build on Windows and
      Linux, so no index override — pointing at a cu12x index would pin a version
      that may not exist for the current torch release.
    - **ROCm**: needs an explicit index; the default wheel has no ROCm support at
      all and fails at `torch.cuda.is_available()` with no useful message.
    - **Metal**: the default macOS wheel carries MPS. Nothing to override.
    - **No accelerator, or we could not ask**: the CPU index, which is a much
      smaller download — but only when the probe is *certain*. If it could not
      ask, installing the CPU build would silently make a machine with a card
      train at CPU speed, which is the failure mode this module exists to avoid.
    """
    if profile is None:
        return "", "no hardware profile; using the default wheel"
    if not getattr(profile, "certain", True):
        return "", (
            "the accelerator probe could not run, so the default (GPU-capable) "
            "wheel is installed rather than the CPU one — a CPU build on a machine "
            "with a card would train slowly with nothing saying why"
        )
    primary = getattr(profile, "primary", None)
    if primary is None:
        return "https://download.pytorch.org/whl/cpu", (
            "no accelerator was found, so the smaller CPU-only wheel is installed"
        )
    if primary.kind == "rocm":
        return "https://download.pytorch.org/whl/rocm6.2", (
            f"{primary.name} detected via {primary.detected_by}; the default wheel "
            "has no ROCm support"
        )
    return "", (
        f"{primary.name} detected via {primary.detected_by}; the default wheel "
        "already targets it"
    )


def install_stack(
    project: ProjectModel,
    packages: list[str],
    profile: Any,
    progress: ProgressLine,
) -> str:
    """Install a recipe backend's requirements, with torch resolved for this box.

    Two `uv pip install` calls rather than one: torch may need its own index URL,
    and passing `--index-url` to a combined install would send *every* package
    through PyTorch's index, where most of them do not exist.

    Returns the sentence explaining the torch choice, so the pane can say why a
    CPU build landed on a machine whose owner knows they have a card.
    """
    index_url, reason = torch_index_url(profile)
    torch_cmd = ["torch"]
    if index_url:
        torch_cmd = ["torch", "--index-url", index_url]
    progress(f"resolving torch: {reason}")
    install(project, torch_cmd, progress)
    install(project, [p for p in packages if p != "torch"], progress)
    return reason
