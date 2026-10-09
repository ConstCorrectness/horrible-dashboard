"""The node's own spans, driven through the **real** `run_agent_loop`.

Everything between the scripted model and the captured span is production code;
the scripted model is wrapped in the same `traced_chat` decorator the provider
functions carry, so the chat span comes from the chokepoint, not the test.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.agent.providers import Usage
from backend.modules.evals import runner_agent
from backend.modules.evals.models import EvalCase, Expect, Expose, ToolCall
from backend.modules.otel import ids, materialize, store, tracing
from backend.tests.test_evals_runner import INFO, ScriptedModel, tool_decl


@pytest.fixture()
def spans(monkeypatch):
    """Capture finished spans synchronously through the exporter slot — the same
    slot the external exporter (Phase 3) is swapped into."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    exporter = InMemorySpanExporter()
    slot = tracing.export_slot()
    slot.inner = SimpleSpanProcessor(exporter)
    materialize.reset()
    yield exporter
    slot.inner = None
    materialize.reset()


@pytest.fixture
def scripted(monkeypatch):
    from backend.modules.agent import providers as P

    def install(turns: list) -> ScriptedModel:
        model = ScriptedModel(turns)
        monkeypatch.setattr(P, "chat_stream", tracing.traced_chat(model))
        return model

    return install


def _case() -> EvalCase:
    return EvalCase(
        id="open",
        prompt="open a terminal",
        expose=Expose(mode="explicit", preload=["ui"]),
        expect=Expect(
            grade="subset",
            calls=[ToolCall(name="open_pane", arguments={"id": "terminal"})],
        ),
        fixtures={"open_pane": {"opened": True}},
    )


async def _run() -> None:
    await runner_agent.run_case(
        _case(),
        [tool_decl("ui.noop")],
        provider=INFO,
        endpoint="http://localhost:11434",
        model="test-model",
    )


def _by_name(finished) -> dict[str, list]:
    out: dict[str, list] = {}
    for s in finished:
        out.setdefault(s.name.split(" ", 1)[0], []).append(s)
    return out


@pytest.mark.anyio
async def test_a_turn_is_one_trace_of_agent_chat_and_tool_spans(scripted, spans):
    scripted(
        [
            {
                "content": "",
                "calls": [("open_pane", {"id": "terminal"})],
                "usage": Usage(tokens_in=40, tokens_out=7),
            },
            "Opened the terminal.",
        ]
    )
    await _run()
    groups = _by_name(spans.get_finished_spans())
    assert sorted(groups) == ["chat", "execute_tool", "invoke_agent"]
    (agent,) = groups["invoke_agent"]
    chats = groups["chat"]
    (tool,) = groups["execute_tool"]
    assert len(chats) == 2

    turn_id = agent.attributes["horrible.turn_id"]
    trace_id = agent.context.trace_id
    # Derived, not random: the trace id is a function of the turn id.
    assert format(trace_id, "032x") == ids.trace_id_for_turn(turn_id)
    assert agent.parent is None
    for child in [*chats, tool]:
        assert child.context.trace_id == trace_id
        assert child.parent.span_id == agent.context.span_id

    assert agent.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert chats[0].attributes["gen_ai.request.model"] == "test-model"
    assert chats[0].attributes["gen_ai.provider.name"] == "ollama"
    assert chats[0].attributes["gen_ai.usage.input_tokens"] == 40
    # The second round reported nothing: absent, not a measured-looking zero.
    assert "gen_ai.usage.input_tokens" not in chats[1].attributes
    assert tool.attributes["gen_ai.tool.name"] == "open_pane"
    assert tool.attributes["horrible.round"] == 0
    assert tool.status.is_ok


@pytest.mark.anyio
async def test_content_is_absent_unless_opted_in(scripted, spans, monkeypatch):
    scripted([[("open_pane", {"id": "terminal", "token": "sk-live-abc"})], "ok"])
    await _run()
    for s in spans.get_finished_spans():
        for key in ("gen_ai.input.messages", "gen_ai.output.messages"):
            assert key not in s.attributes
        assert "gen_ai.tool.call.arguments" not in s.attributes

    spans.clear()
    monkeypatch.setattr(tracing, "capture_content", lambda: True)
    scripted(
        [
            [
                (
                    "open_pane",
                    {
                        "id": "terminal",
                        "api_key": "sk-live-abcdefghijklmnopqrstuvwxyz",
                    },
                )
            ],
            "ok",
        ]
    )
    await _run()
    (tool,) = _by_name(spans.get_finished_spans())["execute_tool"]
    args = tool.attributes["gen_ai.tool.call.arguments"]
    assert "terminal" in args
    assert "sk-live-abcdefghijklmnopqrstuvwxyz" not in args


@pytest.mark.anyio
async def test_a_denied_tool_is_an_error_span(scripted, spans):
    from backend.modules.agent import orchestrator
    from backend.modules.agent.offline_conn import OfflineConnection

    scripted([[("open_pane", {"id": "terminal"})], "gave up"])
    await orchestrator.run_agent_loop(
        OfflineConnection([tool_decl("ui.noop")], {}),
        "t-denied",
        [{"role": "user", "content": "open a terminal"}],
        [
            {
                "type": "function",
                "function": {"name": "open_pane", "description": "", "parameters": {}},
            }
        ],
        INFO,
        "http://localhost:11434",
        "test-model",
        _noop_emit,
        temperature=0.0,
        deny_tools={"open_pane"},
    )
    (tool,) = _by_name(spans.get_finished_spans())["execute_tool"]
    assert not tool.status.is_ok
    assert "not available" in tool.status.description


async def _noop_emit(kind: str, text: str) -> None:
    return None


def test_delegate_nests_in_the_parents_trace(spans):
    with tracing.agent_span(
        turn_id="root1", agent_id="main", agent_name="", model="m", provider="ollama"
    ):
        with tracing.tool_span("agent.delegate", call_id="c0", round_no=0):
            with tracing.agent_span(
                turn_id="root1:coder:abc123",
                agent_id="coder",
                agent_name="Coder",
                model="m",
                provider="ollama",
                parent_turn_id="root1",
            ):
                pass
    groups = _by_name(spans.get_finished_spans())
    # By turn, not start time: on a coarse clock the two start in the same tick.
    by_turn = {s.attributes["horrible.turn_id"]: s for s in groups["invoke_agent"]}
    outer, inner = by_turn["root1"], by_turn["root1:coder:abc123"]
    (delegate_call,) = groups["execute_tool"]
    assert inner.parent.span_id == delegate_call.context.span_id
    assert inner.context.trace_id == outer.context.trace_id
    assert format(outer.context.trace_id, "032x") == ids.trace_id_for_turn(
        "root1:coder:abc123"
    )


def test_local_spans_reach_the_store_and_are_never_projected(spans):
    from backend.modules.trajectories import store as traj_store

    with tracing.agent_span(
        turn_id="local-turn", agent_id="main", agent_name="", model="m", provider="x"
    ):
        pass
    tracing.force_flush()
    trace_id = ids.trace_id_for_turn("local-turn")
    stored = store.get_trace(trace_id)
    assert [s.origin for s in stored] == ["local"]
    # A span sent back under our trace id (a user's MCP server) is not a new run.
    assert materialize.materialize(trace_id) is None
    _, total = traj_store.list_runs()
    assert total == 0


def test_traceparent_round_trips(spans):
    with tracing.agent_span(
        turn_id="tp", agent_id="main", agent_name="", model="m", provider="x"
    ):
        header = tracing.current_traceparent()
    assert header is not None
    version, trace_hex, span_hex, flags = header.split("-")
    assert version == "00" and trace_hex == ids.trace_id_for_turn("tp")

    with tracing.remote_parent(header):
        with tracing.agent_span(
            turn_id="remote-turn",
            agent_id="main",
            agent_name="",
            model="m",
            provider="x",
        ):
            pass
    remote = [
        s
        for s in spans.get_finished_spans()
        if s.attributes.get("horrible.turn_id") == "remote-turn"
    ][0]
    # Continues the caller's trace rather than deriving one from its own turn.
    assert format(remote.context.trace_id, "032x") == trace_hex
    assert format(remote.parent.span_id, "016x") == span_hex


# --- what a trace UI needs: I/O, threads, sources -------------------------------


async def _loop(turn_id: str, **kwargs) -> None:
    from backend.modules.agent import orchestrator
    from backend.modules.agent.offline_conn import OfflineConnection

    await orchestrator.run_agent_loop(
        OfflineConnection([tool_decl("ui.noop")], {}),
        turn_id,
        [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "what is two plus two"},
        ],
        [],
        INFO,
        "http://localhost:11434",
        "test-model",
        _noop_emit,
        temperature=0.0,
        **kwargs,
    )


@pytest.mark.anyio
async def test_root_span_carries_prompt_and_answer_only_when_wanted(
    scripted, spans, monkeypatch
):
    scripted(["four"])
    await _loop("io-off")
    (agent,) = _by_name(spans.get_finished_spans())["invoke_agent"]
    assert "gen_ai.input.messages" not in agent.attributes
    assert "gen_ai.output.messages" not in agent.attributes

    spans.clear()
    # Wanted by a destination (Opik's toggle), not by the global setting.
    tracing.set_destination_content("opik", True)
    try:
        scripted(["four"])
        await _loop("io-on")
    finally:
        tracing.set_destination_content("opik", False)
    (agent,) = _by_name(spans.get_finished_spans())["invoke_agent"]
    # The turn's own question — the newest user message, not the history.
    assert json.loads(agent.attributes["gen_ai.input.messages"]) == [
        {"role": "user", "content": "what is two plus two"}
    ]
    assert json.loads(agent.attributes["gen_ai.output.messages"]) == [
        {"role": "assistant", "content": "four"}
    ]


@pytest.mark.anyio
async def test_session_id_and_source_label_the_root(scripted, spans):
    scripted(["ok"])
    await _loop("sess", trace_source="chat", conversation_id="session-1")
    (agent,) = _by_name(spans.get_finished_spans())["invoke_agent"]
    assert agent.attributes["gen_ai.conversation.id"] == "session-1"
    assert agent.attributes["horrible.source"] == "chat"


@pytest.mark.anyio
async def test_a_flow_node_is_traced_under_its_own_name(scripted, spans):
    scripted(["ok"])
    await _loop("flow-run", otel_agent=("flow:f1:n2", "Summarise"), trace_source="flow")
    (agent,) = _by_name(spans.get_finished_spans())["invoke_agent"]
    assert agent.attributes["gen_ai.agent.id"] == "flow:f1:n2"
    assert agent.name == "invoke_agent Summarise"


def test_labels_stamp_entry_spans_only(spans):
    with tracing.labels(source="evals-judge", eval_case="c1"):
        with tracing.agent_span(
            turn_id="lbl", agent_id="main", agent_name="", model="m", provider="x"
        ):
            with tracing.chat_span(
                provider_kind="x", model="m", endpoint="", messages=[], params={}
            ):
                pass
        with tracing.chat_span(
            provider_kind="x", model="m", endpoint="", messages=[], params={}
        ):
            pass
    finished = spans.get_finished_spans()
    agent = next(s for s in finished if s.name.startswith("invoke_agent"))
    nested, bare = sorted(
        (s for s in finished if s.name.startswith("chat")),
        key=lambda s: s.parent is None,
    )
    assert agent.attributes["horrible.eval_case"] == "c1"
    assert bare.attributes["horrible.source"] == "evals-judge"
    # Nested under our own span: it inherits nothing.
    assert "horrible.eval_case" not in nested.attributes
    # And outside the block, nothing at all.
    with tracing.chat_span(
        provider_kind="x", model="m", endpoint="", messages=[], params={}
    ):
        pass
    assert "horrible.source" not in spans.get_finished_spans()[-1].attributes


def test_delegate_source_defaults_and_explicit_source_wins(spans):
    with tracing.labels(source="ignored-under-explicit"):
        with tracing.agent_span(
            turn_id="src",
            agent_id="main",
            agent_name="",
            model="m",
            provider="x",
            source="mobile",
        ):
            pass
    (agent,) = spans.get_finished_spans()
    assert agent.attributes["horrible.source"] == "mobile"


def test_a_fork_is_its_own_trace_and_its_delegates_join_it() -> None:
    original = ids.trace_id_for_turn("t1")
    fork = ids.trace_id_for_turn("t1:fork:ab12cd34")
    assert fork != original
    assert ids.trace_id_for_turn("t1:fork:ab12cd34:coder:ef56") == fork
    # Two forks of one turn are two traces.
    assert ids.trace_id_for_turn("t1:fork:99999999") not in (original, fork)
    # Ordinary delegates are untouched.
    assert ids.trace_id_for_turn("t1:coder:ab12") == original


def test_the_resource_names_this_node(spans):
    from backend.modules.network.identity import load_identity

    with tracing.agent_span(
        turn_id="node", agent_id="main", agent_name="", model="m", provider="x"
    ):
        pass
    (agent,) = spans.get_finished_spans()
    # The provider is made once per process, so compare against the identity it
    # captured rather than this test's (per-test data dir) one.
    node = agent.resource.attributes.get("service.instance.id")
    assert node and len(node) == len(load_identity().node_id)


# --- outbound propagation ------------------------------------------------------


class _FakeUrl:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    def __init__(self, host: str) -> None:
        self.url = _FakeUrl(host)
        self.headers: dict[str, str] = {}


def test_traceparent_goes_to_local_targets_only(spans, monkeypatch) -> None:
    settings: dict[str, object] = {"otel.propagateHttp": True}
    monkeypatch.setattr(
        tracing, "_setting", lambda key, default: settings.get(key, default)
    )
    with tracing.agent_span(
        turn_id="prop", agent_id="main", agent_name="", model="m", provider="x"
    ):
        local, lan, public = (
            _FakeRequest("127.0.0.1"),
            _FakeRequest("192.168.1.20"),
            _FakeRequest("api.openai.com"),
        )
        for request in (local, lan, public):
            tracing.inject_traceparent(request)
        # A hosted provider has no use for our trace id, and a header is sent
        # whether or not the far side reads it.
        assert "traceparent" in local.headers and "traceparent" in lan.headers
        assert "traceparent" not in public.headers
        assert local.headers["traceparent"].split("-")[1] == ids.trace_id_for_turn(
            "prop"
        )

        # Off by default, and an existing header is never overwritten.
        settings["otel.propagateHttp"] = False
        off = _FakeRequest("127.0.0.1")
        tracing.inject_traceparent(off)
        assert off.headers == {}
        settings["otel.propagateHttp"] = True
        theirs = _FakeRequest("127.0.0.1")
        theirs.headers["traceparent"] = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
        tracing.inject_traceparent(theirs)
        assert theirs.headers["traceparent"].endswith("-01")


# --- games ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_games_move_is_its_own_trace(spans) -> None:
    """Each move is one drive of the harness, so each is its own trace — and the
    move's model calls nest inside it."""
    from backend.modules.games.loadout import LlmHarness
    from backend.modules.games.policy import AgentPolicy

    calls = [
        {"tool_calls": [("game.chooseAction", {"action_id": "4"})]},
    ]

    async def chat(messages, tools):
        from backend.modules.agent.providers import ChatResult, ToolCall

        turn = calls.pop(0)
        return ChatResult(
            assistant_message={"role": "assistant", "content": ""},
            tool_calls=[
                ToolCall(id=f"c{i}", name=name, arguments=args)
                for i, (name, args) in enumerate(turn["tool_calls"])
            ],
            content="",
        )

    policy = AgentPolicy(
        chat_fn=chat,
        load_harness=lambda _g: LlmHarness("tictactoe", context="play well", tools=[]),
    )
    chosen = await policy.run_once(
        {"game": "tictactoe", "board": [None] * 9},
        [{"id": "4", "label": "centre"}],
        "tictactoe",
    )
    assert chosen == "4"
    (move,) = _by_name(spans.get_finished_spans())["invoke_agent"]
    assert move.attributes["horrible.game"] == "tictactoe"
    assert move.attributes["gen_ai.provider.name"] == "games"
    assert move.attributes["horrible.legal_actions"] == 1
    # Not one trace for the whole match: `:` is the delegate separator, so a move id
    # using it would hand every move of the game the same derived trace.
    assert move.attributes["horrible.turn_id"].startswith("game.tictactoe.")
    assert move.parent is None


@pytest.mark.anyio
async def test_a_task_agent_session_is_one_trace(spans, monkeypatch) -> None:
    """A bug-hunt session: its model calls and tool calls nest under one span."""
    from backend.modules.agent.providers import ChatResult, ToolCall
    from backend.modules.games import task_agent
    from backend.modules.games.loadout import LlmHarness

    monkeypatch.setattr(
        task_agent, "get_llm_harness", lambda gid: LlmHarness(gid, context="", tools=[])
    )
    turns = [
        [("task.readFile", {"path": "a.py"})],
        [("task.submit", {})],
    ]

    async def chat(messages, tools):
        calls = turns.pop(0)
        return ChatResult(
            assistant_message={"role": "assistant", "content": ""},
            tool_calls=[
                ToolCall(id=f"c{i}", name=n, arguments=a)
                for i, (n, a) in enumerate(calls)
            ],
            content="",
        )

    agent = task_agent.TaskAgent(chat_fn=chat, game_id="bughunt")
    out = await agent.run({"files": {"a.py": "x = 1"}, "description": "fix it"})
    assert out == {"files": {"a.py": "x = 1"}}
    groups = _by_name(spans.get_finished_spans())
    (session,) = groups["invoke_agent"]
    (tool,) = groups["execute_tool"]
    assert session.parent is None
    assert session.attributes["gen_ai.agent.id"] == "games:bughunt"
    assert session.attributes["horrible.source"] == "games"
    assert session.attributes["horrible.turn_id"].startswith("game.task.bughunt.")
    assert tool.attributes["gen_ai.tool.name"] == "task.readFile"
    assert tool.parent.span_id == session.context.span_id
