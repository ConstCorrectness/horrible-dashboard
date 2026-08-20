"""Suites on disk, results in `app.db`.

The split is the one the library and karaoke modules already use, and it is the
right one here for a specific reason: **a suite is source code and a result is
data**. You edit a suite in the editor, diff it, and commit it; you never edit a
result. So cases live in a `.jsonl` file you can open in a buffer, and the database
holds only the catalog row that points at it plus everything the runs produced.

Results go in `app.db` rather than a private database so the `database` console's
built-in `app` connection and `dash` can query them with no new API — "which cases
does every model fail" is a `GROUP BY`, not a feature request.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir, get_data_dir
from backend.modules.evals import bundled
from backend.modules.evals.models import (
    CaseResult,
    EvalCase,
    EvalRun,
    EvalSuite,
    ToolCall,
)

# Keyed by path, not a bare bool: `HORRIBLE_DATA_DIR` is env-driven and a test that
# points it at a fresh tmp dir would otherwise inherit a True flag from the
# previous test and query tables that were never created in the new file.
_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked *before* the call: `init_evals_db` opens a connection through this
        # same helper, so marking afterwards would recurse forever.
        _initialized.add(path)
        init_evals_db()
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


def suites_dir() -> Path:
    """Where suite files live. Created on demand."""
    path = get_data_dir() / "evals" / "suites"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    """Add a column if it is not there yet. SQLite has no `ADD COLUMN IF NOT EXISTS`."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_evals_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_suites (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_runs (
                id TEXT PRIMARY KEY,
                suite_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                endpoint TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                total INTEGER NOT NULL DEFAULT 0,
                passed INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                localtrack_run_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_results (
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                passed INTEGER NOT NULL DEFAULT 0,
                grade TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                expected TEXT NOT NULL DEFAULT '[]',
                actual TEXT NOT NULL DEFAULT '[]',
                answer TEXT NOT NULL DEFAULT '',
                rounds INTEGER NOT NULL DEFAULT 0,
                tools_offered INTEGER NOT NULL DEFAULT 0,
                tools_dropped TEXT NOT NULL DEFAULT '[]',
                groups_loaded TEXT NOT NULL DEFAULT '[]',
                duration_ms REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                case_hash TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, case_id)
            )
            """
        )
        # `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
        # exists, so a column added later reaches an upgraded install only through
        # an explicit ALTER. Skipping this is how a new field silently never
        # appears on anyone's machine but the one it was written on.
        _ensure_column(conn, "eval_results", "case_hash", "TEXT NOT NULL DEFAULT ''")

        # The two questions the scoreboard asks: every result for one run, and
        # every run's verdict on one case (the "which model fixed this" column).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(run_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eval_results_case ON eval_results(case_id)"
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- suites -----------------------------------------------------------------


def suite_path(suite_id: str) -> Path:
    return suites_dir() / f"{suite_id}.jsonl"


def create_suite(
    name: str, description: str = "", tags: list[str] | None = None
) -> EvalSuite:
    suite_id = uuid.uuid4().hex[:12]
    path = suite_path(suite_id)
    path.touch(exist_ok=True)
    now = _now()
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO eval_suites (id, name, description, path, tags, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (suite_id, name, description, str(path), json.dumps(tags or []), now, now),
        )
    return EvalSuite(
        id=suite_id,
        name=name,
        description=description,
        path=str(path),
        tags=tags or [],
        created_at=now,
        updated_at=now,
    )


def _count(suite: EvalSuite) -> EvalSuite:
    """Fill in `case_count`, leniently.

    A suite with a syntax error in it must still be *listable* and openable, or one
    bad line would take out the whole suite list and leave no way to reach the file
    and fix it. Reporting the parse error is the `get_cases` route's job, where
    there is a pane to show it in.
    """
    try:
        suite.case_count = len(load_cases(suite))
    except SuiteFormatError:
        suite.case_count = 0
    return suite


def _suite_from_row(row: sqlite3.Row) -> EvalSuite:
    suite = EvalSuite(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        path=row["path"],
        tags=json.loads(row["tags"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
    # Counted from the file rather than stored, because the file is editable
    # outside this module — that is the point of keeping cases in a buffer you can
    # open — and a cached count would be wrong the moment somebody saved.
    return _count(suite)


def list_suites() -> list[EvalSuite]:
    """Yours and the bundled ones, in one list.

    Bundled first, the way `hassault` lists its own maps ahead of an install's: a
    fresh node has something to run on day one, and the two catalogs cannot shadow
    each other because a bundled id is prefixed and a user id is 12 hex characters.
    """
    with get_db_conn() as conn:
        rows = conn.execute("SELECT * FROM eval_suites ORDER BY name").fetchall()
    mine = [_suite_from_row(r) for r in rows]
    return [_count(s) for s in bundled.list_bundled()] + mine


def get_suite(suite_id: str) -> EvalSuite | None:
    if bundled.is_bundled(suite_id):
        suite = bundled.get_bundled(suite_id)
        return _count(suite) if suite else None
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM eval_suites WHERE id = ?", (suite_id,)
        ).fetchone()
    return _suite_from_row(row) if row else None


def fork_suite(suite_id: str, name: str = "") -> EvalSuite:
    """Copy a suite's cases into a new one you own.

    How a bundled suite becomes editable. Copies the *cases* rather than the file
    so a fork is normalised through the same parser everything else uses — a fork
    of a suite with a broken line would otherwise carry the broken line forward.
    """
    source = get_suite(suite_id)
    if source is None:
        raise ValueError(f"no suite {suite_id!r}")
    cases = load_cases(source)
    fork = create_suite(
        name or f"{source.name} (copy)", source.description, list(source.tags)
    )
    write_cases(fork, cases)
    return get_suite(fork.id) or fork


def delete_suite(suite_id: str, *, remove_file: bool = False) -> bool:
    """Forget a suite. The file survives unless asked otherwise — a suite is source
    and deleting somebody's authored cases because they tidied a list is not a
    trade this module gets to make on their behalf."""
    suite = get_suite(suite_id)
    if suite is None:
        return False
    if suite.read_only or bundled.is_bundled(suite_id):
        raise ReadOnlySuiteError(
            f"{suite.name!r} ships with the app and cannot be deleted"
        )
    with get_db_conn() as conn:
        conn.execute("DELETE FROM eval_suites WHERE id = ?", (suite_id,))
    if remove_file:
        Path(suite.path).unlink(missing_ok=True)
    return True


class SuiteFormatError(ValueError):
    """A case file that could not be read, naming the line.

    Line-numbered because a suite is hand-authored JSONL and "invalid JSON" without
    a line number in a 200-case file is not a usable error message.
    """


def load_cases(suite: EvalSuite) -> list[EvalCase]:
    """Parse a suite's `.jsonl`.

    Blank lines and `#` comments are skipped: a hand-authored suite wants to be
    groupable, and JSONL has no comment syntax of its own.
    """
    path = Path(suite.path)
    if not path.exists():
        return []
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SuiteFormatError(f"{path.name} line {lineno}: {exc.msg}") from exc
        try:
            case = EvalCase.model_validate(data)
        except Exception as exc:
            raise SuiteFormatError(f"{path.name} line {lineno}: {exc}") from exc
        if case.id in seen:
            # A duplicate id is not a harmless typo: results are keyed by
            # (run, case), so the second one would overwrite the first and the
            # suite would silently be one case shorter than it looks.
            raise SuiteFormatError(
                f"{path.name} line {lineno}: duplicate case id {case.id!r}"
            )
        seen.add(case.id)
        cases.append(case)
    return cases


class ReadOnlySuiteError(PermissionError):
    """Raised on any attempt to write to a bundled suite."""


def write_cases(suite: EvalSuite, cases: list[EvalCase]) -> None:
    """Rewrite a suite file. Used by the case editor and the authoring tools; bulk
    edits go through the code editor, which writes the file directly.

    Refuses a bundled suite outright. Writing to one would edit a file inside the
    repo — you would lose the change on the next pull and it would show up as a
    dirty working tree in the meantime. `fork_suite` is the way through.
    """
    if suite.read_only or bundled.is_bundled(suite.id):
        raise ReadOnlySuiteError(
            f"{suite.name!r} ships with the app and cannot be edited — fork it first"
        )
    path = Path(suite.path)
    lines = [
        json.dumps(c.model_dump(exclude_defaults=True), ensure_ascii=False)
        for c in cases
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE eval_suites SET updated_at = ? WHERE id = ?", (_now(), suite.id)
        )


# --- runs -------------------------------------------------------------------


def create_run(
    suite_id: str, label: str, provider: str, endpoint: str, model: str, total: int
) -> EvalRun:
    run = EvalRun(
        id=uuid.uuid4().hex[:12],
        suite_id=suite_id,
        label=label or model,
        provider=provider,
        endpoint=endpoint,
        model=model,
        status="queued",
        total=total,
        started_at=_now(),
    )
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO eval_runs (id, suite_id, label, provider, endpoint, model,"
            " status, total, passed, completed, started_at, finished_at, error,"
            " localtrack_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, '', '', '')",
            (
                run.id,
                run.suite_id,
                run.label,
                run.provider,
                run.endpoint,
                run.model,
                run.status,
                run.total,
                run.started_at,
            ),
        )
    return run


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{k} = ?" for k in fields)
    with get_db_conn() as conn:
        conn.execute(
            f"UPDATE eval_runs SET {columns} WHERE id = ?",
            (*fields.values(), run_id),
        )


def _run_from_row(row: sqlite3.Row) -> EvalRun:
    return EvalRun(**{k: row[k] for k in row.keys()})


def get_run(run_id: str) -> EvalRun | None:
    with get_db_conn() as conn:
        row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    return _run_from_row(row) if row else None


def list_runs(suite_id: str | None = None, limit: int = 100) -> list[EvalRun]:
    with get_db_conn() as conn:
        if suite_id:
            rows = conn.execute(
                "SELECT * FROM eval_runs WHERE suite_id = ? ORDER BY started_at DESC"
                " LIMIT ?",
                (suite_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_run_from_row(r) for r in rows]


# --- results ----------------------------------------------------------------


def save_result(run_id: str, result: CaseResult) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO eval_results (run_id, case_id, passed, grade,"
            " detail, expected, actual, answer, rounds, tools_offered, tools_dropped,"
            " groups_loaded, duration_ms, error, turn_id, case_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result.case_id,
                1 if result.passed else 0,
                result.grade,
                result.detail,
                json.dumps([c.model_dump() for c in result.expected]),
                json.dumps([c.model_dump() for c in result.actual]),
                result.answer,
                result.rounds,
                result.tools_offered,
                json.dumps(result.tools_dropped),
                json.dumps(result.groups_loaded),
                result.duration_ms,
                result.error,
                result.turn_id,
                result.case_hash,
            ),
        )
        # Recomputed from the rows rather than incremented, so a re-run of one case
        # corrects the totals instead of double-counting it.
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(passed), 0) AS p FROM eval_results"
            " WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        conn.execute(
            "UPDATE eval_runs SET completed = ?, passed = ? WHERE id = ?",
            (row["n"], row["p"], run_id),
        )


def list_results(run_id: str) -> list[CaseResult]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM eval_results WHERE run_id = ? ORDER BY case_id", (run_id,)
        ).fetchall()
    return [
        CaseResult(
            case_id=r["case_id"],
            passed=bool(r["passed"]),
            grade=r["grade"],
            detail=r["detail"],
            expected=[ToolCall(**c) for c in json.loads(r["expected"])],
            actual=[ToolCall(**c) for c in json.loads(r["actual"])],
            answer=r["answer"],
            rounds=r["rounds"],
            tools_offered=r["tools_offered"],
            tools_dropped=json.loads(r["tools_dropped"]),
            groups_loaded=json.loads(r["groups_loaded"]),
            duration_ms=r["duration_ms"],
            error=r["error"],
            turn_id=r["turn_id"],
            case_hash=r["case_hash"],
        )
        for r in rows
    ]
