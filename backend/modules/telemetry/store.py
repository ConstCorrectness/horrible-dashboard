"""Durable I/O events for agent turns, in `app.db`.

The recorder is a 500-event in-memory ring, which is right for a live view and
useless for a postmortem: by the time anyone opens yesterday's run, the wire that
produced it is long gone. This keeps the subset that belongs to an agent turn, so a
trajectory's HTTP traffic can still be read months later.

## Only turn-stamped events are kept

The drain filters on `turn_id IS NOT NULL`. That drops the overwhelming majority of
the volume -- a node's background polling does not belong in an agent postmortem --
and it makes retention nearly a non-problem. It is also the stated key: these rows
exist to join onto `traj_runs.turn_id` and `agent_turns.turn_id`, and an event with
no turn joins to nothing.

## `(boot_id, ev_id)` is the primary key

`IoEvent.id` is an `itertools.count(1)` that resets on every process start, so it is
not durable on its own -- two runs of the backend both produce an event `1`. Prefixed
with a per-process id it becomes unique, and `INSERT OR REPLACE` then makes
`Recorder.amend` -- which deliberately re-emits under the *same* id to backfill a
streamed body -- idempotent by construction. No dedupe logic, no "have I seen this".

## A separate table, not `traj_steps`

One tool call can make forty HTTP requests. Writing them as steps would corrupt
`traj_runs.steps` and every `GROUP BY` in `analyze.py`. Same database file, though:
the join is the entire point.

## Headers are blanked, bodies are opt-in

Header *names* are matched against `SENSITIVE_HEADER_PARTS` and blanked.
`trajectories.store.redact()` is not used for this: its vocabulary is dotted
settings keys, so it matches neither `Authorization` nor `x-api-key`.
Bodies are strings and redaction cannot help them, so they are only persisted when
`telemetry.persistBodies` is on -- default off. The in-memory ring keeps full bodies
either way; this restriction is on the durable copy alone.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator, Iterable

from backend.modules.telemetry.models import IoEvent

logger = logging.getLogger("telemetry")

#: Identifies this process. Minted at import so it is stable for the life of the
#: backend and different across restarts -- which is exactly what `ev_id` is not.
BOOT_ID = uuid.uuid4().hex[:12]

#: Cap on a persisted body, when bodies are persisted at all.
BODY_MAX = 4096

#: Substrings that make a header name credential-bearing. Matched anywhere in the
#: name, case-insensitively.
#:
#: A substring match rather than an exact list because header names are not a closed
#: vocabulary: `x-api-key`, `api-key`, `x-goog-api-key` and `x-amz-security-token`
#: are all the same idea spelled four ways, and an allowlist of exact names is one
#: vendor away from being wrong. Over-blanking a header costs a line of debugging
#: detail; under-blanking writes a live credential to disk, so the asymmetry decides
#: the design.
#:
#: `trajectories.store.redact` cannot do this job: it matches `SECRET_KEY_SUFFIXES`,
#: which is a *settings-key* vocabulary (`github.token`, `foo.apiKey`). Those suffixes
#: are dotted, so `x-api-key` does not match `.key` and `Authorization` matches
#: nothing at all — every credential header would have gone to disk in full, silently.
SENSITIVE_HEADER_PARTS = (
    "authorization",
    "auth",
    "token",
    "key",
    "secret",
    "password",
    "cookie",
    "credential",
)

#: What a blanked value is replaced with. Blanked rather than dropped, for the reason
#: the settings route gives: an absent key is indistinguishable from one that was
#: never sent, and "this request was authenticated" is itself worth knowing.
HEADER_REDACTED = "***"


def is_sensitive_header(name: str) -> bool:
    lower = str(name).lower()
    return any(part in lower for part in SENSITIVE_HEADER_PARTS)


def _clean_headers(headers: dict[str, str]) -> dict[str, Any]:
    return {
        k: (HEADER_REDACTED if is_sensitive_header(k) else v)
        for k, v in dict(headers).items()
    }


_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    # Imported here, not at module scope. `backend.modules.database.__init__` pulls
    # in its routes, which import `agent.routes` — and this module is reached *from*
    # `agent.providers` via `telemetry.instrument`, so a top-level import closes a
    # cycle and every `from backend.modules.agent import ...` fails at collection.
    # `trajectories/store.py` can import it eagerly only because it sits outside
    # that chain.
    from backend.modules.database.app_db import ensure_app_db_dir

    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked *before* the call, for the reason trajectories' copy states: the
        # initializer opens a connection through this same helper.
        _initialized.add(path)
        init_telemetry_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_telemetry_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_events (
                boot_id       TEXT NOT NULL,
                ev_id         INTEGER NOT NULL,
                turn_id       TEXT NOT NULL,
                round         INTEGER,
                ts            REAL NOT NULL,
                source        TEXT NOT NULL,
                method        TEXT NOT NULL DEFAULT '',
                target        TEXT NOT NULL DEFAULT '',
                status        INTEGER,
                duration_ms   REAL,
                request_bytes  INTEGER,
                response_bytes INTEGER,
                error         TEXT,
                verdict       TEXT,
                resource_type TEXT,
                remote_ip     TEXT,
                http_protocol TEXT,
                timing        TEXT,
                detail        TEXT,
                PRIMARY KEY (boot_id, ev_id)
            )
            """
        )
        # The one query this table exists to answer.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_turn "
            "ON telemetry_events(turn_id, ts)"
        )
        # Retention deletes oldest-first across every turn.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry_events(ts)"
        )


def _setting(key: str, default: Any) -> Any:
    try:
        from backend.modules.settings.routes import get_value

        return get_value(key, default)
    except Exception:  # noqa: BLE001 - settings must never break a writer
        return default


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= BODY_MAX else text[:BODY_MAX]


def _detail_for(event: IoEvent) -> str | None:
    """Headers (credential-bearing names blanked) and, only when opted in, bodies.

    See `SENSITIVE_HEADER_PARTS` for why this does not use `trajectories.store.redact`.
    """
    detail: dict[str, Any] = {}
    if event.request_headers:
        detail["request_headers"] = _clean_headers(event.request_headers)
    if event.response_headers:
        detail["response_headers"] = _clean_headers(event.response_headers)
    if _setting("telemetry.persistBodies", False):
        if event.request_body:
            detail["request_body"] = _clip(event.request_body)
        if event.response_body:
            detail["response_body"] = _clip(event.response_body)
    if not detail:
        return None
    try:
        return json.dumps(detail, default=str)
    except Exception:  # noqa: BLE001
        return None


def persist(events: Iterable[IoEvent]) -> int:
    """Write a batch. Returns how many rows were written.

    Events with no `turn_id` are skipped rather than stored with a placeholder: a
    row that joins to nothing is noise in a table whose only purpose is the join.
    """
    rows = []
    for event in events:
        if not event.turn_id:
            continue
        rows.append(
            (
                BOOT_ID,
                event.id,
                event.turn_id,
                event.round,
                event.ts,
                event.source,
                event.method,
                event.target,
                event.status,
                event.duration_ms,
                event.request_bytes,
                event.response_bytes,
                event.error,
                event.verdict,
                event.resource_type,
                event.remote_ip,
                event.http_protocol,
                json.dumps(event.timing) if event.timing else None,
                _detail_for(event),
            )
        )
    if not rows:
        return 0
    with get_db_conn() as conn:
        # REPLACE, so an `amend` re-emitted under the same id updates its row rather
        # than colliding -- the reason the key carries `boot_id`.
        conn.executemany(
            "INSERT OR REPLACE INTO telemetry_events (boot_id, ev_id, turn_id, round,"
            " ts, source, method, target, status, duration_ms, request_bytes,"
            " response_bytes, error, verdict, resource_type, remote_ip,"
            " http_protocol, timing, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def for_turn(turn_id: str, *, round_no: int | None = None) -> list[dict[str, Any]]:
    """Every persisted event for one turn, oldest first."""
    sql = "SELECT * FROM telemetry_events WHERE turn_id = ?"
    params: list[Any] = [turn_id]
    if round_no is not None:
        sql += " AND round = ?"
        params.append(round_no)
    sql += " ORDER BY ts"
    with get_db_conn() as conn:
        return [_row(r) for r in conn.execute(sql, params)]


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in row.keys()}
    for key in ("timing", "detail"):
        raw = out.get(key)
        if raw:
            try:
                out[key] = json.loads(raw)
            except ValueError:
                out[key] = None
    return out


def delete_turn(turn_id: str) -> int:
    """Drop one turn's events. Called when its run is deleted -- otherwise the wire
    traffic is orphaned and unreachable forever."""
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM telemetry_events WHERE turn_id = ?", (turn_id,))
        return cur.rowcount


def prune() -> int:
    """Enforce the age and count limits. Returns rows removed.

    Called from the drain rather than on a timer, for the reason
    `trajectories/recorder.py` gives about retention: this is the only moment the
    table is known to have just grown.
    """
    days = float(_setting("telemetry.retentionDays", 7) or 0)
    cap = int(_setting("telemetry.retentionEvents", 200_000) or 0)
    removed = 0
    with get_db_conn() as conn:
        if days > 0:
            cur = conn.execute(
                "DELETE FROM telemetry_events WHERE ts < ?",
                (time.time() - days * 86400,),
            )
            removed += cur.rowcount
        if cap > 0:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM telemetry_events"
            ).fetchone()["n"]
            if total > cap:
                cur = conn.execute(
                    "DELETE FROM telemetry_events WHERE rowid IN ("
                    " SELECT rowid FROM telemetry_events ORDER BY ts LIMIT ?)",
                    (total - cap,),
                )
                removed += cur.rowcount
    return removed


def count() -> int:
    with get_db_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM telemetry_events").fetchone()
        return int(row["n"])
