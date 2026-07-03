"""API-boundary models for the knowledge library module.

A **source** is one ingested item (a blog post or a note; PDF/EPUB later). Each
source is chunked and every chunk is stored as a row in the shared vector store's
``documents`` table (collection = the library name), so semantic search reuses the
existing engine. See docs/modules/library.mdx.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["blog", "note"]
SourceStatus = Literal["queued", "fetching", "chunking", "embedding", "ready", "failed"]


class SourceModel(BaseModel):
    """A catalog row: one ingested source and its ingestion status."""

    id: str
    library: str
    type: SourceType
    title: str
    url: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: SourceStatus
    error: str | None = None
    chunk_count: int = 0
    added_at: str


class IngestRequest(BaseModel):
    """Add a source. `blog` needs `url`; `note` needs `text` (+ a `title`)."""

    type: SourceType
    library: str = "default"
    url: str | None = None
    title: str | None = None
    text: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)


class SourcesListResponse(BaseModel):
    sources: list[SourceModel]


class LibraryInfo(BaseModel):
    name: str
    source_count: int
    chunk_count: int


class LibrariesResponse(BaseModel):
    libraries: list[LibraryInfo]


class ChunkModel(BaseModel):
    index: int
    text: str


class ChunksResponse(BaseModel):
    source: SourceModel
    chunks: list[ChunkModel]


class LibrarySearchRequest(BaseModel):
    library: str = "default"
    text: str
    limit: int = Field(default=5, ge=1, le=50)


class SearchChunk(BaseModel):
    chunk_index: int
    text: str
    score: float


class SearchGroup(BaseModel):
    """Search hits collapsed to one entry per source, for citation."""

    source_id: str
    title: str
    type: str
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    top_score: float
    chunks: list[SearchChunk]


class LibrarySearchResponse(BaseModel):
    query: str
    library: str
    groups: list[SearchGroup]


class DeleteResult(BaseModel):
    deleted: bool
    id: str
