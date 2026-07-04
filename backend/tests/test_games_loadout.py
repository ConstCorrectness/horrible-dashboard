"""Tests for the agent harness: the tool runtime, the store, and the AgentPolicy
tool-calling loop (driven by a fake provider so no LLM is needed)."""

from __future__ import annotations

import asyncio
import random

from backend.modules.games.loadout import (
    HarnessRuntime,
    Loadout,
    ToolDef,
    get_loadout,
    save_loadout,
)
from backend.modules.games.policy import AgentPolicy, RandomPolicy


def _tool(name: str, code: str) -> ToolDef:
    return ToolDef(name=name, description="", code=code)


# ---- runtime ---------------------------------------------------------------


def test_runtime_compiles_and_runs_a_tool() -> None:
    rt = HarnessRuntime(
        Loadout(
            "g",
            tools=[
                _tool(
                    "empties",
                    "def run(args, obs):\n    return obs['board'].count(None)",
                )
            ],
        )
    )
    assert rt.has("empties")
    assert asyncio.run(rt.call("empties", {}, {"board": [None, None, "X"]})) == 2


def test_runtime_bad_code_is_absent_and_error_captured() -> None:
    rt = HarnessRuntime(Loadout("g", tools=[_tool("bad", "def nope():\n    pass")]))
    assert not rt.has("bad")
    assert rt.compile_error("bad")
    # A broken tool is never advertised to the model.
    assert rt.provider_tools() == []


def test_runtime_tool_exception_becomes_error_result() -> None:
    rt = HarnessRuntime(
        Loadout(
            "g", tools=[_tool("boom", "def run(args, obs):\n    raise ValueError('x')")]
        )
    )
    res = asyncio.run(rt.call("boom", {}, {}))
    assert "error" in res and "ValueError" in res["error"]


# ---- store -----------------------------------------------------------------


def test_loadout_store_roundtrip_and_default_fallback() -> None:
    save_loadout(
        Loadout(
            "tictactoe",
            context="ctx",
            tools=[_tool("a", "def run(args, obs):\n    return 1")],
        )
    )
    got = get_loadout("tictactoe")
    assert got.context == "ctx"
    assert got.tools[0].name == "a"
    # A `default` loadout applies to any game without its own.
    save_loadout(Loadout("default", context="dflt"))
    assert get_loadout("some-other-game").context == "dflt"


# ---- agent loop (fake provider) --------------------------------------------


class _Call:
    def __init__(self, name: str, arguments: dict, cid: str = "c1") -> None:
        self.name = name
        self.arguments = arguments
        self.id = cid


class _Result:
    def __init__(self, tool_calls=None, content: str = "") -> None:
        self.tool_calls = tool_calls or []
        self.content = content
        self.assistant_message = {"role": "assistant", "content": content}


LEGAL = [{"id": "4", "label": "center"}, {"id": "0", "label": "corner"}]


def test_agent_runs_a_custom_tool_then_commits_a_move() -> None:
    loadout = Loadout(
        "tictactoe",
        context="play well",
        tools=[_tool("best_cell", "def run(args, obs):\n    return {'cell': 4}")],
    )
    scripted = [
        _Result(tool_calls=[_Call("best_cell", {})]),  # first: analyze
        _Result(
            tool_calls=[_Call("game.chooseAction", {"action_id": "4"})]
        ),  # then: commit
    ]
    seen: list[list[dict]] = []

    async def chat(messages, tools):
        seen.append(list(messages))
        return scripted.pop(0)

    policy = AgentPolicy(chat_fn=chat, load_loadout=lambda _gid: loadout)
    chosen = asyncio.run(
        policy.choose({"game": "tictactoe", "board": [None] * 9}, LEGAL, "tictactoe")
    )
    assert chosen == "4"
    # The custom tool's result must have been fed back before the commit round.
    second_round = seen[1]
    assert any(
        m.get("role") == "tool" and "cell" in m.get("content", "") for m in second_round
    )


def test_agent_illegal_choice_falls_back_to_random() -> None:
    async def chat(messages, tools):
        # Model keeps trying an illegal action; loop exhausts, fallback kicks in.
        return _Result(tool_calls=[_Call("game.chooseAction", {"action_id": "99"})])

    policy = AgentPolicy(
        chat_fn=chat,
        load_loadout=lambda _gid: Loadout("g"),
        fallback=RandomPolicy(random.Random(0)),
    )
    chosen = asyncio.run(policy.choose({}, LEGAL, "g"))
    assert chosen in {"4", "0"}
