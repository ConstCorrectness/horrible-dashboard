"""HTTP surface for the knowledge library (`/api/library/*`).

Ingestion is fire-and-forget: `POST /sources` records the source as `queued`,
kicks off the background pipeline, and returns immediately; progress arrives on the
`library` `/ws` channel. Search embeds the query and reuses the vector store's
`search_documents`, then collapses hits to one group per source for citation.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from backend.modules.database.embeddings import get_embedding
from backend.modules.database.vectorstore import (
    delete_document,
    init_db,
    search_documents,
)
from backend.modules.library import store
from backend.modules.library.ingest import ingest_source
from backend.modules.library.models import (
    ChunkModel,
    ChunksResponse,
    DeleteResult,
    IngestRequest,
    LibrariesResponse,
    LibraryInfo,
    LibrarySearchRequest,
    LibrarySearchResponse,
    SearchChunk,
    SearchGroup,
    SourceModel,
    SourcesListResponse,
)

router = APIRouter(prefix="/library", tags=["library"])


@router.post("/sources", response_model=SourceModel)
async def add_source(req: IngestRequest) -> SourceModel:
    """Register a source and start ingesting it in the background."""
    if req.type == "blog":
        if not (req.url and req.url.strip()):
            raise HTTPException(status_code=400, detail="url is required for a blog")
        title = req.title or req.url
    else:
        if not (req.text and req.text.strip()):
            raise HTTPException(status_code=400, detail="text is required for a note")
        title = req.title or "Untitled note"

    source = store.create_source(
        library=(req.library or "default"),
        type=req.type,
        title=title,
        url=req.url,
        author=req.author,
        tags=req.tags,
    )
    # Detached so a slow fetch/embed doesn't block the request or the ws loop.
    asyncio.create_task(ingest_source(source["id"], req))
    return SourceModel(**source)


@router.get("/sources", response_model=SourcesListResponse)
def list_sources(
    library: str | None = None,
    type: str | None = Query(default=None),
    tag: str | None = None,
) -> SourcesListResponse:
    rows = store.list_sources(library=library, type=type, tag=tag)
    return SourcesListResponse(sources=[SourceModel(**r) for r in rows])


@router.get("/libraries", response_model=LibrariesResponse)
def list_libraries() -> LibrariesResponse:
    return LibrariesResponse(
        libraries=[LibraryInfo(**item) for item in store.list_libraries()]
    )


@router.get("/sources/{source_id}/chunks", response_model=ChunksResponse)
def get_chunks(source_id: str) -> ChunksResponse:
    source = store.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    chunks = [
        ChunkModel(index=c["index"], text=c["text"])
        for c in store.chunk_docs_for(source)
    ]
    return ChunksResponse(source=SourceModel(**source), chunks=chunks)


@router.delete("/sources/{source_id}", response_model=DeleteResult)
def delete_source(source_id: str) -> DeleteResult:
    source = store.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    # Delete the actual chunk rows (covers partial ingests, not just chunk_count).
    for chunk in store.chunk_docs_for(source):
        delete_document(f"{source_id}#{chunk['index']}")
    store.delete_source(source_id)
    return DeleteResult(deleted=True, id=source_id)


@router.post("/search", response_model=LibrarySearchResponse)
async def search(req: LibrarySearchRequest) -> LibrarySearchResponse:
    library = req.library or "default"
    init_db()  # tolerate search before anything has been ingested
    embedding, _source = await get_embedding(req.text)
    results = search_documents(library, embedding, req.limit)

    groups: dict[str, SearchGroup] = {}
    for r in results:
        meta = r["metadata"]
        source_id = str(meta.get("source_id", r["id"]))
        score = float(r["score"])
        chunk = SearchChunk(
            chunk_index=int(meta.get("chunk_index", 0)),
            text=r["text"],
            score=score,
        )
        group = groups.get(source_id)
        if group is None:
            groups[source_id] = SearchGroup(
                source_id=source_id,
                title=str(meta.get("title", "Untitled")),
                type=str(meta.get("type", "note")),
                url=meta.get("url"),
                tags=list(meta.get("tags", []) or []),
                top_score=score,
                chunks=[chunk],
            )
        else:
            group.chunks.append(chunk)
            group.top_score = max(group.top_score, score)

    ordered = sorted(groups.values(), key=lambda g: g.top_score, reverse=True)
    return LibrarySearchResponse(query=req.text, library=library, groups=ordered)
