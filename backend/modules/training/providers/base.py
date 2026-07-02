"""Environment-provider contract: the training module's pluggable driver layer.

A provider is a thin, synchronous adapter around one environment source (Kaggle,
HuggingFace, Gymnasium, …), mirroring the database module's Driver protocol. Routes
call the short methods (`search`/`resolve`) via ``asyncio.to_thread``; the long one
(`fetch`) runs on a daemon thread with a progress callback that streams to `/ws`.
Providers lazy-import their client library inside methods so a missing optional
dependency fails with a clear message instead of breaking boot.

New environment types plug in two ways: a file in this package (built-in) or a
backend plugin calling ``host.add_training_provider(provider)`` (see backend.sdk).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.modules.training.models import EnvironmentRefModel

# progress(message, fraction_or_None) — fraction is 0..1 when known.
ProgressFn = Callable[[str, float | None], None]


class ProviderError(Exception):
    """Raised by providers for auth/network failures and missing dependencies."""


@dataclass
class FetchResult:
    files: list[str] = field(default_factory=list)
    bytes: int = 0
    note: str = ""


@dataclass
class ScaffoldResult:
    """Starter material for a fresh project: notebook cells (raw nbformat dicts)
    plus pip requirement specs to install into the project venv."""

    cells: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)


@runtime_checkable
class EnvironmentProvider(Protocol):
    """Uniform contract every environment provider implements."""

    provider: str  # registry id, e.g. "kaggle"
    label: str  # UI label, e.g. "Kaggle"
    kinds: tuple[str, ...]  # subset of ("competition", "dataset", "env")

    def search(
        self, query: str, kind: str | None, limit: int
    ) -> list[EnvironmentRefModel]:
        """Find environments matching `query`. Raise ProviderError on failure."""
        ...

    def resolve(self, ref_id: str, kind: str | None) -> EnvironmentRefModel:
        """Validate and enrich a specific environment id."""
        ...

    def fetch(
        self, ref: EnvironmentRefModel, dest: Path, progress: ProgressFn
    ) -> FetchResult:
        """Download whatever the environment needs into `dest` (may be a no-op for
        providers that load lazily at runtime, e.g. HF datasets)."""
        ...

    def scaffold(self, ref: EnvironmentRefModel, project: Any) -> ScaffoldResult:
        """Starter notebook cells + venv requirements for a new project."""
        ...


def md_cell(source: str) -> dict[str, Any]:
    """A markdown nbformat cell (ids are normalized on first save)."""
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def code_cell(source: str) -> dict[str, Any]:
    """A code nbformat cell."""
    return {
        "cell_type": "code",
        "metadata": {},
        "source": source,
        "outputs": [],
        "execution_count": None,
    }
