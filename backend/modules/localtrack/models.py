"""Data models and API schemas for the LocalTrack experiment tracker."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


RunStatus = Literal["running", "finished", "failed", "crashed"]
#: `table` and `parcoords` are the comparison panels: the first is the run ×
#: config × metric grid that answers "which knob caused this", the second plots
#: every run as a line across the config axes, coloured by the metric. Neither is
#: a chart of a series — they read `config_json`, which nothing had ever done.
ChartType = Literal["line", "bar", "scalar", "table", "parcoords"]


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


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(default_factory=list)
    #: The metric each run is judged by. Its last recorded value is what lands in
    #: the table, because a fine-tune's final loss is the number people compare.
    metric: str = ""


class CompareRow(BaseModel):
    run_id: str
    name: str
    status: str
    #: Only the config keys that DIFFER across the compared runs. A row carrying
    #: all forty knobs buries the two that varied.
    config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)


class CompareResponse(BaseModel):
    """Runs side by side, reduced to what actually differs between them."""

    runs: list[CompareRow] = Field(default_factory=list)
    #: Config keys that vary — the axes of the experiment, in the order they
    #: should be shown.
    varied: list[str] = Field(default_factory=list)
    #: Config keys shared by every run. Worth reporting so the table can say what
    #: was held constant without repeating it on every row.
    shared: dict[str, Any] = Field(default_factory=dict)
    metric_keys: list[str] = Field(default_factory=list)
    #: True when the runs disagree about something that makes them incomparable —
    #: a different backend, task or dataset. Comparing those is still allowed; it
    #: just must not be presented as an ablation.
    mixed: list[str] = Field(default_factory=list)
