import asyncio
import json
from typing import Any

import httpx

from backend.modules.agent import orchestrator, permission_store, permissions
from backend.modules.agent.models import AgentConfig
from backend.modules.agent.orchestrator import _history_messages
from backend.modules.agent.permissions import Mode
from backend.modules.ws import WsConnection


def test_history_messages_keeps_text_turns_drops_junk() -> None:
    # Only well-formed user/assistant text survives; tool plumbing and bad shapes drop.
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "content": "ignored"},
        {"role": "assistant", "content": ""},
        {"role": "user"},
        "not a dict",
    ]
    assert _history_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert _history_messages(None) == []


class FakeConn:
    """Captures sends and auto-answers each tool_call by resolving its future,
    standing in for the browser end of the `agent` channel. Optionally auto-answers
    approval_request prompts with a fixed decision."""

    def __init__(
        self,
        tool_results: dict[str, Any] | None = None,
        agent_tools: list[dict[str, Any]] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_approvals: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.tool_results = tool_results or {}
        self.agent_tools = agent_tools or []
        self.approval = approval  # e.g. {"decision": "allow_once"}

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)
        if data.get("event") == "tool_call":
            d = data["data"]
            fut = self.pending.get(d["callId"])
            if fut is not None and not fut.done():
                fut.set_result(
                    {
                        "ok": True,
                        "result": self.tool_results.get(d["name"], {"ok": True}),
                    }
                )
        elif data.get("event") == "approval_request" and self.approval is not None:
            d = data["data"]
            fut = self.pending_approvals.get(d["approvalId"])
            if fut is not None and not fut.done():
                fut.set_result(self.approval)

    def events(self) -> list[tuple[str, dict[str, Any]]]:
        return [(s["event"], s["data"]) for s in self.sent]


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator,
        "_load_config",
        lambda: AgentConfig(model="m", endpoint="http://ollama.test"),
    )


def _mock_ollama(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        orchestrator,
        "instrumented_client",
        lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_turn_calls_tool_then_answers(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        if calls["n"] == 1:
            # tools are advertised; the model asks to open a pane
            assert any(t["function"]["name"] == "open_pane" for t in body["tools"])
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "open_pane",
                                    "arguments": {"id": "observability.io"},
                                }
                            }
                        ],
                    }
                },
            )
        # second round: the tool result must have been fed back in
        assert any(m.get("role") == "tool" for m in body["messages"])
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "Opened the Data flow pane.",
                }
            },
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t1", "open data flow"))
    events = conn.events()

    tool_calls = [d for ev, d in events if ev == "tool_call"]
    assert tool_calls and tool_calls[0]["name"] == "open_pane"
    assert tool_calls[0]["args"] == {"id": "observability.io"}

    answers = [d["text"] for ev, d in events if ev == "answer"]
    assert answers and "Data flow" in answers[0]
    assert any(ev == "done" for ev, _ in events)
    assert calls["n"] == 2


def test_answer_without_tools(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "Hi there."}}
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "hello"))
    events = conn.events()
    assert not [ev for ev, _ in events if ev == "tool_call"]
    assert [d["text"] for ev, d in events if ev == "answer"] == ["Hi there."]


def test_turn_streams_reasoning_and_content(monkeypatch) -> None:
    # Ollama streaming: the round emits `thinking` then `content` chunks; the
    # orchestrator relays them as `reasoning`/`token` deltas, then a final `answer`.
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        lines = [
            {"message": {"role": "assistant", "thinking": "Let me think. "}},
            {"message": {"role": "assistant", "content": "Hello"}},
            {"message": {"role": "assistant", "content": " there."}},
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ]
        return httpx.Response(
            200, content="".join(json.dumps(line) + "\n" for line in lines)
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "hi"))
    events = conn.events()

    reasoning = "".join(d["delta"] for ev, d in events if ev == "reasoning")
    tokens = "".join(d["delta"] for ev, d in events if ev == "token")
    assert reasoning == "Let me think. "
    assert tokens == "Hello there."
    assert [d["text"] for ev, d in events if ev == "answer"] == ["Hello there."]
    assert any(ev == "done" for ev, _ in events)


def test_unconfigured_emits_error(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "_load_config", lambda: None)
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "hi"))
    assert conn.sent[0]["event"] == "error"


def test_manifest_event_stores_tools_on_connection() -> None:
    async def go() -> list[dict[str, Any]]:
        conn = WsConnection(websocket=None)
        await orchestrator.handle_agent_message(
            conn,
            {
                "channel": "agent",
                "event": "manifest",
                "data": {
                    "tools": [
                        {
                            "name": "terminal.exec",
                            "description": "Run a command",
                            "params": {"type": "object", "properties": {}},
                            "sideEffect": True,
                            "specifierTemplate": "terminal.exec({command})",
                            "kind": "agentTool",
                        }
                    ]
                },
            },
        )
        return conn.agent_tools

    tools = asyncio.run(go())
    assert tools[0]["name"] == "terminal.exec"


def test_tools_for_merges_manifest_with_layout_tools() -> None:
    conn = WsConnection(websocket=None)
    conn.agent_tools = [
        {
            "name": "terminal.exec",
            "description": "Run a command",
            "params": {"type": "object", "properties": {"command": {"type": "string"}}},
            "sideEffect": True,
            "specifierTemplate": "terminal.exec({command})",
            "kind": "agentTool",
        }
    ]
    names = [t["function"]["name"] for t in orchestrator._tools_for(conn)]
    # every static layout tool is still present...
    for layout in orchestrator.LAYOUT_TOOLS:
        assert layout["function"]["name"] in names
    # ...plus the runtime-registered tool.
    assert "terminal.exec" in names
    # The schema crossed the wire; the handler never appears in the tool def.
    exec_tool = next(
        t
        for t in orchestrator._tools_for(conn)
        if t["function"]["name"] == "terminal.exec"
    )
    assert "handler" not in exec_tool["function"]
    assert (
        exec_tool["function"]["parameters"]["properties"]["command"]["type"] == "string"
    )


def test_tools_for_dedupes_by_name_static_wins() -> None:
    conn = WsConnection(websocket=None)
    # A pushed tool colliding with a static layout tool must not duplicate it.
    conn.agent_tools = [
        {"name": "open_pane", "description": "shadow", "kind": "command"}
    ]
    names = [t["function"]["name"] for t in orchestrator._tools_for(conn)]
    assert names.count("open_pane") == 1


def test_tools_for_skips_nameless_entries() -> None:
    conn = WsConnection(websocket=None)
    conn.agent_tools = [{"description": "no name", "kind": "agentTool"}, {"name": ""}]
    extra = [
        t
        for t in orchestrator._tools_for(conn)
        if t["function"]["name"]
        not in {lt["function"]["name"] for lt in orchestrator.LAYOUT_TOOLS}
    ]
    assert extra == []


# --- A5: permission gate in the orchestrator loop ---------------------------

# A side-effecting tool the browser "pushed" in its manifest.
_DANGER_MANIFEST = [
    {
        "name": "danger.do",
        "description": "Do a dangerous thing",
        "sideEffect": True,
        "specifierTemplate": "{path}",
        "kind": "agentTool",
    }
]


def _calls_danger_then_answers():
    """Model handler: round 1 calls danger.do({path:/x}); round 2 answers."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "danger.do",
                                    "arguments": {"path": "/x"},
                                }
                            }
                        ],
                    }
                },
            )
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "done"}}
        )

    return handler, calls


def _names(events, ev_name):
    return [d for ev, d in events if ev == ev_name]


def test_gate_denies_side_effect_in_plan(monkeypatch) -> None:
    monkeypatch.setattr(permission_store, "load_mode", lambda: Mode.PLAN)
    monkeypatch.setattr(permission_store, "load_rules", lambda: permissions.RuleSet())
    _configure(monkeypatch)
    handler, _ = _calls_danger_then_answers()
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn(agent_tools=_DANGER_MANIFEST)
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "go"))
    events = conn.events()
    # Denied before relay: no tool_call, no approval prompt.
    assert not _names(events, "tool_call")
    assert not _names(events, "approval_request")
    assert any(ev == "done" for ev, _ in events)


def test_gate_allows_side_effect_in_autonomous(monkeypatch) -> None:
    monkeypatch.setattr(permission_store, "load_mode", lambda: Mode.AUTONOMOUS)
    monkeypatch.setattr(permission_store, "load_rules", lambda: permissions.RuleSet())
    _configure(monkeypatch)
    handler, _ = _calls_danger_then_answers()
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn(agent_tools=_DANGER_MANIFEST)
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "go"))
    events = conn.events()
    relayed = _names(events, "tool_call")
    assert relayed and relayed[0]["name"] == "danger.do"
    assert not _names(events, "approval_request")


def test_gate_prompts_in_default_and_relays_on_allow_once(monkeypatch) -> None:
    monkeypatch.setattr(permission_store, "load_mode", lambda: Mode.DEFAULT)
    monkeypatch.setattr(permission_store, "load_rules", lambda: permissions.RuleSet())
    _configure(monkeypatch)
    handler, _ = _calls_danger_then_answers()
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn(agent_tools=_DANGER_MANIFEST, approval={"decision": "allow_once"})
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "go"))
    events = conn.events()
    prompts = _names(events, "approval_request")
    assert (
        prompts
        and prompts[0]["tool"] == "danger.do"
        and prompts[0]["specifier"] == "/x"
    )
    assert _names(events, "tool_call")  # relayed after approval


def test_gate_denies_on_user_deny(monkeypatch) -> None:
    monkeypatch.setattr(permission_store, "load_mode", lambda: Mode.DEFAULT)
    monkeypatch.setattr(permission_store, "load_rules", lambda: permissions.RuleSet())
    _configure(monkeypatch)
    handler, _ = _calls_danger_then_answers()
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn(agent_tools=_DANGER_MANIFEST, approval={"decision": "deny"})
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "go"))
    events = conn.events()
    assert _names(events, "approval_request")
    assert not _names(events, "tool_call")  # never relayed


def test_allow_always_persists_rule(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    _configure(monkeypatch)
    handler, _ = _calls_danger_then_answers()
    _mock_ollama(monkeypatch, handler)

    conn = FakeConn(agent_tools=_DANGER_MANIFEST, approval={"decision": "allow_always"})
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "go"))
    # The rule was written to the settings store and now allows the same call.
    assert "danger.do(/x)" in [
        f"{r.tool}({r.specifier})" for r in permission_store.load_rules().allow
    ]


def test_tool_result_resolves_pending_future() -> None:
    async def go() -> dict[str, Any]:
        conn = WsConnection(websocket=None)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        conn.pending["abc"] = fut
        await orchestrator.handle_agent_message(
            conn,
            {
                "channel": "agent",
                "event": "tool_result",
                "data": {"callId": "abc", "ok": True, "result": 1},
            },
        )
        return fut.result()

    assert asyncio.run(go()) == {"callId": "abc", "ok": True, "result": 1}
