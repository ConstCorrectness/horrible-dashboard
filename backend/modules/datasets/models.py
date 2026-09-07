"""Pydantic models for the datasets API boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DatasetKind = Literal["sft", "preference", "corpus", "benchmark", "unknown"]


class DatasetRefModel(BaseModel):
    """One dataset as a source describes it, before it is registered."""

    source: str
    id: str
    title: str = ""
    url: str = ""
    #: Rows, when the source knows. `None` means "not reported", which is a
    #: different fact from zero and is rendered differently.
    rows: int | None = None
    description: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class SplitModel(BaseModel):
    config: str = ""
    split: str = ""


class PeekModel(BaseModel):
    """Real columns and real rows, plus what we made of them."""

    source: str
    id: str
    config: str = ""
    split: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    detection: dict[str, Any] = Field(default_factory=dict)


class DatasetModel(BaseModel):
    """A registered dataset: the thing a recipe points at."""

    id: str
    name: str
    source: str
    ref: str
    config: str = ""
    split: str = "train"
    format: str = "unknown"
    column_map: dict[str, str] = Field(default_factory=dict)
    rows: int | None = None
    #: Where the rows physically are, when they are local. Empty for a Hub id.
    path: str = ""
    notes: str = ""
    #: Content identity of the *definition*, so a recipe can record which dataset
    #: it trained on even after the row is edited.
    fingerprint: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


class RegisterRequest(BaseModel):
    name: str = ""
    source: str = "hub"
    ref: str
    config: str = ""
    split: str = "train"
    format: str = ""
    column_map: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    path: str = ""
    rows: int | None = None


class UpdateRequest(BaseModel):
    name: str | None = None
    split: str | None = None
    config: str | None = None
    format: str | None = None
    column_map: dict[str, str] | None = None
    notes: str | None = None


class PeekRequest(BaseModel):
    source: str = "hub"
    ref: str
    config: str = ""
    split: str = "train"
    limit: int = 5


class AdaptRequest(BaseModel):
    """ "Can task X eat this dataset?" — answered against real rows."""

    dataset_id: str = ""
    source: str = "hub"
    ref: str = ""
    config: str = ""
    split: str = "train"
    task: str = "sft"


class TokenStatsRequest(BaseModel):
    dataset_id: str = ""
    source: str = "hub"
    ref: str = ""
    config: str = ""
    split: str = "train"
    #: Rows to sample. A histogram is an estimate; downloading a million rows to
    #: draw one would defeat the purpose of asking before the run.
    limit: int = 200
    model: str = ""
    max_length: int = 1024


class TokenStatsModel(BaseModel):
    sampled: int = 0
    tokenizer: str = ""
    #: Present only when a real tokenizer answered. A character-count estimate
    #: says so rather than pretending to be a token count.
    exact: bool = False
    note: str = ""
    min: int = 0
    max: int = 0
    mean: float = 0.0
    p50: int = 0
    p95: int = 0
    #: Fraction of sampled examples longer than `max_length` — the number the
    #: whole surface exists to show, because silent truncation has no other signal.
    over_limit: float = 0.0
    histogram: list[dict[str, int]] = Field(default_factory=list)


class BuildStepModel(BaseModel):
    id: str = ""
    op: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class PipelineModel(BaseModel):
    id: str = ""
    name: str = ""
    steps: list[BuildStepModel] = Field(default_factory=list)
    updated_at: float = 0.0


class PreviewRequest(BaseModel):
    pipeline: PipelineModel
    limit: int = 10


class PreviewModel(BaseModel):
    """What the pipeline does to the first N rows, step by step.

    Per-step counts, not just the final rows: a filter that drops everything is
    invisible in the output and obvious in the counts.
    """

    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


class BuildRequest(BaseModel):
    pipeline: PipelineModel
    name: str = ""
    #: Not `register` — that shadows a BaseModel attribute and pydantic warns.
    save: bool = True
