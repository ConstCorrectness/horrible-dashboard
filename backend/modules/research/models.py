"""API-boundary models for the research module."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.modules.artifacts.models import ArtifactModel
from backend.modules.library.models import SourceModel


class CaptureRequest(BaseModel):
    """Save a URL as a self-contained page artifact + library source."""

    url: str
    library: str = "default"
    title: str | None = None
    tags: list[str] = Field(default_factory=list)


class SavePdfRequest(BaseModel):
    """Fetch a PDF by URL into the artifact store + library."""

    url: str
    library: str = "default"
    title: str | None = None
    tags: list[str] = Field(default_factory=list)


class CaptureResponse(BaseModel):
    artifact: ArtifactModel
    source: SourceModel


class ExportRequest(BaseModel):
    """Export a stored source (page/pdf/report) into the configured Obsidian vault."""

    source_id: str | None = None
    artifact_id: str | None = None


class ExportResponse(BaseModel):
    note_path: str
    attachment_path: str | None = None


class StartRunRequest(BaseModel):
    """Start a deep-research run."""

    query: str
    effort: str = "auto"  # auto|quick|standard|deep
    library: str = "default"
    provider: str | None = (
        None  # override; empty = research.provider setting/agent config
    )
    model: str | None = None
    # `plan` parks the run after planning so you can edit the plan before any
    # subagent spends a token. `auto` is the old behaviour.
    approval_mode: str = "auto"


class ApprovePlanRequest(BaseModel):
    """Release a run parked at the approval gate, optionally with an edited plan.

    Omitting `plan` approves what the lead proposed unchanged.
    """

    plan: dict[str, Any] | None = None


class FollowupRequest(BaseModel):
    text: str


class FollowupModel(BaseModel):
    id: str
    run_id: str
    text: str
    created_at: str | None = None
    consumed_at: str | None = None


class FollowupsListResponse(BaseModel):
    followups: list[FollowupModel]


class ToolCallModel(BaseModel):
    id: str
    run_id: str
    step_id: str
    seq: int
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    ms: int | None = None
    summary: str = ""
    created_at: str | None = None


class ToolCallsListResponse(BaseModel):
    calls: list[ToolCallModel]


class RunModel(BaseModel):
    id: str
    query: str
    status: str
    effort: str
    library: str
    provider: str | None = None
    model: str | None = None
    plan: dict[str, Any] | None = None
    report_artifact_id: str | None = None
    report_source_id: str | None = None
    error: str | None = None
    tokens_used: int = 0
    token_budget: int = 0
    cancel_requested: bool = False
    approval_mode: str = "auto"
    rounds_used: int = 0
    created_at: str
    updated_at: str


class RunsListResponse(BaseModel):
    runs: list[RunModel]


class StepModel(BaseModel):
    id: str
    run_id: str
    seq: int
    kind: str
    name: str
    status: str
    attempt: int
    max_attempts: int
    # Which gap-filling wave this step belongs to (0 for the first pass and for
    # every linear step).
    round: int = 0
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    transcript: list[dict[str, Any]] | None = None
    tokens_used: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class StepsListResponse(BaseModel):
    steps: list[StepModel]
