"""Records module: schema DDL and its additive evolution, row CRUD, and the
propose → review → apply loop that is the agent's write path."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.records import agent_tools, proposals, store
from backend.modules.records.models import FieldDecl, ProposedField, RecordSchema
from backend.modules.records.seeds import seed_builtin_schemas


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch) -> None:
    """Every test gets its own app.db — these tests create real tables."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store.init_records_db()


def _contacts() -> RecordSchema:
    return RecordSchema(
        id="contacts",
        name="Contacts",
        title_column="name",
        fields=[
            FieldDecl(key="name", label="Name", type="text"),
            FieldDecl(key="email", label="Email", type="email"),
            FieldDecl(key="score", label="Score", type="number"),
        ],
    )


def test_schema_creates_a_real_table_visible_to_sql() -> None:
    """The whole reason for physical tables: the `app` database connection, `dash`
    and the dba agent can query a CRM built here with no records-specific code."""
    store.save_schema(_contacts())
    with store.get_db_conn() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(rec_contacts)")}
    assert {"id", "created_at", "updated_at", "name", "email", "score"} == columns


def test_schema_evolution_is_additive_and_keeps_data() -> None:
    store.save_schema(_contacts())
    row = store.create_row("contacts", {"name": "Ada", "email": "ada@example.com"})

    evolved = _contacts()
    evolved.fields.append(FieldDecl(key="company", label="Company", type="text"))
    evolved.fields = [f for f in evolved.fields if f.key != "email"]
    store.save_schema(evolved)

    with store.get_db_conn() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(rec_contacts)")}
    assert "company" in columns
    # The dropped *declaration* must not drop the column — that would silently
    # destroy data on a form edit.
    assert "email" in columns
    with store.get_db_conn() as conn:
        stored = conn.execute(
            "SELECT email FROM rec_contacts WHERE id = ?", (row["id"],)
        ).fetchone()
    assert stored["email"] == "ada@example.com"


def test_row_crud_and_unknown_fields_are_rejected() -> None:
    store.save_schema(_contacts())
    row = store.create_row("contacts", {"name": "Grace", "score": "12"})
    assert row["name"] == "Grace"
    assert row["score"] == 12.0  # coerced to the declared type

    updated = store.update_row("contacts", row["id"], {"name": "Grace H"})
    assert updated is not None and updated["name"] == "Grace H"
    assert updated["updated_at"] >= row["created_at"]

    assert store.list_rows("contacts", search="grace")
    assert store.list_rows("contacts", where={"name": "Grace H"})
    assert not store.list_rows("contacts", where={"name": "nobody"})

    with pytest.raises(store.RecordsError):
        store.create_row("contacts", {"nope": 1})
    with pytest.raises(store.RecordsError):
        store.list_rows("contacts", where={"nope": 1})

    assert store.delete_row("contacts", row["id"]) is True
    assert store.get_row("contacts", row["id"]) is None


def test_invalid_schema_ids_and_reserved_keys_are_refused() -> None:
    # The id is interpolated into DDL, so the pattern check is the injection guard.
    with pytest.raises(store.RecordsError):
        store.table_name("contacts; DROP TABLE record_schemas")
    with pytest.raises(store.RecordsError):
        store.save_schema(
            RecordSchema(
                id="bad",
                name="Bad",
                fields=[FieldDecl(key="id", label="Id", type="text")],
            )
        )


def test_proposal_applies_only_the_accepted_fields() -> None:
    store.save_schema(_contacts())
    row = store.create_row("contacts", {"name": "Alan"})

    proposal = proposals.create_proposal(
        "contacts",
        {
            "email": ProposedField(value="alan@example.com", source="page 1"),
            "score": ProposedField(value=42, source="page 2"),
        },
        record_id=row["id"],
        source="invoice.pdf",
    )
    assert [p.id for p in proposals.list_proposals("contacts")] == [proposal.id]

    applied = proposals.apply_proposal(proposal.id, accept=["email"])
    assert applied is not None
    assert applied["email"] == "alan@example.com"
    # The declined field must not be written.
    assert applied["score"] is None
    # …and the proposal closes, so a reload doesn't show it again.
    assert proposals.list_proposals("contacts") == []
    assert proposals.apply_proposal(proposal.id) is None


def test_proposal_with_no_record_id_creates_a_row() -> None:
    store.save_schema(_contacts())
    proposal = proposals.create_proposal(
        "contacts", {"name": ProposedField(value="Katherine")}
    )
    row = proposals.apply_proposal(proposal.id)
    assert row is not None and row["name"] == "Katherine"


def test_accepting_nothing_is_a_rejection_not_an_empty_write() -> None:
    store.save_schema(_contacts())
    proposal = proposals.create_proposal("contacts", {"name": ProposedField(value="X")})
    assert proposals.apply_proposal(proposal.id, accept=[]) is None
    assert proposals.get_proposal(proposal.id).status == "rejected"
    assert store.count_rows("contacts") == 0


def test_proposal_rejects_unknown_fields_at_file_time() -> None:
    store.save_schema(_contacts())
    with pytest.raises(ValueError):
        proposals.create_proposal("contacts", {"nope": ProposedField(value=1)})


def test_seeds_are_idempotent_and_give_deals_a_board() -> None:
    created = seed_builtin_schemas()
    assert {"contacts", "deals", "activities", "intake"} <= set(created)
    assert seed_builtin_schemas() == []  # second call is a no-op

    deals = store.get_schema("deals")
    assert deals is not None and deals.board_column == "stage"
    stage = next(f for f in deals.fields if f.key == "stage")
    assert "Won" in stage.options


def _run(handler: Any, args: dict[str, Any]) -> Any:
    return asyncio.run(handler(args))


def test_propose_tool_accepts_flat_and_cited_field_shapes() -> None:
    store.save_schema(_contacts())
    tool = {t.name: t for t in agent_tools._TOOLS}["records.propose"]
    out = _run(
        tool.handler,
        {
            "schema": "contacts",
            "fields": {
                "name": "Ada",
                "email": {"value": "ada@example.com", "source": "signature block"},
            },
            "source": "invoice.pdf",
        },
    )
    assert out["status"] == "awaiting review"
    proposal = proposals.get_proposal(out["proposalId"])
    assert proposal is not None
    # The per-field citation wins; the flat field inherits the call-level source.
    assert proposal.fields["email"].source == "signature block"
    assert proposal.fields["name"].source == "invoice.pdf"
    # Nothing is written until the user accepts.
    assert store.count_rows("contacts") == 0


def test_propose_raises_a_notification(monkeypatch) -> None:
    """Propose is the "safe to run unattended" write path, which makes "the user is
    looking at something else" its normal case. The `records` /ws channel only
    updates an already-open review pane, so without this the proposal reaches
    nobody and shows on no counter — the bug that left a real proposal sitting
    unseen. Routed through the notification service so mutes still apply."""
    store.save_schema(_contacts())
    sent: list[tuple[str, str, str]] = []

    async def fake_notify(category, title, body, **kwargs):
        sent.append((category, title, body))
        return True

    monkeypatch.setattr("backend.modules.notifications.service.notify", fake_notify)
    tool = {t.name: t for t in agent_tools._TOOLS}["records.propose"]
    _run(tool.handler, {"schema": "contacts", "fields": {"name": "Ada"}})

    assert len(sent) == 1
    category, title, body = sent[0]
    assert category == "review"
    assert "1 field" in title
    # Names the table, so the notification says which review is waiting.
    assert "Contacts" in body


def test_review_is_a_muteable_category() -> None:
    """A category the producer emits but no rule can name is one the user cannot
    silence short of muting everything — see the note on CATEGORIES."""
    from backend.modules.notifications import store as notif_store

    assert "review" in notif_store.CATEGORIES


def test_records_tools_are_named_for_their_group() -> None:
    """The orchestrator groups tools by name prefix; `AgentTool.group` only marks a
    tool as progressively disclosed. A mismatch here silently splits the group."""
    for tool in agent_tools._TOOLS:
        assert tool.group == "records"
        assert tool.name.startswith("records.")
    # The review step is only safe if proposing is NOT a side effect and committing is.
    by_name = {t.name: t for t in agent_tools._TOOLS}
    assert by_name["records.propose"].side_effect is False
    assert by_name["records.commit"].side_effect is True
