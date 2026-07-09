""".ipynb IO for training projects — a thin project-aware layer over the shared
`notebook_core.notebooks` engine.

The neutral load/save/op/model machinery lives in the core; this module only adds
the project-rooted path resolution and the training-specific scaffold metadata
(`metadata.horrible.projectId`), keeping the public API training callers use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.modules.training.models import ProjectModel
from backend.notebook_core import notebooks as _core

# Re-export the neutral engine so `training.notebooks.<fn>` keeps working.
NBFORMAT_MINOR = _core.NBFORMAT_MINOR
apply_op = _core.apply_op
from_model = _core.from_model
load = _core.load
save = _core.save
to_model = _core.to_model


def notebook_path(project: ProjectModel, rel_path: str) -> Path:
    """Resolve a notebook path inside the project root, refusing escapes."""
    root = Path(project.root).resolve()
    resolved = (root / rel_path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"notebook path escapes project root: {rel_path}")
    return resolved


def new_notebook(
    project: ProjectModel, rel_path: str, cells: list[dict[str, Any]]
) -> None:
    """Create a fresh notebook from raw nbformat cell dicts (provider scaffolds)."""
    _core.new_notebook(
        notebook_path(project, rel_path),
        cells,
        metadata={"horrible": {"projectId": project.id}},
    )
