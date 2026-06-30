"""Driver contract shared by every database provider.

A driver is a thin, synchronous adapter around one DB client library. Routes call
drivers via ``asyncio.to_thread`` so the (blocking) client never stalls the event
loop. Drivers lazy-import their client library inside methods, so a provider whose
optional dependency is missing fails with a clear message instead of breaking boot.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

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
    if isinstance(value, (list, tuple)):
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
