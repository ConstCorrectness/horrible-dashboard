"""The interpretability worker's Python environment.

The backend has no torch and should not grow one (docs/modules/interpretability.mdx).
What needs autograd or a Hugging Face model — attribution graphs, fitting a Jacobian
lens, SAEs on bf16 activations, steering with hooks — runs in a worker process with
its own managed uv venv, `$DATA/interp-venv`.

Separate from the notebook's venv on purpose: interpretability libraries pin
transformers and torch versions of their own (circuit-tracer, sae-lens, nnsight), and
a research dependency must not be able to break the notebooks.

**Installed in groups**, because the libraries are large and each phase of the suite
needs a different one. `base` (torch + transformers) is what the worker itself needs;
the rest are added by the tool that uses them, so a node that only ever reads SAE
features never downloads circuit-tracer.

**torch matches the notebook venv's when that one exists.** Both come from the same
CUDA index, so asking for the same version lets uv link the wheel it already has
cached instead of downloading another ~2.5 GB of the same thing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from backend import paths
from backend.modules.notebook import env as notebook_env

#: Package groups. `base` is what the worker needs to load and run a model.
GROUPS: dict[str, tuple[str, ...]] = {
    "base": (
        "torch",
        "transformers",
        "accelerate",
        "safetensors",
        "huggingface_hub",
        "numpy",
    ),
    # Added by the phases that need them (features, circuits, lens fitting).
    "sae": ("sae-lens",),
    "circuits": ("circuit-tracer", "nnsight"),
}

STAMP_NAME = ".horrible-interp.json"
PYTHON_VERSION = "3.12"

_lock = threading.Lock()
_state: dict[str, Any] = {"state": "idle", "group": "", "line": "", "error": ""}


def venv_dir() -> Path:
    return paths.data_dir() / "interp-venv"


def python_path() -> Path:
    venv = venv_dir()
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _stamp() -> dict[str, Any]:
    try:
        data = json.loads((venv_dir() / STAMP_NAME).read_text(encoding="utf-8"))
        return {"groups": list(data.get("groups") or []), "torch": data.get("torch")}
    except (OSError, ValueError):
        return {"groups": [], "torch": None}


def _write_stamp(stamp: dict[str, Any]) -> None:
    path = venv_dir() / STAMP_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
    tmp.replace(path)


def installed_groups() -> list[str]:
    return _stamp()["groups"] if python_path().is_file() else []


def notebook_torch_version() -> str | None:
    """The torch the notebook venv runs, without its local tag (`2.11.0`), or None.

    Read by running its interpreter, not by parsing files: the version a wheel
    *reports* is the one uv will match in its cache.
    """
    py = notebook_env.existing_python()
    if py is None:
        return None
    try:
        out = subprocess.run(
            [str(py), "-c", "import torch; print(torch.__version__)"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    version = out.stdout.strip().split("+")[0]
    return version or None


def _torch_spec() -> str:
    version = notebook_torch_version()
    return f"torch=={version}" if version else "torch"


def install_group(
    group: str, progress: notebook_env.ProgressLine | None = None
) -> None:
    """Create the venv if needed and install one group. Blocking; uv skips anything
    already satisfied, so re-running is cheap."""
    if group not in GROUPS:
        raise ValueError(f"unknown package group {group!r}")
    venv = venv_dir()
    py = python_path()
    uv = notebook_env._uv()
    if not py.is_file():
        notebook_env._run([uv, "venv", str(venv), "--python", PYTHON_VERSION], progress)

    stamp = _stamp()
    specs = list(GROUPS[group])
    if group == "base":
        # torch first and from the CUDA index (the notebook module's reasoning:
        # transformers only needs *some* torch and would otherwise pull the CPU one).
        torch_spec = _torch_spec()
        cmd = [uv, "pip", "install", "--python", str(py), torch_spec]
        index = notebook_env.torch_index()
        if index:
            cmd += ["--index-url", index]
        notebook_env._run(cmd, progress)
        stamp["torch"] = torch_spec
        specs = [s for s in specs if s != "torch"]
    notebook_env._run([uv, "pip", "install", "--python", str(py), *specs], progress)
    stamp["groups"] = sorted(set(stamp["groups"]) | {group})
    _write_stamp(stamp)


def status() -> dict[str, Any]:
    with _lock:
        current = dict(_state)
    return {
        **current,
        "path": str(venv_dir()),
        "exists": python_path().is_file(),
        "groups": installed_groups(),
        "available": sorted(GROUPS),
    }


def start_install(group: str) -> bool:
    """Install `group` on a background thread; False if an install is running."""
    if group not in GROUPS:
        raise ValueError(f"unknown package group {group!r}")
    with _lock:
        if _state["state"] == "installing":
            return False
        _state.update(state="installing", group=group, line="", error="")

    def run() -> None:
        def progress(line: str) -> None:
            with _lock:
                _state["line"] = line[-240:]

        try:
            install_group(group, progress)
            with _lock:
                _state.update(state="ready", line="", error="")
        except Exception as exc:  # noqa: BLE001 — reported to the pane, retryable
            with _lock:
                _state.update(state="failed", error=str(exc))

    threading.Thread(target=run, name=f"interp-env-{group}", daemon=True).start()
    return True
