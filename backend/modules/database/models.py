from typing import Any
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Generic database inspector
# ---------------------------------------------------------------------------


class ProviderInfo(BaseModel):
    """A selectable database provider and the config fields it accepts."""

    id: str
    label: str
    fields: list[str]
    # "sql" or "json" — which editor + query shape the console uses for this provider.
    # Vector stores are "json"; see backend/modules/database/drivers/base.py.
    dialect: str = "sql"


class ConnectionInfo(BaseModel):
    """A saved (or built-in) connection, with secrets redacted to booleans."""

    id: str
    name: str
    provider: str
    config: dict[str, Any]
    builtin: bool = False
    # Denormalized from the provider so the console can switch editor modes without
    # cross-referencing the providers list on every connection change.
    dialect: str = "sql"


class ConnectionInput(BaseModel):
    """Create/update payload. Omitted secret fields keep their stored value."""

    name: str
    provider: str
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectionsResponse(BaseModel):
    connections: list[ConnectionInfo]
    providers: list[ProviderInfo]


class ConnectionTestResult(BaseModel):
    ok: bool
    error: str | None = None


class QueryRequest(BaseModel):
    connection_id: str = "app"
    # The query text. SQL for sql-dialect providers; a JSON operation body for
    # json-dialect (vector) providers. The field keeps its name for wire
    # compatibility — see drivers/vector_base.py for the JSON shape.
    sql: str
    params: list[Any] | None = None
    read_only: bool = False
    row_limit: int = Field(default=1000, ge=1, le=10000)


class ResultColumn(BaseModel):
    name: str
    type: str | None = None


class QueryResultModel(BaseModel):
    columns: list[ResultColumn]
    rows: list[list[Any]]
    rowcount: int
    elapsed_ms: float
    truncated: bool = False
    affected: int | None = None
    message: str | None = None


class SchemaColumn(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False


class SchemaTable(BaseModel):
    name: str
    schema_name: str | None = None
    columns: list[SchemaColumn]


class SchemaResponse(BaseModel):
    tables: list[SchemaTable]


# ---------------------------------------------------------------------------
# Built-in app vector store (semantic search + document endpoints)
# ---------------------------------------------------------------------------


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
