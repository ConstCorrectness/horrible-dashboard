"""`otel_spans` in `app.db` — raw spans, the truth a trajectory is projected from.

In `app.db` rather than a private file for the reason trajectories gives: the
database console's `app` connection and `dash` can query it with no new API, and
`trace_id` joins it to everything else.

Writes are **idempotent on `(trace_id, span_id)`**. Exporters retry on timeout, so
the same batch can arrive twice, and a store that appended would double a trace's
spans (and, through the projection, its steps) with no error anywhere.

Attribute maps can be large — a span carrying `gen_ai.input.messages` holds the
whole prompt. Above `ATTRS_INLINE_MAX` they spill to `$HORRIBLE_DATA_DIR/otel/
<trace_id>/` rather than being clipped, the trajectories rule: a debugger that
dropped the value is no debugger.

Retention is bounded two ways (age and count), checked on write at most once a
minute: received traces are pushed at us by anything that can reach the port, so
an unbounded table is a disk the network can fill.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable

from backend.modules.database.app_db import ensure_app_db_dir, get_data_dir
from backend.modules.otel.models import (
    STATUS_ERROR,
    Span,
    SpanEvent,
    SpanOrigin,
    TraceSummary,
)

logger = logging.getLogger("otel")

ATTRS_INLINE_MAX = 64 * 1024
BLOB_PREFIX = "blob:"
RETENTION_S = 7 * 24 * 3600
MAX_SPANS = 200_000
_PRUNE_EVERY_S = 60.0

_HEX = re.compile(r"^[0-9a-f]+$")
_initialized: set[str] = set()
_prune_lock = threading.Lock()
_last_prune = 0.0


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        _initialized.add(path)
        init_otel_db()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_otel_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS otel_spans (
                trace_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                parent_span_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                kind INTEGER NOT NULL DEFAULT 0,
                start_ns INTEGER NOT NULL DEFAULT 0,
                end_ns INTEGER NOT NULL DEFAULT 0,
                status_code INTEGER NOT NULL DEFAULT 0,
                status_message TEXT NOT NULL DEFAULT '',
                attrs TEXT NOT NULL DEFAULT '{}',
                events TEXT NOT NULL DEFAULT '[]',
                resource TEXT NOT NULL DEFAULT '{}',
                scope TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'received',
                received_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (trace_id, span_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_otel_spans_received"
            " ON otel_spans (received_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_otel_spans_origin"
            " ON otel_spans (origin, received_at)"
        )


def _safe_hex(value: str) -> str:
    """Ids become directory names; only hex ever reaches the filesystem."""
    return value if _HEX.match(value or "") else "invalid"


def _blob_dir(trace_id: str) -> Path:
    return get_data_dir() / "otel" / _safe_hex(trace_id)


def _encode(trace_id: str, span_id: str, field: str, value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text.encode("utf-8")) <= ATTRS_INLINE_MAX:
        return text
    name = f"{_safe_hex(span_id)}.{field}.json"
    try:
        directory = _blob_dir(trace_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("otel: could not spill %s/%s: %s", trace_id, name, exc)
        return json.dumps({"_spill_error": str(exc)})
    return BLOB_PREFIX + name


def _decode(trace_id: str, raw: str, empty: Any) -> Any:
    if raw.startswith(BLOB_PREFIX):
        path = _blob_dir(trace_id) / Path(raw[len(BLOB_PREFIX) :]).name
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return {"_blob_missing": path.name}
    try:
        return json.loads(raw)
    except ValueError:
        return empty


def insert_spans(
    spans: Iterable[Span], *, origin: SpanOrigin | None = None
) -> set[str]:
    """Upsert spans. Returns the trace ids touched."""
    now = time.time()
    touched: set[str] = set()
    rows = []
    for span in spans:
        src = origin or span.origin
        touched.add(span.trace_id)
        rows.append(
            (
                span.trace_id,
                span.span_id,
                span.parent_span_id,
                span.name,
                span.kind,
                span.start_ns,
                span.end_ns,
                span.status_code,
                span.status_message,
                _encode(span.trace_id, span.span_id, "attrs", span.attrs),
                _encode(
                    span.trace_id,
                    span.span_id,
                    "events",
                    [e.model_dump() for e in span.events],
                ),
                json.dumps(span.resource, default=str, ensure_ascii=False),
                span.scope,
                src,
                now,
            )
        )
    if not rows:
        return touched
    with get_db_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO otel_spans (trace_id, span_id, parent_span_id,"
            " name, kind, start_ns, end_ns, status_code, status_message, attrs, events,"
            " resource, scope, origin, received_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    _maybe_prune()
    return touched


def _span_from_row(row: sqlite3.Row) -> Span:
    trace_id = row["trace_id"]
    events = _decode(trace_id, row["events"], [])
    return Span(
        trace_id=trace_id,
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        name=row["name"],
        kind=row["kind"],
        start_ns=row["start_ns"],
        end_ns=row["end_ns"],
        status_code=row["status_code"],
        status_message=row["status_message"],
        attrs=_decode(trace_id, row["attrs"], {}),
        events=[SpanEvent(**e) for e in events if isinstance(e, dict)],
        resource=json.loads(row["resource"] or "{}"),
        scope=row["scope"],
        origin=row["origin"],
        received_at=row["received_at"],
    )


def get_trace(trace_id: str) -> list[Span]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM otel_spans WHERE trace_id = ? ORDER BY start_ns, span_id",
            (trace_id,),
        ).fetchall()
    return [_span_from_row(r) for r in rows]


def has_local_spans(trace_id: str) -> bool:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM otel_spans WHERE trace_id = ? AND origin = 'local' LIMIT 1",
            (trace_id,),
        ).fetchone()
    return row is not None


def list_traces(
    *, origin: SpanOrigin | None = None, limit: int = 50
) -> list[TraceSummary]:
    where = "WHERE origin = ?" if origin else ""
    params: list[Any] = [origin] if origin else []
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT trace_id, COUNT(*) AS spans, MIN(start_ns) AS start_ns,"
            " MAX(end_ns) AS end_ns, MAX(received_at) AS received_at,"
            f" SUM(CASE WHEN status_code = {STATUS_ERROR} THEN 1 ELSE 0 END) AS errors,"
            " MIN(origin) AS origin"
            f" FROM otel_spans {where} GROUP BY trace_id"
            " ORDER BY received_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        out: list[TraceSummary] = []
        for r in rows:
            root = conn.execute(
                "SELECT name, resource FROM otel_spans WHERE trace_id = ?"
                " ORDER BY (parent_span_id = '') DESC, start_ns LIMIT 1",
                (r["trace_id"],),
            ).fetchone()
            resource = json.loads(root["resource"] or "{}") if root else {}
            out.append(
                TraceSummary(
                    trace_id=r["trace_id"],
                    spans=r["spans"],
                    service=str(resource.get("service.name") or ""),
                    root_name=root["name"] if root else "",
                    origin=r["origin"],
                    start_ns=r["start_ns"] or 0,
                    end_ns=r["end_ns"] or 0,
                    errors=r["errors"] or 0,
                    received_at=r["received_at"] or 0.0,
                )
            )
    return out


def received_stats() -> tuple[float | None, int]:
    """(last received_at, distinct received traces) for the connect panel."""
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT MAX(received_at) AS last, COUNT(DISTINCT trace_id) AS n"
            " FROM otel_spans WHERE origin = 'received'"
        ).fetchone()
    return (row["last"], int(row["n"] or 0)) if row else (None, 0)


def delete_trace(trace_id: str) -> int:
    with get_db_conn() as conn:
        n = conn.execute(
            "DELETE FROM otel_spans WHERE trace_id = ?", (trace_id,)
        ).rowcount
    shutil.rmtree(_blob_dir(trace_id), ignore_errors=True)
    return n


def _maybe_prune() -> None:
    global _last_prune
    now = time.time()
    if now - _last_prune < _PRUNE_EVERY_S or not _prune_lock.acquire(blocking=False):
        return
    try:
        _last_prune = now
        prune(now=now)
    except Exception:  # noqa: BLE001 — retention must never fail an ingest
        logger.debug("otel: prune failed", exc_info=True)
    finally:
        _prune_lock.release()


def prune(*, now: float | None = None, max_spans: int = MAX_SPANS) -> int:
    """Drop whole traces past retention. Whole traces, never single spans: a
    trace missing its middle projects into a run with steps silently absent."""
    cutoff = (now or time.time()) - RETENTION_S
    with get_db_conn() as conn:
        doomed = {
            r["trace_id"]
            for r in conn.execute(
                "SELECT trace_id FROM otel_spans GROUP BY trace_id"
                " HAVING MAX(received_at) < ?",
                (cutoff,),
            )
        }
        total = conn.execute("SELECT COUNT(*) FROM otel_spans").fetchone()[0]
        excess = total - max_spans
        if excess > 0:
            for r in conn.execute(
                "SELECT trace_id, COUNT(*) AS n FROM otel_spans GROUP BY trace_id"
                " ORDER BY MAX(received_at) ASC"
            ):
                if excess <= 0:
                    break
                # Age-doomed traces count toward the excess too, or the cap
                # over-prunes by exactly the traces already on their way out.
                doomed.add(r["trace_id"])
                excess -= r["n"]
    removed = 0
    for trace_id in doomed:
        removed += delete_trace(trace_id)
    return removed
