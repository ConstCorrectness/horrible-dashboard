"""Driver contract shared by every database provider.

A driver is a thin, synchronous adapter around one DB client library. Routes call
drivers via ``asyncio.to_thread`` so the (blocking) client never stalls the event
loop. Drivers lazy-import their client library inside methods, so a provider whose
optional dependency is missing fails with a clear message instead of breaking boot.

Drivers come in two **dialects**, declared by the module-level ``dialect``:

``sql``
    The query text is SQL, handed to a real SQL engine (sqlite, postgres, duckdb,
    mysql, oracle).
``json``
    The query text is a JSON body describing one operation against a vector store
    (chroma, lancedb, qdrant, weaviate). These stores have no SQL dialect — a
    collection, a metadata filter and a query vector is genuinely all they take —
    so the console sends their native shape rather than pretending otherwise. See
    ``vector_base.py`` for the shared body. Results still come back as
    columns + rows, so the results grid is dialect-agnostic.
``mongo``
    The query text is a JSON body carrying a MongoDB operation — a filter, a
    pipeline, a projection (``mongo_driver.py``). Deliberately **not** folded into
    ``json``: MQL is a real query language with its own vocabulary, and mapping
    ``find`` onto the vector contract's ``search`` would be exactly the silent
    reinterpretation the vector drivers refuse to do.

Results still come back as columns + rows in every dialect, so the results grid, the
CSV export and the agent's reader are dialect-agnostic — only the input surface differs.
"""

from __future__ import annotations

import array as _array
import datetime as _dt
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

Dialect = Literal["sql", "json", "mongo"]

# Hard cap on rows returned to the client for a single query. The driver fetches one
# extra row to detect (and flag) truncation without counting the whole result set.
DEFAULT_ROW_LIMIT = 1000

# Bytes/array cells are summarized rather than dumped so embedding BLOBs and pgvector
# columns don't flood the grid.
_ARRAY_SUMMARY_THRESHOLD = 12


class DriverError(Exception):
    """Raised by drivers for connection/query failures and missing dependencies."""


@dataclass
class ColumnInfo:
    """A column in a query result set."""

    name: str
    type: str | None = None


@dataclass
class QueryResult:
    columns: list[ColumnInfo]
    rows: list[list[Any]]
    rowcount: int
    elapsed_ms: float
    truncated: bool = False
    # Set for non-SELECT statements (INSERT/UPDATE/DELETE/DDL).
    affected: int | None = None
    message: str | None = None


@dataclass
class ColumnSchema:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False


@dataclass
class TableSchema:
    name: str
    schema: str | None = None  # namespace (postgres/mysql); None for sqlite/duckdb
    columns: list[ColumnSchema] = field(default_factory=list)


@dataclass
class DatabaseSchema:
    tables: list[TableSchema] = field(default_factory=list)


@runtime_checkable
class Driver(Protocol):
    """Uniform contract every provider implements."""

    provider: str
    dialect: Dialect

    def test(self, config: dict[str, Any]) -> None:
        """Open a connection and run a trivial probe. Raise DriverError on failure."""
        ...

    def run_query(
        self,
        config: dict[str, Any],
        sql: str,
        params: list[Any] | None = None,
        *,
        read_only: bool = False,
        row_limit: int = DEFAULT_ROW_LIMIT,
    ) -> QueryResult: ...

    def introspect(self, config: dict[str, Any]) -> DatabaseSchema: ...


def jsonable(value: Any) -> Any:
    """Coerce a raw DB cell into a JSON-serializable value for the results grid.

    Binary blobs and long numeric arrays (embeddings, pgvector) are summarized;
    dates/decimals are stringified; non-finite floats become strings so the JSON
    encoder doesn't choke.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    # array.array is how oracledb hands back a 23ai VECTOR column; treat it as the
    # numeric sequence it is so it gets summarized rather than repr'd into the grid.
    if isinstance(value, (list, tuple, _array.array)):
        seq = list(value)
        if len(seq) > _ARRAY_SUMMARY_THRESHOLD and all(
            isinstance(x, (int, float)) for x in seq
        ):
            return f"[{len(seq)} numbers]"
        return [jsonable(x) for x in seq]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def looks_read_only(sql: str) -> bool:
    """Best-effort check that a statement only reads. Used to gate the agent's
    read-only query tool and the pane's read path. Conservative: anything that
    isn't a lone SELECT/WITH/EXPLAIN/SHOW/PRAGMA-read is treated as a write."""
    stripped = sql.strip().rstrip(";").lstrip()
    if not stripped:
        return False
    # Reject multiple statements outright — can't vouch for the tail.
    if ";" in stripped:
        return False
    head = stripped.split(None, 1)[0].lower()
    return head in {"select", "with", "explain", "show", "table", "values"}


# Vector-store ops that only read. Anything absent (upsert/delete/create/drop, or an
# op a future driver adds) is treated as a write, so read-only mode fails closed.
READ_ONLY_OPS = frozenset(
    {"search", "get", "list", "count", "peek", "collections", "describe"}
)


def json_query_is_read_only(query: str) -> bool:
    """Read-only check for a ``json``-dialect query body.

    Unparseable input is *not* read-only: a body we can't understand is one whose
    op we can't vouch for, and read-only mode is a safety gate, not a hint.
    """
    try:
        body = json.loads(query)
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    op = body.get("op")
    return isinstance(op, str) and op.strip().lower() in READ_ONLY_OPS


# MongoDB ops that only read. `aggregate` is here but is **not** unconditionally a
# read: a pipeline ending in `$out` or `$merge` writes a whole collection, which is
# why `mongo_query_is_read_only` inspects the pipeline rather than trusting the op name.
MONGO_READ_OPS = frozenset(
    {
        "find",
        "find_one",
        "aggregate",
        "count",
        "distinct",
        "collections",
        "databases",
        "describe",
        "indexes",
        "stats",
    }
)

# Aggregation stages that write. Searched for at any depth: they are only *legal* as
# the last top-level stage, but a gate that trusts the shape of valid input is a gate
# that can be walked past with invalid input.
_MONGO_WRITE_STAGES = ("$out", "$merge")


def _contains_key(node: Any, keys: tuple[str, ...]) -> bool:
    if isinstance(node, dict):
        return any(k in node for k in keys) or any(
            _contains_key(v, keys) for v in node.values()
        )
    if isinstance(node, list):
        return any(_contains_key(v, keys) for v in node)
    return False


def mongo_query_is_read_only(query: str) -> bool:
    """Read-only check for a ``mongo``-dialect query body.

    Same fail-closed rule as the vector dialect — an unparseable body is not
    read-only — plus the ``$out``/``$merge`` check, which is the one place where an
    op *name* is not enough to classify a Mongo query.
    """
    try:
        body = json.loads(query)
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    op = body.get("op")
    if not isinstance(op, str) or op.strip().lower() not in MONGO_READ_OPS:
        return False
    if op.strip().lower() == "aggregate":
        return not _contains_key(body.get("pipeline"), _MONGO_WRITE_STAGES)
    return True


def query_is_read_only(query: str, dialect: Dialect = "sql") -> bool:
    """Dialect-aware read-only gate used by the routes and the agent's query tool."""
    if dialect == "json":
        return json_query_is_read_only(query)
    if dialect == "mongo":
        return mongo_query_is_read_only(query)
    return looks_read_only(query)
