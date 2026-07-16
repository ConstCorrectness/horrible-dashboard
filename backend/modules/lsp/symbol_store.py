"""Prefix symbol index for editor completion — the "intellisense" lookup.

Editor completion is a fast, indexed **prefix query** against a relational
`code_symbols` table here; no model is in the loop (the agent/orchestrator owns
deliberate edits instead). The table lives in the same `.data/app.db` SQLite file
the library catalog uses (see `backend/modules/library/store.py`), so the built-in
`app` database connection can browse it. See docs/modules/editor.mdx.
"""

from __future__ import annotations

import builtins
import keyword
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterable, Mapping

# Symbols seeded from the language itself (builtins + keywords) live under this
# synthetic source so a buffer re-index never clears them.
_BUILTINS_SOURCE = "<py-builtins>"

# init() is idempotent but does a little work (a SELECT to check the seed); skip it
# after the first successful run in this process.
_initialized = False


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    data_dir = Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "app.db"))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    """Create the table + prefix index and seed language builtins. Idempotent; the
    real work runs once per process."""
    global _initialized
    if _initialized:
        return
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS code_symbols (
                symbol TEXT NOT NULL,
                lang   TEXT NOT NULL,
                kind   TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                module TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                freq   INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        # The index that makes `symbol LIKE 'req%'` sub-millisecond.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_symbols ON code_symbols(lang, symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_symbols_source ON code_symbols(source)"
        )
        _seed_python_static(conn)
    _initialized = True


def _seed_python_static(conn: sqlite3.Connection) -> None:
    """Insert Python builtins + keywords once. High `freq` so short prefixes surface
    them ahead of buffer-local symbols."""
    if conn.execute(
        "SELECT 1 FROM code_symbols WHERE source = ? LIMIT 1", (_BUILTINS_SOURCE,)
    ).fetchone():
        return
    rows: list[tuple[str, str, str, str, str, str, int]] = []
    for name in dir(builtins):
        if name.startswith("_"):
            continue
        obj = getattr(builtins, name, None)
        kind = (
            "class"
            if isinstance(obj, type)
            else "function"
            if callable(obj)
            else "variable"
        )
        rows.append((name, "python", kind, "builtin", "builtins", _BUILTINS_SOURCE, 5))
    for kw in keyword.kwlist:
        rows.append((kw, "python", "keyword", "keyword", "", _BUILTINS_SOURCE, 5))
    conn.executemany(
        "INSERT INTO code_symbols (symbol, lang, kind, detail, module, source, freq) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def replace_source(source: str, lang: str, rows: Iterable[Mapping[str, object]]) -> int:
    """Swap in a fresh harvest for `source`: delete its old rows, insert the new ones.
    Keeps a file's symbols current on every re-index. Returns the inserted count."""
    init()
    data = [
        (
            str(r["symbol"]),
            lang,
            str(r.get("kind", "variable")),
            str(r.get("detail", "")),
            str(r.get("module", "")),
            source,
            int(r.get("freq", 1) or 1),
        )
        for r in rows
        if r.get("symbol")
    ]
    with _conn() as conn:
        conn.execute("DELETE FROM code_symbols WHERE source = ?", (source,))
        if data:
            conn.executemany(
                "INSERT INTO code_symbols (symbol, lang, kind, detail, module, source, freq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                data,
            )
    return len(data)


def query(
    lang: str, prefix: str, limit: int = 25, member_of: str | None = None
) -> list[dict[str, str]]:
    """Ranked prefix lookup — the hot completion path. Case-insensitive (SQLite LIKE),
    one row per distinct symbol (best `freq` wins), ordered freq → shortest → alpha."""
    init()
    if not prefix:
        return []
    # Escape LIKE wildcards in the typed text so `_`/`%` match literally.
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = (
        "SELECT symbol, MIN(kind) AS kind, MIN(detail) AS detail, "
        "MIN(module) AS module, MAX(freq) AS freq "
        "FROM code_symbols WHERE lang = ? AND symbol LIKE ? ESCAPE '\\' "
    )
    params: list[object] = [lang, like]
    if member_of:
        sql += "AND module = ? "
        params.append(member_of)
    sql += "GROUP BY symbol ORDER BY freq DESC, length(symbol), symbol LIMIT ?"
    params.append(max(1, limit))
    with _conn() as conn:
        cur = conn.execute(sql, params)
        return [
            {
                "symbol": r["symbol"],
                "kind": r["kind"],
                "detail": r["detail"],
                "module": r["module"],
            }
            for r in cur.fetchall()
        ]
