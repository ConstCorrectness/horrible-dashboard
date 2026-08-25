"""Forking a recorded turn: the rebuild, the edits, and what a replay may not do.

Three claims are worth pinning here, and they are the three ways this feature could
be quietly wrong rather than loudly broken:

1. **The rebuild is honest.** A snapshot clips block previews and folds an
   assistant's tool calls into its text. If the fork silently ran a truncated
   prompt, or handed the model its own previous calls as prose, it would still
   produce an answer — a different one, for a reason that is not the edit.
2. **An edit that matched nothing says so.** "I dropped the tool and nothing
   changed" and "I dropped a tool whose name I misspelled and nothing changed" are
   the same sentence with the meaning removed.
3. **Nothing acts.** Not just the browser leg: backend tools and backend-plugin
   tools never reach a connection at all, so fixtures alone would not have stopped
   a replayed turn from delegating or emailing a second time.

The model is scripted, as in `test_evals_runner`, but everything between the script
and the record is production code — `_select_tools`, the gate, `_dispatch_call`,
the real `run_agent_loop`.
"""

from __future__ import annotations

import pytest

from backend.modules.agent import providers as P
from backend.modules.agent.models import AgentConfig
from backend.modules.agentpedia import fork, rebuild, store
from backend.modules.agentpedia.models import ForkEdit, ForkRequest
from backend.modules.interpretability import recorder
from backend.modules.interpretability.models import (
    ContextBlock,
    RoundSnapshot,
    ToolEntry,
    TurnSnapshot,
)

INFO = P.PROVIDERS["ollama"]


@pytest.fixture(autouse=True)
def clean():
    recorder.clear()
    store.clear()
    yield
    recorder.clear()
    store.clear()


def block(kind: str, role: str, content: str, **kw) -> ContextBlock:
    return ContextBlock(
        kind=kind,
        role=role,
        label=kind.replace("_", " ").title(),
        content=content,
        tokens=len(content) // 4,
        **kw,
    )


def tool_decl(name: str) -> dict:
    """A frontend tool in the shape the browser pushes onto the connection."""
    return {
        "name": name,
        "description": "a tool",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
        "sideEffect": False,
    }


class ScriptedModel:
    """A `chat_stream` stand-in playing a fixed list of turns: a string is a final
    answer, a list of `(name, args)` pairs is a round of tool calls."""

    def __init__(self, turns: list) -> None:
        self.turns = list(turns)
        self.seen: list[dict] = []

    async def __call__(
        self, client, info, endpoint, model, messages, tools, on_delta, **kw
    ):
        self.seen.append({"messages": list(messages), "tools": list(tools)})
        turn = self.turns.pop(0) if self.turns else "done"
        if isinstance(turn, str):
            return P.ChatResult(
                assistant_message={"role": "assistant", "content": turn},
                tool_calls=[],
                content=turn,
            )
        calls = [
            P.ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(turn)
        ]
        return P.ChatResult(
            assistant_message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in calls
                ],
            },
            tool_calls=calls,
            content="",
        )


# ── The rebuild ──────────────────────────────────────────────────────────────


def test_an_assistants_tool_calls_come_back_as_tool_calls():
    """The recorder folds them into the block text so their cost is counted. Handed
    back unfolded, the model would be reading a transcript of a conversation it did
    not have — and no error would say so."""
    folded = '[{"function":{"name":"files.read","arguments":{"path":"a"}}}]'
    snapshot = RoundSnapshot(
        round=1,
        blocks=[
            block("system", "system", "be good"),
            block("user", "user", "read it"),
            block("assistant", "assistant", "on it\n" + folded),
            block("tool_result", "tool", '{"ok":true}'),
        ],
    )
    messages, report = rebuild.messages_from(snapshot)

    assert messages[2]["content"] == "on it"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "files.read"
    assert report.tool_calls_recovered == 1
    # The tool result is keyed to the call above it, both ways: which key the
    # provider reads depends on the dialect the *fork* runs against.
    assert messages[3]["tool_call_id"] == messages[2]["tool_calls"][0]["id"]
    assert messages[3]["tool_name"] == "files.read"
    assert report.exact


def test_a_tool_result_with_no_call_above_it_is_reported_not_invented():
    snapshot = RoundSnapshot(
        round=0,
        blocks=[block("system", "system", "hi"), block("tool_result", "tool", "{}")],
    )
    messages, report = rebuild.messages_from(snapshot)

    assert "tool_call_id" not in messages[1]
    assert report.unlinked_tool_results == 1
    assert not report.exact


def test_a_clipped_block_is_named_and_the_rebuild_is_not_exact():
    snapshot = RoundSnapshot(
        round=0,
        blocks=[block("history", "user", "the first 4000 chars", clipped=True)],
    )
    _messages, report = rebuild.messages_from(snapshot)

    assert report.clipped == ["History"]
    assert not report.exact


def test_a_clipped_system_prompt_is_restored_from_the_live_spec():
    """Verified restoration, not a guess: the live prompt has to *start with* the
    recorded preview. Otherwise the fork runs the first 4000 characters of a prompt
    and reports the difference as a finding."""
    full = "you are a careful agent. " * 300
    snapshot = RoundSnapshot(
        round=0, blocks=[block("system", "system", full[:4000], clipped=True)]
    )
    messages, report = rebuild.messages_from(snapshot, system_prompt=full)

    assert messages[0]["content"] == full
    assert report.restored == ["System"]
    assert report.clipped == []
    assert report.exact


def test_a_system_prompt_that_no_longer_matches_stays_clipped():
    snapshot = RoundSnapshot(
        round=0, blocks=[block("system", "system", "the old prompt", clipped=True)]
    )
    _messages, report = rebuild.messages_from(
        snapshot, system_prompt="a completely different prompt"
    )
    assert report.clipped == ["System"]


# ── The edits ────────────────────────────────────────────────────────────────


def _four_message_round() -> RoundSnapshot:
    return RoundSnapshot(
        round=0,
        blocks=[
            block("system", "system", "be good"),
            block("history", "user", "older"),
            block("history", "assistant", "older reply"),
            block("user", "user", "the ask"),
        ],
    )


def test_set_system_and_edit_message_apply_by_index():
    snapshot = _four_message_round()
    messages, report = rebuild.messages_from(snapshot)
    out = rebuild.apply_edits(
        messages,
        snapshot,
        [
            ForkEdit(op="set_system", content="be terse"),
            ForkEdit(op="edit_message", index=3, content="a different ask"),
        ],
        report,
    )
    assert out[0]["content"] == "be terse"
    assert out[3]["content"] == "a different ask"
    assert report.rejected == []


def test_an_edit_that_matches_nothing_is_rejected_loudly():
    snapshot = _four_message_round()
    messages, report = rebuild.messages_from(snapshot)
    out = rebuild.apply_edits(
        messages, snapshot, [ForkEdit(op="edit_message", index=9, content="x")], report
    )
    assert out == messages
    assert report.rejected and "no message at index 9" in report.rejected[0]


def test_truncate_history_runs_last_so_indices_still_mean_what_they_meant():
    """`edit_message` addresses a message by index. Applied after a truncation had
    already removed two, the index would land on whatever slid into the slot."""
    snapshot = _four_message_round()
    messages, report = rebuild.messages_from(snapshot)
    out = rebuild.apply_edits(
        messages,
        snapshot,
        [
            ForkEdit(op="truncate_history", keep=0),
            ForkEdit(op="edit_message", index=3, content="a different ask"),
        ],
        report,
    )
    assert [m["content"] for m in out] == ["be good", "a different ask"]


# ── The fork, end to end ─────────────────────────────────────────────────────


@pytest.fixture
def parent_turn() -> TurnSnapshot:
    """A recorded turn that called `ui.open_pane`, in the ring."""
    calls = '[{"function":{"name":"ui.open_pane","arguments":{"id":"terminal"}}}]'
    turn = TurnSnapshot(
        turnId="parent1",
        agentId="main",
        model="test-model",
        provider="ollama",
        startedAt=1000.0,
        rounds=[
            RoundSnapshot(
                round=0,
                blocks=[
                    block("system", "system", "be good"),
                    block("user", "user", "open a terminal"),
                ],
                tools=[ToolEntry(name="ui.open_pane", group="ui", tokens=20)],
                activeGroups=["ui"],
            ),
            RoundSnapshot(
                round=1,
                blocks=[
                    block("system", "system", "be good"),
                    block("user", "user", "open a terminal"),
                    block("assistant", "assistant", calls),
                    block("tool_result", "tool", '{"ok":true}'),
                ],
                tools=[ToolEntry(name="ui.open_pane", group="ui", tokens=20)],
                activeGroups=["ui"],
            ),
        ],
    )
    recorder._turns.append(turn)
    return turn


@pytest.fixture
def forkable(monkeypatch):
    """A node that can run a fork: a configured agent and one browser catalog."""
    from backend.modules.agent import offline_conn
    from backend.modules.agent import routes as agent_routes

    monkeypatch.setattr(
        offline_conn,
        "live_agent_tools",
        lambda: [tool_decl("ui.open_pane"), tool_decl("ui.close_pane")],
    )
    monkeypatch.setattr(
        agent_routes,
        "_load_config",
        lambda: AgentConfig(model="test-model", provider="ollama"),
    )

    def install(turns: list) -> ScriptedModel:
        model = ScriptedModel(turns)
        monkeypatch.setattr(P, "chat_stream", model)
        return model

    return install


def test_the_decision_is_read_off_the_next_rounds_blocks(parent_turn):
    """Which is what makes the two sides of a diff comparable: it works the same
    way for the parent and for the fork, and needs no trajectory capture."""
    assert fork.decision_at(parent_turn, 0) == ["ui.open_pane"]
    assert fork.decision_at(parent_turn, 1) == []


def test_preview_shows_the_catalog_without_running_anything(parent_turn, forkable):
    model = forkable([])
    preview = fork.preview(
        ForkRequest(
            turn_id="parent1", edits=[ForkEdit(op="drop_tool", name="ui.open_pane")]
        )
    )

    assert "ui.open_pane" not in preview.tools
    assert "ui.close_pane" in preview.tools
    assert preview.drift.denied == ["ui.open_pane"]
    assert model.seen == []  # no provider call


def test_dropping_a_tool_removes_it_and_refuses_it_if_the_model_asks_anyway(
    parent_turn, forkable
):
    """Both halves matter. Under progressive disclosure the catalog is rebuilt every
    round, and `_dispatch_call` will forgivingly load a group for a tool it
    recognizes — so filtering the list once would not have been enough."""
    model = forkable([[("ui.open_pane", {"id": "terminal"})], "I cannot open panes."])

    record = await_(
        fork.run(
            ForkRequest(
                turn_id="parent1",
                edits=[ForkEdit(op="drop_tool", name="ui.open_pane")],
            )
        )
    )

    offered = [t["function"]["name"] for t in model.seen[0]["tools"]]
    assert "ui.open_pane" not in offered
    assert record.status == "complete"
    assert record.answer == "I cannot open panes."
    # The refusal reached the model as a tool result rather than as a success.
    tool_message = model.seen[1]["messages"][-1]
    assert "not available in this run" in tool_message["content"]
    # And the tool never ran: `simulate` covers the browser leg too.
    assert record.calls == []


def test_a_simulated_fork_never_runs_a_backend_plugin_tool(parent_turn, forkable):
    """The leg fixtures would have missed. A plugin tool is resolved server-side and
    never reaches a connection, so `OfflineConnection` alone would have executed it
    — which for a replay means doing the thing a second time."""
    from backend.sdk.registry import registry
    from backend.sdk.types import AgentTool

    ran: list[str] = []

    async def handler(args):
        ran.append("yes")
        return {"ok": True}

    registry.agent_tools["ui.plugin_action"] = AgentTool(
        name="ui.plugin_action",
        description="acts",
        handler=handler,
        parameters={},
        required=[],
        group="ui",
    )
    try:
        forkable([[("ui.plugin_action", {})], "done"])
        record = await_(
            fork.run(
                ForkRequest(
                    turn_id="parent1",
                    edits=[ForkEdit(op="set_system", content="be terse")],
                    fixtures={"ui.plugin_action": {"pretended": True}},
                )
            )
        )
    finally:
        registry.agent_tools.pop("ui.plugin_action", None)

    assert ran == []
    assert record.calls == ["ui.plugin_action"]


def test_the_fork_is_recorded_as_an_edge_and_diffs_against_its_parent(
    parent_turn, forkable
):
    forkable(["I cannot open panes."])
    record = await_(
        fork.run(
            ForkRequest(
                turn_id="parent1",
                edits=[ForkEdit(op="drop_tool", name="ui.open_pane")],
            )
        )
    )

    assert store.get_fork(record.fork_turn_id) is not None
    assert [f.fork_turn_id for f in store.list_forks(parent_turn_id="parent1")] == [
        record.fork_turn_id
    ]

    diff = fork.diff(record.fork_turn_id)
    assert diff.a.decision == ["ui.open_pane"]
    assert diff.b.decision == []
    assert diff.same_decision is False
    assert diff.tools_removed == ["ui.open_pane"]


def test_a_fork_of_a_turn_that_is_not_there_is_the_callers_error():
    with pytest.raises(fork.ForkError):
        fork.preview(ForkRequest(turn_id="nope"))


def test_a_round_out_of_range_says_how_many_there_are(parent_turn):
    with pytest.raises(fork.ForkError, match="this turn has 2"):
        fork.preview(ForkRequest(turn_id="parent1", from_round=7))


def await_(coro):
    """Run one coroutine to completion.

    The suite's `anyio` marker is not used here: these tests are synchronous except
    for the one call into the loop, and a bare `asyncio.run` keeps the fork's own
    event loop out of the fixtures' way.
    """
    import asyncio

    return asyncio.run(coro)


# ── The HTTP surface ─────────────────────────────────────────────────────────


def test_the_routes_answer_and_a_bad_turn_is_a_400(parent_turn, forkable):
    """Tested through the app rather than the functions, because a Pydantic
    response model silently drops a field the browser then reads as undefined —
    `to_dict()` passing proves nothing about what the browser receives."""
    from fastapi.testclient import TestClient

    from backend.app import app

    client = TestClient(app)

    forkable([])
    preview = client.post(
        "/api/agentpedia/fork/preview",
        json={
            "turn_id": "parent1",
            "edits": [{"op": "drop_tool", "name": "ui.open_pane"}],
        },
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["drift"]["denied"] == ["ui.open_pane"]
    assert body["rebuild"]["exact"] is True
    assert "ui.open_pane" not in body["tools"]

    assert client.post(
        "/api/agentpedia/fork/preview", json={"turn_id": "missing", "edits": []}
    ).status_code == 400

    assert client.get("/api/agentpedia/forks").json() == {"forks": []}
    assert client.get("/api/agentpedia/forks/nope/diff").status_code == 404
