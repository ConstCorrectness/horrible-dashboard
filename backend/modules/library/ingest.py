"""The ingest pipeline: source → (fetch) → chunk → embed → store, with live status.

Runs as a detached background task (`asyncio.create_task` in the route), so a slow
fetch/embed never blocks the request or the `/ws` receive loop
(see the ws-ui-driving-handler-must-detach note). Every status transition is
broadcast on the `library` channel so the panel updates live.
"""

from __future__ import annotations

import logging

from backend.modules.database.embeddings import get_embedding
from backend.modules.database.vectorstore import init_db, upsert_document
from backend.modules.library import store
from backend.modules.library.broadcast import publish_source
from backend.modules.library.chunking import chunk_text
from backend.modules.library.extract import fetch_article
from backend.modules.library.models import IngestRequest
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


def _doc_id(source_id: str, index: int) -> str:
    return f"{source_id}#{index}"


def _emit(source_id: str) -> None:
    source = store.get_source(source_id)
    if source is not None:
        publish_source(source)


async def ingest_source(source_id: str, req: IngestRequest) -> None:
    """Drive one source from `queued` to `ready` (or `failed`). Never raises —
    failures are recorded on the source and broadcast."""
    try:
        init_db()  # ensure the shared `documents` table exists
        title = req.title
        author = req.author

        if req.type == "blog":
            store.set_status(source_id, "fetching")
            _emit(source_id)
            article = await fetch_article(req.url or "")
            title = req.title or article.title
            author = req.author or article.author
            text = article.text
            store.update_meta(source_id, title=title, author=author)
        else:
            text = req.text or ""

        store.set_status(source_id, "chunking")
        _emit(source_id)
        size = int(get_value("library.chunkSize", 1000) or 1000)
        chunks = chunk_text(text, size=size)
        if not chunks:
            store.set_status(source_id, "failed", error="no extractable text")
            _emit(source_id)
            return

        store.set_status(source_id, "embedding")
        _emit(source_id)
        source = store.get_source(source_id)
        assert source is not None  # in-flight source can't vanish mid-ingest
        for index, chunk in enumerate(chunks):
            embedding, _source = await get_embedding(chunk)
            upsert_document(
                _doc_id(source_id, index),
                source["library"],
                chunk,
                {
                    "source_id": source_id,
                    "title": title,
                    "type": req.type,
                    "url": req.url,
                    "author": author,
                    "tags": source["tags"],
                    "chunk_index": index,
                },
                embedding,
            )

        store.set_status(source_id, "ready", chunk_count=len(chunks))
        _emit(source_id)
    except Exception as exc:  # noqa: BLE001 — surface any failure on the source
        logger.exception("library ingest failed for %s", source_id)
        store.set_status(source_id, "failed", error=str(exc))
        _emit(source_id)
