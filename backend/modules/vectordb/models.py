from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class CollectionStats(BaseModel):
    name: str
    count: int


class VectorDbStatus(BaseModel):
    db_path: str
    size_bytes: int
    num_documents: int
    collections: list[CollectionStats]
    active_provider: str
    active_model: str


class SearchRequest(BaseModel):
    text: str
    collection: str
    limit: int = Field(default=5, ge=1, le=100)


class SearchResult(BaseModel):
    id: str
    collection: str
    text: str
    metadata: dict[str, Any]
    score: float


class UpsertDocumentRequest(BaseModel):
    id: str | None = None
    collection: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    id: str
    collection: str
    text: str
    metadata: dict[str, Any]
    created_at: str


class DocumentsListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    limit: int
    offset: int
