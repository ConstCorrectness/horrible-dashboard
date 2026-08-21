"""Trajectories in `app.db`, oversized payloads on disk.

Results go in `app.db` rather than a private database for the reason the evals
module already states: the `database` console's built-in `app` connection and
`dash` can then query them with no new API. "Which tool does the coder waste the
most rounds on" is a `GROUP BY`, not a feature request — and because the run rows
carry `turn_id`, they join straight onto interpretability's `agent_turns` (what the
model was *shown*) and onto `eval_results` (how it was graded).

Timestamps are **REAL epoch seconds**, matching `agent_turns`. The repo has three
timestamp conventions in three modules; this table's most important join partner
decides which one applies here, because a join that needs a format conversion is a
join nobody writes.

## Payloads spill, they never truncate

A step's `args`/`result` are inlined while their JSON is at most
`STEP_PAYLOAD_MAX`. Above that the payload is written under
`$HORRIBLE_DATA_DIR/trajectories/<run_id>/` and the column holds `blob:<relpath>`.
Clipping instead would throw away exactly the 400 KB tool result you opened the
postmortem to read. (`llamacpp/traces.py` is the precedent for the on-disk half.)

## Raw in, redacted out

Tool arguments are stored **raw** — this is a local introspection tool, the stance
`telemetry/models.py` already takes for I/O bodies, and a debugger that has already
dropped the value cannot help you. That means `app.db` holds whatever your agent
passed to a tool, credentials included. `redact()` here is the boundary applied at
the three exits where data leaves the node: SFT export, peer share, and MCP content
exposure. It shape-matches on key suffixes (`SECRET_KEY_SUFFIXES`) rather than
trusting a declaration, for the same reason the settings blanking does.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir, get_data_dir
from backend.modules.settings.models import SECRET_KEY_SUFFIXES
from backend.modules.trajectories.models import (
    Dataset,
    Harness,
    HarnessWrite,
    LabelWrite,
    StepWrite,
    TrajectoryDetail,
    TrajectoryLabel,
    TrajectoryRun,
    TrajectoryStep,
    TrajectoryWrite,
)

logger = logging.getLogger("trajectories")

#: Inline a payload up to this many JSON bytes; spill anything larger to disk.
STEP_PAYLOAD_MAX = 16 * 1024

#: Prefix marking a column value as a pointer into the run's blob directory.
BLOB_PREFIX = "blob:"

#: What `redact()` puts in place of a secret-shaped value.
REDACTED = "[redacted]"

# Keyed by path, not a bare bool: `HORRIBLE_DATA_DIR` is env-driven and a test
# that points it at a fresh tmp dir would otherwise inherit a True flag from the
# previous test and query tables that were never created in the new file.
_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked *before* the call: `init_trajectories_db` opens a connection
        # through this same helper, so marking afterwards would recurse forever.
        _initialized.add(path)
        init_trajectories_db()
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


def blobs_dir(run_id: str) -> Path:
    path = get_data_dir() / "trajectories" / _safe_id(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    """Add a column if it is not there yet. SQLite has no `ADD COLUMN IF NOT EXISTS`."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_trajectories_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traj_datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT 'local',
                capture INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '[]',
                schema_version INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traj_harnesses (
                fingerprint TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                system_prompt TEXT NOT NULL DEFAULT '',
                tool_names TEXT NOT NULL DEFAULT '[]',
                tool_schemas TEXT NOT NULL DEFAULT '{}',
                params TEXT NOT NULL DEFAULT '{}',
                label TEXT NOT NULL DEFAULT '',
                first_seen REAL NOT NULL DEFAULT 0,
                last_seen REAL NOT NULL DEFAULT 0,
                run_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traj_runs (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'local',
                external_id TEXT,
                turn_id TEXT,
                parent_run_id TEXT,
                harness TEXT,
                agent_id TEXT NOT NULL DEFAULT '',
                agent_name TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                outcome TEXT,
                reward REAL,
                steps INTEGER NOT NULL DEFAULT 0,
                rounds INTEGER NOT NULL DEFAULT 0,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                started_at REAL NOT NULL DEFAULT 0,
                finished_at REAL,
                duration_ms INTEGER,
                error TEXT NOT NULL DEFAULT '',
                node_id TEXT NOT NULL DEFAULT '',
                person_id TEXT NOT NULL DEFAULT '',
                meta TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traj_steps (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                round INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL DEFAULT 'action',
                role TEXT,
                name TEXT,
                args TEXT,
                result TEXT,
                ok INTEGER,
                content TEXT,
                tokens INTEGER,
                duration_ms INTEGER,
                gated INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                ts REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (run_id, seq)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traj_labels (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_seq INTEGER,
                key TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                score REAL,
                source TEXT NOT NULL DEFAULT 'human',
                rationale TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        # Added after the table shipped, so it needs an explicit ALTER: a
        # `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
        # exists, and skipping this is how a new column reaches nobody's machine
        # but the one it was written on.
        _ensure_column(conn, "traj_runs", "indexed_at", "REAL")

        # Idempotent ingest depends on this being enforced by the database rather
        # than by a SELECT-then-INSERT, which races two SDK clients against
        # each other. Partial, so the many runs with no external id do not all
        # collide on NULL.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_traj_runs_external "
            "ON traj_runs(dataset_id, external_id) WHERE external_id IS NOT NULL"
        )
        # The listing, the two joins, and the delegation tree.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traj_runs_dataset "
            "ON traj_runs(dataset_id, started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traj_runs_turn ON traj_runs(turn_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traj_runs_harness ON traj_runs(harness)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traj_runs_parent ON traj_runs(parent_run_id)"
        )
        # "How often is this tool called, and how often does it fail" is the
        # single most-run aggregate in the module.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traj_steps_name ON traj_steps(name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traj_labels_run ON traj_labels(run_id)"
        )


def _now() -> float:
    return time.time()


def _safe_id(raw: str) -> str:
    """Strip anything that could walk out of the blob directory.

    A run id reaches this from an HTTP path, and `..` is how a delete route
    becomes an arbitrary-file-delete route.
    """
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return cleaned or "unknown"


def new_run_id() -> str:
    """A time-sortable id, so `ORDER BY id` is chronological and the common
    listing needs no extra index."""
    return f"{int(_now() * 1000):013d}-{uuid.uuid4().hex[:8]}"


# --- canonicalisation and fingerprinting ------------------------------------


def canonical_json(value: Any) -> str:
    """The one canonical encoding, matching the peer wire's rule.

    `sort_keys` + no whitespace, so the same harness hashes identically in two
    processes, on two machines, and across a restart. Anything looser and a
    fingerprint drifts, which does not fail — it silently splits one harness into
    two and makes every comparison across them empty.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint_harness(h: HarnessWrite | Harness) -> str:
    """Content-address a harness.

    Deliberately covers only what changes behaviour: the prompt, the tools by name
    *and* schema, the model, the provider and the sampling params. It does not
    cover the label — renaming a harness must not fork it.
    """
    payload = {
        "agent_id": h.agent_id,
        "model": h.model,
        "provider": h.provider,
        "system_prompt": h.system_prompt,
        "tool_names": sorted(h.tool_names),
        "tool_schemas": h.tool_schemas,
        "params": h.params,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


# --- redaction --------------------------------------------------------------


def _is_secret_key(key: str) -> bool:
    lower = key.lower()
    return any(lower.endswith(suffix) for suffix in SECRET_KEY_SUFFIXES)


def redact(value: Any) -> Any:
    """Blank secret-shaped keys, recursively.

    The boundary function for the three exits where trajectory data leaves this
    node. Matches on the key *shape*, never on a declaration: a declaration would
    mean asking the data which parts of itself are sensitive.
    """
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_secret_key(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# --- payload spill ----------------------------------------------------------


def _write_payload(run_id: str, seq: int, field: str, value: Any) -> str | None:
    """Encode a step payload, spilling to disk when it is too big to inline."""
    if value is None:
        return None
    encoded = json.dumps(value, default=str)
    if len(encoded) <= STEP_PAYLOAD_MAX:
        return encoded
    name = f"{seq}.{field}.json"
    try:
        (blobs_dir(run_id) / name).write_text(encoded, encoding="utf-8")
    except OSError as exc:
        # Losing the payload must not lose the step. Record the failure in place
        # of the value so the pane can say why it is missing.
        logger.warning("trajectories: could not spill %s/%s: %s", run_id, name, exc)
        return json.dumps({"_spill_error": str(exc), "bytes": len(encoded)})
    return f"{BLOB_PREFIX}{name}"


def _read_payload(run_id: str, raw: str | None) -> Any:
    """Decode a step payload, following a `blob:` pointer when there is one."""
    if raw is None or raw == "":
        return None
    if raw.startswith(BLOB_PREFIX):
        # `Path(...).name` is the traversal guard: the stored pointer is written
        # by `_write_payload`, but a row in `app.db` is editable by anything with
        # the file, and this path ends in a filesystem read.
        name = Path(raw[len(BLOB_PREFIX) :]).name
        try:
            text = (blobs_dir(run_id) / name).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("trajectories: blob %s/%s unreadable: %s", run_id, name, exc)
            return {"_blob_missing": name}
    else:
        text = raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# --- datasets ---------------------------------------------------------------


def _dataset_from_row(row: sqlite3.Row, run_count: int = 0) -> Dataset:
    return Dataset(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        source_kind=row["source_kind"],
        capture=bool(row["capture"]),
        tags=json.loads(row["tags"] or "[]"),
        schema_version=row["schema_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        run_count=run_count,
    )


def create_dataset(
    dataset_id: str,
    name: str,
    description: str = "",
    source_kind: str = "local",
    capture: bool = False,
    tags: list[str] | None = None,
) -> Dataset:
    now = _now()
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO traj_datasets (id, name, description, source_kind, capture,"
            " tags, schema_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                dataset_id,
                name,
                description,
                source_kind,
                1 if capture else 0,
                json.dumps(tags or []),
                now,
                now,
            ),
        )
    return Dataset(
        id=dataset_id,
        name=name,
        description=description,
        source_kind=source_kind,  # type: ignore[arg-type]
        capture=capture,
        tags=tags or [],
        created_at=now,
        updated_at=now,
    )


def list_datasets() -> list[Dataset]:
    with get_db_conn() as conn:
        counts = {
            r["dataset_id"]: r["n"]
            for r in conn.execute(
                "SELECT dataset_id, COUNT(*) AS n FROM traj_runs GROUP BY dataset_id"
            )
        }
        rows = conn.execute("SELECT * FROM traj_datasets ORDER BY created_at DESC")
        return [_dataset_from_row(r, counts.get(r["id"], 0)) for r in rows]


def get_dataset(dataset_id: str) -> Dataset | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM traj_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if row is None:
            return None
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM traj_runs WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()["n"]
        return _dataset_from_row(row, n)


def update_dataset(dataset_id: str, **fields: Any) -> Dataset | None:
    sets: list[str] = []
    values: list[Any] = []
    for key in ("name", "description", "capture", "tags"):
        if fields.get(key) is None:
            continue
        value = fields[key]
        if key == "capture":
            value = 1 if value else 0
        elif key == "tags":
            value = json.dumps(value)
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_dataset(dataset_id)
    sets.append("updated_at = ?")
    values.extend([_now(), dataset_id])
    with get_db_conn() as conn:
        conn.execute(f"UPDATE traj_datasets SET {', '.join(sets)} WHERE id = ?", values)
    return get_dataset(dataset_id)


def delete_dataset(dataset_id: str) -> bool:
    """Drop a dataset and every run in it. Blob directories go with the runs."""
    with get_db_conn() as conn:
        run_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM traj_runs WHERE dataset_id = ?", (dataset_id,)
            )
        ]
        cur = conn.execute("DELETE FROM traj_datasets WHERE id = ?", (dataset_id,))
        removed = cur.rowcount > 0
    for run_id in run_ids:
        delete_run(run_id)
    # Deleting a whole dataset is an explicit "remove this body of work", so the
    # configurations that only existed to describe it go too.
    prune_orphan_harnesses()
    return removed


def prune_orphan_harnesses() -> int:
    """Drop harness rows no run references any more. Returns how many went.

    A harness is only ever reachable through its runs — you pick one from a list
    to compare, or you follow a run to it. One with no runs cannot be compared
    (nothing graded), cannot be searched, and cannot be reached from anything;
    it is a permanent entry in the compare picker offering an empty report.

    Called when a **dataset** is deleted, not when a single run is: deleting one
    run of fifty is not a statement about the configuration, and a harness that
    vanished on the last delete would take its fingerprint with it — so a re-run
    of the same configuration would look like a different harness in every
    report drawn before the delete.
    """
    with get_db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM traj_harnesses WHERE fingerprint NOT IN"
            " (SELECT DISTINCT harness FROM traj_runs WHERE harness IS NOT NULL)"
        )
        return cur.rowcount


def capture_dataset_id() -> str | None:
    """The dataset live capture writes into, or None when capture is off.

    Off is the default and the common case, so this is the cheapest question the
    recorder can ask. The first dataset with `capture=1` wins; the pane enforces
    that only one is set at a time.
    """
    try:
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM traj_datasets WHERE capture = 1 ORDER BY updated_at DESC"
                " LIMIT 1"
            ).fetchone()
            return row["id"] if row else None
    except Exception as exc:  # pragma: no cover - observation must not break the app
        logger.debug("trajectories: capture lookup failed: %s", exc)
        return None


def ensure_dataset(dataset_id: str, name: str = "", source_kind: str = "local") -> None:
    """Create a dataset if it is missing.

    Ingest from an adapter or the SDK should not fail because nobody clicked
    "new dataset" first — a rejected trajectory is a trajectory lost forever,
    while an auto-created dataset is a row you can rename.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM traj_datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if row is not None:
            return
    create_dataset(dataset_id, name or dataset_id, source_kind=source_kind)


# --- harnesses --------------------------------------------------------------


def _harness_from_row(row: sqlite3.Row, run_count: int = 0) -> Harness:
    """`run_count` is **derived**, never stored.

    It began as a counter bumped in `start_run`, which meant deleting a run left
    the harness claiming runs that no longer existed — and the dropdown you pick
    harnesses from is labelled with it. A denormalised counter that only ever
    increments is a lie with a delay on it.
    """
    return Harness(
        fingerprint=row["fingerprint"],
        agent_id=row["agent_id"],
        model=row["model"],
        provider=row["provider"],
        system_prompt=row["system_prompt"],
        tool_names=json.loads(row["tool_names"] or "[]"),
        tool_schemas=json.loads(row["tool_schemas"] or "{}"),
        params=json.loads(row["params"] or "{}"),
        label=row["label"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        run_count=run_count,
    )


def upsert_harness(h: HarnessWrite) -> str:
    """Record a harness if it is new, touch it if it is not. Returns the fingerprint."""
    fp = fingerprint_harness(h)
    now = _now()
    label = h.label or f"{h.agent_id or 'agent'} @ {h.model or 'model'}"
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT fingerprint FROM traj_harnesses WHERE fingerprint = ?", (fp,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO traj_harnesses (fingerprint, agent_id, model, provider,"
                " system_prompt, tool_names, tool_schemas, params, label, first_seen,"
                " last_seen, run_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    fp,
                    h.agent_id,
                    h.model,
                    h.provider,
                    h.system_prompt,
                    json.dumps(sorted(h.tool_names)),
                    canonical_json(h.tool_schemas),
                    canonical_json(h.params),
                    label,
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                "UPDATE traj_harnesses SET last_seen = ? WHERE fingerprint = ?",
                (now, fp),
            )
    return fp


def list_harnesses(limit: int = 100) -> list[Harness]:
    with get_db_conn() as conn:
        counts = {
            r["harness"]: r["n"]
            for r in conn.execute(
                "SELECT harness, COUNT(*) AS n FROM traj_runs WHERE harness IS NOT NULL"
                " GROUP BY harness"
            )
        }
        rows = conn.execute(
            "SELECT * FROM traj_harnesses ORDER BY last_seen DESC LIMIT ?", (limit,)
        )
        return [_harness_from_row(r, counts.get(r["fingerprint"], 0)) for r in rows]


def get_harness(fingerprint: str) -> Harness | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM traj_harnesses WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if row is None:
            return None
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM traj_runs WHERE harness = ?", (fingerprint,)
        ).fetchone()["n"]
        return _harness_from_row(row, n)


# --- runs -------------------------------------------------------------------


def _run_from_row(row: sqlite3.Row) -> TrajectoryRun:
    return TrajectoryRun(
        id=row["id"],
        dataset_id=row["dataset_id"],
        source=row["source"],
        external_id=row["external_id"],
        turn_id=row["turn_id"],
        parent_run_id=row["parent_run_id"],
        harness=row["harness"],
        agent_id=row["agent_id"],
        agent_name=row["agent_name"],
        model=row["model"],
        provider=row["provider"],
        goal=row["goal"],
        status=row["status"],
        outcome=row["outcome"],
        reward=row["reward"],
        steps=row["steps"],
        rounds=row["rounds"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost_usd=row["cost_usd"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
        error=row["error"],
        node_id=row["node_id"],
        person_id=row["person_id"],
        meta=json.loads(row["meta"] or "{}"),
    )


def start_run(
    dataset_id: str,
    *,
    source: str = "local",
    run_id: str | None = None,
    external_id: str | None = None,
    turn_id: str | None = None,
    parent_run_id: str | None = None,
    harness: str | None = None,
    agent_id: str = "",
    agent_name: str = "",
    model: str = "",
    provider: str = "",
    goal: str = "",
    node_id: str = "",
    person_id: str = "",
    meta: dict[str, Any] | None = None,
    started_at: float | None = None,
) -> str:
    """Open a run in `running` state. Returns its id."""
    rid = run_id or new_run_id()
    with get_db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO traj_runs (id, dataset_id, source, external_id,"
            " turn_id, parent_run_id, harness, agent_id, agent_name, model, provider,"
            " goal, status, steps, rounds, started_at, error, node_id, person_id, meta)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 0, 0, ?, '', ?, ?, ?)",
            (
                rid,
                dataset_id,
                source,
                external_id,
                turn_id,
                parent_run_id,
                harness,
                agent_id,
                agent_name,
                model,
                provider,
                goal,
                started_at if started_at is not None else _now(),
                node_id,
                person_id,
                canonical_json(meta or {}),
            ),
        )
    return rid


def append_step(run_id: str, step: StepWrite) -> int:
    """Append one step. Returns the assigned `seq`.

    Refuses to append to a sealed run: a run's steps are what an export is built
    from, and a step arriving after the export was taken would make that export
    unreproducible without anything ever reporting an error.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT status, steps FROM traj_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such run: {run_id}")
        if row["status"] != "running":
            raise ValueError(f"run {run_id} is sealed ({row['status']})")
        seq = step.seq if step.seq is not None else int(row["steps"])
        conn.execute(
            "INSERT OR REPLACE INTO traj_steps (run_id, seq, round, kind, role, name,"
            " args, result, ok, content, tokens, duration_ms, gated, error, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                seq,
                step.round,
                step.kind,
                step.role,
                step.name,
                _write_payload(run_id, seq, "args", step.args),
                _write_payload(run_id, seq, "result", step.result),
                None if step.ok is None else (1 if step.ok else 0),
                step.content,
                step.tokens,
                step.duration_ms,
                1 if step.gated else 0,
                step.error,
                step.ts if step.ts is not None else _now(),
            ),
        )
        conn.execute(
            "UPDATE traj_runs SET steps = (SELECT COUNT(*) FROM traj_steps WHERE"
            " run_id = ?) WHERE id = ?",
            (run_id, run_id),
        )
    return seq


def finish_run(
    run_id: str,
    *,
    status: str = "complete",
    outcome: str | None = None,
    reward: float | None = None,
    rounds: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    error: str = "",
    finished_at: float | None = None,
) -> None:
    """Seal a run. After this, steps are immutable and only labels may be added."""
    end = finished_at if finished_at is not None else _now()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT started_at FROM traj_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return
        duration = int(max(0.0, end - float(row["started_at"] or end)) * 1000)
        conn.execute(
            "UPDATE traj_runs SET status = ?, outcome = COALESCE(?, outcome),"
            " reward = COALESCE(?, reward), rounds = COALESCE(?, rounds),"
            " tokens_in = COALESCE(?, tokens_in), tokens_out = COALESCE(?, tokens_out),"
            " cost_usd = COALESCE(?, cost_usd), error = ?, finished_at = ?,"
            " duration_ms = ? WHERE id = ?",
            (
                status,
                outcome,
                reward,
                rounds,
                tokens_in,
                tokens_out,
                cost_usd,
                error,
                end,
                duration,
                run_id,
            ),
        )


def get_run(run_id: str, *, with_steps: bool = True) -> TrajectoryDetail | None:
    with get_db_conn() as conn:
        row = conn.execute("SELECT * FROM traj_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        detail = TrajectoryDetail(**_run_from_row(row).model_dump())
        if with_steps:
            detail.step_list = [
                _step_from_row(run_id, r)
                for r in conn.execute(
                    "SELECT * FROM traj_steps WHERE run_id = ? ORDER BY seq", (run_id,)
                )
            ]
        detail.labels = [
            _label_from_row(r)
            for r in conn.execute(
                "SELECT * FROM traj_labels WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            )
        ]
    if detail.harness:
        detail.harness_detail = get_harness(detail.harness)
    return detail


def _step_from_row(run_id: str, row: sqlite3.Row) -> TrajectoryStep:
    return TrajectoryStep(
        seq=row["seq"],
        kind=row["kind"],
        round=row["round"],
        role=row["role"],
        name=row["name"],
        args=_read_payload(run_id, row["args"]),
        result=_read_payload(run_id, row["result"]),
        ok=None if row["ok"] is None else bool(row["ok"]),
        content=row["content"],
        tokens=row["tokens"],
        duration_ms=row["duration_ms"],
        gated=bool(row["gated"]),
        error=row["error"],
        ts=row["ts"],
    )


def _label_from_row(row: sqlite3.Row) -> TrajectoryLabel:
    return TrajectoryLabel(
        id=row["id"],
        run_id=row["run_id"],
        step_seq=row["step_seq"],
        key=row["key"],
        value=row["value"],
        score=row["score"],
        source=row["source"],
        rationale=row["rationale"],
        created_at=row["created_at"],
    )


def list_runs(
    *,
    dataset_id: str | None = None,
    source: str | None = None,
    harness: str | None = None,
    outcome: str | None = None,
    agent_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TrajectoryRun], int]:
    where: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("dataset_id", dataset_id),
        ("source", source),
        ("harness", harness),
        ("outcome", outcome),
        ("agent_id", agent_id),
        ("status", status),
    ):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if q:
        where.append("goal LIKE ?")
        params.append(f"%{q}%")
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    with get_db_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM traj_runs{clause}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM traj_runs{clause} ORDER BY started_at DESC, id DESC"
            " LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [_run_from_row(r) for r in rows], total


def prune(dataset_id: str, keep: int) -> int:
    """Drop the oldest runs beyond `keep`, and their blobs. Returns how many went.

    Oldest-first rather than by size: a trajectory store whose retention evicted
    the big runs would silently delete exactly the ones worth keeping.
    """
    if keep <= 0:
        return 0
    with get_db_conn() as conn:
        doomed = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM traj_runs WHERE dataset_id = ?"
                " ORDER BY started_at DESC, id DESC LIMIT -1 OFFSET ?",
                (dataset_id, keep),
            )
        ]
    for run_id in doomed:
        delete_run(run_id)
    return len(doomed)


def mark_indexed(run_ids: list[str], *, reset_dataset: str | None = None) -> None:
    """Stamp runs as present in the vector index.

    `reset_dataset` clears the stamp for a whole dataset, which is what a full
    rebuild needs: the collection is dropped, so every run in it is unindexed
    again and a stale stamp would make the rebuild skip everything.
    """
    with get_db_conn() as conn:
        if reset_dataset is not None:
            conn.execute(
                "UPDATE traj_runs SET indexed_at = NULL WHERE dataset_id = ?",
                (reset_dataset,),
            )
        elif reset_dataset is None and not run_ids:
            return
        now = _now()
        for run_id in run_ids:
            conn.execute(
                "UPDATE traj_runs SET indexed_at = ? WHERE id = ?", (now, run_id)
            )


def unindexed_run_ids(dataset_id: str | None = None, limit: int = 5000) -> list[str]:
    """Runs that are sealed but not yet in the vector index.

    Only sealed runs: a `running` run has no final answer yet, and indexing it
    would pin a half-written document that nothing ever refreshes.
    """
    where = ["indexed_at IS NULL", "status != 'running'"]
    params: list[Any] = []
    if dataset_id:
        where.append("dataset_id = ?")
        params.append(dataset_id)
    with get_db_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM traj_runs WHERE {' AND '.join(where)}"
            " ORDER BY started_at DESC LIMIT ?",
            [*params, limit],
        )
        return [r["id"] for r in rows]


def find_by_turn_id(turn_id: str) -> TrajectoryRun | None:
    """The run recorded for one orchestrator turn.

    Indexed on `turn_id`, which is also the join into interpretability's
    `agent_turns` — the delegation tree is built from this, since a delegated
    turn knows only its parent's *turn* id.
    """
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM traj_runs WHERE turn_id = ? ORDER BY started_at DESC LIMIT 1",
            (turn_id,),
        ).fetchone()
        return _run_from_row(row) if row else None


def delete_run(run_id: str) -> bool:
    with get_db_conn() as conn:
        conn.execute("DELETE FROM traj_steps WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM traj_labels WHERE run_id = ?", (run_id,))
        cur = conn.execute("DELETE FROM traj_runs WHERE id = ?", (run_id,))
        removed = cur.rowcount > 0
    directory = get_data_dir() / "trajectories" / _safe_id(run_id)
    if directory.is_dir():
        for child in directory.iterdir():
            try:
                child.unlink()
            except OSError:  # pragma: no cover
                pass
        try:
            directory.rmdir()
        except OSError:  # pragma: no cover
            pass
    return removed


def add_label(run_id: str, label: LabelWrite) -> TrajectoryLabel:
    row = TrajectoryLabel(
        id=uuid.uuid4().hex[:12],
        run_id=run_id,
        step_seq=label.step_seq,
        key=label.key,
        value=label.value,
        score=label.score,
        source=label.source,
        rationale=label.rationale,
        created_at=_now(),
    )
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO traj_labels (id, run_id, step_seq, key, value, score, source,"
            " rationale, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.id,
                row.run_id,
                row.step_seq,
                row.key,
                row.value,
                row.score,
                row.source,
                row.rationale,
                row.created_at,
            ),
        )
        # A label carrying an outcome is the usual way a run gets graded, so
        # mirror it onto the run for the cheap `GROUP BY outcome`. The label row
        # stays the record of who said so.
        if row.key == "outcome" and row.step_seq is None and row.value:
            conn.execute(
                "UPDATE traj_runs SET outcome = ? WHERE id = ?", (row.value, run_id)
            )
    return row


# --- ingest -----------------------------------------------------------------


def ingest_run(write: TrajectoryWrite) -> tuple[str, bool]:
    """Write one whole run. Returns `(run_id, created)`.

    Idempotent on `(dataset_id, external_id)`: a retried SDK batch replaces the
    run's steps rather than filing a second copy of it. Without that, any client
    with a retry policy silently doubles its own dataset.
    """
    ensure_dataset(write.dataset_id, source_kind=write.source)
    fingerprint = upsert_harness(write.harness) if write.harness else None

    # The harness is authoritative for who ran and on what, so a caller that set
    # those there does not have to repeat them on the run. Without this the run's
    # `model` stays empty and `meta.drawn_from` in an SFT export — which exists so
    # a chat-template mismatch is at least visible — silently says nothing.
    agent_id = write.agent_id
    model = write.model
    provider = write.provider
    if write.harness:
        agent_id = agent_id or write.harness.agent_id
        model = model or write.harness.model
        provider = provider or write.harness.provider

    existing: str | None = None
    if write.external_id:
        with get_db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM traj_runs WHERE dataset_id = ? AND external_id = ?",
                (write.dataset_id, write.external_id),
            ).fetchone()
            existing = row["id"] if row else None

    run_id = existing or write.run_id or new_run_id()
    if existing:
        # Replacing, not appending: the incoming payload is the whole run.
        with get_db_conn() as conn:
            conn.execute("DELETE FROM traj_steps WHERE run_id = ?", (run_id,))

    start_run(
        write.dataset_id,
        source=write.source,
        run_id=run_id,
        external_id=write.external_id,
        turn_id=write.turn_id,
        parent_run_id=write.parent_run_id,
        harness=fingerprint,
        agent_id=agent_id,
        agent_name=write.agent_name,
        model=model,
        provider=provider,
        goal=write.goal,
        node_id=write.node_id,
        person_id=write.person_id,
        meta=write.meta,
        started_at=write.started_at,
    )
    for index, step in enumerate(write.step_list):
        if step.seq is None:
            step = step.model_copy(update={"seq": index})
        append_step(run_id, step)
    if write.status != "running":
        finish_run(
            run_id,
            status=write.status,
            outcome=write.outcome,
            reward=write.reward,
            rounds=write.rounds or None,
            tokens_in=write.tokens_in,
            tokens_out=write.tokens_out,
            cost_usd=write.cost_usd,
            error=write.error,
            finished_at=write.finished_at,
        )
    for label in write.labels:
        add_label(
            run_id,
            LabelWrite(
                step_seq=label.step_seq,
                key=label.key,
                value=label.value,
                score=label.score,
                source=label.source,
                rationale=label.rationale,
            ),
        )
    return run_id, existing is None
