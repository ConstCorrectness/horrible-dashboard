"""Abstract Base Class for LocalTrack loggers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseLocalTrackLogger(ABC):
    """Abstract base class defining the experiment tracking logging interface.

    Extensible across Hugging Face Trainer, PyTorch Lightning, pure PyTorch,
    and custom training scripts.
    """

    @abstractmethod
    def init_run(
        self,
        project_name: str,
        run_name: str | None = None,
        config: dict[str, Any] | None = None,
        system_info: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Initialize a new experiment run and return the run_id."""
        raise NotImplementedError

    @abstractmethod
    def log_metrics(
        self,
        metrics: dict[str, float | int],
        step: int,
        epoch: float | None = None,
    ) -> None:
        """Log a batch of metrics for a given training step."""
        raise NotImplementedError

    @abstractmethod
    def log_artifact(
        self,
        file_path: str,
        artifact_name: str | None = None,
    ) -> None:
        """Upload and associate an artifact file with the active run."""
        raise NotImplementedError

    @abstractmethod
    def finish_run(self, status: str = "finished") -> None:
        """Mark the active run as finished/failed and flush all pending metric buffers."""
        raise NotImplementedError
