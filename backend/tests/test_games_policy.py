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

    # A tool that is NOT the bot is never run as one. This used to assert the
    # opposite — that the loadout's *first* tool played — which is the bug the
    # default bot tool replaces: a helper returning analysis rather than a move
    # answered illegally every turn and degraded to random, silently, in ranked
    # matches too. Now the default random-legal bot plays instead.
    loadout_helper_only = Loadout(
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
    policy = BotPolicy(trace=steps.append, load_loadout=lambda _g: loadout_helper_only)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen in IDS
    # The default bot answered directly — nothing had to fall back to random.
    assert not any(s["kind"].startswith("fallback") for s in steps)

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
    policy = BotPolicy(
        trace=steps.append, load_loadout=lambda _g: loadout_compile_error
    )
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


def test_primary_seat_refreshes_policy_between_games(monkeypatch) -> None:
    """The primary seat (follow_setting) re-reads games.policy on demand — the hook
    that lets a switch land between games without a reconnect — while a seat that
    doesn't follow the setting keeps whatever it was built with."""
    from backend.modules.games import client as client_mod
    from backend.modules.games.policy import BotPolicy

    setting = {"value": "random"}
    monkeypatch.setattr(
        client_mod, "get_value", lambda key, default=None: setting["value"]
    )

    primary = client_mod._PlayerConn(
        "ws://x", "tok", None, lambda _m: None, follow_setting=True
    )
    primary._refresh_policy_from_setting()
    assert isinstance(primary._policy, RandomPolicy)

    # Switch to agent -> next refresh rebuilds; the applied name is tracked.
    setting["value"] = "agent"
    primary._refresh_policy_from_setting()
    assert isinstance(primary._policy, AgentPolicy)
    assert primary._policy_name == "agent"

    # Unchanged setting => same object (no needless rebuild).
    same = primary._policy
    primary._refresh_policy_from_setting()
    assert primary._policy is same

    # bot and manual are reachable too; manual clears the auto-play policy.
    setting["value"] = "bot"
    primary._refresh_policy_from_setting()
    assert isinstance(primary._policy, BotPolicy)
    setting["value"] = "manual"
    primary._refresh_policy_from_setting()
    assert primary._policy is None

    # A seat that doesn't follow the setting ignores the switch entirely.
    sparring = client_mod._PlayerConn(
        "ws://x", "tok", make_policy("random"), lambda _m: None
    )
    setting["value"] = "agent"
    sparring._refresh_policy_from_setting()
    assert isinstance(sparring._policy, RandomPolicy)


def test_resolve_policy_name_prefers_the_games_declared_default(monkeypatch) -> None:
    """Policy is a property of the game: with no per-game override set, the game's
    declared `default_policy` (from GameSpec) wins over the legacy global setting."""
    from backend.modules.games import client as client_mod

    # get_value returns the caller's default for every key => no overrides anywhere.
    monkeypatch.setattr(client_mod, "get_value", lambda key, default=None: default)
    assert client_mod._resolve_policy_name("vizdoom_duel") == "bot"
    assert client_mod._resolve_policy_name("rag_race") == "agent"
    assert client_mod._resolve_policy_name("tictactoe") == "agent"


def test_resolve_policy_name_per_game_override_beats_default(monkeypatch) -> None:
    from backend.modules.games import client as client_mod

    store = {"games.policy.vizdoom_duel": "agent"}
    monkeypatch.setattr(
        client_mod, "get_value", lambda key, default=None: store.get(key, default)
    )
    # The explicit per-game override wins over the game's `bot` default.
    assert client_mod._resolve_policy_name("vizdoom_duel") == "agent"
    # A different game with no override still resolves to its own default.
    assert client_mod._resolve_policy_name("rag_race") == "agent"


def test_resolve_policy_name_falls_back_to_global_for_uncatalogued(monkeypatch) -> None:
    from backend.modules.games import client as client_mod

    store = {"games.policy": "random"}
    monkeypatch.setattr(
        client_mod, "get_value", lambda key, default=None: store.get(key, default)
    )
    # No per-game override and no catalog default => the legacy global setting.
    assert client_mod._resolve_policy_name("nope_missing") == "random"
    assert client_mod._resolve_policy_name(None) == "random"


def test_refresh_policy_from_setting_resolves_per_game(monkeypatch) -> None:
    """The primary seat rebuilds its policy for the specific game at each match
    boundary, so a VizDoom table gets `bot` and a RAG Race table `agent` without the
    player flipping a global switch."""
    from backend.modules.games import client as client_mod
    from backend.modules.games.policy import AgentPolicy, BotPolicy

    monkeypatch.setattr(client_mod, "get_value", lambda key, default=None: default)
    conn = client_mod._PlayerConn(
        "ws://x", "tok", None, lambda _m: None, follow_setting=True
    )
    conn._refresh_policy_from_setting("vizdoom_duel")
    assert isinstance(conn._policy, BotPolicy)
    conn._refresh_policy_from_setting("rag_race")
    assert isinstance(conn._policy, AgentPolicy)


def test_catalog_carries_decision_metadata() -> None:
    """The /games/status catalog surfaces the decision-class axis to the frontend."""
    from backend.modules.games.routes import _catalog

    by_id = {g.id: g for g in _catalog()}
    vd = by_id["vizdoom_duel"]
    assert vd.decision_class == "policy"
    assert vd.default_policy == "bot"
    assert vd.obs_kind == "frames"
    assert vd.pacing == "realtime"
    rr = by_id["rag_race"]
    assert rr.decision_class == "reasoner"
    assert rr.default_policy == "agent"
