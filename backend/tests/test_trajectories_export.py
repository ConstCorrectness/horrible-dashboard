"""SFT export and semantic search.

The export half is where a mistake is expensive: bad training data is not a bug
you notice, it is a model that got slightly worse. So most of these tests are
about what the exporter *refuses* to write.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.trajectories import export, search, store
from backend.modules.trajectories.models import (
    HarnessWrite,
    LabelWrite,
    StepWrite,
    TrajectoryWrite,
)


@pytest.fixture()
def db():
    store._initialized.clear()
    store.init_trajectories_db()
    return store


def _run(
    db, *, goal="do the thing", outcome="success", steps=None, prompt="be careful"
):
    run_id, _ = db.ingest_run(
        TrajectoryWrite(
            dataset_id="d",
            goal=goal,
            status="complete",
            outcome=outcome,
            harness=HarnessWrite(
                agent_id="coder",
                model="m",
                system_prompt=prompt,
                tool_names=["bash"],
                tool_schemas={
                    "bash": {"type": "function", "function": {"name": "bash"}}
                },
            ),
            step_list=steps
            if steps is not None
            else [
                StepWrite(kind="message", role="user", content=goal),
                StepWrite(
                    kind="action",
                    name="bash",
                    args={"cmd": "pytest"},
                    result={"rc": 0},
                    ok=True,
                ),
                StepWrite(kind="message", role="assistant", content="Done."),
            ],
        )
    )
    return run_id


def _lines(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --- shape ------------------------------------------------------------------


def test_export_rebuilds_the_openai_message_shape(db):
    _run(db)
    report = export.export_dataset(name="t")
    (example,) = _lines(report["path"])

    roles = [m["role"] for m in example["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    call = example["messages"][2]["tool_calls"][0]
    assert call["function"]["name"] == "bash"
    # Arguments are a JSON *string*, matching what the serving path emits.
    assert json.loads(call["function"]["arguments"]) == {"cmd": "pytest"}
    assert example["messages"][3]["tool_call_id"] == call["id"]
    assert example["tools"][0]["function"]["name"] == "bash"


def test_export_writes_messages_not_a_rendered_template(db):
    """Rendering here would bake in one model's chat template and silently
    mistrain every other model — the failure `--jinja` exists to avoid."""
    _run(db)
    (example,) = _lines(export.export_dataset(name="t")["path"])
    assert isinstance(example["messages"], list)
    assert "text" not in example
    assert example["meta"]["drawn_from"] == "m"


def test_export_flattens_multiple_system_messages(db):
    """A strict Jinja template *raises* on a second system message — a 500 from
    the engine, not a warning."""
    _run(
        db,
        steps=[
            StepWrite(kind="message", role="system", content="extra guidance"),
            StepWrite(kind="message", role="user", content="go"),
            StepWrite(kind="message", role="assistant", content="ok"),
        ],
    )
    (example,) = _lines(export.export_dataset(name="t")["path"])
    systems = [m for m in example["messages"] if m["role"] == "system"]
    assert len(systems) == 1
    assert "be careful" in systems[0]["content"]
    assert "extra guidance" in systems[0]["content"]
    assert example["messages"][0]["role"] == "system"


# --- what it refuses --------------------------------------------------------


def test_export_skips_ungraded_runs(db):
    """Training on what the agent happened to do distils its failure modes."""
    _run(db, goal="graded", outcome="success")
    _run(db, goal="ungraded", outcome=None)
    report = export.export_dataset(name="t")
    lines = _lines(report["path"])
    assert len(lines) == 1
    assert lines[0]["messages"][1]["content"] == "graded"


def test_export_skips_failures(db):
    _run(db, goal="bad", outcome="failure")
    assert export.export_dataset(name="t")["examples"] == 0


def test_export_reports_what_it_skipped_rather_than_patching_it(db):
    """A silently repaired trajectory is training data nobody checked."""
    _run(
        db,
        goal="truncated",
        steps=[
            StepWrite(kind="message", role="user", content="go"),
            StepWrite(kind="action", name="bash", args={}, result={"rc": 0}, ok=True),
        ],
    )
    report = export.export_dataset(name="t")
    assert report["examples"] == 0
    assert report["skippedCount"] == 1
    assert "does not end on an assistant turn" in report["skipped"][0]


def test_export_can_require_a_human_label(db):
    """An `agent-critic` label is a model grading a model."""
    machine = _run(db, goal="machine graded")
    human = _run(db, goal="human graded")
    db.add_label(
        machine, LabelWrite(key="outcome", value="success", source="agent-critic")
    )
    db.add_label(human, LabelWrite(key="outcome", value="success", source="human"))

    report = export.export_dataset(name="t", label_source="human")
    lines = _lines(report["path"])
    assert len(lines) == 1
    assert lines[0]["messages"][1]["content"] == "human graded"
    assert any("no 'human' outcome label" in s for s in report["skipped"])


# --- redaction --------------------------------------------------------------


def test_export_redacts_secrets_although_the_store_keeps_them(db):
    """The store is raw on purpose; a training file full of API keys is not."""
    _run(
        db,
        steps=[
            StepWrite(kind="message", role="user", content="auth"),
            StepWrite(
                kind="action",
                name="bash",
                args={"api_token": "sk-live-123", "cmd": "ls"},
                result={"password": "hunter2", "rc": 0},
                ok=True,
            ),
            StepWrite(kind="message", role="assistant", content="done"),
        ],
    )
    report = export.export_dataset(name="t")
    raw = open(report["path"], encoding="utf-8").read()
    assert "sk-live-123" not in raw
    assert "hunter2" not in raw
    assert store.REDACTED in raw
    # The non-secret argument survives, or the example would be useless. Parsed
    # rather than substring-matched: tool arguments are a JSON string nested
    # inside JSON, so the quotes are escaped on disk.
    example = json.loads(raw)
    args = json.loads(example["messages"][2]["tool_calls"][0]["function"]["arguments"])
    assert args == {"api_token": store.REDACTED, "cmd": "ls"}

    # And the store still holds the real value for debugging.
    runs, _ = db.list_runs()
    detail = db.get_run(runs[0].id)
    action = [s for s in detail.step_list if s.kind == "action"][0]
    assert action.args["api_token"] == "sk-live-123"


# --- semantic search --------------------------------------------------------


def test_compose_document_keeps_tool_order(db):
    """ "read then edit then test" and "test then read then edit" are different
    strategies; a bag of tool names cannot tell them apart."""
    run_id = _run(
        db,
        steps=[
            StepWrite(kind="message", role="user", content="fix it"),
            StepWrite(kind="action", name="read"),
            StepWrite(kind="action", name="edit"),
            StepWrite(kind="action", name="test"),
            StepWrite(kind="message", role="assistant", content="fixed"),
        ],
    )
    run = db.get_run(run_id)
    doc = search.compose_document(run, run.step_list)
    assert "read → edit → test" in doc
    assert "do the thing" in doc
    assert "fixed" in doc
    assert "outcome: success" in doc


@pytest.mark.anyio
async def test_indexing_refuses_hash_fallback_vectors(db, monkeypatch):
    """Persisting fallback vectors pins the collection to 384 dims forever, and
    every real embedding afterwards is a DimensionMismatch."""
    run_id = _run(db)

    async def fallback(texts):
        return [[0.0] * 384 for _ in texts], "local-fallback"

    monkeypatch.setattr(search, "get_embeddings", fallback)
    result = await search.index_runs([run_id])
    assert result == {"indexed": 0, "skipped": 1}
    # And the run stays unindexed, so a later reindex picks it up.
    assert run_id in db.unindexed_run_ids()


@pytest.mark.anyio
async def test_search_falls_back_to_substring_and_says_so(db, monkeypatch):
    """A caller told it got `semantic` and no hits concludes no similar run
    exists. The truth may be that nobody could embed the query."""
    _run(db, goal="deploy the service")

    async def fallback(text):
        return [0.0] * 384, "local-fallback"

    monkeypatch.setattr(search, "get_embedding", fallback)
    runs, method = await search.search_runs("deploy")
    assert method == "substring"
    assert [r.goal for r in runs] == ["deploy the service"]


@pytest.mark.anyio
async def test_search_with_an_empty_query_returns_recent(db):
    _run(db, goal="a")
    runs, method = await search.search_runs("")
    assert method == "recent"
    assert len(runs) == 1


def test_only_sealed_runs_are_offered_for_indexing(db):
    """A running run has no final answer; indexing it would pin a half-written
    document that nothing refreshes."""
    live = db.start_run("d", goal="in flight")
    done = _run(db)
    pending = db.unindexed_run_ids()
    assert done in pending
    assert live not in pending


def test_full_reindex_clears_the_stamps(db):
    """The collection is dropped, so a stale stamp would make the rebuild skip
    everything and leave an empty index."""
    run_id = _run(db)
    db.mark_indexed([run_id])
    assert db.unindexed_run_ids() == []
    db.mark_indexed([], reset_dataset="d")
    assert db.unindexed_run_ids() == [run_id]


def test_narration_and_its_tool_call_are_one_assistant_turn(db):
    """Two assistant messages in a row is not a conversation.

    A trajectory stores "Let me run the tests" and the call it then made as two
    steps, because a step is one decision and narration is not a decision. A chat
    transcript works the other way: an assistant turn carries its text *and* its
    tool_calls. Emitting two consecutive assistant messages produces training data
    that teaches a model to emit a bare text turn followed by a bare tool turn.
    """
    _run(
        db,
        steps=[
            StepWrite(kind="message", role="user", content="fix it"),
            StepWrite(
                kind="message", role="assistant", content="Let me run the tests."
            ),
            StepWrite(
                kind="action",
                name="bash",
                args={"cmd": "pytest"},
                result={"rc": 0},
                ok=True,
            ),
            StepWrite(kind="message", role="assistant", content="Fixed."),
        ],
    )
    (example,) = _lines(export.export_dataset(name="t")["path"])
    roles = [m["role"] for m in example["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert not any(
        a == "assistant" and b == "assistant" for a, b in zip(roles, roles[1:])
    )
    # The narration rides on the turn that made the call, not on its own.
    call_turn = example["messages"][2]
    assert call_turn["content"] == "Let me run the tests."
    assert call_turn["tool_calls"][0]["function"]["name"] == "bash"


def test_trailing_narration_still_becomes_its_own_turn(db):
    """Prose with no action after it is a real final answer and must survive."""
    _run(
        db,
        steps=[
            StepWrite(kind="message", role="user", content="explain"),
            StepWrite(kind="message", role="assistant", content="Here is why."),
        ],
    )
    (example,) = _lines(export.export_dataset(name="t")["path"])
    assert example["messages"][-1] == {"role": "assistant", "content": "Here is why."}
