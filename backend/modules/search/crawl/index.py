"""The `webindex` vector collection — the node's own crawled corpus.

A dedicated LanceDB collection, deliberately **not** a user library:

- a library is curated personal knowledge, and twenty thousand crawled doc chunks in
  `default` would swamp every `library.search`;
- `list_libraries()` projects `library_sources`, and crawled pages have no business
  appearing as catalog rows the user is invited to manage one by one;
- "forget the crawl" has to be one `delete_collection`, not a source-by-source sweep.

The **meta sidecar** (`webindex.meta.json`) is copied from symdex and is load-bearing
rather than decorative. A LanceDB collection's vector width is fixed when it is
created, so a crawl kicked off while Ollama is down would pin the whole collection to
384-dim hash-fallback vectors — permanently, and every later real embedding would then
blow up inside Arrow. Recording the model and width at build time is what lets us
refuse instead.

Doc ids are `web:<sha1(canonical_url)[:16]>#<chunk_index>`, so every chunk of a page
shares a prefix and re-indexing a changed page is one prefix delete.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "webindex"
_FALLBACK_PREFIX = "local-fallback"

# Documents per embed+write round. `upsert_documents` is a whole-table `merge_insert`
# whose cost is a function of table size, not batch size (~1.5s a call), so this
# buffer is flushed **across page boundaries** — a docs page is 3–10 chunks, and
# flushing per page would spend five minutes in Arrow to index two hundred pages.
EMBED_CHUNK = 256


def doc_prefix(canonical: str) -> str:
    """The shared id prefix for every chunk of one page."""
    digest = hashlib.sha1(canonical.encode("utf-8", "replace")).hexdigest()[:16]
    return f"web:{digest}#"


def doc_id(canonical: str, chunk_index: int) -> str:
    return f"{doc_prefix(canonical)}{chunk_index}"


def _meta_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "webindex.meta.json"


def read_meta() -> dict[str, Any]:
    try:
        return json.loads(_meta_path().read_text())
    except (OSError, ValueError):
        return {}


def write_meta(meta: dict[str, Any]) -> None:
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta))


def is_fallback(method: str) -> bool:
    """Whether an embedding came from the deterministic hash fallback.

    Cosine similarity over hash vectors is noise. Indexing them poisons the
    collection's width, and ranking with them is actively worse than not ranking at
    all — both paths refuse when this is true.
    """
    return method.startswith(_FALLBACK_PREFIX)


def index_dim() -> int | None:
    """The vector width this collection was built with, or None if never built."""
    dim = read_meta().get("dim")
    return int(dim) if dim else None


def index_model() -> str:
    return str(read_meta().get("embed_model") or "")


def note_build(embed_model: str, dim: int, docs: int) -> None:
    """Record what the collection now holds, after a successful write."""
    meta = read_meta()
    meta.update({"embed_model": embed_model, "dim": dim, "docs": docs})
    write_meta(meta)


def drift_error(embed_model: str, dim: int) -> str | None:
    """Why these vectors can't join the existing collection, or None if they can.

    A model change is not an error the user can ignore: the old vectors and the new
    ones no longer share a space, so mixing them silently degrades every search.
    """
    if is_fallback(embed_model):
        return (
            "the embedding provider is unavailable, so vectors would be hash "
            "fallbacks — start Ollama (or configure an embedding provider) and retry"
        )
    existing_dim = index_dim()
    if existing_dim is not None and existing_dim != dim:
        return (
            f"the web index holds {existing_dim}-dim vectors but the current model "
            f"produces {dim}-dim. Reindex the crawl to switch embedding model."
        )
    return None


def status() -> dict[str, Any]:
    """Docs held, model used, and whether a reindex is owed."""
    from backend.modules.database.vectorstore import init_db, list_documents

    init_db()
    _docs, total = list_documents(COLLECTION, 1, 0)
    meta = read_meta()
    return {
        "collection": COLLECTION,
        "docs": total,
        "embed_model": meta.get("embed_model") or None,
        "dim": meta.get("dim") or None,
        # An empty collection with recorded meta means someone dropped the table out
        # from under us; the crawl needs re-running to be useful again.
        "reindex_needed": bool(meta.get("embed_model")) and total == 0,
    }


def has_content() -> bool:
    """Whether the index can answer a query at all. Never raises — it is called on
    every `configured()` check."""
    try:
        return int(status()["docs"]) > 0
    except Exception:  # noqa: BLE001 — an unreadable index is an empty one
        logger.exception("couldn't read webindex status")
        return False


def forget_page(canonical: str) -> None:
    """Drop every chunk of one page (it changed, or it's gone)."""
    from backend.modules.database.vectorstore import delete_documents_with_prefix

    delete_documents_with_prefix(COLLECTION, doc_prefix(canonical))
