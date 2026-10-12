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
from dataclasses import dataclass
from typing import Any

from backend.modules.artifacts.pdftext import extract_pdf_text_from_path
from backend.modules.artifacts.store import artifact_path
from backend.modules.browser.fetch import (
    fetch_readable,
    safe_fetch_bytes,
    safe_fetch_html,
)
from backend.modules.database.embeddings import get_embeddings
from backend.modules.database.vectorstore import (
    clip_collection,
    init_db,
    replace_documents,
    upsert_document,
)
from backend.modules.library import store
from backend.modules.library.broadcast import publish_source
from backend.modules.library.chunking import chunk_markdown, chunk_text
from backend.modules.library.clip import MODEL_REPO as CLIP_MODEL_REPO
from backend.modules.library.clip import clip_enabled, encode_image
from backend.modules.library.extract import extract_article, extract_structured
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


def _is_fallback(method: str) -> bool:
    # Same prefix `search/crawl/index.is_fallback` checks; kept local so library
    # ingest does not import the crawler.
    return method.startswith("local-fallback")


Row = tuple[str, str, dict[str, Any], list[float]]


@dataclass
class Prepared:
    """A source that has been extracted, chunked and embedded but not yet written.

    The split exists for bulk producers. A site import files pages one after
    another into one library, and writing each page as it lands means one table
    write per page; holding a few prepared pages and committing them together turns
    that into one write per batch.
    """

    source_id: str
    library: str
    rows: list[Row]
    clip: Row | None = None


async def ingest_source(source_id: str, req: IngestRequest) -> None:
    """Drive one source from `queued` to `ready` (or `failed`). Never raises —
    failures are recorded on the source and broadcast."""
    prepared = await prepare_source(source_id, req)
    if prepared is not None:
        commit_prepared([prepared])


def commit_prepared(batch: list[Prepared]) -> None:
    """Write prepared sources, one table write per library, and mark them ready.

    Never raises. A failed write fails every source in the batch — their rows went
    in one call, so none of them can be claimed as stored.
    """
    by_library: dict[str, list[Prepared]] = {}
    for prepared in batch:
        by_library.setdefault(prepared.library, []).append(prepared)
    for library, group in by_library.items():
        try:
            # A predicate delete of each source's old rows plus an append — cost in
            # proportion to the batch. See `vectorstore.replace_documents`.
            replace_documents(
                library,
                [f"{p.source_id}#" for p in group],
                [row for p in group for row in p.rows],
            )
            for p in group:
                if p.clip is not None:
                    # Same doc id as chunk 0, in the sibling table. Sharing the id is
                    # what makes deletes cascade. `text` here is only for display;
                    # nothing searches it in this space.
                    doc_id, text, meta, vector = p.clip
                    upsert_document(
                        doc_id, clip_collection(library), text, meta, vector
                    )
        except Exception as exc:  # noqa: BLE001 — surface any failure on the sources
            logger.exception("library write failed for %s", library)
            for p in group:
                store.set_status(p.source_id, "failed", error=str(exc))
                _emit(p.source_id)
            continue
        for p in group:
            store.set_status(p.source_id, "ready", chunk_count=len(p.rows))
            _emit(p.source_id)


async def prepare_source(source_id: str, req: IngestRequest) -> Prepared | None:
    """Extract, chunk and embed one source. Returns None when it failed — the
    failure is already recorded on the source row and broadcast."""
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
        # Structured sources keep markdown + heading anchors; the rest stay plain.
        anchors: dict[str, str] = {}
        canonical: str | None = None

        if req.type == "blog":
            store.set_status(source_id, "fetching")
            _emit(source_id)
            # Through the browser module's SSRF guard — blog ingest fetches an
            # arbitrary user-supplied URL server-side, exactly the sink the guard
            # exists for.
            if req.structured:
                final_url, html = await safe_fetch_html(req.url or "")
                structured = extract_structured(html, final_url, req.content_selector)
                title = req.title or structured.title
                author = req.author or structured.author
                text, anchors = structured.markdown, structured.anchors
                canonical = structured.canonical
            else:
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
                return None
            extracted = extract_pdf_text_from_path(path, name=title or "")
            if isinstance(extracted, dict):
                store.set_status(source_id, "failed", error=extracted["error"])
                _emit(source_id)
                return None
            text = extracted
        elif req.type == "page":
            store.set_status(source_id, "fetching")
            _emit(source_id)
            path = artifact_path(req.artifact_id or "")
            if path is None or not path.is_file():
                store.set_status(source_id, "failed", error="artifact blob missing")
                _emit(source_id)
                return None
            html = path.read_text(encoding="utf-8", errors="replace")
            if req.structured:
                structured = extract_structured(
                    html, req.url or "", req.content_selector
                )
                title = req.title or structured.title
                author = req.author or structured.author
                text, anchors = structured.markdown, structured.anchors
                canonical = structured.canonical
            else:
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
        size = req.chunk_size or int(get_value("library.chunkSize", 1000) or 1000)
        # (text, section, anchor) per chunk; section/anchor only for structured.
        pieces: list[tuple[str, str | None, str | None]]
        if req.type in MEDIA_TYPES:
            # A media source is one indivisible unit — its proxy text is a handful of
            # phrases about one asset, and splitting it would scatter an image across
            # chunks that each point back at the same `src`.
            pieces = [(text, None, None)] if text else []
        elif req.structured:
            pieces = [
                (c.text, c.section or None, c.anchor)
                for c in chunk_markdown(
                    text, title=title or "", size=size, anchors=anchors
                )
            ]
        else:
            pieces = [(c, None, None) for c in chunk_text(text, size=size)]

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

        if not pieces and clip_vector is None:
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
            return None

        source = store.get_source(source_id)
        assert source is not None  # in-flight source can't vanish mid-ingest

        def meta(index: int, embed_model: str) -> dict[str, Any]:
            section = pieces[index][1] if index < len(pieces) else None
            anchor = pieces[index][2] if index < len(pieces) else None
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
                # Which model actually produced this vector. `get_embeddings`
                # auto-selects one and silently falls back to a hash when the provider
                # is offline, so without this there's no way to tell a real embedding
                # from a placeholder after the fact.
                "embed_model": embed_model,
                "section": section,
                "anchor": anchor,
                "canonical": canonical,
            }

        rows: list[Row] = []
        if pieces:
            # One batched embed; the write happens once per batch in
            # `commit_prepared`. `upsert_document` per chunk was a whole-table
            # `merge_insert` each — ~1.5s apiece, so a 300-chunk primer spent seven
            # minutes in Arrow.
            texts = [p[0] for p in pieces]
            vectors, embed_model = await get_embeddings(texts)
            if _is_fallback(embed_model) and req.strict_embeddings:
                store.set_status(
                    source_id,
                    "failed",
                    error=(
                        "the embedding provider is unavailable — refusing to store "
                        "hash-fallback vectors, which would pin this library's vector "
                        "width. Start the embedder and re-index."
                    ),
                )
                _emit(source_id)
                return None
            if len(vectors) != len(texts):
                raise RuntimeError("embedding returned the wrong number of vectors")
            rows = [
                (_doc_id(source_id, i), texts[i], meta(i, embed_model), vectors[i])
                for i in range(len(texts))
            ]

        clip_row: Row | None = None
        if clip_vector is not None:
            clip_row = (
                _doc_id(source_id, 0),
                text or title or "",
                meta(0, CLIP_MODEL_REPO),
                clip_vector,
            )
        return Prepared(
            source_id=source_id, library=source["library"], rows=rows, clip=clip_row
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure on the source
        logger.exception("library ingest failed for %s", source_id)
        store.set_status(source_id, "failed", error=str(exc))
        _emit(source_id)
        return None
