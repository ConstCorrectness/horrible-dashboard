"""The outbox: every post bound for X, LinkedIn or YouTube, from draft to sent.

One table, `scrive_outbox` in `app.db`. A row is one send to one target: a thread, a
link post, a video. Its life:

    draft ──approve──▶ approved ──send──▶ sending ──▶ sent
                          │                  │
                          └──schedule──▶ scheduled (run_at) ──tick──┘   └──▶ failed ──retry──▶ sending

Rules that are quiet if wrong:

- **Only a person moves a row past `draft`.** The agent's `scrive.draftSocial` calls
  `create(..., created_by="agent")` and nothing else here; approve, send, schedule and
  retry are HTTP routes the composer's buttons call.
- **Approval freezes the payload.** Preflight runs on it (`social.preflight`),
  `{{post.url}}` is filled in, and what is stored is what will be sent, however long
  it waits. Editing an approved or scheduled row puts it back to `draft`.
- **Steps are checkpointed** in the row (`steps`, a JSON object by step name), so a send
  interrupted by a restart resumes: finished posts are not posted again, uploads
  continue from their saved offset. A post whose outcome is unknown (the process died,
  or the network failed, after the request went out) is marked `unknown` and the row
  fails rather than risk a duplicate; **Retry** is the person accepting that risk.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.scrive import social, store
from backend.modules.scrive.publish import Finding

Status = Literal["draft", "approved", "scheduled", "sending", "sent", "failed"]
EDITABLE = {"draft", "approved", "scheduled", "failed"}

_DDL = """
CREATE TABLE IF NOT EXISTS scrive_outbox (
    id TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    page TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'person',
    note TEXT NOT NULL DEFAULT '',
    run_at REAL,
    findings TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL,
    error TEXT NOT NULL DEFAULT '',
    remote_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    approved_at REAL,
    sent_at REAL
)
"""
_INDEX = (
    "CREATE INDEX IF NOT EXISTS scrive_outbox_status ON scrive_outbox (status, run_at)"
)


class OutboxError(ValueError):
    """A request the row's state does not allow."""


class OutboxItem(BaseModel):
    id: str
    site: str
    page: str = ""
    target: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Status
    created_by: Literal["person", "agent"] = "person"
    #: What the agent says about its draft, for the person reviewing it.
    note: str = ""
    run_at: float | None = None
    findings: list[Finding] = Field(default_factory=list)
    steps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    attempts: int = 0
    next_attempt_at: float | None = None
    error: str = ""
    remote_id: str = ""
    url: str = ""
    created_at: float
    updated_at: float
    approved_at: float | None = None
    sent_at: float | None = None


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(_DDL)
        conn.execute(_INDEX)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _conn():
        pass


def _row(row: sqlite3.Row) -> OutboxItem:
    data = dict(row)
    for key, empty in (("payload", {}), ("findings", []), ("steps", {})):
        try:
            data[key] = json.loads(data[key] or "null") or empty
        except ValueError:
            data[key] = empty
    return OutboxItem.model_validate(data)


def get(item_id: str) -> OutboxItem:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM scrive_outbox WHERE id = ?", (item_id,)
        ).fetchone()
    if row is None:
        raise FileNotFoundError(f"outbox item {item_id}")
    return _row(row)


def list_items(
    site: str | None = None,
    page: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[OutboxItem]:
    where, args = [], []
    for column, value in (("site", site), ("page", page), ("status", status)):
        # `page=""` is a filter of its own: the rows that belong to no page.
        if value or (column == "page" and value is not None):
            where.append(f"{column} = ?")
            args.append(value)
    sql = "SELECT * FROM scrive_outbox"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    with _conn() as conn:
        rows = conn.execute(sql, (*args, limit)).fetchall()
    return [_row(r) for r in rows]


def _update(item_id: str, **fields: Any) -> OutboxItem:
    fields["updated_at"] = time.time()
    for key in ("payload", "findings", "steps"):
        if key in fields:
            value = fields[key]
            if key == "findings":
                value = [f.model_dump() if isinstance(f, Finding) else f for f in value]
            fields[key] = json.dumps(value)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(
            f"UPDATE scrive_outbox SET {cols} WHERE id = ?", (*fields.values(), item_id)
        )
    return get(item_id)


# --- drafts ---------------------------------------------------------------------------


def create(
    site_id: str,
    page: str,
    target: str,
    payload: dict[str, Any] | None = None,
    *,
    created_by: Literal["person", "agent"] = "person",
    note: str = "",
) -> OutboxItem:
    """A new **draft**. The only thing the agent's tool can do here."""
    store.site_dir(site_id)
    if page:
        store.resolve_page(site_id, page)
    if target not in social.TARGETS:
        raise OutboxError(f"not a target: {target!r} (x, linkedin or youtube)")
    if payload is None:
        payload = (
            social.suggest(site_id, page, target) if page else social.blank(target)
        )
    payload = social.parse_payload(target, payload).model_dump()
    now = time.time()
    item_id = secrets.token_hex(6)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO scrive_outbox (id, site, page, target, payload, status, "
            "created_by, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                site_id,
                page,
                target,
                json.dumps(payload),
                "draft",
                created_by,
                note.strip()[:2000],
                now,
                now,
            ),
        )
    return get(item_id)


def edit(item_id: str, payload: dict[str, Any]) -> OutboxItem:
    """Change what a row says. An approved or scheduled row goes back to `draft`: its
    approval was of the old text."""
    item = get(item_id)
    if item.status not in EDITABLE:
        raise OutboxError(f"a {item.status} post cannot be edited")
    payload = social.parse_payload(item.target, payload).model_dump()
    return _update(
        item_id,
        payload=payload,
        status="draft",
        run_at=None,
        approved_at=None,
        findings=[],
        error="",
    )


def delete(item_id: str) -> None:
    item = get(item_id)
    if item.status == "sending":
        raise OutboxError("a post is being sent; wait for it to finish or fail")
    with _conn() as conn:
        conn.execute("DELETE FROM scrive_outbox WHERE id = ?", (item_id,))


def sent_today(target: str) -> int:
    start = time.time() - time.time() % 86400  # UTC midnight
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM scrive_outbox WHERE target = ? AND status = 'sent' "
            "AND sent_at >= ?",
            (target, start),
        ).fetchone()
    return int(row[0])


# --- the person's decisions -----------------------------------------------------------


def check(item_id: str) -> list[Finding]:
    """Preflight without approving — what the composer shows while editing."""
    item = get(item_id)
    return social.preflight(
        item.site,
        item.page,
        item.target,
        item.payload,
        sent_today=sent_today(item.target),
    )


def approve(item_id: str, *, acknowledged: bool = False) -> tuple[OutboxItem, bool]:
    """Run preflight; when nothing stops it, freeze the payload (link filled in) and
    mark it approved. Answers the row (with its findings) and whether it was approved."""
    item = get(item_id)
    if item.status not in ("draft", "failed"):
        raise OutboxError(f"a {item.status} post cannot be approved")
    findings = check(item_id)
    if social.blocked(findings, acknowledged):
        return _update(item_id, findings=findings), False
    payload = social.resolve(
        item.target,
        item.payload,
        social.published_url(item.site, item.page),
        social.site_url(item.site),
    )
    return (
        _update(
            item_id,
            payload=payload,
            findings=findings,
            status="approved",
            approved_at=time.time(),
            steps={},
            attempts=0,
            error="",
        ),
        True,
    )


def send(item_id: str) -> OutboxItem:
    item = get(item_id)
    if item.status not in ("approved", "scheduled"):
        raise OutboxError("approve the post before sending it")
    return _update(
        item_id, status="sending", run_at=None, next_attempt_at=None, error=""
    )


def schedule(item_id: str, run_at: float) -> OutboxItem:
    item = get(item_id)
    if item.status not in ("approved", "scheduled"):
        raise OutboxError("approve the post before scheduling it")
    if run_at <= time.time() + 30:
        raise OutboxError("pick a time at least a minute from now, or send it now")
    return _update(item_id, status="scheduled", run_at=run_at)


def unschedule(item_id: str) -> OutboxItem:
    item = get(item_id)
    if item.status != "scheduled":
        raise OutboxError("this post is not scheduled")
    return _update(item_id, status="approved", run_at=None)


def retry(item_id: str) -> OutboxItem:
    """Send a failed row again. Steps that finished stay finished; a step whose outcome
    was unknown is tried again — the person has seen the warning and chosen to."""
    item = get(item_id)
    if item.status != "failed":
        raise OutboxError("only a failed post can be retried")
    steps = {
        name: (
            {**step, "status": "pending"} if step.get("status") == "unknown" else step
        )
        for name, step in item.steps.items()
    }
    return _update(
        item_id,
        status="sending",
        steps=steps,
        attempts=0,
        next_attempt_at=None,
        error="",
    )


# --- the runner's side ----------------------------------------------------------------


def due(now: float | None = None) -> list[str]:
    """Scheduled rows whose time has come, promoted to `sending`."""
    now = time.time() if now is None else now
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM scrive_outbox WHERE status = 'scheduled' AND run_at <= ?",
            (now,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        for item_id in ids:
            conn.execute(
                "UPDATE scrive_outbox SET status = 'sending', updated_at = ? WHERE id = ?",
                (now, item_id),
            )
    return ids


def ready(now: float | None = None) -> list[str]:
    """Rows being sent whose backoff (if any) has passed."""
    now = time.time() if now is None else now
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM scrive_outbox WHERE status = 'sending' AND "
            "(next_attempt_at IS NULL OR next_attempt_at <= ?)",
            (now,),
        ).fetchall()
    return [r["id"] for r in rows]


def save_step(item_id: str, name: str, status: str, **data: Any) -> OutboxItem:
    item = get(item_id)
    steps = dict(item.steps)
    steps[name] = {**steps.get(name, {}), **data, "status": status}
    return _update(item_id, steps=steps)


def mark_unknown_on_boot() -> int:
    """After a restart: a post step left `running` may or may not have gone out."""
    count = 0
    for item in list_items(status="sending", limit=10_000):
        steps = dict(item.steps)
        changed = False
        for name, step in steps.items():
            if step.get("status") == "running":
                step["status"] = "unknown" if step.get("kind") == "post" else "pending"
                changed = True
                count += 1
        if changed:
            _update(item.id, steps=steps)
    return count


def finish(item_id: str, *, remote_id: str, url: str) -> OutboxItem:
    return _update(
        item_id,
        status="sent",
        remote_id=remote_id,
        url=url,
        sent_at=time.time(),
        error="",
        next_attempt_at=None,
    )


def fail(item_id: str, message: str) -> OutboxItem:
    return _update(item_id, status="failed", error=message[:2000], next_attempt_at=None)


def back_off(item_id: str, message: str, wait: float) -> OutboxItem:
    item = get(item_id)
    return _update(
        item_id,
        attempts=item.attempts + 1,
        next_attempt_at=time.time() + wait,
        error=message[:2000],
    )
