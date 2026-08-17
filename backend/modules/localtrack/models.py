"""Data models and API schemas for the LocalTrack experiment tracker."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


RunStatus = Literal["running", "finished", "failed", "crashed"]
ChartType = Literal["line", "bar", "scalar"]


class ProjectModel(BaseModel):
    """A project grouping multiple experiment runs."""

    id: str
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    run_count: int = 0
    last_run_at: str | None = None


class CreateProjectRequest(BaseModel):
    id: str | None = None
    name: str
    description: str = ""


class ProjectListResponse(BaseModel):
    projects: list[ProjectModel]


class RunModel(BaseModel):
    """A single training/experiment run."""

    id: str
    project_id: str
    name: str
    status: RunStatus = "running"
    config: dict[str, Any] = Field(default_factory=dict)
    system_info: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, float | int] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    start_time: str = ""
    end_time: str | None = None
    duration_seconds: float = 0.0


class CreateRunRequest(BaseModel):
    id: str | None = None
    project_id: str = "default"
    name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    system_info: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class UpdateRunRequest(BaseModel):
    name: str | None = None
    status: RunStatus | None = None
    config: dict[str, Any] | None = None
    summary: dict[str, float | int] | None = None
    tags: list[str] | None = None
    end_time: str | None = None
    duration_seconds: float | None = None


class RunListResponse(BaseModel):
    runs: list[RunModel]


class MetricPoint(BaseModel):
    """A single metric measurement point."""

    step: int
    epoch: float | None = None
    timestamp: float = 0.0
    value: float


class MetricLogItem(BaseModel):
    """An ingestion entry carrying one or more metric values at a step."""

    run_id: str
    step: int
    epoch: float | None = None
    timestamp: float | None = None
    metrics: dict[str, float | int]


class BatchIngestRequest(BaseModel):
    logs: list[MetricLogItem]


class BatchIngestResponse(BaseModel):
    ingested_count: int
    status: str = "ok"


class MetricQueryRequest(BaseModel):
    """Query time-series metric data across runs with downsampling."""

    run_ids: list[str]
    keys: list[str]
    max_points: int = 500
    smoothing: float = 0.0  # Exponential Moving Average factor [0.0, 0.99]
    min_step: int | None = None
    max_step: int | None = None


class MetricSeriesResponse(BaseModel):
    """Downsampled time-series series data for a single metric on a single run."""

    run_id: str
    key: str
    steps: list[int]
    values: list[float]
    epochs: list[float | None] = Field(default_factory=list)
    raw_point_count: int = 0


class MetricQueryResponse(BaseModel):
    series: list[MetricSeriesResponse]


class RunArtifactModel(BaseModel):
    """An artifact file associated with a run (e.g. config.json, trainer_state.json)."""

    id: str
    run_id: str
    filename: str
    file_path: str
    size_bytes: int = 0
    content_type: str = "application/octet-stream"
    created_at: str = ""


class ArtifactListResponse(BaseModel):
    artifacts: list[RunArtifactModel]
