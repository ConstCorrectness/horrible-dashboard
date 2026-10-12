"""The knowledge-base catalog: `kb_items`, `kb_collections` and their join, in `app.db`.

A **catalog**, not a content store. An item is a thing the user wants to keep track
of — a bookmark, a primer, a course and its lessons — with the human-facing state a
library source has no room for: reading status, notes, a rating, course progress,
collections. Content, when an item has any, lives where it already lives elsewhere
and is *linked*:

- text and vectors → a `library_sources` row (`source_id`) in a library table;
- an offline archive → an artifact (`artifact_id`), or a doc-set page
  (`docset_id` / `docpage_id`) when the item came from a site import.

`index_mode` is the user's choice of how much of that to keep (`link`, `text`,
`archive`, `site`); `index_status` is **not stored** — it is read off the linked
library source at query time, so it can never drift from what the ingest pipeline
actually did (see `library-ingest-does-not-raise`: the source row is the truth).

**Browser bookmarks live here.** `browser_bookmarks` rows are copied in once as
`kind='bookmark'`, `starred=1`, keeping their ids so the browser's existing routes
keep addressing them; `browser.store`'s bookmark functions are a façade over this
module from then on. `starred` is what the browser's bookmark strip shows — not
"every item", which after one site import would be 150 primers in a toolbar.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Generator, Iterable

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.search.canonical import canonical_url

KINDS = (
    "bookmark",
    "article",
    "primer",
    "course",
    "chapter",
    "lesson",
    "paper",
    "video",
    "site",
    "note",
)
STATUSES = ("to_read", "reading", "done", "archived")
INDEX_MODES = ("link", "text", "archive", "site")

# Every column a caller may set. `id`, timestamps and `canonical_url` are derived.
_WRITABLE = (
    "kind",
    "url",
    "title",
    "description",
    "author",
    "tags",
    "status",
    "rating",
    "notes",
    "parent_id",
    "ordinal",
    "index_mode",
    "index_error",
    "library",
    "source_id",
    "docset_id",
    "docpage_id",
    "artifact_id",
    "content_signal",
    "starred",
    "profile_id",
    "alias_url",
)


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_initialized: set[str] = set()


def init_kb_db() -> None:
    """Create the tables (idempotent) and run the one-time bookmark migration.

    Memoized per database path: every store call goes through here, and re-running
    four `CREATE … IF NOT EXISTS` plus a migration probe on each one is pure waste.
    Keyed by path rather than a bare flag because tests point `HORRIBLE_DATA_DIR` at
    a fresh directory per test.
    """
    path = str(ensure_app_db_dir())
    if path in _initialized:
        return
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_items (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'bookmark',
                url TEXT,
                canonical_url TEXT,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                author TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'to_read',
                rating INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                parent_id TEXT,
                ordinal INTEGER NOT NULL DEFAULT 0,
                index_mode TEXT NOT NULL DEFAULT 'link',
                index_error TEXT,
                library TEXT,
                source_id TEXT,
                docset_id TEXT,
                docpage_id TEXT,
                artifact_id TEXT,
                content_signal TEXT,
                starred INTEGER NOT NULL DEFAULT 0,
                profile_id TEXT,
                alias_url TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                opened_at TIMESTAMP,
                completed_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_items_canonical "
            "ON kb_items(canonical_url) WHERE canonical_url IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_items_parent "
            "ON kb_items(parent_id, ordinal)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_items_docset ON kb_items(docset_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_items_alias ON kb_items(alias_url)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_item_collections (
                item_id TEXT NOT NULL,
                collection_id TEXT NOT NULL,
                PRIMARY KEY (item_id, collection_id)
            )
            """
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        _migrate_bookmarks(conn)
    _initialized.add(path)


def _migrate_bookmarks(conn: sqlite3.Connection) -> None:
    """Copy `browser_bookmarks` into the catalog once. Non-destructive: the old
    table is left in place, it just stops being read."""
    done = conn.execute(
        "SELECT value FROM kb_meta WHERE key = 'bookmarks_migrated'"
    ).fetchone()
    if done is not None:
        return
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='browser_bookmarks'"
    ).fetchone()
    if has_table is not None:
        for r in conn.execute("SELECT * FROM browser_bookmarks").fetchall():
            conn.execute(
                """
                INSERT OR IGNORE INTO kb_items
                    (id, kind, url, canonical_url, title, tags, starred, added_at)
                VALUES (?, 'bookmark', ?, ?, ?, ?, 1, ?)
                """,
                (
                    r["id"],
                    r["url"],
                    canonical_url(r["url"]) or None,
                    r["title"] or r["url"],
                    r["tags"] or "[]",
                    r["added_at"],
                ),
            )
    conn.execute(
        "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('bookmarks_migrated', '1')"
    )


# ---- rows ----------------------------------------------------------------------


def _collections_by_item(
    conn: sqlite3.Connection, ids: Iterable[str]
) -> dict[str, list[str]]:
    ids = list(ids)
    if not ids:
        return {}
    out: dict[str, list[str]] = {i: [] for i in ids}
    # Chunked: SQLite caps bound parameters (999 on older builds).
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ",".join("?" * len(part))
        for r in conn.execute(
            f"""
            SELECT ic.item_id, c.name FROM kb_item_collections ic
            JOIN kb_collections c ON c.id = ic.collection_id
            WHERE ic.item_id IN ({marks}) ORDER BY c.name
            """,
            part,
        ):
            out[r["item_id"]].append(r["name"])
    return out


def _item_row(r: Any, collections: list[str]) -> dict[str, Any]:
    def ts(key: str) -> str | None:
        return str(r[key]) if r[key] else None

    return {
        "id": r["id"],
        "kind": r["kind"],
        "url": r["url"],
        "canonical_url": r["canonical_url"],
        "title": r["title"],
        "description": r["description"],
        "author": r["author"],
        "tags": json.loads(r["tags"] or "[]"),
        "status": r["status"],
        "rating": r["rating"],
        "notes": r["notes"],
        "parent_id": r["parent_id"],
        "ordinal": r["ordinal"],
        "index_mode": r["index_mode"],
        "index_error": r["index_error"],
        "library": r["library"],
        "source_id": r["source_id"],
        "docset_id": r["docset_id"],
        "docpage_id": r["docpage_id"],
        "artifact_id": r["artifact_id"],
        "content_signal": r["content_signal"],
        "starred": bool(r["starred"]),
        "profile_id": r["profile_id"],
        "alias_url": r["alias_url"],
        "collections": collections,
        "added_at": ts("added_at") or "",
        "updated_at": ts("updated_at"),
        "opened_at": ts("opened_at"),
        "completed_at": ts("completed_at"),
    }


def _rows(conn: sqlite3.Connection, rows: list[Any]) -> list[dict[str, Any]]:
    cols = _collections_by_item(conn, (r["id"] for r in rows))
    return [_item_row(r, cols.get(r["id"], [])) for r in rows]


def _encode(field: str, value: Any) -> Any:
    if field == "tags":
        return json.dumps(sorted({t.strip() for t in value or [] if t.strip()}))
    if field == "starred":
        return 1 if value else 0
    if field == "alias_url":
        return canonical_url(value) or None if value else None
    return value


# ---- items ---------------------------------------------------------------------


def get_item(item_id: str) -> dict[str, Any] | None:
    init_kb_db()
    with get_db_conn() as conn:
        r = conn.execute("SELECT * FROM kb_items WHERE id = ?", (item_id,)).fetchone()
        return _rows(conn, [r])[0] if r else None


def get_by_url(url: str) -> dict[str, Any] | None:
    """The item for `url`, matched on canonical form (scheme, `www.`, trailing slash,
    tracking params and fragment don't fork it) — or on the canonical URL a page
    *declared*, which is how `aman.ai/primers/RAG/` finds the item filed as
    `aman.ai/primers/ai/RAG/`."""
    key = canonical_url(url)
    if not key:
        return None
    init_kb_db()
    with get_db_conn() as conn:
        r = conn.execute(
            "SELECT * FROM kb_items WHERE canonical_url = ? OR alias_url = ? "
            "ORDER BY canonical_url = ? DESC LIMIT 1",
            (key, key, key),
        ).fetchone()
        return _rows(conn, [r])[0] if r else None


def create_item(
    *, collections: list[str] | None = None, **fields: Any
) -> dict[str, Any]:
    """Insert a new item. Raises `ValueError` on an unknown field and
    `sqlite3.IntegrityError` when an item for the same canonical URL exists — use
    `upsert_by_url` when "already saved" is fine."""
    unknown = set(fields) - set(_WRITABLE)
    if unknown:
        raise ValueError(f"unknown item fields: {sorted(unknown)}")
    init_kb_db()
    item_id = uuid.uuid4().hex
    url = fields.get("url")
    data = {k: _encode(k, v) for k, v in fields.items()}
    data.setdefault("title", url or "Untitled")
    data["canonical_url"] = canonical_url(url) or None if url else None
    cols = ["id", *data.keys()]
    with get_db_conn() as conn:
        conn.execute(
            f"INSERT INTO kb_items ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            [item_id, *data.values()],
        )
        if collections:
            _set_collections(conn, item_id, collections)
    item = get_item(item_id)
    assert item is not None  # just inserted
    return item


def upsert_by_url(
    url: str, *, collections: list[str] | None = None, **fields: Any
) -> tuple[dict[str, Any], bool]:
    """Create the item for `url`, or update the existing one. Returns
    `(item, created)`. On update only the fields actually passed are written, so a
    crawl re-filing a lesson never clobbers the user's status or notes."""
    existing = get_by_url(url)
    if existing is None:
        return create_item(url=url, collections=collections, **fields), True
    updated = update_item(existing["id"], **fields)
    if collections:
        add_to_collections(existing["id"], collections)
        updated = get_item(existing["id"])
    assert updated is not None
    return updated, False


def update_item(item_id: str, **fields: Any) -> dict[str, Any] | None:
    unknown = set(fields) - set(_WRITABLE)
    if unknown:
        raise ValueError(f"unknown item fields: {sorted(unknown)}")
    init_kb_db()
    if not fields:
        return get_item(item_id)
    sets = [f"{k} = ?" for k in fields]
    values = [_encode(k, v) for k, v in fields.items()]
    if "url" in fields:
        sets.append("canonical_url = ?")
        values.append(canonical_url(fields["url"]) or None if fields["url"] else None)
    status = fields.get("status")
    if status == "done":
        sets.append("completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)")
    elif status is not None:
        sets.append("completed_at = NULL")
    if status == "reading":
        sets.append("opened_at = CURRENT_TIMESTAMP")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    with get_db_conn() as conn:
        cur = conn.execute(
            f"UPDATE kb_items SET {', '.join(sets)} WHERE id = ?", [*values, item_id]
        )
        if cur.rowcount == 0:
            return None
    return get_item(item_id)


def touch_opened(item_id: str) -> dict[str, Any] | None:
    """Record that the user opened an item. A `to_read` item becomes `reading`;
    `done`/`archived` are left alone — re-reading a finished lesson must not
    un-finish it."""
    init_kb_db()
    with get_db_conn() as conn:
        conn.execute(
            """
            UPDATE kb_items SET
                opened_at = CURRENT_TIMESTAMP,
                status = CASE WHEN status = 'to_read' THEN 'reading' ELSE status END
            WHERE id = ?
            """,
            (item_id,),
        )
    return get_item(item_id)


def list_items(
    *,
    collection: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    parent_id: str | None = None,
    top_level: bool = False,
    starred: bool | None = None,
    docset_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Filtered items plus the total matching count (for paging).

    `top_level=True` hides lessons and chapters under a course, which is what a
    catalog list wants: a course is one row, its 40 lessons are its tree.
    """
    init_kb_db()
    where: list[str] = []
    params: list[Any] = []
    join = ""
    if collection:
        join = (
            "JOIN kb_item_collections ic ON ic.item_id = i.id "
            "JOIN kb_collections c ON c.id = ic.collection_id"
        )
        where.append("c.name = ?")
        params.append(collection)
    if tag:
        # Tags are a JSON list; match the quoted element so `ml` doesn't hit `mlops`.
        where.append("i.tags LIKE ?")
        params.append(f'%"{tag}"%')
    if status:
        where.append("i.status = ?")
        params.append(status)
    if kind:
        where.append("i.kind = ?")
        params.append(kind)
    if q:
        where.append(
            "(i.title LIKE ? OR i.url LIKE ? OR i.notes LIKE ? OR i.description LIKE ?)"
        )
        params.extend([f"%{q}%"] * 4)
    if parent_id:
        where.append("i.parent_id = ?")
        params.append(parent_id)
    elif top_level:
        where.append("i.parent_id IS NULL")
    if starred is not None:
        where.append("i.starred = ?")
        params.append(1 if starred else 0)
    if docset_id:
        where.append("i.docset_id = ?")
        params.append(docset_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = (
        "ORDER BY i.ordinal, i.added_at"
        if parent_id
        else "ORDER BY i.added_at DESC, i.id DESC"
    )
    with get_db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM kb_items i {join} {clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT i.* FROM kb_items i {join} {clause} {order} LIMIT ? OFFSET ?",
            [*params, max(1, min(limit, 5000)), max(0, offset)],
        ).fetchall()
        return _rows(conn, rows), int(total)


def descendants(item_id: str) -> list[dict[str, Any]]:
    """Every item below `item_id`, breadth-first, each with its `parent_id`."""
    init_kb_db()
    out: list[dict[str, Any]] = []
    frontier = [item_id]
    seen = {item_id}
    with get_db_conn() as conn:
        while frontier:
            marks = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"SELECT * FROM kb_items WHERE parent_id IN ({marks}) "
                "ORDER BY ordinal, added_at",
                frontier,
            ).fetchall()
            batch = [r for r in rows if r["id"] not in seen]
            seen.update(r["id"] for r in batch)
            out.extend(_rows(conn, batch))
            frontier = [r["id"] for r in batch][:500]
    return out


def delete_item(item_id: str, *, cascade: bool = True) -> list[dict[str, Any]]:
    """Delete an item (and, by default, everything under it). Returns the deleted
    rows so the caller can drop the content they link to — this module owns the
    catalog only."""
    item = get_item(item_id)
    if item is None:
        return []
    doomed = [item, *(descendants(item_id) if cascade else [])]
    ids = [d["id"] for d in doomed]
    with get_db_conn() as conn:
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            marks = ",".join("?" * len(part))
            conn.execute(f"DELETE FROM kb_items WHERE id IN ({marks})", part)
            conn.execute(
                f"DELETE FROM kb_item_collections WHERE item_id IN ({marks})", part
            )
        if not cascade:
            conn.execute(
                "UPDATE kb_items SET parent_id = NULL WHERE parent_id = ?", (item_id,)
            )
    return doomed


# ---- collections ---------------------------------------------------------------


def _ensure_collection(conn: sqlite3.Connection, name: str) -> str:
    name = " ".join(name.split())
    r = conn.execute("SELECT id FROM kb_collections WHERE name = ?", (name,)).fetchone()
    if r is not None:
        return str(r["id"])
    cid = uuid.uuid4().hex
    conn.execute("INSERT INTO kb_collections (id, name) VALUES (?, ?)", (cid, name))
    return cid


def _set_collections(conn: sqlite3.Connection, item_id: str, names: list[str]) -> None:
    conn.execute("DELETE FROM kb_item_collections WHERE item_id = ?", (item_id,))
    for name in names:
        if name.strip():
            cid = _ensure_collection(conn, name)
            conn.execute(
                "INSERT OR IGNORE INTO kb_item_collections (item_id, collection_id) "
                "VALUES (?, ?)",
                (item_id, cid),
            )


def set_collections(item_id: str, names: list[str]) -> None:
    init_kb_db()
    with get_db_conn() as conn:
        _set_collections(conn, item_id, names)


def add_to_collections(item_id: str, names: list[str]) -> None:
    init_kb_db()
    with get_db_conn() as conn:
        for name in names:
            if name.strip():
                cid = _ensure_collection(conn, name)
                conn.execute(
                    "INSERT OR IGNORE INTO kb_item_collections (item_id, collection_id) "
                    "VALUES (?, ?)",
                    (item_id, cid),
                )


def list_collections() -> list[dict[str, Any]]:
    init_kb_db()
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.*, COUNT(ic.item_id) AS item_count FROM kb_collections c
            LEFT JOIN kb_item_collections ic ON ic.collection_id = c.id
            GROUP BY c.id ORDER BY c.name
            """
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "item_count": int(r["item_count"]),
        }
        for r in rows
    ]


def create_collection(name: str, description: str = "") -> dict[str, Any]:
    init_kb_db()
    with get_db_conn() as conn:
        cid = _ensure_collection(conn, name)
        if description:
            conn.execute(
                "UPDATE kb_collections SET description = ? WHERE id = ?",
                (description, cid),
            )
    return next(c for c in list_collections() if c["id"] == cid)


def delete_collection(collection_id: str) -> bool:
    """Remove a collection. Its items stay — a collection is a grouping, not an
    owner."""
    init_kb_db()
    with get_db_conn() as conn:
        conn.execute(
            "DELETE FROM kb_item_collections WHERE collection_id = ?", (collection_id,)
        )
        cur = conn.execute("DELETE FROM kb_collections WHERE id = ?", (collection_id,))
        return cur.rowcount > 0


def collection_item_ids(names: list[str]) -> set[str]:
    """Ids of every item in any of the named collections, *including* items below
    them (a course in `ml` puts its lessons in scope too)."""
    init_kb_db()
    if not names:
        return set()
    marks = ",".join("?" * len(names))
    with get_db_conn() as conn:
        roots = [
            str(r["item_id"])
            for r in conn.execute(
                f"""
                SELECT ic.item_id FROM kb_item_collections ic
                JOIN kb_collections c ON c.id = ic.collection_id
                WHERE c.name IN ({marks})
                """,
                names,
            )
        ]
    ids = set(roots)
    for root in roots:
        ids.update(d["id"] for d in descendants(root))
    return ids


# ---- counts ----------------------------------------------------------------------


def summary() -> dict[str, Any]:
    """Cheap totals for the agent's one-line nudge and the pane header."""
    init_kb_db()
    with get_db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM kb_items").fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM kb_items WHERE source_id IS NOT NULL"
        ).fetchone()[0]
        by_kind = {
            r["kind"]: int(r["n"])
            for r in conn.execute(
                "SELECT kind, COUNT(*) AS n FROM kb_items GROUP BY kind"
            )
        }
    return {
        "items": int(total),
        "indexed": int(indexed),
        "by_kind": by_kind,
        "collections": [
            c["name"]
            for c in sorted(list_collections(), key=lambda c: -c["item_count"])
        ],
    }
