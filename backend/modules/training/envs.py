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

import re
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


#: Installed into every project venv beside the kernel. `ipywidgets` is here
#: because the libraries a training notebook runs *look* for it: tqdm and the
#: Hugging Face `Trainer` draw a widget progress bar when it is importable and
#: fall back to printing a frame per update when it is not — which is both a
#: worse bar and the thing that fills a notebook with carriage returns. It also
#: prints a warning telling the user to update Jupyter, which is not the problem.
KERNEL_PACKAGES = ("ipykernel", "ipywidgets")


def bootstrap(
    project: ProjectModel, requirements: list[str], progress: ProgressLine
) -> None:
    """Create the venv and install the kernel + helper + provider requirements."""
    create(project, progress)
    install(project, [*KERNEL_PACKAGES, str(HELPER_DIR), *requirements], progress)


def torch_index_url(profile: Any, os_name: str = sys.platform) -> tuple[str, str]:
    """(index URL, why) for `torch` on this machine — or ("", why) for the default.

    The step that was missing, and the reason the recipe form has spent its whole
    life reporting "trl and peft are not installed": `bootstrap` installs
    `ipykernel` and the helper and nothing else, so there has never been a moment
    where the ML stack arrived in a project venv.

    Getting `torch` itself right needs the **card and the OS together**, the same
    rule `hardware._variant_for` follows for llama.cpp builds:

    - **CUDA on Windows**: PyPI's Windows `torch` wheels are **CPU-only**, so the
      CUDA index is required. This used to claim the default wheel "already
      targets" the card, which installed `+cpu` on an RTX machine and surfaced
      three cells later as trl's "Your setup doesn't support bf16/gpu". The index
      is the notebook module's (`notebook.env.PYTORCH_CUDA_INDEX`), so the two
      venvs a user trains in cannot disagree about it.
    - **CUDA on Linux**: PyPI's default wheel already bundles CUDA. No override.
    - **ROCm**: needs an explicit index; the default wheel has no ROCm support at
      all and fails at `torch.cuda.is_available()` with no useful message.
    - **Metal**: the default macOS wheel carries MPS. Nothing to override.
    - **No accelerator**: the CPU index, which is a much smaller download — but
      only when the probe is *certain*. If it could not ask, a GPU-capable wheel is
      installed instead, because a CPU build on a machine with a card would train
      slowly with nothing saying why. On Windows "GPU-capable" means the CUDA
      index again: PyPI's default there is the CPU build this rule is avoiding.
    """
    from backend.modules.notebook.env import PYTORCH_CUDA_INDEX

    windows = os_name.lower().startswith("win")
    if profile is None or not getattr(profile, "certain", True):
        why = (
            "no hardware profile"
            if profile is None
            else "the accelerator probe could not run"
        )
        if windows:
            return PYTORCH_CUDA_INDEX, (
                f"{why}, so the CUDA wheel is installed rather than PyPI's "
                "CPU-only Windows build — it also runs on a machine with no card"
            )
        return "", (
            f"{why}, so the default (GPU-capable) wheel is installed rather than "
            "the CPU one — a CPU build on a machine with a card would train slowly "
            "with nothing saying why"
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
    if primary.kind == "cuda" and windows:
        return PYTORCH_CUDA_INDEX, (
            f"{primary.name} detected via {primary.detected_by}; PyPI's Windows "
            "torch wheels are CPU-only, so the CUDA build is installed"
        )
    return "", (
        f"{primary.name} detected via {primary.detected_by}; the default wheel "
        "already targets it"
    )


def installed_torch_version(project: ProjectModel) -> str:
    """The venv's torch build as torch reports it (`2.14.0+cpu`), or "".

    Read off disk rather than by importing torch (an import is seconds), and from
    `torch/version.py` specifically: the wheel's `METADATA` and dist-info name
    both say plain `2.14.0` for the CPU build, so the local label that decides
    everything here exists only in the file torch itself reads `__version__` from.
    """
    for version_py in venv_dir(project).glob("**/site-packages/torch/version.py"):
        try:
            text = version_py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
        if found:
            return found.group(1)
    return ""


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

    A **CPU build already in the venv is replaced** when the index says otherwise.
    `uv pip install torch` treats any installed torch as satisfying the request, so
    without `--reinstall-package` a venv that got the wrong wheel once keeps it
    forever, and re-running the install reports success while changing nothing.

    Returns the sentence explaining the torch choice, so the pane can say why a
    CPU build landed on a machine whose owner knows they have a card.
    """
    index_url, reason = torch_index_url(profile)
    torch_cmd = ["torch"]
    if index_url:
        torch_cmd = ["torch", "--index-url", index_url]
        wants_cpu = index_url.rstrip("/").endswith("/cpu")
        if installed_torch_version(project).endswith("+cpu") and not wants_cpu:
            torch_cmd.append("--reinstall-package=torch")
            reason += " (replacing the CPU build that was installed before)"
    progress(f"resolving torch: {reason}")
    install(project, torch_cmd, progress)
    # The kernel packages go in here too, not only in `bootstrap`: a venv is
    # bootstrapped once and never revisited, so a project created before
    # `ipywidgets` joined the list would never get it. `uv pip install` on an
    # already-satisfied package is a no-op that costs milliseconds.
    install(
        project,
        [*KERNEL_PACKAGES, *[p for p in packages if p != "torch"]],
        progress,
    )
    return reason
