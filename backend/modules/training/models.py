"""Pydantic models for the training module's API boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# CellModel/NotebookModel are domain-neutral and live in the shared core; re-export
# them here so training callers keep importing from `training.models` unchanged.
from backend.notebook_core.models import CellModel as CellModel
from backend.notebook_core.models import NotebookModel as NotebookModel

EnvironmentKind = Literal["competition", "dataset", "env"]


class EnvironmentRefModel(BaseModel):
    """A reference to something trainable-against: a Kaggle competition, an HF
    dataset, a Gymnasium environment, or whatever a plugin provider serves."""

    provider: str
    kind: EnvironmentKind
    id: str
    title: str = ""
    url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ProviderInfoModel(BaseModel):
    """UI metadata for one environment provider."""

    provider: str
    label: str
    kinds: list[EnvironmentKind]


class ProjectModel(BaseModel):
    """One training project: a directory with a notebook, data, and its own venv."""

    id: str  # slug; doubles as the directory name
    name: str
    root: str  # absolute path
    refs: list[EnvironmentRefModel] = Field(default_factory=list)
    python: str = "3.12"
    venv_ready: bool = False
    data_ready: bool = False
    created_at: str = ""


class SearchResponse(BaseModel):
    results: list[EnvironmentRefModel]


class ResolveRequest(BaseModel):
    id: str
    kind: EnvironmentKind | None = None


class CreateProjectRequest(BaseModel):
    provider: str
    ref: str
    kind: EnvironmentKind | None = None
    name: str | None = None


class InstallDepsRequest(BaseModel):
    packages: list[str]


class ProjectListResponse(BaseModel):
    projects: list[ProjectModel]


class ProviderListResponse(BaseModel):
    providers: list[ProviderInfoModel]


class AcceptedResponse(BaseModel):
    """202-style reply for long-running work whose progress streams over `/ws`."""

    status: str = "started"
    detail: str = ""


class PushResultModel(BaseModel):
    target: str
    url: str | None = None
    status: str = "unknown"
    detail: str = ""


class ManimRequest(BaseModel):
    scene: str
    source: str | None = None  # scene source to write; None = use `file`
    file: str | None = None  # existing file relative to project root
    quality: str = "m"


class TrainingAdModel(BaseModel):
    """A peer-fabric advertisement: this node offers or seeks training compute."""

    node_id: str
    node_name: str = ""
    status: Literal["offering", "seeking", "none"]
    specs: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    ts: float = 0.0
