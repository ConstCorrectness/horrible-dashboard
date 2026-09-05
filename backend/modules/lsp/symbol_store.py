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
from typing import Generator, Iterable, Mapping
from backend import paths

# Symbols seeded from the language itself (builtins + keywords) live under this
# synthetic source so a buffer re-index never clears them.
_BUILTINS_SOURCE = "<py-builtins>"

# init() is idempotent but does a little work (a SELECT to check the seed); skip it
# after the first successful run in this process.
_initialized = False


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    data_dir = paths.data_dir()
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
                freq   INTEGER NOT NULL DEFAULT 1,
                doc    TEXT NOT NULL DEFAULT '',
                imp    TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Additive migrations for tables created before the symdex `doc` column and
        # before the auto-import `imp` column.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(code_symbols)")}
        if "doc" not in cols:
            conn.execute(
                "ALTER TABLE code_symbols ADD COLUMN doc TEXT NOT NULL DEFAULT ''"
            )
        if "imp" not in cols:
            conn.execute(
                "ALTER TABLE code_symbols ADD COLUMN imp TEXT NOT NULL DEFAULT ''"
            )
        # The index that makes `symbol LIKE 'req%'` sub-millisecond.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_symbols ON code_symbols(lang, symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_code_symbols_source ON code_symbols(source)"
        )
        _seed_python_static(conn)
        _purge_orphan_buffers(conn)
    _initialized = True


def _purge_orphan_buffers(conn: sqlite3.Connection) -> None:
    """Drop rows harvested from `workspace-file:` buffers whose file no longer exists.

    A buffer's symbols are replaced on every re-index, but a file that's deleted or
    renamed is never re-indexed — its rows would linger forever and keep polluting
    completion with symbols from files that are gone. Runs once per process, on the
    same startup pass that seeds the builtins."""
    orphans = [
        row["source"]
        for row in conn.execute(
            "SELECT DISTINCT source FROM code_symbols WHERE source LIKE 'workspace-file:%'"
        )
        if not os.path.isfile(row["source"][len("workspace-file:") :])
    ]
    if orphans:
        conn.executemany(
            "DELETE FROM code_symbols WHERE source = ?", [(s,) for s in orphans]
        )


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
            str(r.get("doc", "")),
            str(r.get("imp", "")),
        )
        for r in rows
        if r.get("symbol")
    ]
    with _conn() as conn:
        conn.execute("DELETE FROM code_symbols WHERE source = ?", (source,))
        if data:
            conn.executemany(
                "INSERT INTO code_symbols "
                "(symbol, lang, kind, detail, module, source, freq, doc, imp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    # Group by (symbol, module), not symbol alone: `Path` exists in both `pathlib` and
    # `fastapi.params`, and collapsing them produced one blended row — pathlib's import
    # module glued to fastapi's signature and docstring. They're different symbols and
    # both deserve their own suggestion.
    #
    # Ranking is tiered, because the corpora mean different things:
    #   0 — your own buffers and the language builtins: what you're most likely typing
    #   1 — an importable indexed symbol (`json.dumps`, `pathlib.Path`)
    #   2 — an indexed *method* (`Marshaller.dumps`), which a bare-prefix query is
    #       almost never after; without this tier they buried the real answers
    # Within a tier: *import depth* first, so `json.dumps` beats `xmlrpc.client.dumps`
    # and `numpy.array` beats `numpy.core.multiarray.array` — a symbol you reach at the
    # top of a package is the one people mean. Then frequency, length, alphabetical.
    sql = (
        "SELECT symbol, MIN(kind) AS kind, MIN(detail) AS detail, module, "
        "MAX(freq) AS freq, MAX(doc) AS doc, MAX(imp) AS imp, "
        "(length(MAX(imp)) - length(replace(MAX(imp), '.', ''))) AS depth, "
        "CASE WHEN MIN(source) LIKE 'workspace-file:%' "
        "       OR MIN(source) LIKE 'note:%' "
        "       OR MIN(source) = ? THEN 0 "
        "     WHEN MAX(imp) != '' THEN 1 ELSE 2 END AS tier "
        "FROM code_symbols WHERE lang = ? AND symbol LIKE ? ESCAPE '\\' "
    )
    params: list[object] = [_BUILTINS_SOURCE, lang, like]
    if member_of:
        sql += "AND module = ? "
        params.append(member_of)
    sql += (
        "GROUP BY symbol, module "
        "ORDER BY tier ASC, depth ASC, freq DESC, length(symbol), symbol LIMIT ?"
    )
    params.append(max(1, limit))
    with _conn() as conn:
        cur = conn.execute(sql, params)
        return [
            {
                "symbol": r["symbol"],
                "kind": r["kind"],
                "detail": r["detail"],
                "module": r["module"],
                "doc": r["doc"] or "",
                "imp": r["imp"] or "",
            }
            for r in cur.fetchall()
        ]


def _like(prefix: str) -> str:
    """A LIKE pattern for `prefix`, with the wildcards in the typed text escaped so
    `_` and `%` match literally."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def query_modules(lang: str, prefix: str, limit: int = 40) -> list[dict[str, object]]:
    """Importable module paths for `from <prefix>` / `import <prefix>`.

    Unlike `query`, an **empty prefix is legal** — `from <Tab>` is a real question and
    the answer is "the top-level modules". Without a prefix the result is restricted to
    depth 0 (`imp NOT LIKE '%.%'`), because listing every dotted submodule of every
    indexed package is thousands of rows and none of them is what was asked.

    Ranked by import depth first, so `vllm` beats `vllm.lora.request` — the module you
    reach at the top of a package is the one people mean, the same rationale that tiers
    `query`.
    """
    init()
    sql = (
        "SELECT imp AS module, MAX(freq) AS freq, "
        "(length(imp) - length(replace(imp, '.', ''))) AS depth "
        "FROM code_symbols WHERE lang = ? AND imp != '' "
    )
    params: list[object] = [lang]
    if prefix:
        sql += "AND imp LIKE ? ESCAPE '\\' "
        params.append(_like(prefix))
    else:
        sql += "AND imp NOT LIKE '%.%' "
    sql += "GROUP BY imp ORDER BY depth ASC, freq DESC, length(imp), imp LIMIT ?"
    params.append(max(1, limit))
    with _conn() as conn:
        return [
            {"module": r["module"], "freq": int(r["freq"] or 1)}
            for r in conn.execute(sql, params).fetchall()
        ]


def query_import_members(
    lang: str, module: str, prefix: str = "", limit: int = 50
) -> list[dict[str, str]]:
    """The names importable from `module` — the answer to `from vllm import <Tab>`.

    Filters on **`imp`, not `module`**. For a method the `module` column holds the
    *class* it belongs to (`extract_packages` stores `member_of or module`), so a
    `module = ?` filter would answer with `Marshaller`'s methods rather than with the
    package's importable names. `imp` is by construction "the module this symbol can be
    imported from", and is empty for methods — exactly the distinction wanted here.

    An empty prefix is legal for the same reason as `query_modules`.

    **A package with no rows of its own falls back to its submodules.** `imp` records
    where a symbol is *defined*, never where it is re-exported from: `transformers`
    re-exports `AutoModel` out of `transformers.models.auto`, so an exact match
    answered `from transformers import <Tab>` with **nothing** while the table held
    530 transformers symbols. Same for numpy and torch — for every package that
    assembles its public surface in `__init__.py`, which is most large ones. Only
    packages that define their names directly there (huggingface_hub, sympy) ever
    worked, which made this read as a flaky index rather than a structural miss.

    The fallback fires **only when the exact match is empty**, and that is the whole
    of its safety. A submodule name is a guess — `vllm` really does define `LLM` and
    really does not re-export `LoRARequest`, so mixing the two in would offer an
    import that fails for a package the index already describes correctly. Where
    there is nothing to be wrong about, a guess costs nothing and is usually right;
    where there is, the precise answer stands alone. Fallback rows carry their
    defining module in `detail` so the guess is legible as one.

    Resolving this properly means following `__init__` re-exports at index time —
    a change to the indexer, not to this query.
    """
    init()
    if not module:
        return []

    def run(exact: bool) -> list[dict[str, str]]:
        sql = (
            "SELECT symbol, MIN(kind) AS kind, MIN(detail) AS detail, MIN(imp) AS imp, "
            "MAX(freq) AS freq, MAX(doc) AS doc "
            "FROM code_symbols WHERE lang = ? AND "
            + ("imp = ? " if exact else "imp LIKE ? ESCAPE '\\' ")
        )
        params: list[object] = [lang, module if exact else _like(module + ".")]
        if prefix:
            sql += "AND symbol LIKE ? ESCAPE '\\' "
            params.append(_like(prefix))
        sql += "GROUP BY symbol ORDER BY freq DESC, length(symbol), symbol LIMIT ?"
        params.append(max(1, limit))
        with _conn() as conn:
            return [
                {
                    "symbol": r["symbol"],
                    "kind": r["kind"],
                    # A fallback row shows where the name actually lives instead of
                    # its kind (which the popup already draws as an icon), so an
                    # offer the package does not re-export is visibly a guess rather
                    # than an import that silently fails.
                    "detail": (r["detail"] if exact else r["imp"]) or "",
                    "doc": r["doc"] or "",
                }
                for r in conn.execute(sql, params).fetchall()
            ]

    return run(exact=True) or run(exact=False)
