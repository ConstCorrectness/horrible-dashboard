import asyncio
import json
from typing import Any

import httpx

from backend.modules.agent import orchestrator, permission_store, permissions, roster
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
            # tools are advertised; the model asks to show a pane. `show` rather
            # than `open_pane`: the arrangement verbs are no longer advertised by
            # default, and `show` is the one-call path a turn like this now takes.
            assert any(t["function"]["name"] == "show" for t in body["tools"])
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "show",
                                    "arguments": {"target": "Data flow"},
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
    assert tool_calls and tool_calls[0]["name"] == "show"
    assert tool_calls[0]["args"] == {"target": "Data flow"}

    answers = [d["text"] for ev, d in events if ev == "answer"]
    assert answers and "Data flow" in answers[0]
    assert any(ev == "done" for ev, _ in events)
    assert calls["n"] == 2


def test_turn_sends_low_temperature(monkeypatch) -> None:
    # Tool-calling turns decode greedily so the model emits structured calls
    # instead of narrating them; the temperature must reach the provider payload.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}}
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)
    asyncio.run(orchestrator.run_agent_turn(FakeConn(), "t", "hi"))
    assert seen["body"]["options"]["temperature"] == 0.0


def test_tool_temperature_setting_override(monkeypatch) -> None:
    monkeypatch.setattr(
        roster,
        "get_value",
        lambda key, default=None: 0.7 if "temperature" in key else default,
    )
    assert orchestrator._tool_temperature() == 0.7
    # A non-numeric override falls back to the default rather than crashing a turn.
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: "oops")
    assert orchestrator._tool_temperature() == orchestrator.DEFAULT_TOOL_TEMPERATURE


def test_orchestrator_model_setting_override(monkeypatch) -> None:
    # A blank/whitespace/non-string override reuses the configured agent model;
    # a real value lets a stronger model drive tool calls than chat/autosuggest.
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: "gemma4:12b")
    assert orchestrator._orchestrator_model("m") == "gemma4:12b"
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: "   ")
    assert orchestrator._orchestrator_model("m") == "m"
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: 123)
    assert orchestrator._orchestrator_model("m") == "m"


def test_active_editor_message_carries_open_buffer() -> None:
    ctx = {
        "instanceId": "editor.buffer:workspace-file:/x/sample.py",
        "snapshot": {
            "uri": "workspace-file:/x/sample.py",
            "title": "sample.py",
            "content": "print('hi')",
            "selection": {"from": 0, "to": 5, "text": "print"},
        },
    }
    msg = orchestrator._active_editor_message(ctx)
    assert msg is not None
    assert msg["role"] == "system"
    assert "print('hi')" in msg["content"]  # the open code is handed to the model
    assert (
        "workspace-file:/x/sample.py" in msg["content"]
    )  # addressable for proposeEdit
    assert "editor.proposeEdit" in msg["content"]
    assert "'print'" in msg["content"]  # the selection is surfaced


def test_truncated_buffer_forbids_writing_the_whole_file_back() -> None:
    """The un-truncated message tells the model to write the COMPLETE buffer back.
    When the frontend clips an oversized buffer to `agent.activeBufferBudget`, that
    same instruction would silently delete everything past the cut — so a `truncated`
    snapshot must flip the instruction, not merely carry less text."""
    snap = {
        "uri": "workspace-file:/x/big.py",
        "title": "big.py",
        "content": "line one\nline two",
    }
    full = orchestrator._active_editor_message({"snapshot": snap})
    clipped = orchestrator._active_editor_message(
        {"snapshot": {**snap, "truncated": True}}
    )
    assert full is not None and clipped is not None

    assert "complete updated buffer" in full["content"]
    assert "editor.proposeEdit" in full["content"]

    # The clipped one says the opposite, and says why.
    assert "complete updated buffer" not in clipped["content"]
    assert "CUT OFF" in clipped["content"]
    assert "must NOT write the whole buffer back" in clipped["content"]


def test_active_editor_message_skips_unsaved_or_missing() -> None:
    # An unsaved scratch buffer has no uri to target, and junk shapes are ignored.
    assert orchestrator._active_editor_message(None) is None
    assert orchestrator._active_editor_message({"snapshot": "nope"}) is None
    assert (
        orchestrator._active_editor_message(
            {"snapshot": {"uri": "(unsaved)", "content": "x"}}
        )
        is None
    )


def test_workspace_context_message_indexes_visible_panes() -> None:
    ctx = {
        "instanceId": "records.form#1",
        "snapshot": {"uri": "x", "content": "y"},
        "panes": [
            {
                "instanceId": "records.board#2",
                "viewId": "records.board",
                "title": "Board",
                "location": "area",
                "snapshot": {"schema": "deals", "stages": ["new", "won"]},
            },
            {"instanceId": "junk", "viewId": "junk"},  # no snapshot — dropped
        ],
    }
    msg = orchestrator._workspace_context_message(ctx)
    assert msg is not None
    assert msg["role"] == "system"
    assert "records.board#2" in msg["content"]
    assert '"deals"' in msg["content"]
    assert "junk" not in msg["content"]
    # It must actively suppress the discovery round it exists to replace.
    assert "get_pane_context" in msg["content"]


def test_workspace_context_message_absent_without_panes() -> None:
    assert orchestrator._workspace_context_message(None) is None
    assert orchestrator._workspace_context_message({"panes": []}) is None
    assert orchestrator._workspace_context_message({"snapshot": {"uri": "x"}}) is None
    # Every entry malformed → no message at all, not a bare header.
    assert orchestrator._workspace_context_message({"panes": ["nope"]}) is None


def test_turn_injects_active_editor_context(monkeypatch) -> None:
    # The focused buffer must reach the provider payload, right before the user turn.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}}
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)
    ctx = {
        "snapshot": {
            "uri": "note:abc",
            "title": "draft.md",
            "content": "ORIGINAL TEXT",
            "selection": {"from": 0, "to": 0, "text": ""},
        }
    }
    asyncio.run(orchestrator.run_agent_turn(FakeConn(), "t", "tidy this up", None, ctx))
    msgs = seen["body"]["messages"]
    assert any(m["role"] == "system" and "ORIGINAL TEXT" in m["content"] for m in msgs)
    # The editor context is the message immediately before the user prompt.
    user_idx = next(i for i, m in enumerate(msgs) if m["role"] == "user")
    assert "ORIGINAL TEXT" in msgs[user_idx - 1]["content"]


def test_turn_uses_orchestrator_model_override(monkeypatch) -> None:
    # The override must reach the provider payload, leaving config.model for the
    # chat/autosuggest paths untouched.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}}
        )

    _configure(monkeypatch)
    monkeypatch.setattr(
        roster,
        "get_value",
        lambda key, default=None: "gemma4:12b" if key.endswith(".model") else default,
    )
    _mock_ollama(monkeypatch, handler)
    asyncio.run(orchestrator.run_agent_turn(FakeConn(), "t", "hi"))
    assert seen["body"]["model"] == "gemma4:12b"


def test_orchestrator_model_override(monkeypatch) -> None:
    # Blank/unset → the configured agent model; a non-blank override wins (lets a
    # stronger model drive tool calls than chat/autosuggest use).
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: default)
    assert orchestrator._orchestrator_model("gemma4:e2b") == "gemma4:e2b"
    monkeypatch.setattr(
        roster,
        "get_value",
        lambda key, default=None: (
            "gemma4:12b" if key == "agent.orchestrator.model" else default
        ),
    )
    assert orchestrator._orchestrator_model("gemma4:e2b") == "gemma4:12b"
    # Whitespace-only override is treated as unset.
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: "   ")
    assert orchestrator._orchestrator_model("gemma4:e2b") == "gemma4:e2b"


def test_turn_uses_hyperparameters_overrides(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        # return streamed response
        body = '{"message":{"role":"assistant","content":"ok"},"done":true}\n'
        return httpx.Response(200, text=body)

    _configure(monkeypatch)  # configured model = "m"

    settings_dict = {
        "agent.orchestrator.temperature": 0.5,
        "agent.orchestrator.contextSize": 4096,
        "agent.orchestrator.maxTokens": 100,
        "agent.orchestrator.topP": 0.85,
    }
    monkeypatch.setattr(
        roster,
        "get_value",
        lambda key, default=None: settings_dict.get(key, default),
    )
    _mock_ollama(monkeypatch, handler)

    asyncio.run(orchestrator.run_agent_turn(FakeConn(), "t", "hi"))
    options = seen["body"]["options"]
    assert options["temperature"] == 0.5
    assert options["num_ctx"] == 4096
    assert options["num_predict"] == 100
    assert options["top_p"] == 0.85


def test_tools_for_presents_core_plus_only_preloaded_groups() -> None:
    # Many dynamic tools across several groups.
    dynamic = []
    for grp in ("files", "editor", "terminal", "database", "visualizer"):
        for i in range(5):
            dynamic.append({"name": f"{grp}.tool_{i}", "description": "desc"})
    conn = FakeConn(agent_tools=dynamic)

    # No matching keywords: the starting list is just the core (no dynamic groups).
    core_names = {t["function"]["name"] for t in orchestrator._core_tools()}
    tools = orchestrator._tools_for(conn, prompt="hello")
    assert {t["function"]["name"] for t in tools} == core_names
    # The meta tools are always present so the model can discover/load the rest.
    assert "list_tool_groups" in core_names
    assert "load_tools" in core_names

    # A database-flavored prompt preloads only that group, not the others.
    tools_db = orchestrator._tools_for(conn, prompt="run a sql query on the database")
    names_db = {t["function"]["name"] for t in tools_db}
    for i in range(5):
        assert f"database.tool_{i}" in names_db
        assert f"visualizer.tool_{i}" not in names_db


def test_group_catalog_groups_by_prefix() -> None:
    conn = FakeConn(
        agent_tools=[
            {"name": "files.read", "description": "r"},
            {"name": "files.write", "description": "w"},
            {"name": "editor.save", "description": "s"},
        ]
    )
    catalog = {g["name"]: g["tools"] for g in orchestrator._group_catalog(conn)}
    # Browser-pushed tools group by their name prefix. (Backend-registered grouped
    # tools, e.g. the training module's, may also appear in the catalog — assert on
    # the groups this test set up rather than the whole catalog.)
    assert catalog["files"] == 2 and catalog["editor"] == 1


def test_select_tools_caps_at_budget() -> None:
    dynamic = [{"name": f"files.tool_{i}", "description": "d"} for i in range(60)]
    conn = FakeConn(agent_tools=dynamic)
    tools = orchestrator._select_tools(conn, {"files"})
    assert len(tools) == orchestrator.TOOL_BUDGET
    # Core is kept (it's added first).
    names = {t["function"]["name"] for t in tools}
    assert "list_available_panes" in names and "load_tools" in names


def test_unemitted_tool_call_heuristic() -> None:
    tools = [{"function": {"name": "editor.applyEdit"}}]
    # Action phrasing → looks like it meant to act.
    assert orchestrator._looks_like_unemitted_tool_call(
        "I'll create the file now.", tools
    )
    # Naming a tool in prose → same.
    assert orchestrator._looks_like_unemitted_tool_call(
        "Calling editor.applyEdit.", tools
    )
    # A plain conversational reply → not a missed call (don't force a tool).
    assert not orchestrator._looks_like_unemitted_tool_call(
        "There are three panes open.", tools
    )
    assert not orchestrator._looks_like_unemitted_tool_call("", tools)


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
    # A terminal-flavored prompt preloads the terminal group, so the pushed tool is
    # presented alongside the static layout tools.
    tools = orchestrator._tools_for(conn, prompt="run a terminal command")
    names = [t["function"]["name"] for t in tools]
    # the always-on layout tools are still present — the cheap read set plus `show`.
    # The 16 arrangement verbs are NOT: they moved to the loadable `layout` group, and
    # a terminal-flavoured prompt has no reason to preload them.
    for layout in orchestrator.LAYOUT_READ_TOOLS:
        assert layout["function"]["name"] in names
    assert "split_area" not in names
    # ...plus the runtime-registered tool.
    assert "terminal.exec" in names
    # The schema crossed the wire; the handler never appears in the tool def.
    exec_tool = next(t for t in tools if t["function"]["name"] == "terminal.exec")
    assert "handler" not in exec_tool["function"]
    assert (
        exec_tool["function"]["parameters"]["properties"]["command"]["type"] == "string"
    )


def test_tools_for_dedupes_by_name_static_wins() -> None:
    conn = WsConnection(websocket=None)
    # A pushed tool colliding with a static layout tool must not duplicate it.
    # `show` rather than `open_pane`: the collision that matters is with an
    # always-on tool, and the arrangement verbs are no longer in the default set.
    conn.agent_tools = [{"name": "show", "description": "shadow", "kind": "command"}]
    names = [t["function"]["name"] for t in orchestrator._tools_for(conn)]
    assert names.count("show") == 1


def test_tools_for_skips_nameless_entries() -> None:
    conn = WsConnection(websocket=None)
    conn.agent_tools = [{"description": "no name", "kind": "agentTool"}, {"name": ""}]
    # Nameless browser entries never become tools or groups. (Backend-registered
    # grouped tools may still populate the dynamic pool; assert the nameless
    # *browser* entries contributed nothing, not that the pool is empty.)
    dynamic_names = {
        t["function"]["name"] for t in orchestrator._all_dynamic_tools(conn)
    }
    assert "" not in dynamic_names
    assert all(name for name in dynamic_names)  # no empty/None names
    # The conn's own (nameless) entries added no groups beyond backend ones.
    assert not any(g["name"] == "" for g in orchestrator._group_catalog(conn))
    # With nothing keyword-preloaded, the turn still starts from exactly core.
    core_names = {t["function"]["name"] for t in orchestrator._core_tools()}
    assert {t["function"]["name"] for t in orchestrator._tools_for(conn)} == core_names


class _Call:
    def __init__(self, name, arguments=None, arg_error=None):
        self.name = name
        self.arguments = arguments or {}
        self.arg_error = arg_error


def test_builtin_modules_can_ship_a_guide() -> None:
    """The second disclosure tier used to be reachable only by connectors and MCP
    servers, so every built-in group had a blurb and nothing else — and detail that
    belonged in a guide had nowhere to go but the system prompt, where it was charged
    to every round of every turn.

    `layout` is the load-bearing case: it has no connector and no MCP server behind
    it, and it now carries the geometry rules the prompt used to.
    """
    guide = orchestrator._group_guide("layout")
    assert guide and "instanceId" in guide
    # It really is the disclosed tier, delivered as a system message.
    msg = orchestrator._guides_message({"layout"})
    assert msg and msg["role"] == "system" and "instanceId" in msg["content"]

    # A group with no guide file is silent, not an error.
    assert orchestrator._group_guide("definitely_not_a_group") is None

    # ...and connectors still resolve, so module guides didn't shadow them.
    # Registration is a side effect of `register_connectors()`, so call it here
    # rather than relying on another test file having done so — otherwise this
    # assertion only holds when the whole suite runs.
    from backend.modules.connectors.setup import register_connectors

    register_connectors()
    assert orchestrator._group_guide("github")


def test_unparseable_arguments_are_refused_before_the_tool_runs() -> None:
    """A call whose argument payload failed to parse must come back as an error, not
    execute with `{}`. Running it anyway would fire the tool with every argument
    missing — and the browser would never even be asked."""
    conn = FakeConn(agent_tools=[{"name": "files.delete", "description": "d"}])
    res = asyncio.run(
        orchestrator._dispatch_call(
            conn,
            "t",
            _Call("files.delete", arg_error="arguments were not valid JSON (…)"),
            {"files"},
        )
    )
    assert "error" in res
    assert "could not read the arguments" in res["error"]
    # Nothing was relayed to the browser.
    assert not [s for s in conn.sent if s.get("event") == "tool_call"]


def test_list_tool_groups_meta_lists_dynamic_groups() -> None:
    conn = FakeConn(agent_tools=[{"name": "files.read", "description": "r"}])
    res = asyncio.run(
        orchestrator._dispatch_call(conn, "t", _Call("list_tool_groups"), set())
    )
    assert any(g["name"] == "files" for g in res["groups"])


def test_load_tools_meta_activates_group() -> None:
    conn = FakeConn(agent_tools=[{"name": "files.read", "description": "r"}])
    active: set[str] = set()
    res = asyncio.run(
        orchestrator._dispatch_call(
            conn, "t", _Call("load_tools", {"groups": ["files", "bogus"]}), active
        )
    )
    assert res["loaded"] == ["files"]
    assert res["unknown"] == ["bogus"]
    assert "files" in active
    assert "files.read" in res["tools"]


def _fake_connector(connector_id: str, *, blurb: str, guide: str | None):
    """Register a throwaway connector so the group blurb/guide lookups have something
    to find. Returns a cleanup callable."""
    from backend.sdk.registry import registry as _plugins
    from backend.sdk.types import Connector, ConnectorStatus

    _plugins.connectors[connector_id] = Connector(
        id=connector_id,
        label=connector_id,
        kind="oauth",
        icon="x",
        blurb=blurb,
        status=lambda: ConnectorStatus(connected=False),
        begin=lambda _o: {},
        disconnect=lambda: None,
        guide=guide,
    )
    return lambda: _plugins.connectors.pop(connector_id, None)


def test_group_catalog_blurb_falls_back_to_the_connector() -> None:
    # One connector definition feeds both the home tile and the agent's catalog, so a
    # connector never shows up as a bare "<id> tools" fallback.
    cleanup = _fake_connector("fakegh", blurb="Search code on FakeGH.", guide=None)
    try:
        conn = FakeConn(agent_tools=[{"name": "fakegh.search", "description": "s"}])
        catalog = {
            g["name"]: g["description"] for g in orchestrator._group_catalog(conn)
        }
        assert catalog["fakegh"] == "Search code on FakeGH."
    finally:
        cleanup()


def test_load_tools_delivers_the_group_guide() -> None:
    cleanup = _fake_connector(
        "fakegh", blurb="b", guide="# FakeGH\nUse repo: to scope."
    )
    try:
        conn = FakeConn(agent_tools=[{"name": "fakegh.search", "description": "s"}])
        res = asyncio.run(
            orchestrator._dispatch_call(
                conn, "t", _Call("load_tools", {"groups": ["fakegh"]}), set()
            )
        )
        assert "Use repo: to scope." in res["guide"]
    finally:
        cleanup()


def test_load_tools_omits_guide_when_the_group_has_none() -> None:
    conn = FakeConn(agent_tools=[{"name": "files.read", "description": "r"}])
    res = asyncio.run(
        orchestrator._dispatch_call(
            conn, "t", _Call("load_tools", {"groups": ["files"]}), set()
        )
    )
    assert "guide" not in res


def test_preloaded_group_guide_is_injected_as_a_system_message() -> None:
    # The path that actually matters: a keyword preload activates a group without the
    # model ever calling load_tools, so this is the only chance to deliver the guide.
    cleanup = _fake_connector(
        "fakegh", blurb="b", guide="# FakeGH\nOnly the default branch."
    )
    try:
        msg = orchestrator._guides_message({"fakegh"})
        assert msg["role"] == "system"
        assert "Only the default branch." in msg["content"]
        assert orchestrator._guides_message({"files"}) is None
        assert orchestrator._guides_message(set()) is None
    finally:
        cleanup()


def test_github_keywords_preload_the_group() -> None:
    conn = FakeConn(agent_tools=[{"name": "github.searchCode", "description": "s"}])
    # "repo" is the word people use; the group's own name is an implicit keyword.
    assert "github" in orchestrator._preload_groups(conn, "find it in my repo")
    assert "github" in orchestrator._preload_groups(conn, "search github for X")
    assert "github" not in orchestrator._preload_groups(conn, "what time is it")


def test_auto_load_forgiveness_activates_group_and_runs() -> None:
    # The model calls a known tool from a group it never loaded — run it anyway and
    # activate the group so it's visible next round.
    conn = FakeConn(agent_tools=[{"name": "widget.foo", "description": "foo"}])
    active: set[str] = set()
    res = asyncio.run(
        orchestrator._dispatch_call(conn, "t", _Call("widget.foo"), active)
    )
    assert "widget" in active
    # FakeConn auto-resolved the relayed frontend call.
    assert res == {"ok": True}


def _ollama_tool_call(name, arguments):
    return httpx.Response(
        200,
        json={
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
            }
        },
    )


def test_dynamic_injection_across_rounds(monkeypatch) -> None:
    # widget.* has no preload keyword, so it is NOT advertised until loaded.
    conn = FakeConn(agent_tools=[{"name": "widget.foo", "description": "do foo"}])
    seen: list[set[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        names = {
            t["function"]["name"] for t in json.loads(request.content).get("tools", [])
        }
        seen.append(names)
        n = len(seen)
        if n == 1:
            assert {"list_tool_groups", "load_tools"} <= names
            assert "widget.foo" not in names  # not yet disclosed
            return _ollama_tool_call("list_tool_groups", {})
        if n == 2:
            return _ollama_tool_call("load_tools", {"groups": ["widget"]})
        if n == 3:
            assert "widget.foo" in names  # injected after load_tools
            return _ollama_tool_call("widget.foo", {})
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "done"}}
        )

    _configure(monkeypatch)
    _mock_ollama(monkeypatch, handler)
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "please proceed"))

    # widget.foo was actually relayed to the browser once disclosed.
    assert any(
        d["name"] == "widget.foo" for ev, d in conn.events() if ev == "tool_call"
    )
    assert len(seen) == 4


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


def test_loaded_tool_groups_survive_into_the_next_turn() -> None:
    """Progressive disclosure used to reset every turn.

    The model spent a `load_tools` round to reach `files` in turn 1, used it, and
    in turn 2 the tools were simply gone — so a multi-turn task re-paid discovery
    on every turn, and a follow-up like "now delete it" arrived with nothing to
    delete it *with*. Groups now carry within a session.
    """
    conn = FakeConn()
    # Turn 1 ended with `files` loaded.
    orchestrator._remember_groups(conn, "main", {"files"})

    # A follow-up turn (non-empty history) starts with it still active.
    assert orchestrator._carried(conn, "main", [{"role": "user", "content": "hi"}]) == {
        "files"
    }
    # A different agent on the same socket keeps its own scope.
    assert (
        orchestrator._carried(conn, "dba", [{"role": "user", "content": "hi"}]) == set()
    )


def test_a_new_session_starts_with_nothing_carried() -> None:
    # Empty history is the widget saying "new chat" — an exact signal that needs no
    # extra protocol, and what makes New Chat genuinely start over.
    conn = FakeConn()
    orchestrator._remember_groups(conn, "main", {"files", "editor"})
    assert orchestrator._carried(conn, "main", []) == set()
    # …and the reset is durable, not just this call.
    assert (
        orchestrator._carried(conn, "main", [{"role": "user", "content": "x"}]) == set()
    )


def test_the_carry_is_bounded_and_drops_the_oldest() -> None:
    # Every carried group costs schema bytes on every later turn; unbounded growth
    # walks the tool list back over the reasoning cliff progressive disclosure exists
    # to stay under.
    conn = FakeConn()
    history = [{"role": "user", "content": "x"}]
    orchestrator._remember_groups(conn, "main", {"files"})
    orchestrator._remember_groups(conn, "main", {"files", "editor"})
    orchestrator._remember_groups(
        conn, "main", {"files", "editor", "database", "library"}
    )
    carried = orchestrator._carried(conn, "main", history)
    assert len(carried) == orchestrator.MAX_CARRIED_GROUPS
    # The two newcomers are the ones most likely to be about the current thread, so
    # the oldest survivor (`files`) is what goes.
    assert {"database", "library"} <= carried
    assert "files" not in carried


def _answer_once(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"message": {"role": "assistant", "content": "done"}}
    )


def test_turn_stamps_its_io_and_closes_the_capture(monkeypatch) -> None:
    """The two halves of correlating a turn to its wire: every request the loop
    makes carries the turn and round, and the capture is closed out at the end with
    the model's real context window.

    `instrumented_client` is used for real here — the mock transport goes
    *underneath* it — because the stamping happens in the recorder it wraps, and a
    test that patched the client away would prove nothing.
    """
    from backend.modules.interpretability import recorder as interp
    from backend.modules.telemetry import turn as telemetry_turn
    from backend.modules.telemetry.recorder import recorder as io

    _configure(monkeypatch)
    real_client = orchestrator.instrumented_client
    monkeypatch.setattr(
        orchestrator,
        "instrumented_client",
        lambda **kw: real_client(transport=httpx.MockTransport(_answer_once), **kw),
    )

    async def fake_window(*_args: Any, **_kw: Any) -> int:
        return 8192

    from backend.modules.interpretability import window

    monkeypatch.setattr(window, "context_length", fake_window)

    io.clear()
    interp.clear()
    asyncio.run(orchestrator.run_agent_turn(FakeConn(), "t-stamp", "hi"))

    provider_calls = [e for e in io.recent() if e.source == "outbound"]
    assert provider_calls, "the provider round trip was not recorded at all"
    assert all(e.turn_id == "t-stamp" for e in provider_calls)
    assert provider_calls[0].round == 0

    # The window landed on the turn, and the ambient stamp did not outlive the loop.
    [turn] = [t for t in interp.recent_turns() if t.turnId == "t-stamp"]
    assert turn.modelContextLength == 8192
    assert telemetry_turn.current() is None
