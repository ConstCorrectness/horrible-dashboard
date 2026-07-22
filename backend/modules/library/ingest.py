"""The ingest pipeline: source → (fetch) → chunk → embed → store, with live status.

Runs on the shared task queue (`enqueue_task("ingest_source", ...)` in the route),
so a slow fetch/embed never blocks the request or the `/ws` receive loop. Every
status transition is broadcast on the `library` channel so the panel updates live.

Media sources land in **two** spaces when CLIP is enabled: proxy text into the
library's own table (the app embedder), and a CLIP image vector into the
`<library>__clip` sibling. See modules/library/clip.py for why they can't share one.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.artifacts.pdftext import extract_pdf_text_from_path
from backend.modules.artifacts.store import artifact_path
from backend.modules.browser.fetch import fetch_readable, safe_fetch_bytes
from backend.modules.database.embeddings import get_embedding
from backend.modules.database.vectorstore import (
    clip_collection,
    init_db,
    upsert_document,
)
from backend.modules.library import store
from backend.modules.library.broadcast import publish_source
from backend.modules.library.chunking import chunk_text
from backend.modules.library.clip import MODEL_REPO as CLIP_MODEL_REPO
from backend.modules.library.clip import clip_enabled, encode_image
from backend.modules.library.extract import extract_article
from backend.modules.library.models import MEDIA_TYPES, IngestRequest
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


def _doc_id(source_id: str, index: int) -> str:
    return f"{source_id}#{index}"


def _media_proxy_text(req: IngestRequest, title: str | None) -> str:
    """The text that *stands in for* an image/video in the text vector store.

    Absent CLIP this is the entire searchable surface of a media source — everything
    a query could match on. Ordered most- to least-specific (own labels → caption →
    surrounding context), and deduped, because the same string often arrives as both
    `alt` and `title` and a doubled phrase would skew the vector toward it.

    **Only things that actually describe the picture go in.** Two near-misses that
    don't, both learned the hard way:

    - `title` must be a *caller-supplied* title, never the filename `add_source`
      falls back to. `baboon.jpg` or `240` embeds to a vector with no relationship
      to the image.
    - The page URL is provenance, not description. It's also *identical for every
      asset on a page*, so including it gives a whole gallery the same text vector.

    Either one yields noise that then competes with real signal on equal footing at
    search time — that's how a CLIP hit for "a photograph of a dog" lost to a baboon.
    Media with nothing real to say is better represented by **no text row at all**:
    CLIP can still index it by appearance, and without CLIP it is genuinely
    unfindable and gets rejected. Provenance still reaches the caller via metadata
    (`asset.page_url`), which is where it belongs.

    No bytes are fetched here: the asset is referenced by `src`, so the text side of
    ingest stays offline. CLIP embedding does fetch — see `clip_embed`.
    """
    asset = req.asset
    parts: list[str] = [title or "", asset.alt or "" if asset else ""]
    if asset:
        parts.append(asset.caption or "")
        parts.extend(asset.context)
    parts.append(req.text or "")

    seen: set[str] = set()
    kept: list[str] = []
    for part in parts:
        cleaned = " ".join(part.split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            kept.append(cleaned)
    return "\n".join(kept)


def _emit(source_id: str) -> None:
    source = store.get_source(source_id)
    if source is not None:
        publish_source(source)


async def clip_embed(req: IngestRequest) -> list[float] | None:
    """CLIP vector for a media source's asset, or None if it can't be produced.

    Never raises: a CLIP vector is a *bonus* space on top of the text proxy, so a
    dead image URL or an unreadable format must not fail a source whose text side
    already succeeded. Returns None and lets the caller carry on.

    Note this is the one place library ingest reaches the network for media — it
    fetches the actual pixels through the browser module's SSRF guard. See
    `safe_fetch_bytes`.
    """
    asset = req.asset
    if asset is None:
        return None
    # Video has no frame to encode without decoding it; the poster is the one still
    # the page already gives us. Real keyframe sampling would need ffmpeg.
    src = asset.src if req.type == "image" else asset.poster
    if not src:
        return None
    try:
        _final, raw = await safe_fetch_bytes(src)
        return await encode_image(raw)
    except Exception as exc:  # noqa: BLE001 — bonus space; never fail the source
        logger.warning("CLIP embed failed for %s: %s", src, exc)
        return None


async def ingest_source(source_id: str, req: IngestRequest) -> None:
    """Drive one source from `queued` to `ready` (or `failed`). Never raises —
    failures are recorded on the source and broadcast."""
    try:
        init_db()  # ensure the shared `documents` table exists
        # Fall back to the catalog row's title, not just `req.title`: the route
        # normalizes a missing title (a media asset's alt text, or its filename)
        # before inserting, and that resolved title is the one the source is known
        # by. Reading only `req.title` would put a null title in the chunk metadata
        # and drop it from a media asset's proxy text.
        row = store.get_source(source_id)
        title = req.title or (row["title"] if row else None)
        author = req.author

        if req.type == "blog":
            store.set_status(source_id, "fetching")
            _emit(source_id)
            # Through the browser module's SSRF guard — blog ingest fetches an
            # arbitrary user-supplied URL server-side, exactly the sink the guard
            # exists for.
            article = await fetch_readable(req.url or "")
            title = req.title or article.title
            author = req.author or article.author
            text = article.text
            store.update_meta(source_id, title=title, author=author)
        elif req.type == "pdf":
            store.set_status(source_id, "fetching")
            _emit(source_id)
            path = artifact_path(req.artifact_id or "")
            if path is None or not path.is_file():
                store.set_status(source_id, "failed", error="artifact blob missing")
                _emit(source_id)
                return
            extracted = extract_pdf_text_from_path(path, name=title or "")
            if isinstance(extracted, dict):
                store.set_status(source_id, "failed", error=extracted["error"])
                _emit(source_id)
                return
            text = extracted
        elif req.type == "page":
            store.set_status(source_id, "fetching")
            _emit(source_id)
            path = artifact_path(req.artifact_id or "")
            if path is None or not path.is_file():
                store.set_status(source_id, "failed", error="artifact blob missing")
                _emit(source_id)
                return
            html = path.read_text(encoding="utf-8", errors="replace")
            article = extract_article(html, req.url or "")
            title = req.title or article.title
            author = req.author or article.author
            text = article.text
            store.update_meta(source_id, title=title, author=author)
        elif req.type in MEDIA_TYPES:
            # `req.title`, deliberately — not the resolved `title` above, which may be
            # a filename `add_source` invented. See `_media_proxy_text`.
            text = _media_proxy_text(req, req.title)
        else:
            text = req.text or ""

        store.set_status(source_id, "chunking")
        _emit(source_id)
        size = int(get_value("library.chunkSize", 1000) or 1000)
        # A media source is one indivisible unit — its proxy text is a handful of
        # phrases about one asset, and splitting it would scatter an image across
        # chunks that each point back at the same `src`.
        chunks = (
            [text] if req.type in MEDIA_TYPES and text else chunk_text(text, size=size)
        )

        store.set_status(source_id, "embedding")
        _emit(source_id)

        # The visual space, when it's switched on. This is what lets an image with no
        # alt text be stored at all: without CLIP its only index is proxy text, so an
        # undescribed asset is unreachable and we reject it rather than bury it.
        clip_vector = (
            await clip_embed(req)
            if req.type in MEDIA_TYPES and clip_enabled()
            else None
        )

        if not chunks and clip_vector is None:
            store.set_status(
                source_id,
                "failed",
                error=(
                    "no text describes this media (no alt text, caption, or title) — "
                    "add a description, or enable CLIP visual search to index it by "
                    "appearance"
                    if req.type in MEDIA_TYPES
                    else "no extractable text"
                ),
            )
            _emit(source_id)
            return

        source = store.get_source(source_id)
        assert source is not None  # in-flight source can't vanish mid-ingest

        def meta(index: int, embed_model: str) -> dict[str, Any]:
            return {
                "source_id": source_id,
                "title": title,
                "type": req.type,
                "url": req.url,
                "author": author,
                "tags": source["tags"],
                "chunk_index": index,
                # Media hits are useless without the thing they point at: a search
                # result has to be able to render the image and link back to the page
                # it came from without a second lookup.
                "asset": req.asset.model_dump() if req.asset else None,
                # page/pdf hits open their stored blob in a viewer directly.
                "artifact_id": req.artifact_id,
                # Which model actually produced this vector. `get_embedding`
                # auto-selects one and silently falls back to a hash when the provider
                # is offline, so without this there's no way to tell a real embedding
                # from a placeholder after the fact.
                "embed_model": embed_model,
            }

        for index, chunk in enumerate(chunks):
            embedding, embed_model = await get_embedding(chunk)
            upsert_document(
                _doc_id(source_id, index),
                source["library"],
                chunk,
                meta(index, embed_model),
                embedding,
            )

        if clip_vector is not None:
            # Same doc id as chunk 0, in the sibling table. Sharing the id is what
            # makes deletes cascade — `delete_document` sweeps every table by id.
            # `text` here is only for display; nothing searches it in this space.
            upsert_document(
                _doc_id(source_id, 0),
                clip_collection(source["library"]),
                text or title or "",
                meta(0, CLIP_MODEL_REPO),
                clip_vector,
            )

        store.set_status(source_id, "ready", chunk_count=len(chunks))
        _emit(source_id)
    except Exception as exc:  # noqa: BLE001 — surface any failure on the source
        logger.exception("library ingest failed for %s", source_id)
        store.set_status(source_id, "failed", error=str(exc))
        _emit(source_id)
