import uuid
import httpx
from typing import Annotated, Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.modules.agent import providers as P
from backend.modules.agent.routes import _load_config
from backend.modules.vectordb.database import (
    init_db,
    get_db_stats,
    list_documents,
    upsert_document,
    delete_document,
    search_documents,
)
from backend.modules.vectordb.embeddings import get_embedding
from backend.modules.vectordb.models import (
    VectorDbStatus,
    SearchRequest,
    SearchResult,
    UpsertDocumentRequest,
    DocumentResponse,
    DocumentsListResponse,
)

# Initialize database schema immediately on module load
init_db()

router = APIRouter(prefix="/vectordb", tags=["vectordb"])


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
    matches = search_documents(req.collection, emb, req.limit)

    return [
        SearchResult(
            id=m["id"],
            collection=m["collection"],
            text=m["text"],
            metadata=m["metadata"],
            score=m["score"],
        )
        for m in matches
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
