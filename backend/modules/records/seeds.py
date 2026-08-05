"""The starter schemas a records pane offers on a node that has none.

Seeded on demand (`POST /api/records/seed`, when a records pane finds an empty
catalog) rather than at boot: a node that never opens records shouldn't grow four
empty tables in its app database. Seeding is idempotent and never touches a schema
that already exists — these are starting points the user is expected to edit or
delete, not a fixed model, and `records.schema` ("Table setup") is how they define
their own.
"""

from __future__ import annotations

from backend.modules.records.models import FieldDecl, RecordSchema
from backend.modules.records.store import get_schema, save_schema

DEAL_STAGES = ["Lead", "Qualified", "Proposal", "Negotiation", "Won", "Lost"]

BUILTIN_SCHEMAS: list[RecordSchema] = [
    RecordSchema(
        id="contacts",
        name="Contacts",
        icon="👤",
        title_column="name",
        fields=[
            FieldDecl(key="name", label="Name", type="text", required=True),
            FieldDecl(key="company", label="Company", type="text"),
            FieldDecl(key="role", label="Role", type="text"),
            FieldDecl(key="email", label="Email", type="email"),
            FieldDecl(key="phone", label="Phone", type="text"),
            FieldDecl(key="website", label="Website", type="url"),
            FieldDecl(key="location", label="Location", type="text"),
            FieldDecl(key="notes", label="Notes", type="longtext"),
        ],
    ),
    RecordSchema(
        id="deals",
        name="Deals",
        icon="💼",
        title_column="title",
        board_column="stage",
        fields=[
            FieldDecl(key="title", label="Deal", type="text", required=True),
            FieldDecl(
                key="stage",
                label="Stage",
                type="select",
                options=DEAL_STAGES,
                required=True,
            ),
            FieldDecl(key="value", label="Value", type="number"),
            FieldDecl(
                key="contact", label="Contact", type="ref", ref_schema="contacts"
            ),
            FieldDecl(key="owner", label="Owner", type="text"),
            FieldDecl(key="close_date", label="Expected close", type="date"),
            FieldDecl(key="notes", label="Notes", type="longtext"),
        ],
    ),
    RecordSchema(
        id="activities",
        name="Activities",
        icon="🗒",
        title_column="summary",
        fields=[
            FieldDecl(key="summary", label="Summary", type="text", required=True),
            FieldDecl(
                key="kind",
                label="Kind",
                type="select",
                options=["Call", "Email", "Meeting", "Note"],
            ),
            FieldDecl(key="occurred_at", label="When", type="date"),
            FieldDecl(
                key="contact", label="Contact", type="ref", ref_schema="contacts"
            ),
            FieldDecl(key="deal", label="Deal", type="ref", ref_schema="deals"),
            FieldDecl(key="detail", label="Detail", type="longtext"),
        ],
    ),
    RecordSchema(
        id="intake",
        name="Intake",
        icon="📥",
        title_column="title",
        board_column="status",
        fields=[
            FieldDecl(key="title", label="Title", type="text", required=True),
            FieldDecl(
                key="status",
                label="Status",
                type="select",
                options=["New", "In review", "Needs info", "Done"],
            ),
            FieldDecl(key="source_url", label="Source", type="url"),
            FieldDecl(key="reference", label="Reference #", type="text"),
            FieldDecl(key="party", label="Party", type="text"),
            FieldDecl(key="amount", label="Amount", type="number"),
            FieldDecl(key="dated", label="Date", type="date"),
            FieldDecl(key="summary", label="Summary", type="longtext"),
        ],
    ),
]


def seed_builtin_schemas(ids: list[str] | None = None) -> list[str]:
    """Create any missing built-in schemas; returns the ids actually created."""
    wanted = set(ids) if ids else {s.id for s in BUILTIN_SCHEMAS}
    created: list[str] = []
    for schema in BUILTIN_SCHEMAS:
        if schema.id not in wanted or get_schema(schema.id) is not None:
            continue
        save_schema(schema)
        created.append(schema.id)
    return created
