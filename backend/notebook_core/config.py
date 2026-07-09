"""The parametrization seam: a `KernelSession` is built from a `SessionConfig`
instead of any module's domain model. `training` fills it from a `ProjectModel`
(+ its venv python); `notebook` fills it from a file path (+ a managed venv)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionConfig:
    """Everything the neutral kernel engine needs to run one notebook."""

    key: (
        str  # opaque session key, e.g. "{scope}:{path}" — matches the frontend store id
    )
    python_executable: str  # interpreter to spawn ipykernel from (a venv python)
    cwd: str  # kernel working directory
    notebook_abs_path: Path  # resolved .ipynb on disk (authoritative doc)
    rel_path: (
        str  # display path (relative to the module's root), used in NotebookModel.path
    )
    channel: str = "notebook"  # /ws channel the session fans events to
    display_name: str = "notebook"  # kernelspec display name
    default_mode: str = "classic"  # execution mode when the .ipynb has no flag
