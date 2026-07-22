"""HTTP surface for the knowledge library (`/api/library/*`).

Ingestion is fire-and-forget: `POST /sources` records the source as `queued`,
kicks off the background pipeline, and returns immediately; progress arrives on the
`library` `/ws` channel. Search embeds the query and reuses the vector store's
`search_documents`, then collapses hits to one group per source for citation.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, Query

from backend.modules.database.embeddings import get_embedding
from backend.modules.database.vectorstore import (
    CLIP_SUFFIX,
    clip_collection,
    delete_document,
    init_db,
    search_documents,
)
from backend.modules.library import store
from backend.modules.library.clip import CLIP_DIM
from backend.modules.library.clip import MODEL_REPO as CLIP_MODEL_REPO
from backend.modules.library.clip import clip_enabled, clip_installed
from backend.modules.library.clip import encode_text as clip_encode_text
from backend.modules.artifacts.store import get_artifact
from backend.modules.tasks import enqueue_task
from backend.modules.library.models import (
    ARTIFACT_TYPES,
    MEDIA_TYPES,
    ChunkModel,
    ChunksResponse,
    ClipStatus,
    DeleteResult,
    IngestRequest,
    LibrariesResponse,
    LibraryInfo,
    LibrarySearchRequest,
    LibrarySearchResponse,
    MediaAsset,
    ReindexResult,
    SearchChunk,
    SearchGroup,
    SourceModel,
    SourcesListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])


def _title_from_src(src: str) -> str:
    """Last-resort title for a media asset with no alt text: its filename."""
    name = unquote(urlsplit(src).path.rsplit("/", 1)[-1])
    return name or src


@router.post("/sources", response_model=SourceModel)
async def add_source(req: IngestRequest) -> SourceModel:
    """Register a source and start ingesting it in the background."""
    if req.type == "blog":
        if not (req.url and req.url.strip()):
            raise HTTPException(status_code=400, detail="url is required for a blog")
        title = req.title or req.url
    elif req.type in ARTIFACT_TYPES:
        if not req.artifact_id:
            raise HTTPException(
                status_code=400, detail=f"artifact_id is required for a {req.type}"
            )
        artifact = get_artifact(req.artifact_id)
        if artifact is None:
            raise HTTPException(status_code=400, detail="artifact not found")
        if artifact["kind"] != req.type:
            raise HTTPException(
                status_code=400,
                detail=f"artifact is a {artifact['kind']}, not a {req.type}",
            )
        title = (
            req.title or (artifact["meta"] or {}).get("title") or artifact["filename"]
        )
    elif req.type in MEDIA_TYPES:
        if not (req.asset and req.asset.src.strip()):
            raise HTTPException(
                status_code=400, detail=f"asset.src is required for {req.type}"
            )
        # Media is referenced, not copied, and its `src` is rendered by the client —
        # so keep the scheme to things a page can safely point at.
        scheme = urlsplit(req.asset.src).scheme
        if scheme not in ("http", "https"):
            raise HTTPException(
                status_code=400,
                detail=f"asset.src must be http(s), got: {scheme or '(none)'}",
            )
        title = req.title or req.asset.alt or _title_from_src(req.asset.src)
    else:
        if not (req.text and req.text.strip()):
            raise HTTPException(status_code=400, detail="text is required for a note")
        title = req.title or "Untitled note"

    source = store.create_source(
        library=(req.library or "default"),
        type=req.type,
        title=title,
        url=req.url or (req.asset.page_url if req.asset else None),
        author=req.author,
        tags=req.tags,
        asset=req.asset.model_dump() if req.asset else None,
        artifact_id=req.artifact_id,
    )
    # Queue the document for background ingestion via the task queue
    enqueue_task(
        task_type="ingest_source",
        payload={"source_id": source["id"], "req": req.model_dump()},
    )
    return SourceModel(**source)


@router.get("/sources", response_model=SourcesListResponse)
def list_sources(
    library: str | None = None,
    type: str | None = Query(default=None),
    tag: str | None = None,
) -> SourcesListResponse:
    rows = store.list_sources(library=library, type=type, tag=tag)
    return SourcesListResponse(sources=[SourceModel(**r) for r in rows])


@router.get("/sources/{source_id}", response_model=SourceModel)
def get_source(source_id: str) -> SourceModel:
    source = store.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return SourceModel(**source)


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
    # `delete_document` sweeps every table by id, so a chunk's CLIP sibling — which
    # shares its id — goes with it.
    for chunk in store.chunk_docs_for(source):
        delete_document(f"{source_id}#{chunk['index']}")
    # ...but a CLIP-only source (indexed by appearance, no describing text) has *no*
    # chunk rows to iterate, so its sibling row would survive the loop above and
    # linger as an orphan hit forever. Sweep its id explicitly; it's a no-op when the
    # loop already covered it.
    delete_document(f"{source_id}#0")
    store.delete_source(source_id)
    return DeleteResult(deleted=True, id=source_id)


def _clip_indexed_libraries() -> set[str]:
    """Libraries that actually have a CLIP sibling table on disk."""
    from backend.modules.database.vectorstore import _get_db

    try:
        names = _get_db().table_names()
    except Exception:  # noqa: BLE001 — status must never break on a missing store
        return set()
    return {n[: -len(CLIP_SUFFIX)] for n in names if n.endswith(CLIP_SUFFIX)}


@router.get("/clip", response_model=ClipStatus)
def clip_status() -> ClipStatus:
    """Whether visual search is available, and how much media it actually covers.

    `media_sources` vs `libraries_indexed` is the honest pair to report: switching
    CLIP on does nothing for media ingested before it, which needs `POST
    /reindex-clip`. Without this the feature looks broken — searches just silently
    fail to find older images.
    """
    media = [s for s in store.list_sources() if s["type"] in MEDIA_TYPES]
    return ClipStatus(
        enabled=clip_enabled(),
        installed=clip_installed(),
        model=CLIP_MODEL_REPO,
        dim=CLIP_DIM,
        media_sources=len(media),
        libraries_indexed=sorted(_clip_indexed_libraries()),
    )


@router.post("/reindex-clip", response_model=ReindexResult)
def reindex_clip(library: str | None = None) -> ReindexResult:
    """Backfill CLIP vectors for media ingested before visual search was enabled.

    Enqueues **one task per source** rather than a single sweeping job: the task
    queue runs strictly serially, so a monolithic backfill would park every
    `ingest_source` behind it until the last image finished.
    """
    if not clip_enabled():
        raise HTTPException(
            status_code=400,
            detail=(
                "CLIP visual search is off. Install the extra "
                "(`uv sync --extra clip`) and enable the library.clipEnabled setting."
            ),
        )
    sources = [
        s
        for s in store.list_sources(library=library)
        if s["type"] in MEDIA_TYPES and s.get("asset")
    ]
    for source in sources:
        enqueue_task(task_type="clip_embed_source", payload={"source_id": source["id"]})
    return ReindexResult(started=True, queued=len(sources))


# Reciprocal Rank Fusion constant. 60 is the value from the original paper and the
# de-facto default; it damps the difference between the top few ranks so one space
# can't dominate purely by being more confident.
_RRF_K = 60


def _rrf(ranked_source_ids: list[str]) -> dict[str, float]:
    """Reciprocal-rank score per source, from one ranked result list.

    Only the *best* rank a source achieves counts — a source with six matching chunks
    isn't six times more relevant than one with a single strong chunk.
    """
    scores: dict[str, float] = {}
    for rank, source_id in enumerate(ranked_source_ids):
        if source_id not in scores:
            scores[source_id] = 1.0 / (_RRF_K + rank + 1)
    return scores


def _source_id_of(row: dict) -> str:
    return str(row["metadata"].get("source_id", row["id"]))


@router.post("/search", response_model=LibrarySearchResponse)
async def search(req: LibrarySearchRequest) -> LibrarySearchResponse:
    """Semantic search over a library, fusing the text and visual spaces.

    A media source can be indexed twice — as proxy text in the library's own table,
    and as a CLIP image vector in `<library>__clip` — so a query is encoded once per
    space and the two ranked lists are fused.

    Fusion is **rank**-based (RRF), not score-based, and that's the whole point: the
    two lists come from different embedding models, so their cosine scores share no
    scale. `0.31` from CLIP and `0.31` from all-MiniLM mean unrelated things, and
    averaging them would be arithmetic on incomparable units. Ranks are all they have
    in common.
    """
    library = req.library or "default"
    init_db()  # tolerate search before anything has been ingested
    embedding, _source = await get_embedding(req.text)
    text_results = search_documents(library, embedding, req.limit)

    clip_results: list[dict] = []
    if clip_enabled():
        try:
            clip_vector = await clip_encode_text(req.text)
            clip_results = search_documents(
                clip_collection(library), clip_vector, req.limit
            )
        except Exception:  # noqa: BLE001 — visual search is additive; text still works
            logger.exception("CLIP query failed; falling back to text-only search")

    groups: dict[str, SearchGroup] = {}

    def ensure_group(row: dict, score: float) -> SearchGroup:
        meta = row["metadata"]
        source_id = _source_id_of(row)
        group = groups.get(source_id)
        if group is None:
            group = SearchGroup(
                source_id=source_id,
                title=str(meta.get("title") or "Untitled"),
                type=str(meta.get("type", "note")),
                url=meta.get("url"),
                tags=list(meta.get("tags", []) or []),
                top_score=score,
                chunks=[],
                asset=MediaAsset(**meta["asset"]) if meta.get("asset") else None,
                artifact_id=meta.get("artifact_id"),
                matched_by=[],
            )
            groups[source_id] = group
        return group

    for row in text_results:
        score = float(row["score"])
        group = ensure_group(row, score)
        group.chunks.append(
            SearchChunk(
                chunk_index=int(row["metadata"].get("chunk_index", 0)),
                text=row["text"],
                score=score,
            )
        )
        group.top_score = max(group.top_score, score)
        if "text" not in group.matched_by:
            group.matched_by.append("text")

    for row in clip_results:
        # No chunk is appended: a CLIP row's text is display filler, not a passage
        # that matched. What matched was the image itself.
        group = ensure_group(row, float(row["score"]))
        if "clip" not in group.matched_by:
            group.matched_by.append("clip")

    fused = _rrf([_source_id_of(r) for r in text_results])
    for source_id, score in _rrf([_source_id_of(r) for r in clip_results]).items():
        fused[source_id] = fused.get(source_id, 0.0) + score

    ordered = sorted(
        groups.values(), key=lambda g: fused.get(g.source_id, 0.0), reverse=True
    )
    return LibrarySearchResponse(
        query=req.text, library=library, groups=ordered[: req.limit]
    )
