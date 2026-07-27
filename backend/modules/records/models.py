"""Pydantic models for the records module — the API boundary for user-defined
tables ("schemas") and their rows. See docs/modules/records.mdx."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# A schema id is also a physical table name (`rec_<id>`), so it is deliberately
# narrower than a workspace id: lowercase, no dots, no dashes.
SCHEMA_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
FIELD_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"

FieldType = Literal[
    "text",
    "longtext",
    "number",
    "date",
    "select",
    "url",
    "email",
    "ref",
]


class FieldDecl(BaseModel):
    """One column of a schema. `type` drives both the SQLite affinity and the form
    widget; `options` only means anything for `select`, `ref_schema` for `ref`."""

    key: str = Field(pattern=FIELD_KEY_PATTERN)
    label: str
    type: FieldType = "text"
    options: list[str] = []
    required: bool = False
    ref_schema: str | None = None
    # Fields are never dropped (v1 evolution is ADD COLUMN only) — retiring one
    # hides it from the grid and the form instead. See store.py.
    hidden: bool = False


class RecordSchema(BaseModel):
    id: str = Field(pattern=SCHEMA_ID_PATTERN)
    name: str
    icon: str | None = None
    fields: list[FieldDecl] = []
    # The `select` field the board pane groups cards by. None = no board view.
    board_column: str | None = None
    # The field shown as a row's human label (defaults to the first text field).
    title_column: str | None = None


class CreateSchema(BaseModel):
    id: str = Field(pattern=SCHEMA_ID_PATTERN)
    name: str
    icon: str | None = None
    fields: list[FieldDecl] = []
    board_column: str | None = None
    title_column: str | None = None


class UpdateSchema(BaseModel):
    """Partial update. Passing `fields` adds new columns and rewrites the
    declarations; it never drops a column (see `store.save_schema`)."""

    name: str | None = None
    icon: str | None = None
    fields: list[FieldDecl] | None = None
    board_column: str | None = None
    title_column: str | None = None


class RowWrite(BaseModel):
    """Values for a row, keyed by field. Unknown keys are rejected rather than
    silently dropped — a typo'd field is a bug worth surfacing."""

    values: dict[str, Any] = {}


class ProposedField(BaseModel):
    """One agent-proposed value, with where it came from. The provenance is the
    point: a data-entry review is only fast if each field says why it's there."""

    value: Any = None
    source: str | None = None
    confidence: float | None = None


class Proposal(BaseModel):
    id: str
    schema_id: str
    # None = the agent proposes creating a new row.
    record_id: str | None = None
    fields: dict[str, ProposedField] = {}
    source: str | None = None
    status: Literal["pending", "applied", "rejected"] = "pending"
    created_at: str | None = None


class ProposalDecision(BaseModel):
    """Accept a subset of a proposal's fields (all of them when `fields` is None)
    and reject the rest."""

    accept: list[str] | None = None
