"""The kernel interpreter for generic notebooks.

Unlike training (one venv per project), the notebook module uses a single managed
uv venv under the data dir. It is populated in two stages, because they have very
different costs:

1. **Kernel packages** (`ipykernel`, `ipywidgets`, `anywidget`) — small, and nothing
   runs without them, so they install **blocking** on first open.
2. **Libraries** (`notebook.python.packages`, the common AI stack by default: torch,
   transformers, datasets, …) — gigabytes, so they install **in the background**
   while the kernel is already usable. The pane polls `GET /notebook/env` and shows
   progress; a cell importing `transformers` mid-install fails with a message the
   banner already explains, rather than a kernel that takes five minutes to start.

What is installed is recorded in a **stamp file** inside the venv. That is what lets an
existing venv pick up a package added to the list later — the venv used to be
bootstrapped once and never revisited, so a new default simply never arrived. uv skips
anything already satisfied, so re-running a list against a venv that has most of it is
seconds, not minutes.

**PyTorch is installed first, from the right index.** PyPI's Windows wheels are CPU-only,
so a Windows machine with an NVIDIA card (per the hardware probe) gets PyTorch's CUDA
index; Linux PyPI wheels already carry CUDA, and macOS wheels carry Metal. Installing
torch before `transformers`/`accelerate` matters: those only require *some* torch, and
if they ran first they would pull the CPU wheel from PyPI and the CUDA one would never
be chosen. `notebook.python.torchIndex` overrides the choice.

A `notebook.python` setting overrides the whole venv with an existing interpreter (which
must have ipykernel + ipywidgets). That interpreter is the user's own, so this module
**never installs into it**; its library status is `unmanaged`.

Every spawn is blocking `subprocess.Popen` pumped on the calling thread — never
`asyncio.create_subprocess_exec`, which breaks on the SelectorEventLoop uvicorn
uses under `--reload` (the LSP manager's Windows-safe pattern).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend import paths
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

ProgressLine = Callable[[str], None]

KERNEL_PACKAGES = ("ipykernel", "ipywidgets", "anywidget")

#: The libraries a new notebook environment gets. A space-separated string because a
#: setting is a scalar; `notebook.python.packages` replaces it wholesale. Keep it in
#: step with the declared default in `packages/core/src/modules/notebook/index.ts`.
DEFAULT_PACKAGES = (
    "numpy pandas scipy matplotlib scikit-learn tqdm "
    "torch transformers datasets accelerate huggingface_hub safetensors sentencepiece"
)

#: Installed from the torch index, before anything else.
TORCH_FAMILY = frozenset({"torch", "torchvision", "torchaudio"})

#: CUDA 12.8 wheels: the oldest line that supports every current NVIDIA generation
#: (RTX 50-series needs 12.8), and the runtime is bundled, so only the driver matters.
PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"

STAMP_NAME = ".horrible-packages.json"

# One managed venv, serialized so a lazy bootstrap can't race a second open.
_bootstrap_lock = threading.Lock()

#: Background library install state, read by `GET /notebook/env`.
_state_lock = threading.Lock()
_state: dict[str, Any] = {"state": "idle", "installing": [], "line": "", "error": ""}


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
    tail: deque[str] = deque(maxlen=4)
    for line in proc.stdout:
        stripped = line.rstrip()
        if stripped:
            tail.append(stripped)
            if progress:
                progress(stripped)
    code = proc.wait()
    if code != 0:
        # The last lines, not the command: "uv exited with 1: pip install…" tells the
        # person nothing, while uv's own last words usually name the package and why.
        detail = " | ".join(tail) or " ".join(cmd[1:3])
        raise RuntimeError(f"{Path(cmd[0]).name} exited with {code}: {detail}")


def _override() -> str:
    return str(get_value("notebook.python", "") or "").strip()


def python_ready() -> bool:
    """True if a usable interpreter is already available (override or bootstrapped)."""
    override = _override()
    if override:
        return Path(override).is_file()
    return _venv_python(managed_venv_dir()).is_file()


def existing_python() -> Path | None:
    """The user-code interpreter if one already exists — never bootstraps.

    The terminal puts this on its shells' PATH so a script run there sees the same
    libraries (torch, …) a notebook cell does, rather than the backend's own venv.
    """
    override = _override()
    py = Path(override) if override else _venv_python(managed_venv_dir())
    return py if py.is_file() else None


def ensure_python(progress: ProgressLine | None = None) -> str:
    """Resolve the kernel interpreter, bootstrapping the managed venv on first use.
    Blocking — call from a daemon thread / `asyncio.to_thread`, never the loop.

    Installs only the kernel packages; libraries go through `start_library_install`.
    """
    override = _override()
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
            [_uv(), "pip", "install", "--python", str(py), *KERNEL_PACKAGES],
            progress,
        )
    return str(py)


# --- libraries -----------------------------------------------------------------


def requested_packages() -> list[str]:
    """The configured library specs, in order, de-duplicated."""
    raw = str(get_value("notebook.python.packages", DEFAULT_PACKAGES) or "")
    specs: list[str] = []
    for token in re.split(r"[\s,]+", raw):
        if token and token not in specs:
            specs.append(token)
    return specs


def package_name(spec: str) -> str:
    """`torch>=2.4` → `torch`; `huggingface_hub[cli]` → `huggingface-hub`."""
    return (
        re.split(r"[<>=!~\[;@ ]", spec, maxsplit=1)[0].strip().lower().replace("_", "-")
    )


def auto_torch_index(os_name: str, accelerator_kind: str | None) -> str | None:
    """Which index PyTorch should come from, from what the hardware probe found.

    Only Windows + NVIDIA needs a different index: PyPI's Windows torch wheels are
    CPU-only, while Linux PyPI wheels already bundle CUDA and macOS wheels use Metal.
    """
    # The hardware probe names an NVIDIA card by its API, `cuda` (see `probe.KINDS`).
    if accelerator_kind == "cuda" and os_name.lower().startswith("win"):
        return PYTORCH_CUDA_INDEX
    return None


def _profile() -> Any:
    try:
        from backend.modules.hardware.probe import get_profile

        return get_profile()
    except Exception:  # noqa: BLE001 — "could not ask" falls back to PyPI
        logger.debug("hardware probe unavailable for torch index choice", exc_info=True)
        return None


def torch_index() -> str | None:
    """The torch index to use: the setting when set, else chosen from the hardware.

    `notebook.python.torchIndex`: blank = automatic, `pypi` = plain PyPI, otherwise a
    full index URL (e.g. `https://download.pytorch.org/whl/cpu` to force CPU wheels
    on Linux and skip the multi-gigabyte CUDA download).
    """
    configured = str(get_value("notebook.python.torchIndex", "") or "").strip()
    if configured.lower() == "pypi":
        return None
    if configured:
        return configured
    profile = _profile()
    if profile is None:
        return None
    primary = getattr(profile, "primary", None)
    return auto_torch_index(
        str(getattr(profile, "os", "")), getattr(primary, "kind", None)
    )


def _stamp_path() -> Path:
    return managed_venv_dir() / STAMP_NAME


def _read_stamp() -> dict[str, Any]:
    try:
        data = json.loads(_stamp_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"installed": [], "torch_index": None}
    return {
        "installed": list(data.get("installed") or []),
        "torch_index": data.get("torch_index"),
    }


def _write_stamp(stamp: dict[str, Any]) -> None:
    path = _stamp_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    tmp.replace(path)


def missing_packages() -> list[str]:
    """Configured specs not yet installed into the managed venv.

    A torch-family spec also counts as missing when the index it came from is not
    the index that would be chosen now — a machine that gained a GPU, or a changed
    `torchIndex` setting, should get the other wheel rather than keep the old one.
    """
    stamp = _read_stamp()
    installed = set(stamp["installed"])
    wanted_index = None
    missing: list[str] = []
    for spec in requested_packages():
        if package_name(spec) in TORCH_FAMILY:
            if wanted_index is None:
                wanted_index = torch_index() or ""
            if spec not in installed or (stamp["torch_index"] or "") != wanted_index:
                missing.append(spec)
        elif spec not in installed:
            missing.append(spec)
    return missing


def install_packages(
    python: str, specs: list[str], progress: ProgressLine | None
) -> None:
    """Install `specs` into the managed venv: torch family first, then the rest.

    Stamps after each stage, so a failure in the second leaves torch recorded and a
    retry does not download it again.
    """
    torch_specs = [s for s in specs if package_name(s) in TORCH_FAMILY]
    rest = [s for s in specs if s not in torch_specs]
    stamp = _read_stamp()

    if torch_specs:
        index = torch_index()
        cmd = [_uv(), "pip", "install", "--python", python, *torch_specs]
        if index:
            # `--index-url` replaces PyPI for this command. That is PyTorch's own
            # recipe: its index mirrors torch's dependencies too.
            cmd += ["--index-url", index]
        _run(cmd, progress)
        stamp["installed"] = sorted(set(stamp["installed"]) | set(torch_specs))
        stamp["torch_index"] = index or ""
        _write_stamp(stamp)

    if rest:
        _run([_uv(), "pip", "install", "--python", python, *rest], progress)
        stamp["installed"] = sorted(set(stamp["installed"]) | set(rest))
        _write_stamp(stamp)


def library_status() -> dict[str, Any]:
    """What `GET /notebook/env` reports about the libraries."""
    if _override():
        return {
            "state": "unmanaged",
            "missing": [],
            "installing": [],
            "line": "",
            "error": "",
        }
    with _state_lock:
        current = dict(_state)
    if current["state"] == "installing":
        return {**current, "missing": list(current["installing"])}
    missing = missing_packages()
    index = ""
    if any(package_name(s) in TORCH_FAMILY for s in missing):
        index = torch_index() or ""
    if current["state"] == "failed" and missing:
        return {**current, "missing": missing, "installing": [], "torch_index": index}
    return {
        "state": "idle" if missing else "ready",
        "missing": missing,
        "installing": [],
        "line": "",
        "error": "",
        "torch_index": index,
    }


def _install_worker() -> None:
    def progress(line: str) -> None:
        with _state_lock:
            _state["line"] = line[-240:]

    try:
        python = ensure_python(progress)
        missing = missing_packages()
        with _state_lock:
            _state["installing"] = missing
        if missing:
            logger.info("notebook: installing libraries %s", " ".join(missing))
            install_packages(python, missing, progress)
        with _state_lock:
            _state.update(state="ready", installing=[], line="", error="")
    except Exception as exc:  # noqa: BLE001 — reported to the pane, retryable
        logger.warning("notebook: library install failed: %s", exc)
        with _state_lock:
            _state.update(state="failed", installing=[], error=str(exc))


def start_library_install() -> bool:
    """Install any missing libraries on a background thread. Returns whether it started.

    Never for a `notebook.python` override (the user's own interpreter), and never twice
    at once. Cheap to call on every kernel open: with nothing missing the worker finds
    that out and finishes without spawning uv.
    """
    if _override():
        return False
    with _state_lock:
        if _state["state"] == "installing":
            return False
        _state.update(state="installing", installing=[], line="", error="")
    threading.Thread(
        target=_install_worker, name="notebook-libraries", daemon=True
    ).start()
    return True
