"""Push-target contract (same driver style as environment providers)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.modules.training.models import ProjectModel, PushResultModel

ProgressLine = Callable[[str], None]


class PushError(Exception):
    """Raised for auth/upload failures and missing dependencies."""


@runtime_checkable
class PushTarget(Protocol):
    target: str  # registry id, e.g. "kaggle"
    label: str  # UI label

    def push(
        self, project: ProjectModel, notebook: Path, progress: ProgressLine
    ) -> PushResultModel:
        """Upload the notebook; returns the destination URL/status. Blocking —
        callers offload to a thread."""
        ...

    def status(self, project: ProjectModel) -> PushResultModel:
        """Current state of the last push (e.g. Kaggle kernel run status)."""
        ...
