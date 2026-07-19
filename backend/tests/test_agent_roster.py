"""The agent roster: spec resolution, per-agent settings fallback, mode override,
and the tool scoping a spec applies to the orchestrator's selection."""

import asyncio
from typing import Any

from backend.modules.agent import orchestrator, roster
from backend.modules.agent.permissions import Mode
from backend.sdk.registry import registry
from backend.sdk.types import AgentSpec, AgentTool


class FakeConn:
    def __init__(self, agent_tools: list[dict[str, Any]] | None = None) -> None:
        self.pending: dict[str, Any] = {}
        self.pending_approvals: dict[str, Any] = {}
        self.agent_tools = agent_tools or []
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def test_builtin_roster_resolves() -> None:
    ids = {a.id for a in roster.list_agents()}
    assert {"main", "coder", "dba", "researcher"} <= ids
    main = roster.get_agent("main")
    assert main is not None and main.tool_groups is None and main.can_delegate
    coder = roster.get_agent("coder")
    assert coder is not None and "editor" in (coder.tool_groups or [])
    assert not coder.can_delegate and not coder.include_peer_tools
    assert roster.get_agent("nope") is None


def test_plugin_agents_join_but_cannot_shadow_builtins() -> None:
    registry.agents["custom"] = AgentSpec(
        id="custom", name="Custom", description="", system_prompt="x"
    )
    registry.agents["main"] = AgentSpec(
        id="main", name="Impostor", description="", system_prompt="evil"
    )
    try:
        assert roster.get_agent("custom") is not None
        assert roster.get_agent("main").name == "Orchestrator"  # builtin wins
        assert [a.id for a in roster.list_agents()].count("main") == 1
    finally:
        registry.agents.clear()


def test_agent_setting_falls_back_to_orchestrator_keys(monkeypatch) -> None:
    values = {
        "agent.coder.model": "qwen3:8b",
        "agent.orchestrator.model": "gemma4:12b",
        "agent.orchestrator.temperature": 0.5,
    }
    monkeypatch.setattr(
        roster, "get_value", lambda key, default=None: values.get(key, default)
    )
    # Per-agent key wins for coder …
    assert roster.agent_setting("coder", "model") == "qwen3:8b"
    # … and an unset per-agent key falls back to the orchestrator's.
    assert roster.agent_setting("dba", "model") == "gemma4:12b"
    assert roster.agent_setting("dba", "temperature") == 0.5
    # main reads the orchestrator keys directly.
    assert roster.agent_setting("main", "model") == "gemma4:12b"


def test_resolve_mode_prefers_setting_over_spec_default(monkeypatch) -> None:
    coder = roster.get_agent("coder")
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: default)
    assert roster.resolve_mode(coder) is Mode.ACCEPT_EDITS  # spec default
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: "plan")
    assert roster.resolve_mode(coder) is Mode.PLAN  # setting override
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: "bogus")
    assert roster.resolve_mode(coder) is None  # unknown name → no override
    main = roster.get_agent("main")
    monkeypatch.setattr(roster, "get_value", lambda key, default=None: default)
    assert roster.resolve_mode(main) is None  # main has no default mode


def test_core_tools_gated_by_spec() -> None:
    names_default = {t["function"]["name"] for t in orchestrator._core_tools()}
    assert {"agent.ask_peer", "agent.delegate", "list_tool_groups"} <= names_default

    coder = roster.get_agent("coder")
    names_coder = {t["function"]["name"] for t in orchestrator._core_tools(coder)}
    assert "agent.ask_peer" not in names_coder
    assert "agent.delegate" not in names_coder
    # Layout verbs and the meta tools stay for every local agent.
    assert {"open_pane", "get_layout", "load_tools"} <= names_coder


def test_select_tools_scoped_to_spec_groups() -> None:
    conn = FakeConn(
        agent_tools=[
            {"name": "editor.proposeEdit", "description": "", "params": None},
            {"name": "browser.read", "description": "", "params": None},
        ]
    )
    coder = roster.get_agent("coder")
    # browser is active but NOT in coder's allowed groups — it must not appear.
    tools = orchestrator._select_tools(conn, {"editor", "browser"}, coder)
    names = {t["function"]["name"] for t in tools}
    assert "editor.proposeEdit" in names
    assert "browser.read" not in names


def test_dispatch_scoped_catalog_and_no_out_of_scope_forgiveness() -> None:
    conn = FakeConn(
        agent_tools=[
            {"name": "editor.proposeEdit", "description": "", "params": None},
            {"name": "browser.read", "description": "", "params": None},
        ]
    )
    coder = roster.get_agent("coder")

    class _Call:
        def __init__(self, name: str, arguments: dict[str, Any] | None = None) -> None:
            self.name = name
            self.arguments = arguments or {}

    catalog = asyncio.run(
        orchestrator._dispatch_call(conn, "t", _Call("list_tool_groups"), set(), coder)
    )
    # The catalog only shows coder's allowed groups (registered backend tool
    # groups like `symbols` may or may not be present depending on app wiring,
    # so assert containment, not equality).
    names = {g["name"] for g in catalog["groups"]}
    assert "editor" in names
    assert names <= set(coder.tool_groups or [])
    assert "browser" not in names

    loaded = asyncio.run(
        orchestrator._dispatch_call(
            conn,
            "t",
            _Call("load_tools", {"groups": ["browser", "editor"]}),
            set(),
            coder,
        )
    )
    assert loaded["loaded"] == ["editor"]
    assert "browser" in loaded["unknown"]

    refused = asyncio.run(
        orchestrator._dispatch_call(conn, "t", _Call("browser.read"), set(), coder)
    )
    assert "outside this agent's allowed groups" in refused["error"]


def test_ungrouped_plugin_core_tools_filtered_for_scoped_agents() -> None:
    registry.agent_tools["mytool.doThing"] = AgentTool(
        name="mytool.doThing", description="", handler=lambda args: {"ok": True}
    )
    try:
        assert "mytool.doThing" in {
            t["function"]["name"] for t in orchestrator._core_tools()
        }
        coder = roster.get_agent("coder")
        assert "mytool.doThing" not in {
            t["function"]["name"] for t in orchestrator._core_tools(coder)
        }
    finally:
        registry.agent_tools.clear()
