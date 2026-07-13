"""Tests for the node's move policies: random always picks legal, and the agent
policy degrades to random when no model is configured (so a table never hangs)."""

from __future__ import annotations

import asyncio
import random

from backend.modules.games.policy import AgentPolicy, RandomPolicy, make_policy

LEGAL = [
    {"id": "0", "label": "a"},
    {"id": "4", "label": "b"},
    {"id": "8", "label": "c"},
]
IDS = {a["id"] for a in LEGAL}


def test_random_policy_picks_a_legal_action() -> None:
    policy = RandomPolicy(random.Random(0))
    for _ in range(20):
        chosen = asyncio.run(policy.choose({}, LEGAL))
        assert chosen in IDS


def test_make_policy_selects_type() -> None:
    from backend.modules.games.policy import BotPolicy
    assert isinstance(make_policy("random"), RandomPolicy)
    assert isinstance(make_policy("agent"), AgentPolicy)
    assert isinstance(make_policy("bot"), BotPolicy)
    # Unknown names fall back to random rather than erroring.
    assert isinstance(make_policy("bogus"), RandomPolicy)


def test_bot_policy_executes_script() -> None:
    from backend.modules.games.loadout import Loadout, ToolDef
    from backend.modules.games.policy import BotPolicy

    # Test running a tool named "bot"
    loadout_with_bot = Loadout(
        game_id="t",
        tools=[
            ToolDef(
                name="bot",
                description="",
                code="def run(args, obs):\n    return '4'\n",
            )
        ],
    )
    steps: list[dict] = []
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_with_bot)
    
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen == "4"
    kinds = [s["kind"] for s in steps]
    assert kinds == ["assistant", "tool_result", "chose"]

    # Test running with first tool (no tool named "bot")
    loadout_first_tool = Loadout(
        game_id="t",
        tools=[
            ToolDef(
                name="my_strategy",
                description="",
                code="def run(args, obs):\n    return {'action': '8'}\n",
            )
        ],
    )
    steps = []
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_first_tool)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen == "8"

    # Test bot policy robust fallback to RandomPolicy on runtime error
    loadout_error = Loadout(
        game_id="t",
        tools=[
            ToolDef(
                name="bot",
                description="",
                code="def run(args, obs):\n    raise ValueError('runtime fail')\n",
            )
        ],
    )
    steps = []
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_error)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen in IDS
    kinds = [s["kind"] for s in steps]
    assert "fallback_reason" in kinds
    assert "fallback" in kinds

    # Test bot policy robust fallback to RandomPolicy on compilation error
    loadout_compile_error = Loadout(
        game_id="t",
        tools=[
            ToolDef(
                name="bot",
                description="",
                code="def run(args, obs)\n    return '4'\n",  # syntax error
            )
        ],
    )
    steps = []
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_compile_error)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen in IDS
    kinds = [s["kind"] for s in steps]
    assert "fallback_reason" in kinds
    assert "fallback" in kinds

    # Test bot policy robust fallback on returning illegal action
    loadout_illegal = Loadout(
        game_id="t",
        tools=[
            ToolDef(
                name="bot",
                description="",
                code="def run(args, obs):\n    return '99'\n",  # illegal action
            )
        ],
    )
    steps = []
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_illegal)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen in IDS
    kinds = [s["kind"] for s in steps]
    assert "fallback" in kinds

    # Test that legal_actions are injected into obs passed to the bot
    loadout_legal_check = Loadout(
        game_id="t",
        tools=[
            ToolDef(
                name="bot",
                description="",
                code="def run(args, obs):\n    assert 'legal_actions' in obs\n    return obs['legal_actions'][1]['id']\n",
            )
        ],
    )
    steps = []
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_legal_check)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen == "4"





class _Call:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments
        self.id = name


class _Result:
    def __init__(self, content: str = "", tool_calls: list[_Call] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.assistant_message = {"role": "assistant", "content": content}


def test_agent_policy_emits_a_trace_of_its_reasoning() -> None:
    """The trace sink sees every assistant step, tool result, and the commit — the
    feed behind the live thoughts pane and the replay upload."""
    from backend.modules.games.loadout import Loadout, ToolDef

    loadout = Loadout(
        game_id="t",
        context="think",
        tools=[
            ToolDef(
                name="scan",
                description="",
                code="def run(args, obs):\n    return {'seen': True}\n",
            )
        ],
    )
    turns = iter(
        [
            _Result("let me look", [_Call("scan", {})]),
            _Result(
                "taking the corner", [_Call("game.chooseAction", {"action_id": "8"})]
            ),
        ]
    )

    async def chat(messages, tools):
        return next(turns)

    steps: list[dict] = []
    policy = AgentPolicy(
        chat_fn=chat, load_loadout=lambda _g: loadout, trace=steps.append
    )
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen == "8"
    kinds = [s["kind"] for s in steps]
    assert kinds == ["assistant", "tool_result", "assistant", "chose"]
    assert steps[1]["name"] == "scan"
    assert steps[-1]["action_id"] == "8"


def test_agent_policy_traces_the_fallback(monkeypatch) -> None:
    import backend.modules.agent.routes as agent_routes

    monkeypatch.setattr(agent_routes, "_load_config", lambda: None)
    steps: list[dict] = []
    policy = AgentPolicy(fallback=RandomPolicy(random.Random(1)), trace=steps.append)
    chosen = asyncio.run(policy.choose({"x": 1}, LEGAL))
    assert chosen in IDS
    assert steps[-1]["kind"] == "fallback"
    assert steps[-1]["action_id"] == chosen


def test_agent_policy_falls_back_to_random_without_a_model(monkeypatch) -> None:
    # No agent config on disk -> _load_config returns None -> fallback to random.
    import backend.modules.agent.routes as agent_routes

    monkeypatch.setattr(agent_routes, "_load_config", lambda: None)
    policy = AgentPolicy(fallback=RandomPolicy(random.Random(1)))
    chosen = asyncio.run(policy.choose({"x": 1}, LEGAL))
    assert chosen in IDS
