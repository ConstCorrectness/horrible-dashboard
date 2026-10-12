import json
import time
from pathlib import Path
from typing import Any

try:
    import lancedb
except ImportError:
    lancedb = None
from backend import paths


def get_db_path() -> Path:
    data_dir = paths.data_dir()
    return data_dir / "lancedb"


def _get_db():
    if lancedb is None:
        return None
    db_path = get_db_path()
    db_path.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(db_path))


# Suffix for a collection's **sibling** table — a second vector space over the same
# documents, keyed by the same doc ids. Today that's `<library>__clip` (CLIP image
# vectors; see modules/library/clip.py). A sibling can't just be another collection:
# LanceDB fixes vector width per table, and CLIP's 512 dims don't fit the text
# embedder's column. Siblings are internal plumbing, not user-facing collections, so
# the "all collections" sweeps below skip them — otherwise the database panel would
# double-count every media row and list `default__clip` as if it were a library.
SIBLING_SEP = "__"
CLIP_SUFFIX = f"{SIBLING_SEP}clip"


def clip_collection(collection: str) -> str:
    """The CLIP sibling table name for `collection`."""
    return f"{collection}{CLIP_SUFFIX}"


def is_sibling_table(name: str) -> bool:
    """True for an internal sibling table (see SIBLING_SEP)."""
    return SIBLING_SEP in name


def init_db() -> None:
    """Initialize the database. LanceDB handles this automatically on connect/create_table."""
    if lancedb is None:
        return
    _get_db()


class DimensionMismatch(ValueError):
    """A vector's width doesn't match the collection it's being written to."""


def _table_dim(tbl: Any) -> int | None:
    """The vector width a table was created with, or None if undeterminable."""
    try:
        return int(tbl.schema.field("vector").type.list_size)
    except Exception:  # noqa: BLE001 — schema shape varies across LanceDB versions
        return None


def upsert_documents(
    collection: str,
    rows: list[tuple[str, str, dict[str, Any], list[float]]],
) -> None:
    """Upsert many documents in **one** write.

    `merge_insert` is a whole-table operation — LanceDB rewrites data files and
    recomputes the merge on every call — so its cost is a function of table size, not
    of how many rows you hand it. Measured on a ~5k-row table it was **~1.5s per
    call**, which is why a bulk index built one row at a time crawled: the embedder
    answered in 65ms and then the process sat in Arrow for two seconds. Batching turns
    N whole-table merges into one, and the per-document cost effectively disappears.

    Rows are `(doc_id, text, metadata, embedding)`. Same width check as the single-row
    path, applied once to the batch.
    """
    if not rows:
        return
    db = _get_db()
    now = time.time()
    data = [
        {
            "id": doc_id,
            "text": text,
            "metadata": json.dumps(metadata),
            "vector": embedding,
            "created_at": now,
        }
        for doc_id, text, metadata, embedding in rows
    ]
    widths = {len(embedding) for _i, _t, _m, embedding in rows}
    if len(widths) > 1:
        raise DimensionMismatch(
            f"batch mixes vector widths {sorted(widths)}; a collection holds one width"
        )
    width = widths.pop()

    if collection in db.table_names():
        tbl = db.open_table(collection)
        expected = _table_dim(tbl)
        if expected is not None and width != expected:
            raise DimensionMismatch(
                f"collection {collection!r} holds {expected}-dim vectors but got "
                f"{width}. A collection's width is fixed when it is created; "
                "delete and re-ingest it to switch embedding model."
            )
        tbl.merge_insert(
            "id"
        ).when_matched_update_all().when_not_matched_insert_all().execute(data)
    else:
        db.create_table(collection, data=data)


def replace_documents(
    collection: str,
    id_prefixes: list[str],
    rows: list[tuple[str, str, dict[str, Any], list[float]]],
) -> None:
    """Drop every row under `id_prefixes`, then **append** `rows`.

    The bulk-ingest write. `upsert_documents` is a `merge_insert`, which rewrites
    data files across the whole table on every call — its cost tracks table size,
    so a site import that writes page after page into a growing 40k-row table gets
    slower with every page. Here the rows being replaced are known up front (a
    source's chunks all share the `<source_id>#` prefix), so a predicate delete plus
    an append does the same job in time proportional to the batch.
    """
    if not rows and not id_prefixes:
        return
    db = _get_db()
    exists = collection in db.table_names()
    if exists:
        tbl = db.open_table(collection)
        for prefix in id_prefixes:
            tbl.delete(_like_prefix(prefix))
    if not rows:
        return
    widths = {len(embedding) for _i, _t, _m, embedding in rows}
    if len(widths) > 1:
        raise DimensionMismatch(
            f"batch mixes vector widths {sorted(widths)}; a collection holds one width"
        )
    width = widths.pop()
    now = time.time()
    data = [
        {
            "id": doc_id,
            "text": text,
            "metadata": json.dumps(metadata),
            "vector": embedding,
            "created_at": now,
        }
        for doc_id, text, metadata, embedding in rows
    ]
    if not exists:
        db.create_table(collection, data=data)
        return
    expected = _table_dim(tbl)
    if expected is not None and width != expected:
        raise DimensionMismatch(
            f"collection {collection!r} holds {expected}-dim vectors but got "
            f"{width}. A collection's width is fixed when it is created; "
            "delete and re-ingest it to switch embedding model."
        )
    tbl.add(data)


def upsert_document(
    doc_id: str,
    collection: str,
    text: str,
    metadata: dict[str, Any],
    embedding: list[float],
) -> None:
    """Upsert a document into the database.

    A collection's vector width is fixed by its **first** row (LanceDB infers the
    schema on create) and can't change afterwards. That's easy to violate by
    accident: `embeddings.get_embedding` auto-selects whichever embedding model the
    provider happens to offer — 384, 768, or 1024 dims — and silently falls back to a
    384-dim hash when the provider is offline. So a library whose first document
    landed while Ollama was down is pinned to 384 forever, and the next real
    embedding would otherwise fail deep inside Arrow with an unreadable error. Check
    up front and say what actually happened.
    """
    db = _get_db()
    data = [
        {
            "id": doc_id,
            "text": text,
            "metadata": json.dumps(metadata),
            "vector": embedding,
            "created_at": time.time(),
        }
    ]

    if collection in db.table_names():
        tbl = db.open_table(collection)
        expected = _table_dim(tbl)
        if expected is not None and len(embedding) != expected:
            raise DimensionMismatch(
                f"collection {collection!r} holds {expected}-dim vectors but got "
                f"{len(embedding)}. A collection's width is fixed when it is created; "
                "delete and re-ingest it to switch embedding model."
            )
        # LanceDB merge insert
        tbl.merge_insert(
            "id"
        ).when_matched_update_all().when_not_matched_insert_all().execute(data)
    else:
        db.create_table(collection, data=data)


def delete_document(doc_id: str) -> bool:
    """Delete a document by ID across all collections."""
    db = _get_db()
    deleted = False
    for tbl_name in db.table_names():
        tbl = db.open_table(tbl_name)
        # Lancedb delete uses a sql-like where clause
        try:
            # check if it exists first to return correct boolean
            res = tbl.search().where(f"id = '{doc_id}'").limit(1).to_list()
            if res:
                tbl.delete(f"id = '{doc_id}'")
                deleted = True
        except Exception:
            pass
    return deleted


def delete_documents_with_prefix(collection: str, id_prefix: str) -> None:
    """Delete every document whose id starts with `id_prefix` (used by the symdex
    per-kind rebuild — ids are ours, so a LIKE predicate is safe)."""
    db = _get_db()
    if collection not in db.table_names():
        return
    try:
        db.open_table(collection).delete(_like_prefix(id_prefix))
    except Exception:  # noqa: BLE001 — dialect quirks; fall back to per-id deletes
        tbl = db.open_table(collection)
        rows = tbl.search().limit(len(tbl)).to_list()
        for r in rows:
            row_id = str(r.get("id", ""))
            if row_id.startswith(id_prefix):
                tbl.delete("id = '{}'".format(row_id.replace("'", "''")))


def _like_prefix(id_prefix: str) -> str:
    escaped = id_prefix.replace("'", "''").replace("%", r"\%").replace("_", r"\_")
    return f"id LIKE '{escaped}%' ESCAPE '\\'"


def list_documents_with_prefix(collection: str, id_prefix: str) -> list[dict[str, Any]]:
    """Every document in one collection whose id starts with `id_prefix`.

    A predicate scan on the id column, not "load the table and filter in Python" —
    the difference matters once a collection is a whole imported site (tens of
    thousands of rows) and the caller wants one source's few hundred chunks.
    """
    db = _get_db()
    if db is None or collection not in db.table_names():
        return []
    tbl = db.open_table(collection)
    try:
        rows = tbl.search().where(_like_prefix(id_prefix)).limit(len(tbl)).to_list()
    except Exception:  # noqa: BLE001 — dialect quirks; fall back to a filtered scan
        rows = [
            r
            for r in tbl.search().limit(len(tbl)).to_list()
            if str(r.get("id", "")).startswith(id_prefix)
        ]
    return [
        {
            "id": r["id"],
            "collection": collection,
            "text": r["text"],
            "metadata": json.loads(r["metadata"]),
            "created_at": r.get("created_at", 0),
        }
        for r in rows
    ]


def delete_collection(collection: str) -> int:
    """Delete every document in a collection (used for a full reindex)."""
    db = _get_db()
    if collection in db.table_names():
        tbl = db.open_table(collection)
        count = len(tbl)
        db.drop_table(collection)
        return count
    return 0


def search_documents(
    collection: str, query_embedding: list[float], limit: int
) -> list[dict[str, Any]]:
    """Perform a semantic search in a given collection using cosine similarity."""
    db = _get_db()
    if collection not in db.table_names():
        return []

    tbl = db.open_table(collection)
    try:
        # cosine distance is default in LanceDB if we specify it or we can just use search()
        results = tbl.search(query_embedding).metric("cosine").limit(limit).to_list()
    except Exception:
        return []

    out = []
    for r in results:
        out.append(
            {
                "id": r["id"],
                "collection": collection,
                "text": r["text"],
                "metadata": json.loads(r["metadata"]),
                "score": 1.0
                - float(
                    r.get("_distance", 0.0)
                ),  # LanceDB returns distance, convert to similarity
            }
        )
    return out


ANN_MIN_ROWS = 20_000


def ensure_ann_index(collection: str, min_rows: int = ANN_MIN_ROWS) -> str:
    """Build (or refresh) a cosine ANN index once a table is big enough to need one.

    Nothing else in the app ever builds an index, so every search is a flat scan.
    That is the right call for a few thousand rows and stops being one for a
    knowledge-base site import — the aman.ai primers alone are ~45k chunks. Below
    `min_rows` this does nothing: training an index on a small table costs more than
    it saves and can lower recall.

    IVF_HNSW_SQ rather than LanceDB's IVF_PQ default: PQ's recall loss is what makes
    the *right* passage miss the top 8, and that is the failure retrieval for an
    agent can least afford. Rows written after the build are still found — LanceDB
    scans unindexed fragments flat — and `optimize()` folds them in once they are a
    meaningful share of the table.

    Returns what happened: `skipped`, `built`, `optimized` or `current`.
    """
    db = _get_db()
    if db is None or collection not in db.table_names():
        return "skipped"
    tbl = db.open_table(collection)
    rows = len(tbl)
    if rows < min_rows:
        return "skipped"
    existing = [
        idx for idx in tbl.list_indices() if "vector" in list(idx.columns or [])
    ]
    if not existing:
        tbl.create_index(
            metric="cosine",
            vector_column_name="vector",
            index_type="IVF_HNSW_SQ",
            replace=True,
        )
        return "built"
    try:
        stats = tbl.index_stats(existing[0].name)
        unindexed = int(getattr(stats, "num_unindexed_rows", 0) or 0)
    except Exception:  # noqa: BLE001 — stats shape varies across versions
        unindexed = 0
    if unindexed > rows // 10:
        tbl.optimize()
        return "optimized"
    return "current"


def list_documents(
    collection: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve documents optionally filtered by collection with total count."""
    db = _get_db()
    docs = []
    total = 0

    if collection:
        if collection in db.table_names():
            tbl = db.open_table(collection)
            total = len(tbl)
            # Fetch all and sort in python (fine for moderate sizes, or use lancedb query)
            results = tbl.search().limit(total).to_list()
            results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            page = results[offset : offset + limit]
            for r in page:
                docs.append(
                    {
                        "id": r["id"],
                        "collection": collection,
                        "text": r["text"],
                        "metadata": json.loads(r["metadata"]),
                        "created_at": r.get("created_at", 0),
                    }
                )
    else:
        # Collect from all tables — skipping siblings, whose rows are the *same*
        # documents in a second vector space and would list as duplicate ids.
        all_results = []
        for tbl_name in db.table_names():
            if is_sibling_table(tbl_name):
                continue
            tbl = db.open_table(tbl_name)
            t_len = len(tbl)
            total += t_len
            res = tbl.search().limit(t_len).to_list()
            for r in res:
                r["_collection"] = tbl_name
            all_results.extend(res)

        all_results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        page = all_results[offset : offset + limit]
        for r in page:
            docs.append(
                {
                    "id": r["id"],
                    "collection": r["_collection"],
                    "text": r["text"],
                    "metadata": json.loads(r["metadata"]),
                    "created_at": r.get("created_at", 0),
                }
            )

    return docs, total


def get_db_stats() -> dict[str, Any]:
    """Get database statistics (path, disk size, record counts, active collections).

    Sibling tables are excluded from the counts: they hold the same documents in a
    second vector space, so counting them would report twice as many documents as
    the user actually has. Their bytes still show in `size_bytes` (that's real disk).
    """
    db_path = get_db_path()
    size_bytes = 0
    if db_path.exists():
        for f in db_path.glob("**/*"):
            if f.is_file():
                size_bytes += f.stat().st_size

    db = _get_db()
    total_docs = 0
    collections = []

    for tbl_name in db.table_names():
        if is_sibling_table(tbl_name):
            continue
        tbl = db.open_table(tbl_name)
        count = len(tbl)
        total_docs += count
        collections.append({"name": tbl_name, "count": count})

    return {
        "db_path": str(db_path),
        "size_bytes": size_bytes,
        "num_documents": total_docs,
        "collections": collections,
    }
