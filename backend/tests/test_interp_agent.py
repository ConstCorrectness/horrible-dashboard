"""The interpretability guide: the `interp` roster agent, its tool group, and the
guide that is its primer.

The tools themselves are declared in the frontend (`interp/agent-tools.ts`) and
pushed in the manifest, so the drift worth guarding is between the two halves:
a tool the guide never mentions is one the model is never told how to use, and a
guide naming a tool that no longer exists teaches it to call nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.modules.agent import orchestrator, roster
from backend.modules.agent.guides import module_guide

TOOLS_TS = (
    Path(__file__).resolve().parents[2]
    / "packages/core/src/modules/interp/agent-tools.ts"
)


def declared_tools() -> set[str]:
    source = TOOLS_TS.read_text(encoding="utf-8")
    return set(re.findall(r"name: '(interp\.[A-Za-z]+)'", source))


class FakeConn:
    def __init__(self, agent_tools: list[dict[str, Any]]) -> None:
        self.pending: dict[str, Any] = {}
        self.pending_approvals: dict[str, Any] = {}
        self.agent_tools = agent_tools
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def test_the_interp_agent_is_on_the_roster() -> None:
    spec = roster.get_agent("interp")
    assert spec is not None
    assert spec.tool_groups is not None and "interp" in spec.tool_groups
    # A preloaded group outside the allowed set would be loaded and then refused.
    assert set(spec.preload_groups or []) <= set(spec.tool_groups)
    assert not spec.can_delegate
    assert "interp.state" in spec.system_prompt


def test_the_group_has_a_blurb_and_a_guide() -> None:
    assert orchestrator._group_description("interp") != "interp tools"
    guide = module_guide("interp")
    assert guide is not None and "## Glossary" in guide


def test_guide_and_frontend_declare_the_same_tools() -> None:
    tools = declared_tools()
    assert tools == {
        "interp.state",
        "interp.attention",
        "interp.heads",
        "interp.features",
        "interp.graph",
        "interp.lenses",
        "interp.library",
        "interp.steer",
        "interp.axis",
        "interp.goto",
    }
    guide = module_guide("interp") or ""
    named = set(re.findall(r"`(interp\.[A-Za-z]+)", guide))
    assert named == tools, f"guide names {named}, frontend declares {tools}"


def test_the_agent_sees_its_tools_and_no_one_elses() -> None:
    conn = FakeConn(
        [
            {"name": "interp.state", "description": "", "params": None},
            {"name": "interp.goto", "description": "", "params": None},
            {"name": "editor.proposeEdit", "description": "", "params": None},
        ]
    )
    spec = roster.get_agent("interp")
    names = {
        t["function"]["name"]
        for t in orchestrator._select_tools(conn, {"interp", "editor"}, spec)
    }
    assert {"interp.state", "interp.goto"} <= names
    assert "editor.proposeEdit" not in names
