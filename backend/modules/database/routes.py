import asyncio
import uuid
import httpx
from typing import Annotated, Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.modules.agent import providers as P
from backend.modules.agent.routes import _load_config
from backend.modules.database.connections import (
    BUILTIN_APP_ID,
    add_connection,
    delete_connection,
    get_connection,
    list_connections,
    redact,
    resolve_config,
    update_connection,
)
from backend.modules.database.drivers import PROVIDERS, DriverError, get_driver
from backend.modules.database.drivers.base import looks_read_only
from backend.modules.database.vectorstore import (
    init_db,
    get_db_stats,
    list_documents,
    upsert_document,
    delete_document,
    search_documents,
)
from backend.modules.database.embeddings import get_embedding
from backend.modules.database.models import (
    ConnectionInfo,
    ConnectionInput,
    ConnectionTestResult,
    ConnectionsResponse,
    ProviderInfo,
    QueryRequest,
    QueryResultModel,
    ResultColumn,
    SchemaColumn,
    SchemaResponse,
    SchemaTable,
    VectorDbStatus,
    SearchRequest,
    SearchResult,
    UpsertDocumentRequest,
    DocumentResponse,
    DocumentsListResponse,
)

# Initialize the built-in app vector store schema immediately on module load.
init_db()

router = APIRouter(prefix="/database", tags=["database"])


# ---------------------------------------------------------------------------
# Generic inspector: connections, query, schema
# ---------------------------------------------------------------------------


@router.get("/connections", response_model=ConnectionsResponse)
async def get_connections() -> ConnectionsResponse:
    return ConnectionsResponse(
        connections=[ConnectionInfo(**redact(c)) for c in list_connections()],
        providers=[ProviderInfo(**p) for p in PROVIDERS],
    )


@router.post("/connections", response_model=ConnectionInfo)
async def create_connection(body: ConnectionInput) -> ConnectionInfo:
    record = add_connection(body.name, body.provider, body.config)
    return ConnectionInfo(**redact(record))


@router.put("/connections/{conn_id}", response_model=ConnectionInfo)
async def edit_connection(conn_id: str, body: ConnectionInput) -> ConnectionInfo:
    if conn_id == BUILTIN_APP_ID:
        raise HTTPException(
            status_code=400, detail="The built-in connection is read-only"
        )
    record = update_connection(conn_id, body.name, body.provider, body.config)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No connection '{conn_id}'")
    return ConnectionInfo(**redact(record))


@router.delete("/connections/{conn_id}")
async def remove_connection(conn_id: str) -> dict[str, Any]:
    if conn_id == BUILTIN_APP_ID:
        raise HTTPException(
            status_code=400, detail="The built-in connection is read-only"
        )
    if not delete_connection(conn_id):
        raise HTTPException(status_code=404, detail=f"No connection '{conn_id}'")
    return {"deleted": True, "id": conn_id}


def _test_config(provider: str, config: dict[str, Any]) -> ConnectionTestResult:
    try:
        get_driver(provider).test(config)
        return ConnectionTestResult(ok=True)
    except DriverError as exc:
        return ConnectionTestResult(ok=False, error=str(exc))
    except Exception as exc:  # surface unexpected client errors as a failed probe
        return ConnectionTestResult(ok=False, error=str(exc))


@router.post("/connections/test", response_model=ConnectionTestResult)
async def test_unsaved_connection(body: ConnectionInput) -> ConnectionTestResult:
    """Probe an unsaved connection (the add/edit form still holds the password)."""
    return await asyncio.to_thread(_test_config, body.provider, body.config)


@router.post("/connections/{conn_id}/test", response_model=ConnectionTestResult)
async def test_saved_connection(conn_id: str) -> ConnectionTestResult:
    conn = get_connection(conn_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"No connection '{conn_id}'")
    return await asyncio.to_thread(_test_config, conn["provider"], resolve_config(conn))


@router.get("/connections/{conn_id}/schema", response_model=SchemaResponse)
async def get_schema(conn_id: str) -> SchemaResponse:
    conn = get_connection(conn_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"No connection '{conn_id}'")
    driver = get_driver(conn["provider"])
    try:
        schema = await asyncio.to_thread(driver.introspect, resolve_config(conn))
    except DriverError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SchemaResponse(
        tables=[
            SchemaTable(
                name=t.name,
                schema_name=t.schema,
                columns=[
                    SchemaColumn(
                        name=c.name,
                        type=c.type,
                        nullable=c.nullable,
                        primary_key=c.primary_key,
                    )
                    for c in t.columns
                ],
            )
            for t in schema.tables
        ]
    )


@router.post("/query", response_model=QueryResultModel)
async def run_query(req: QueryRequest) -> QueryResultModel:
    conn = get_connection(req.connection_id)
    if conn is None:
        raise HTTPException(
            status_code=404, detail=f"No connection '{req.connection_id}'"
        )
    if req.read_only and not looks_read_only(req.sql):
        raise HTTPException(
            status_code=400,
            detail="Read-only mode allows a single SELECT/WITH/EXPLAIN statement only.",
        )
    driver = get_driver(conn["provider"])
    try:
        result = await asyncio.to_thread(
            driver.run_query,
            resolve_config(conn),
            req.sql,
            req.params,
            read_only=req.read_only,
            row_limit=req.row_limit,
        )
    except DriverError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return QueryResultModel(
        columns=[ResultColumn(name=c.name, type=c.type) for c in result.columns],
        rows=result.rows,
        rowcount=result.rowcount,
        elapsed_ms=result.elapsed_ms,
        truncated=result.truncated,
        affected=result.affected,
        message=result.message,
    )


# ---------------------------------------------------------------------------
# Built-in app vector store: status, embedding-model pull, semantic search,
# document CRUD. These operate on the LanceDB vector store (`.data/lancedb`) — NOT the
# `app` connection, which is the SQLite app database (`.data/app.db`). They back the
# semantic-search agent tool + commons matchmaking config.
# ---------------------------------------------------------------------------


@router.get("/status", response_model=VectorDbStatus)
async def get_status() -> VectorDbStatus:
    stats = get_db_stats()
    config = _load_config()

    active_provider = config.provider if config else "none"
    active_model = config.model if config else "none"

    # Check if a dedicated embedding model (e.g. all-minilm) is pulled
    # and override active_model display if it is detected by get_embedding
    if config:
        info = P.provider_for(config.provider)
        endpoint = config.endpoint or info.default_endpoint
        async with httpx.AsyncClient(timeout=1.0) as client:
            try:
                available_models = await P.list_models(client, info, endpoint)
                embedding_keywords = ["all-minilm", "nomic-embed", "bge-", "embed"]
                for kw in embedding_keywords:
                    matched = next(
                        (m for m in available_models if kw in m.lower()), None
                    )
                    if matched:
                        active_model = f"{matched} (dedicated)"
                        break
            except Exception:
                pass

    return VectorDbStatus(
        db_path=stats["db_path"],
        size_bytes=stats["size_bytes"],
        num_documents=stats["num_documents"],
        collections=stats["collections"],
        active_provider=active_provider,
        active_model=active_model,
    )


@router.post("/pull")
async def pull_embedding_model() -> StreamingResponse:
    config = _load_config()
    if not config or config.provider != "ollama":
        raise HTTPException(
            status_code=400, detail="Only Ollama provider supports pulling models"
        )

    info = P.provider_for("ollama")
    endpoint = config.endpoint or info.default_endpoint

    async def gen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{endpoint}/api/pull",
                json={"model": "all-minilm", "stream": True},
            ) as res:
                res.raise_for_status()
                async for line in res.aiter_lines():
                    if line:
                        yield line + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/collections", response_model=list[str])
async def get_collections() -> list[str]:
    stats = get_db_stats()
    return [col.name for col in stats["collections"]]


@router.get("/documents", response_model=DocumentsListResponse)
async def get_all_documents(
    collection: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentsListResponse:
    docs, total = list_documents(collection, limit, offset)

    doc_responses = [
        DocumentResponse(
            id=d["id"],
            collection=d["collection"],
            text=d["text"],
            metadata=d["metadata"],
            created_at=str(d["created_at"]),
        )
        for d in docs
    ]

    return DocumentsListResponse(
        documents=doc_responses,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/search", response_model=list[SearchResult])
async def search(req: SearchRequest) -> list[SearchResult]:
    # 1. Generate the query embedding (using agent provider or local fallback)
    emb, _ = await get_embedding(req.text)

    # 2. Search database using cosine similarity
    # We query with a larger limit to ensure we retrieve enough unique intents after grouping
    db_limit = max(100, req.limit * 5)
    matches = search_documents(req.collection, emb, db_limit)

    # Check if this collection contains intent metadata (i.e. at least one match has "intent" key)
    is_intent_collection = any(
        isinstance(m.get("metadata"), dict) and "intent" in m["metadata"]
        for m in matches
    )

    if is_intent_collection:
        intent_groups = {}
        for m in matches:
            meta = m.get("metadata") or {}
            # Try to get intent identifier: full_intent first, then intent
            intent_val = meta.get("full_intent") or meta.get("intent")
            if not intent_val:
                continue

            score = m["score"]
            if (
                intent_val not in intent_groups
                or score > intent_groups[intent_val]["score"]
            ):
                intent_groups[intent_val] = {
                    "id": m["id"],
                    "collection": m["collection"],
                    "text": intent_val,
                    "metadata": meta,
                    "score": score,
                }

        # Sort by score descending and limit to req.limit
        sorted_groups = sorted(
            intent_groups.values(), key=lambda x: x["score"], reverse=True
        )
        return [
            SearchResult(
                id=g["id"],
                collection=g["collection"],
                text=g["text"],
                metadata=g["metadata"],
                score=g["score"],
            )
            for g in sorted_groups[: req.limit]
        ]
    else:
        # Fallback to standard document search (using requested limit)
        return [
            SearchResult(
                id=m["id"],
                collection=m["collection"],
                text=m["text"],
                metadata=m["metadata"],
                score=m["score"],
            )
            for m in matches[: req.limit]
        ]


@router.post("/documents", response_model=DocumentResponse)
async def upsert(req: UpsertDocumentRequest) -> DocumentResponse:
    doc_id = req.id or uuid.uuid4().hex[:12]

    # 1. Generate text embedding
    emb, source = await get_embedding(req.text)

    # 2. Inject embedding source into metadata for observability
    metadata = dict(req.metadata)
    metadata["_embedding_source"] = source

    # 3. Store document
    upsert_document(doc_id, req.collection, req.text, metadata, emb)

    # Retrieve to get created_at
    docs, _ = list_documents(req.collection, 1, 0)
    matched = next((d for d in docs if d["id"] == doc_id), None)

    created_at = str(matched["created_at"]) if matched else ""

    return DocumentResponse(
        id=doc_id,
        collection=req.collection,
        text=req.text,
        metadata=metadata,
        created_at=created_at,
    )


@router.delete("/documents/{doc_id}")
async def remove(doc_id: str) -> dict[str, Any]:
    deleted = delete_document(doc_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Document with ID '{doc_id}' not found"
        )
    return {"deleted": True, "id": doc_id}
