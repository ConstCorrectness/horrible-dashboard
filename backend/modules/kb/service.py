"""Knowledge-base operations that span the catalog and the content stores.

`store.py` owns the catalog rows; this module is what makes an item *mean*
something — its live index status (read off the library source), changing how much
of it is kept (`index_mode`), course progress, and retrieval across every library
the knowledge base has written to.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.kb import store
from backend.modules.kb.broadcast import publish_item
from backend.modules.kb.models import (
    Item,
    Passage,
    Progress,
    SearchRequest,
    SearchResponse,
    TreeNode,
)
from backend.modules.search.canonical import host_of
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

# Single saved pages share one library; a site import gets `kb-<host>` of its own
# (see `site_library`). One table per bookmark host would be a hundred tiny tables
# for the search to fan out across.
PAGES_LIBRARY = "kb-pages"
KB_LIBRARY_PREFIX = "kb-"
DEFAULT_CHUNK_SIZE = 1200

_LIBRARY_STATUS = {
    "queued": "queued",
    "fetching": "indexing",
    "chunking": "indexing",
    "embedding": "indexing",
    "ready": "ready",
    "failed": "failed",
}
_DOCSET_STATUS = {
    "queued": "queued",
    "crawling": "indexing",
    "ready": "ready",
    "failed": "failed",
}

# Background archive captures, so a second request for the same item can't race.
_pending: dict[str, asyncio.Task[None]] = {}


def site_library(url: str) -> str:
    host = host_of(url) or "site"
    return f"{KB_LIBRARY_PREFIX}{host}"


def chunk_size() -> int:
    try:
        return max(400, min(int(get_value("kb.chunkSize", DEFAULT_CHUNK_SIZE)), 4000))
    except (TypeError, ValueError):
        return DEFAULT_CHUNK_SIZE


def ingest_options(url: str | None, selector: str | None = None) -> dict[str, Any]:
    """`IngestRequest` fields every knowledge-base ingest uses.

    `strict_embeddings` is the important one: a knowledge-base library is built in
    bulk, and one hash-fallback row written while the embedder was down would pin a
    new table to 384 dims. The content selector comes from the site's profile, when
    one matches the URL.
    """
    if selector is None and url:
        from backend.modules.kb.profiles import match

        hit = match(url)
        if hit is not None:
            selector = hit[0].get("content_selector")
    return {
        "structured": True,
        "chunk_size": chunk_size(),
        "content_selector": selector or None,
        "strict_embeddings": True,
    }


# ---- status, progress ---------------------------------------------------------


def _source_states(source_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not source_ids:
        return {}
    from backend.modules.library.store import get_db_conn, init_library_db

    init_library_db()
    out: dict[str, dict[str, Any]] = {}
    with get_db_conn() as conn:
        for start in range(0, len(source_ids), 500):
            part = source_ids[start : start + 500]
            marks = ",".join("?" * len(part))
            for r in conn.execute(
                f"SELECT id, status, error, chunk_count FROM library_sources "
                f"WHERE id IN ({marks})",
                part,
            ):
                out[r["id"]] = {
                    "status": r["status"],
                    "error": r["error"],
                    "chunk_count": int(r["chunk_count"] or 0),
                }
    return out


def _docset_states(set_ids: list[str]) -> dict[str, str]:
    if not set_ids:
        return {}
    from backend.modules.docviewer import store as docstore

    out: dict[str, str] = {}
    for sid in set(set_ids):
        doc_set = docstore.get_set(sid)
        if doc_set is not None:
            out[sid] = doc_set["status"]
    return out


def _progress(item_ids: list[str]) -> dict[str, tuple[Progress, int]]:
    """Leaf-lesson completion and direct child count for each parent id."""
    out: dict[str, tuple[Progress, int]] = {}
    for item_id in item_ids:
        below = store.descendants(item_id)
        if not below:
            continue
        parents = {d["parent_id"] for d in below}
        leaves = [d for d in below if d["id"] not in parents]
        children = sum(1 for d in below if d["parent_id"] == item_id)
        out[item_id] = (
            Progress(
                done=sum(1 for d in leaves if d["status"] == "done"), total=len(leaves)
            ),
            children,
        )
    return out


# Row fields copied straight onto `Item`; the derived ones are computed in `enrich`.
_DERIVED = {"index_status", "index_error", "chunk_count", "progress", "child_count"}
_ROW_FIELDS = set(Item.model_fields) - _DERIVED


def enrich(rows: list[dict[str, Any]], *, with_progress: bool = True) -> list[Item]:
    """Catalog rows → `Item`s with live index status, chunk count and progress."""
    sources = _source_states([r["source_id"] for r in rows if r["source_id"]])
    docsets = _docset_states([r["docset_id"] for r in rows if r["docset_id"]])
    progress = (
        _progress([r["id"] for r in rows if r["kind"] in ("course", "chapter", "site")])
        if with_progress
        else {}
    )
    items: list[Item] = []
    for r in rows:
        status = "none"
        error = r["index_error"]
        chunks = 0
        src = sources.get(r["source_id"] or "")
        if src is not None:
            status = _LIBRARY_STATUS.get(src["status"], "indexing")
            error = src["error"] or error
            chunks = src["chunk_count"]
        elif r["docset_id"] and r["docset_id"] in docsets and not r["parent_id"]:
            status = _DOCSET_STATUS.get(docsets[r["docset_id"]], "indexing")
        elif r["index_mode"] != "link":
            status = "failed" if error else "queued"
        if "ai-input=no" in (r.get("content_signal") or "") and src is None:
            status = "skipped"
        prog, kids = progress.get(r["id"], (None, 0))
        items.append(
            Item(
                **{k: v for k, v in r.items() if k in _ROW_FIELDS},
                index_status=status,
                index_error=error,
                chunk_count=chunks,
                progress=prog,
                child_count=kids,
            )
        )
    return items


def item_model(item_id: str) -> Item | None:
    row = store.get_item(item_id)
    return enrich([row])[0] if row else None


def emit(item_id: str) -> None:
    item = item_model(item_id)
    if item is not None:
        publish_item(item.model_dump())


def tree(item_id: str) -> TreeNode | None:
    root = store.get_item(item_id)
    if root is None:
        return None
    below = store.descendants(item_id)
    enriched = {i.id: i for i in enrich([root, *below])}
    nodes = {i: TreeNode(item=it) for i, it in enriched.items()}
    for d in below:
        parent = nodes.get(d["parent_id"])
        if parent is not None:
            parent.children.append(nodes[d["id"]])
    for node in nodes.values():
        node.children.sort(key=lambda n: (n.item.ordinal, n.item.added_at))
    return nodes[item_id]


# ---- content -------------------------------------------------------------------


def drop_content(row: dict[str, Any], *, include_site: bool = True) -> None:
    """Remove the indexed text an item owns — and, for an import's root item, the
    whole imported site (archives, sources, the doc set).

    Single-page archives are left on disk: the artifact store is content-addressed,
    so the same blob may back a research capture of the same page.
    """
    source_id = row.get("source_id")
    if source_id:
        from backend.modules.library import store as library_store
        from backend.modules.library.routes import delete_source_chunks

        source = library_store.get_source(source_id)
        if source is not None:
            delete_source_chunks(source)
            library_store.delete_source(source_id)
    if include_site and row.get("docset_id"):
        from backend.modules.docviewer import crawl
        from backend.modules.docviewer import store as docstore
        from backend.modules.docviewer.routes import remove_set_content

        doc_set = docstore.get_set(row["docset_id"])
        recipe = (doc_set or {}).get("recipe") or {}
        if doc_set is not None and recipe.get("kb_root_item_id") == row["id"]:
            crawl.cancel_crawl(doc_set["id"])
            remove_set_content(doc_set["id"])


async def set_index_mode(item_id: str, mode: str) -> dict[str, Any]:
    """Change how much of an item is kept, and start whatever ingest that needs.

    Returns the updated catalog row immediately; ingest status arrives on the
    `library` channel (text) or via `emit` when a background capture finishes
    (archive). `site` is a crawl, started by `imports.start_import` instead.
    """
    row = store.get_item(item_id)
    if row is None:
        raise KeyError(item_id)
    if mode == "site":
        raise ValueError("use an import (POST /api/kb/imports) to index a whole site")
    if mode != "link" and not row["url"]:
        raise ValueError("only items with a URL can be indexed")

    previous = _pending.pop(item_id, None)
    if previous is not None and not previous.done():
        previous.cancel()
    drop_content(row)
    store.update_item(
        item_id,
        index_mode=mode,
        index_error=None,
        source_id=None,
        library=None,
        artifact_id=None if mode != "archive" else row["artifact_id"],
    )

    if mode == "text":
        from backend.modules.library.models import IngestRequest
        from backend.modules.library.routes import add_source

        source = await add_source(
            IngestRequest(
                type="blog",
                library=PAGES_LIBRARY,
                url=row["url"],
                title=row["title"] if row["title"] != row["url"] else None,
                tags=row["tags"],
                **ingest_options(row["url"]),
            )
        )
        store.update_item(item_id, source_id=source.id, library=PAGES_LIBRARY)
    elif mode == "archive":
        _pending[item_id] = asyncio.create_task(_archive(item_id, row["url"]))

    updated = store.get_item(item_id)
    assert updated is not None
    return updated


async def _archive(item_id: str, url: str) -> None:
    from backend.modules.research.service import capture_url

    try:
        captured = await capture_url(
            url,
            library=PAGES_LIBRARY,
            tags=["kb"],
            ingest_options=ingest_options(url),
        )
        store.update_item(
            item_id,
            artifact_id=captured["artifact"]["id"],
            source_id=captured["source"]["id"],
            library=PAGES_LIBRARY,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — recorded on the item, not raised
        logger.info("kb: archive of %s failed: %s", url, exc)
        store.update_item(item_id, index_error=f"capture failed: {exc}")
    finally:
        _pending.pop(item_id, None)
        emit(item_id)


# ---- retrieval -----------------------------------------------------------------


def kb_libraries() -> list[str]:
    """Every library a knowledge-base item has content in."""
    store.init_kb_db()
    with store.get_db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT library FROM kb_items WHERE library IS NOT NULL"
        ).fetchall()
    return sorted(str(r["library"]) for r in rows)


def _scope(req: SearchRequest) -> set[str] | None:
    """Item ids the search may return, or None for "everything in the KB"."""
    ids: set[str] | None = None
    if req.collections:
        ids = store.collection_item_ids(req.collections)
    if req.item_id:
        below = {req.item_id, *(d["id"] for d in store.descendants(req.item_id))}
        ids = below if ids is None else ids & below
    return ids


def _items_by_source(source_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not source_ids:
        return {}
    store.init_kb_db()
    out: dict[str, dict[str, Any]] = {}
    with store.get_db_conn() as conn:
        for start in range(0, len(source_ids), 500):
            part = source_ids[start : start + 500]
            marks = ",".join("?" * len(part))
            for r in conn.execute(
                f"SELECT id, source_id, url, docpage_id, artifact_id, title "
                f"FROM kb_items WHERE source_id IN ({marks})",
                part,
            ):
                out[r["source_id"]] = dict(r)
    return out


class SearchUnavailable(RuntimeError):
    """The embedder is down: a query vector would be a hash, and ranking by it
    would be noise presented as an answer."""


async def search(req: SearchRequest) -> SearchResponse:
    """Semantic search across every knowledge-base library.

    The libraries are separate LanceDB tables, so each is searched on its own and
    the lists are merged. Cosine scores **are** comparable across tables when the
    same model produced every vector (checked from chunk metadata) — then the merge
    is by score. When the models differ, scores share no scale and the lists are
    fused by rank instead, exactly as the library's text+CLIP search does.

    A scoped search (collection or item) over-fetches and filters, since LanceDB
    holds no catalog columns to pre-filter on. At most three passages per source
    are returned, so one long primer can't take every slot.
    """
    from backend.modules.database.embeddings import get_embedding
    from backend.modules.database.vectorstore import search_documents
    from backend.modules.search.fusion import fuse

    scope = _scope(req)
    libraries = kb_libraries()
    if scope is not None:
        scoped_rows = [store.get_item(i) for i in scope]
        libraries = sorted({r["library"] for r in scoped_rows if r and r["library"]})
    if not libraries:
        return SearchResponse(query=req.query, passages=[], libraries=[])

    vector, method = await get_embedding(req.query, input_type="query")
    if method.startswith("local-fallback"):
        raise SearchUnavailable(
            "the embedding provider is unavailable, so the query can't be embedded "
            "in the same space as the stored passages"
        )

    fetch = req.limit * (6 if scope is not None else 3)
    per_library: list[list[dict[str, Any]]] = []
    models: set[str] = set()
    for library in libraries:
        rows = await asyncio.to_thread(search_documents, library, vector, fetch)
        per_library.append(rows)
        models.update(
            str(r["metadata"].get("embed_model") or "") for r in rows if r["metadata"]
        )

    by_source = _items_by_source(
        sorted(
            {
                str(r["metadata"].get("source_id"))
                for rows in per_library
                for r in rows
                if r["metadata"].get("source_id")
            }
        )
    )

    fused_by_rank = len(models - {""}) > 1
    if fused_by_rank:
        rank = fuse([[r["id"] for r in rows] for rows in per_library])
        merged = sorted(
            (r for rows in per_library for r in rows),
            key=lambda r: rank.get(r["id"], 0.0),
            reverse=True,
        )
    else:
        merged = sorted(
            (r for rows in per_library for r in rows),
            key=lambda r: float(r["score"]),
            reverse=True,
        )

    passages: list[Passage] = []
    per_source: dict[str, int] = {}
    for r in merged:
        meta = r["metadata"] or {}
        source_id = str(meta.get("source_id") or "")
        item = by_source.get(source_id)
        if item is None:
            continue  # a row in a kb library no catalog item owns any more
        if scope is not None and item["id"] not in scope:
            continue
        if per_source.get(source_id, 0) >= 3:
            continue
        per_source[source_id] = per_source.get(source_id, 0) + 1
        url = meta.get("canonical") or item["url"] or meta.get("url")
        anchor = meta.get("anchor")
        passages.append(
            Passage(
                item_id=item["id"],
                source_id=source_id,
                title=str(meta.get("title") or item["title"] or url or ""),
                section=meta.get("section"),
                url=url,
                link=f"{url}#{anchor}" if url and anchor else url,
                docpage_id=item["docpage_id"],
                artifact_id=item["artifact_id"] or meta.get("artifact_id"),
                library=r["collection"],
                score=float(r["score"]),
                text=r["text"],
            )
        )
        if len(passages) >= req.limit:
            break
    return SearchResponse(
        query=req.query,
        passages=passages,
        libraries=libraries,
        fused_by_rank=fused_by_rank,
    )
