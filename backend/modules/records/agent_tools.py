"""The `records` agent tool group — the read path, the propose path, and (gated)
the direct write path.

The important distinction is `records.propose` vs `records.commit`. Propose is not
side-effecting: it files a reviewable diff and returns immediately, which is what
makes "read this invoice and fill the form" safe to run unattended. Commit writes
straight through and is therefore gated by the permission system. The `intake`
agent's prompt forbids commit outright, and `researcher` is told to propose rather
than commit for anything it found on the open web — commit is for the bookkeeping
writes a human explicitly asked for and would only rubber-stamp.

Group name is the tool-name prefix (`records.`) — `AgentTool.group` does not name
the group, it only marks the tool as progressively disclosed.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.records import proposals as proposal_store
from backend.modules.records import store
from backend.modules.records.models import FieldDecl, ProposedField, RecordSchema
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

# Rows carry free text; a hundred of them would swamp a turn.
_MAX_ROWS = 50


def _schema_brief(schema: RecordSchema, count: int | None = None) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "id": schema.id,
        "name": schema.name,
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "type": f.type,
                **({"options": f.options} if f.options else {}),
                **({"required": True} if f.required else {}),
                **({"refSchema": f.ref_schema} if f.ref_schema else {}),
            }
            for f in schema.fields
            if not f.hidden
        ],
    }
    if schema.board_column:
        brief["boardColumn"] = schema.board_column
    if count is not None:
        brief["rows"] = count
    return brief


async def _list_schemas(_args: dict[str, Any]) -> dict[str, Any]:
    schemas = store.list_schemas()
    return {
        "schemas": [_schema_brief(s, store.count_rows(s.id)) for s in schemas],
        **(
            {"note": "no schemas yet — records.createSchema makes one"}
            if not schemas
            else {}
        ),
    }


async def _query(args: dict[str, Any]) -> dict[str, Any]:
    schema_id = str(args.get("schema") or "")
    where = args.get("where") if isinstance(args.get("where"), dict) else None
    try:
        limit = max(1, min(int(args.get("limit") or 20), _MAX_ROWS))
    except (TypeError, ValueError):
        limit = 20
    try:
        rows = store.list_rows(
            schema_id,
            where=where,
            search=str(args["search"]) if args.get("search") else None,
            limit=limit,
        )
    except store.RecordsError as exc:
        return {"error": str(exc)}
    return {"schema": schema_id, "count": len(rows), "rows": rows}


async def _get(args: dict[str, Any]) -> dict[str, Any]:
    try:
        row = store.get_row(str(args.get("schema") or ""), str(args.get("id") or ""))
    except store.RecordsError as exc:
        return {"error": str(exc)}
    return row or {"error": "no such record"}


def _proposed_fields(raw: Any) -> dict[str, ProposedField]:
    """Accept both `{key: value}` and `{key: {value, source, confidence}}` — a local
    model reliably produces the flat form no matter how the schema is described, and
    refusing it would just cost a retry round."""
    out: dict[str, ProposedField] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if isinstance(value, dict) and "value" in value:
            out[str(key)] = ProposedField(
                value=value.get("value"),
                source=value.get("source"),
                confidence=value.get("confidence"),
            )
        else:
            out[str(key)] = ProposedField(value=value)
    return out


async def _propose(args: dict[str, Any]) -> dict[str, Any]:
    fields = _proposed_fields(args.get("fields"))
    if not fields:
        return {"error": "no fields proposed"}
    source = str(args["source"]) if args.get("source") else None
    # A per-field source wins; this is the fallback for the whole extraction.
    if source:
        for field in fields.values():
            if field.source is None:
                field.source = source
    try:
        proposal = proposal_store.create_proposal(
            str(args.get("schema") or ""),
            fields,
            record_id=str(args["recordId"]) if args.get("recordId") else None,
            source=source,
        )
    except (store.RecordsError, ValueError) as exc:
        return {"error": str(exc)}
    # Announce it. The `records` /ws channel already updates an *open* review pane,
    # but the whole point of propose is that it is safe to run unattended — so the
    # one case that matters is the user looking at something else entirely. Routed
    # through the notification service (like network chat does) rather than raised
    # locally, so the mute rules apply: an extraction run filing forty proposals
    # must be silenceable. Function-scoped import, same as `network/chat.py`.
    from backend.modules.notifications.service import notify

    schema = store.get_schema(proposal.schema_id)
    await notify(
        "review",
        f"{len(fields)} field(s) to review",
        f"{schema.name if schema else proposal.schema_id}"
        f"{f' · {source}' if source else ''}",
        data={"proposalId": proposal.id, "schemaId": proposal.schema_id},
    )
    return {
        "proposalId": proposal.id,
        "status": "awaiting review",
        "fields": sorted(fields),
        "note": (
            "Filed for the user to accept or reject in the record form. Nothing is "
            "written yet — do not call records.commit for the same values."
        ),
    }


async def _commit(args: dict[str, Any]) -> dict[str, Any]:
    schema_id = str(args.get("schema") or "")
    values = args.get("values")
    if not isinstance(values, dict) or not values:
        return {"error": "no values given"}
    record_id = str(args["recordId"]) if args.get("recordId") else None
    try:
        row = (
            store.update_row(schema_id, record_id, values)
            if record_id
            else store.create_row(schema_id, values)
        )
    except store.RecordsError as exc:
        return {"error": str(exc)}
    if row is None:
        return {"error": "no such record"}
    proposal_store.records_events.publish(
        {"event": "row", "data": {"schemaId": schema_id, "row": row}}
    )
    return {"ok": True, "row": row}


async def _create_schema(args: dict[str, Any]) -> dict[str, Any]:
    raw_fields = args.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        return {"error": "a schema needs at least one field"}
    try:
        fields = [
            FieldDecl(
                key=str(f.get("key")),
                label=str(f.get("label") or f.get("key")),
                type=f.get("type") or "text",
                options=[str(o) for o in f.get("options", [])],
                required=bool(f.get("required")),
                ref_schema=f.get("refSchema"),
            )
            for f in raw_fields
            if isinstance(f, dict) and f.get("key")
        ]
        schema = RecordSchema(
            id=str(args.get("id") or ""),
            name=str(args.get("name") or args.get("id") or ""),
            icon=args.get("icon"),
            fields=fields,
            board_column=args.get("boardColumn"),
            title_column=args.get("titleColumn") or (fields[0].key if fields else None),
        )
        store.save_schema(schema)
    except (store.RecordsError, ValueError) as exc:
        return {"error": str(exc)}
    return {"ok": True, "schema": _schema_brief(schema, 0)}


_SCHEMA_PARAM = {
    "type": "string",
    "description": "Schema id from records.listSchemas (e.g. 'contacts')",
}

_TOOLS: list[AgentTool] = [
    AgentTool(
        name="records.listSchemas",
        description=(
            "List the user's record tables (papers to read, contacts, intake forms, "
            "anything row-shaped) with each one's fields, types and row count. Call "
            "this first — field keys are not guessable and every other records tool "
            "needs them."
        ),
        handler=_list_schemas,
        group="records",
    ),
    AgentTool(
        name="records.query",
        description=(
            "Read rows from a schema, optionally filtered by exact field values "
            "(`where`) or a text search across its text fields. Returns whole rows."
        ),
        handler=_query,
        parameters={
            "schema": _SCHEMA_PARAM,
            "where": {
                "type": "object",
                "description": 'Exact-match filter, e.g. {"stage": "Won"}',
            },
            "search": {
                "type": "string",
                "description": "Free-text match across the schema's text fields",
            },
            "limit": {
                "type": "number",
                "description": f"Max rows (default 20, max {_MAX_ROWS})",
            },
        },
        required=["schema"],
        group="records",
    ),
    AgentTool(
        name="records.get",
        description="Read one row by its id.",
        handler=_get,
        parameters={"schema": _SCHEMA_PARAM, "id": {"type": "string"}},
        required=["schema", "id"],
        group="records",
    ),
    AgentTool(
        name="records.propose",
        description=(
            "PROPOSE field values for the user to review — the normal way to write. "
            "Nothing is saved: the values appear in the record form as an "
            "accept/reject diff, per field, with the source you cite. Use this when "
            "filling a record from a document, page or search result. Give "
            "`recordId` to propose changes to an existing row, or omit it to "
            "propose a new one. Cite where each value came from in `source`."
        ),
        handler=_propose,
        parameters={
            "schema": _SCHEMA_PARAM,
            "recordId": {
                "type": "string",
                "description": "Existing row to amend; omit to propose a new row",
            },
            "fields": {
                "type": "object",
                "description": (
                    "Field key → value, or key → {value, source, confidence} to "
                    'cite each field individually. e.g. {"amount": {"value": 240, '
                    '"source": "invoice total, page 1"}}'
                ),
            },
            "source": {
                "type": "string",
                "description": "Where this extraction came from (URL, file, page)",
            },
        },
        required=["schema", "fields"],
        group="records",
    ),
    AgentTool(
        name="records.commit",
        description=(
            "Write a row directly, with no review step. Only for changes the user "
            "explicitly asked you to make. To fill a record from a source document, "
            "use records.propose instead."
        ),
        handler=_commit,
        parameters={
            "schema": _SCHEMA_PARAM,
            "recordId": {
                "type": "string",
                "description": "Row to update; omit to create a new one",
            },
            "values": {"type": "object", "description": "Field key → value"},
        },
        required=["schema", "values"],
        side_effect=True,
        specifier_template="{schema}",
        group="records",
    ),
    AgentTool(
        name="records.createSchema",
        description=(
            "Create a new record table from a description of what to track "
            '("track my job applications"). Field types: text, longtext, number, '
            "date, select (give `options`), url, email, ref (give `refSchema`). Set "
            "`boardColumn` to a select field to give it a kanban board."
        ),
        handler=_create_schema,
        parameters={
            "id": {
                "type": "string",
                "description": "Lowercase identifier, e.g. 'applications'",
            },
            "name": {"type": "string", "description": "Display name"},
            "icon": {"type": "string", "description": "Optional emoji"},
            "fields": {
                "type": "array",
                "description": (
                    "Field declarations: {key, label, type, options?, required?, "
                    "refSchema?}"
                ),
                "items": {"type": "object"},
            },
            "boardColumn": {
                "type": "string",
                "description": "Key of a select field to group the board by",
            },
            "titleColumn": {"type": "string", "description": "Key of the label field"},
        },
        required=["id", "fields"],
        side_effect=True,
        specifier_template="{id}",
        group="records",
    ),
]


def register_records_tools() -> None:
    """Insert the records tools into the sdk registry (called from app.py)."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
