"""API-boundary models for the artifact store."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal["pdf", "page", "report"]


class ArtifactModel(BaseModel):
    """One stored blob: content-addressed on disk, metadata here."""

    id: str
    sha256: str
    kind: ArtifactKind
    mime: str
    filename: str
    size: int
    origin_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ArtifactsListResponse(BaseModel):
    artifacts: list[ArtifactModel]


class UploadResponse(BaseModel):
    artifact: ArtifactModel


class DeleteArtifactResult(BaseModel):
    deleted: bool
    id: str
