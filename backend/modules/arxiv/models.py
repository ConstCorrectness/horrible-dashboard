"""API-boundary models for the arXiv module."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.modules.artifacts.models import ArtifactModel
from backend.modules.library.models import SourceModel


class ArxivEntryModel(BaseModel):
    id: str
    title: str
    summary: str
    authors: list[str] = Field(default_factory=list)
    published: str = ""
    updated: str = ""
    categories: list[str] = Field(default_factory=list)
    pdf_url: str = ""
    abs_url: str = ""
    comment: str | None = None
    doi: str | None = None


class ArxivSearchResponse(BaseModel):
    query: str
    total: int
    start: int
    entries: list[ArxivEntryModel]


class ArxivDownloadRequest(BaseModel):
    arxiv_id: str
    library: str = "default"
    tags: list[str] = Field(default_factory=list)


class ArxivDownloadResponse(BaseModel):
    artifact: ArtifactModel
    source: SourceModel
    entry: ArxivEntryModel
