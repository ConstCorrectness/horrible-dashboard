"""HTTP surface for the records module: schemas, rows, and proposal review.

The panes (`records.grid` / `records.form` / `records.board` / `records.list`) are
the only clients; the agent goes through its own tools, which call the same store.
See docs/modules/records.mdx.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.modules.records import proposals as proposal_store
from backend.modules.records import store
from backend.modules.records.models import (
    CreateSchema,
    Proposal,
    ProposalDecision,
    RecordSchema,
    RowWrite,
    UpdateSchema,
)
from backend.modules.records.seeds import seed_builtin_schemas

router = APIRouter(prefix="/records", tags=["records"])


def _guard(exc: store.RecordsError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/schemas")
def get_schemas() -> dict[str, Any]:
    schemas = store.list_schemas()
    return {
        "schemas": [
            {**s.model_dump(), "count": store.count_rows(s.id)} for s in schemas
        ]
    }


@router.post("/schemas")
def post_schema(body: CreateSchema) -> RecordSchema:
    if store.get_schema(body.id):
        raise HTTPException(status_code=409, detail=f"schema {body.id} already exists")
    try:
        return store.save_schema(RecordSchema(**body.model_dump()))
    except store.RecordsError as exc:
        raise _guard(exc) from exc


@router.patch("/schemas/{schema_id}")
def patch_schema(schema_id: str, body: UpdateSchema) -> RecordSchema:
    current = store.get_schema(schema_id)
    if not current:
        raise HTTPException(status_code=404, detail="unknown schema")
    merged = current.model_copy(update=body.model_dump(exclude_unset=True))
    try:
        return store.save_schema(merged)
    except store.RecordsError as exc:
        raise _guard(exc) from exc


@router.delete("/schemas/{schema_id}")
def remove_schema(schema_id: str, drop_table: bool = False) -> dict[str, bool]:
    try:
        store.delete_schema(schema_id, drop_table=drop_table)
    except store.RecordsError as exc:
        raise _guard(exc) from exc
    return {"ok": True}


@router.post("/seed")
def post_seed(ids: list[str] | None = None) -> dict[str, Any]:
    """Create any starter schemas that don't exist yet. Idempotent — the frontend
    calls it once per session when it finds an empty catalog."""
    return {"created": seed_builtin_schemas(ids)}


@router.get("/{schema_id}/rows")
def get_rows(
    schema_id: str,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        return {
            "rows": store.list_rows(
                schema_id, search=search, limit=limit, offset=offset
            )
        }
    except store.RecordsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{schema_id}/rows")
def post_row(schema_id: str, body: RowWrite) -> dict[str, Any]:
    try:
        return store.create_row(schema_id, body.values)
    except store.RecordsError as exc:
        raise _guard(exc) from exc


@router.get("/{schema_id}/rows/{record_id}")
def get_one_row(schema_id: str, record_id: str) -> dict[str, Any]:
    try:
        row = store.get_row(schema_id, record_id)
    except store.RecordsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="unknown record")
    return row


@router.patch("/{schema_id}/rows/{record_id}")
def patch_row(schema_id: str, record_id: str, body: RowWrite) -> dict[str, Any]:
    try:
        row = store.update_row(schema_id, record_id, body.values)
    except store.RecordsError as exc:
        raise _guard(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="unknown record")
    return row


@router.delete("/{schema_id}/rows/{record_id}")
def remove_row(schema_id: str, record_id: str) -> dict[str, bool]:
    try:
        ok = store.delete_row(schema_id, record_id)
    except store.RecordsError as exc:
        raise _guard(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="unknown record")
    return {"ok": True}


@router.get("/proposals/pending")
def get_proposals(schema_id: str | None = None) -> dict[str, list[Proposal]]:
    return {"proposals": proposal_store.list_proposals(schema_id)}


@router.post("/proposals/{proposal_id}/apply")
def post_apply(
    proposal_id: str, body: ProposalDecision | None = None
) -> dict[str, Any]:
    if proposal_store.get_proposal(proposal_id) is None:
        raise HTTPException(status_code=404, detail="unknown proposal")
    row = proposal_store.apply_proposal(proposal_id, body.accept if body else None)
    # No row means every field was declined — a rejection, not a failure.
    return {"applied": row is not None, "row": row}


@router.post("/proposals/{proposal_id}/reject")
def post_reject(proposal_id: str) -> dict[str, bool]:
    if not proposal_store.reject_proposal(proposal_id):
        raise HTTPException(status_code=404, detail="unknown or closed proposal")
    return {"ok": True}
