"""Field proposals: the agent's write path, and the human's review of it.

`records.propose` never writes a row. It files a **proposal** — per-field values,
each carrying where it came from — which surfaces in the open `records.form` pane
as an accept/reject diff. This mirrors `editor.proposeEdit`, the convention this
codebase already uses for agent writes a human should see first, and it is what
makes agentic data entry safe to leave on: extraction is the model's job, the
commit is the user's.

Proposals are persisted (not held in memory) so a reload mid-review doesn't lose
them, and broadcast on the `records` `/ws` channel so an already-open form updates
the moment the agent files one. See docs/modules/records.mdx.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from backend.modules.records.models import Proposal, ProposedField
from backend.modules.records.store import (
    get_db_conn,
    require_schema,
    update_row,
    create_row,
)


class RecordsBroadcaster:
    """Mirrors the library's broadcaster: push-only fan-out to each `/ws` client."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


records_events = RecordsBroadcaster()


async def push_records_events(conn: Any) -> None:
    queue = records_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "records", "event": event["event"], "data": event["data"]}
            )
    finally:
        records_events.unsubscribe(queue)


def _row_to_proposal(row: Any) -> Proposal:
    return Proposal(
        id=row["id"],
        schema_id=row["schema_id"],
        record_id=row["record_id"],
        fields={
            k: ProposedField(**v) for k, v in json.loads(row["fields"] or "{}").items()
        },
        source=row["source"],
        status=row["status"],
        created_at=str(row["created_at"]) if row["created_at"] else None,
    )


def create_proposal(
    schema_id: str,
    fields: dict[str, ProposedField],
    *,
    record_id: str | None = None,
    source: str | None = None,
) -> Proposal:
    """File a proposal against a schema. Field keys are validated here so a bad
    extraction fails at propose time, in the agent's tool result, rather than
    silently sitting in the review queue until the user hits Accept."""
    schema = require_schema(schema_id)
    known = {f.key for f in schema.fields}
    unknown = [k for k in fields if k not in known]
    if unknown:
        raise ValueError(
            f"unknown field(s) for {schema_id}: {', '.join(sorted(unknown))}"
        )
    proposal = Proposal(
        id=uuid.uuid4().hex[:12],
        schema_id=schema_id,
        record_id=record_id,
        fields=fields,
        source=source,
    )
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO record_proposals (id, schema_id, record_id, fields, source, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (
                proposal.id,
                schema_id,
                record_id,
                json.dumps({k: v.model_dump() for k, v in fields.items()}),
                source,
            ),
        )
    records_events.publish(
        {"event": "proposal", "data": json.loads(proposal.model_dump_json())}
    )
    return proposal


def list_proposals(
    schema_id: str | None = None, *, status: str = "pending"
) -> list[Proposal]:
    sql = "SELECT * FROM record_proposals WHERE status = ?"
    params: list[Any] = [status]
    if schema_id:
        sql += " AND schema_id = ?"
        params.append(schema_id)
    sql += " ORDER BY created_at DESC LIMIT 200"
    with get_db_conn() as conn:
        return [_row_to_proposal(r) for r in conn.execute(sql, params).fetchall()]


def get_proposal(proposal_id: str) -> Proposal | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM record_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_proposal(row) if row else None


def _set_status(proposal_id: str, status: str) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE record_proposals SET status = ? WHERE id = ?",
            (status, proposal_id),
        )
    records_events.publish(
        {"event": "proposal_closed", "data": {"id": proposal_id, "status": status}}
    )


def apply_proposal(
    proposal_id: str, accept: list[str] | None = None
) -> dict[str, Any] | None:
    """Commit the accepted fields (all of them when `accept` is None) and close the
    proposal. Accepting nothing is a rejection, not a no-op write — leaving a
    half-reviewed proposal open would show it again on the next load."""
    proposal = get_proposal(proposal_id)
    if proposal is None or proposal.status != "pending":
        return None
    chosen = {
        key: field.value
        for key, field in proposal.fields.items()
        if accept is None or key in accept
    }
    if not chosen:
        _set_status(proposal_id, "rejected")
        return None
    row = (
        update_row(proposal.schema_id, proposal.record_id, chosen)
        if proposal.record_id
        else create_row(proposal.schema_id, chosen)
    )
    _set_status(proposal_id, "applied")
    if row:
        records_events.publish(
            {
                "event": "row",
                "data": {"schemaId": proposal.schema_id, "row": row},
            }
        )
    return row


def reject_proposal(proposal_id: str) -> bool:
    proposal = get_proposal(proposal_id)
    if proposal is None or proposal.status != "pending":
        return False
    _set_status(proposal_id, "rejected")
    return True
