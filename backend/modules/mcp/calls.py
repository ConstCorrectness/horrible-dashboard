"""A durable summary of every MCP tool call, in `app.db`.

The JSON-RPC transcript (`transcript.py`) is deliberately a small in-process ring: it
is a debugging view, and tool arguments often carry the user's own text. This is the
other half — one row per call with no arguments and no results in it, only the facts a
postmortem and a dashboard need: which server and tool, which agent turn, how long,
whether it worked, and how big.

## Written from `McpSession.call_tool`, the one chokepoint

Every invocation passes there — the agent bridge, the pane's tool invoker, and the
conformance runner. Recording in the bridge instead would have been a smaller change
and would have missed two of the three, so a server exercised from the pane would
look idle on its own dashboard.

## Two kinds of failure, kept apart

`error_kind` is `tool` when the server answered and said the call failed (`isError`),
and `transport` when it never answered usefully: not connected, timed out, or the
session raised. They look identical from the model's side — an `error` key on a result
— and mean opposite things when diagnosing: the first is a bad call or a bad tool, the
second is a bad connection. An error rate that averages them tells you neither.

## Joined, not linked

A call does not know its trajectory step at the moment it runs; the step is written
after the call returns. So nothing here stores a run id. The join is
`(turn_id, round, server_id, tool)` plus the call's ordinal within that group, done by
`GET /api/trajectories/runs/{run_id}/mcp`. That ordinal match is exact because the
orchestrator runs a round's tool calls strictly in sequence and both rows come from
the same invocation.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger("mcp")

#: Rows kept. Each is a few hundred bytes with no payloads, so this is a generous
#: history rather than a storage concern — the cap exists so it is bounded at all.
MAX_ROWS = 50_000
#: Prune every Nth write, not every one.
PRUNE_EVERY = 500

ErrorKind = str  # "tool" | "transport"

_initialized: set[str] = set()
_writes = 0


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    # Lazy for the same reason as `telemetry/store.py`: `database.__init__` imports
    # `agent.routes`, and this module is reachable from the agent's import chain.
    from backend.modules.database.app_db import ensure_app_db_dir

    path = str(ensure_app_db_dir())
    if path not in _initialized:
        _initialized.add(path)
        init_calls_db()
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


def init_calls_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_calls (
                id             TEXT PRIMARY KEY,
                server_id      TEXT NOT NULL,
                tool           TEXT NOT NULL,
                turn_id        TEXT,
                round          INTEGER,
                started_at     REAL NOT NULL,
                duration_ms    INTEGER,
                ok             INTEGER NOT NULL,
                error_kind     TEXT,
                error          TEXT,
                request_bytes  INTEGER,
                response_bytes INTEGER,
                content_blocks INTEGER,
                rpc_ids        TEXT NOT NULL DEFAULT '',
                session        TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # `session` arrived after the table did. `CREATE TABLE IF NOT EXISTS` does
        # nothing to an existing table, so without this an install that already has
        # `mcp_calls` fails every insert on an unknown column.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(mcp_calls)")}
        if "session" not in cols:
            conn.execute(
                "ALTER TABLE mcp_calls ADD COLUMN session TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcp_calls_turn ON mcp_calls(turn_id, started_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcp_calls_server "
            "ON mcp_calls(server_id, started_at)"
        )


def record(
    *,
    server_id: str,
    tool: str,
    started_at: float,
    duration_ms: int | None,
    ok: bool,
    error_kind: ErrorKind | None = None,
    error: str | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    content_blocks: int | None = None,
    rpc_ids: list[str] | None = None,
    session: str = "",
    turn_id: str | None = None,
    round_no: int | None = None,
) -> str | None:
    """Write one call. Returns its id, or None if it could not be stored.

    Never raises: this runs after a tool call has already produced its answer, and a
    dashboard row is not worth turning a working call into a failed one.
    """
    global _writes
    call_id = uuid.uuid4().hex
    try:
        with get_db_conn() as conn:
            conn.execute(
                "INSERT INTO mcp_calls (id, server_id, tool, turn_id, round, started_at,"
                " duration_ms, ok, error_kind, error, request_bytes, response_bytes,"
                " content_blocks, rpc_ids, session)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call_id,
                    server_id,
                    tool,
                    turn_id,
                    round_no,
                    started_at,
                    duration_ms,
                    1 if ok else 0,
                    None if ok else error_kind,
                    None if ok else (error or "")[:500],
                    request_bytes,
                    response_bytes,
                    content_blocks,
                    ",".join(rpc_ids or []),
                    session,
                ),
            )
        _writes += 1
        if _writes % PRUNE_EVERY == 0:
            prune()
        return call_id
    except Exception:  # noqa: BLE001
        logger.debug("mcp: call summary dropped", exc_info=True)
        return None


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in row.keys()}
    out["ok"] = bool(out["ok"])
    out["rpc_ids"] = [i for i in (out.get("rpc_ids") or "").split(",") if i]
    return out


def list_calls(
    *,
    turn_id: str | None = None,
    server_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Calls oldest-first for a turn (that is the order they happened), newest-first
    otherwise (that is the order a reader scanning recent activity wants)."""
    where: list[str] = []
    params: list[Any] = []
    if turn_id:
        where.append("turn_id = ?")
        params.append(turn_id)
    if server_id:
        where.append("server_id = ?")
        params.append(server_id)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    order = "started_at ASC" if turn_id else "started_at DESC"
    with get_db_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM mcp_calls{clause} ORDER BY {order} LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [_row(r) for r in rows]


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of already-collected values; None when there are none.

    Nearest-rank rather than interpolated so every reported latency is one a call
    actually took — an interpolated p95 of `[10, 3000]` is a number no call ever had.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


def activity(*, since: float | None = None) -> list[dict[str, Any]]:
    """Per-server aggregates, with a per-tool breakdown, over a window.

    Computed in Python over the window's rows rather than in SQL: SQLite has no
    percentile function, the table is capped at `MAX_ROWS`, and nearest-rank over a
    plain list is short enough to be obviously right.
    """
    cutoff = since if since is not None else time.time() - 7 * 86400
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT server_id, tool, turn_id, duration_ms, ok, error_kind,"
            " request_bytes, response_bytes, content_blocks, started_at"
            " FROM mcp_calls WHERE started_at >= ?",
            (cutoff,),
        ).fetchall()

    servers: dict[str, dict[str, Any]] = {}
    for r in rows:
        server = servers.setdefault(
            r["server_id"],
            {"server_id": r["server_id"], "_rows": [], "_tools": {}, "_turns": set()},
        )
        server["_rows"].append(r)
        server["_tools"].setdefault(r["tool"], []).append(r)
        if r["turn_id"]:
            server["_turns"].add(r["turn_id"])

    out = []
    for server in servers.values():
        summary = _summarize(server["_rows"])
        summary["server_id"] = server["server_id"]
        summary["tools"] = sorted(
            (
                {"tool": name, **_summarize(tool_rows)}
                for name, tool_rows in server["_tools"].items()
            ),
            key=lambda t: t["calls"],
            reverse=True,
        )
        summary["runs"] = _run_cost(server["_turns"])
        out.append(summary)
    return sorted(out, key=lambda s: s["calls"], reverse=True)


def _summarize(rows: list[sqlite3.Row]) -> dict[str, Any]:
    durations = [float(r["duration_ms"]) for r in rows if r["duration_ms"] is not None]
    calls = len(rows)
    tool_errors = sum(1 for r in rows if not r["ok"] and r["error_kind"] == "tool")
    transport_errors = sum(1 for r in rows if not r["ok"] and r["error_kind"] != "tool")
    return {
        "calls": calls,
        "tool_errors": tool_errors,
        "transport_errors": transport_errors,
        # Rates are None rather than 0.0 with no calls: "never failed" and "never ran"
        # must not render alike.
        "tool_error_rate": tool_errors / calls if calls else None,
        "transport_error_rate": transport_errors / calls if calls else None,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "p99_ms": percentile(durations, 99),
        "request_bytes": sum(r["request_bytes"] or 0 for r in rows),
        "response_bytes": sum(r["response_bytes"] or 0 for r in rows),
        "content_blocks": sum(r["content_blocks"] or 0 for r in rows),
        "last_at": max((r["started_at"] for r in rows), default=None),
    }


def _run_cost(turn_ids: set[str]) -> dict[str, Any]:
    """What the *runs that used this server* cost — never "what this server cost".

    A turn's bill cannot be apportioned across the tools it called: the model's tokens
    are spent reading all of them together. So this reports the total over runs that
    touched the server, how many of those runs were priced at all, and says in its own
    field name which question it answers. A confident per-server dollar figure would be
    a number with no derivation behind it.
    """
    report: dict[str, Any] = {
        "runs_seen": 0,
        "runs_priced": 0,
        "cost_usd_of_runs_using_server": None,
    }
    if not turn_ids:
        return report
    try:
        from backend.modules.trajectories import store as traj

        ids = list(turn_ids)
        with traj.get_db_conn() as conn:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT cost_usd FROM traj_runs WHERE turn_id IN ({placeholders})",
                ids,
            ).fetchall()
    except Exception:  # noqa: BLE001 - trajectories absent or empty: no figure
        return report
    priced = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    report["runs_seen"] = len(rows)
    report["runs_priced"] = len(priced)
    report["cost_usd_of_runs_using_server"] = sum(priced) if priced else None
    return report


def prune() -> int:
    try:
        with get_db_conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM mcp_calls").fetchone()["n"]
            if total <= MAX_ROWS:
                return 0
            cur = conn.execute(
                "DELETE FROM mcp_calls WHERE id IN ("
                " SELECT id FROM mcp_calls ORDER BY started_at LIMIT ?)",
                (total - MAX_ROWS,),
            )
            return cur.rowcount
    except Exception:  # noqa: BLE001
        logger.debug("mcp: prune failed", exc_info=True)
        return 0
