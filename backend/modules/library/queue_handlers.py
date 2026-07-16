"""Task-queue handlers for the library, registered at import.

`backend/app.py` imports this module purely for the side effect of the
`register_handler` calls at the bottom.
"""

from typing import Any
import logging

from backend.modules.database.vectorstore import clip_collection, upsert_document
from backend.modules.library import store
from backend.modules.library.broadcast import publish_source
from backend.modules.library.clip import MODEL_REPO as CLIP_MODEL_REPO
from backend.modules.library.clip import clip_enabled
from backend.modules.library.ingest import clip_embed
from backend.modules.library.models import MEDIA_TYPES, IngestRequest, MediaAsset
from backend.modules.library.ingest import ingest_source
from backend.modules.tasks import queue

logger = logging.getLogger(__name__)


async def handle_ingest_source(payload: dict[str, Any]) -> None:
    source_id = payload.get("source_id")
    req_dict = payload.get("req")
    if not source_id or not req_dict:
        logger.error(f"Invalid payload for ingest_source: {payload}")
        return

    req = IngestRequest(**req_dict)
    await ingest_source(source_id, req)


async def handle_clip_embed_source(payload: dict[str, Any]) -> None:
    """Backfill one already-ingested media source's CLIP vector.

    Deliberately one source per task (`POST /library/reindex-clip` fans out): the
    queue worker is serial, so a single job looping over a whole library would block
    every new ingest behind it for as long as the backfill runs.

    Additive only — it never touches the text vectors or the source's status, so a
    failure here leaves a fully working text-indexed source.
    """
    source_id = payload.get("source_id")
    if not source_id:
        logger.error("clip_embed_source: no source_id in %s", payload)
        return
    if not clip_enabled():
        logger.info("clip_embed_source: CLIP disabled, skipping %s", source_id)
        return

    source = store.get_source(source_id)
    if source is None or source["type"] not in MEDIA_TYPES or not source.get("asset"):
        return

    # Rebuild the request shape `clip_embed` expects from the stored asset.
    req = IngestRequest(
        type=source["type"],
        library=source["library"],
        asset=MediaAsset(**source["asset"]),
    )
    vector = await clip_embed(req)
    if vector is None:
        logger.warning("clip backfill produced no vector for %s", source_id)
        return

    upsert_document(
        f"{source_id}#0",
        clip_collection(source["library"]),
        source["title"] or "",
        {
            "source_id": source_id,
            "title": source["title"],
            "type": source["type"],
            "url": source["url"],
            "author": source["author"],
            "tags": source["tags"],
            "chunk_index": 0,
            "asset": source["asset"],
            "embed_model": CLIP_MODEL_REPO,
        },
        vector,
    )
    # Re-broadcast so the panel reflects the new coverage without a refresh.
    publish_source(source)


queue.register_handler("ingest_source", handle_ingest_source)
queue.register_handler("clip_embed_source", handle_clip_embed_source)
