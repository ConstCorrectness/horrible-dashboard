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
    """The coded harness is one policy body, so every case here is about that body:
    what it returns and how failure degrades. There is no tool list to pick from —
    see `test_a_helper_tool_can_no_longer_be_run_as_the_policy` in test_games_env."""
    from backend.modules.games.loadout import CodedHarness
    from backend.modules.games.policy import BotPolicy

    NL = chr(10)

    def policy_for(code: str, steps: list[dict]):
        return BotPolicy(
            trace=steps.append,
            load_harness=lambda g: CodedHarness(game_id=g, bot_code=code),
        )

    steps: list[dict] = []
    chosen = asyncio.run(
        policy_for("def run(args, obs):" + NL + "    return '4'" + NL, steps).choose(
            {"board": []}, LEGAL, "t"
        )
    )
    assert chosen == "4"
    assert [s["kind"] for s in steps] == ["assistant", "tool_result", "chose"]

    # Every failure mode degrades to a random legal move so a table never hangs, and
    # says why in the trace: a raise, a syntax error, and an illegal answer.
    for code in (
        "def run(args, obs):" + NL + "    raise ValueError('boom')" + NL,
        "def run(args, obs)" + NL + "    return '4'" + NL,  # syntax error
        "def run(args, obs):" + NL + "    return '99'" + NL,  # illegal action
    ):
        steps = []
        chosen = asyncio.run(policy_for(code, steps).choose({"board": []}, LEGAL, "t"))
        assert chosen in IDS
        kinds = [s["kind"] for s in steps]
        assert "fallback" in kinds
        assert "fallback_reason" in kinds

    # legal_actions are injected into the obs a legacy bot sees.
    steps = []
    code = (
        "def run(args, obs):"
        + NL
        + "    assert 'legal_actions' in obs"
        + NL
        + "    return obs['legal_actions'][1]['id']"
        + NL
    )
    chosen = asyncio.run(policy_for(code, steps).choose({"board": []}, LEGAL, "t"))
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
    from backend.modules.games.loadout import LlmHarness, ToolDef

    loadout = LlmHarness(
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
        chat_fn=chat, load_harness=lambda _g: loadout, trace=steps.append
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

    store = {"games.policy.tictactoe": "bot"}
    monkeypatch.setattr(
        client_mod, "get_value", lambda key, default=None: store.get(key, default)
    )
    # An override the game allows wins over its own `agent` default.
    assert client_mod._resolve_policy_name("tictactoe") == "bot"
    # A different game with no override still resolves to its own default.
    assert client_mod._resolve_policy_name("rag_race") == "agent"


def test_resolve_policy_name_refuses_a_cross_category_override(monkeypatch) -> None:
    """The category is binding: an override outside the game's `allowed_policies`
    is ignored and its declared default used instead. Without this gate, setting
    `games.policy.vizdoom_duel = agent` really does put a multi-second LLM loop on
    a 1.2s tick — the exact configuration the axis exists to make impossible."""
    from backend.modules.games import client as client_mod

    store = {
        "games.policy.vizdoom_duel": "agent",  # LLM on a real-time seat
        "games.policy.rag_race": "bot",  # codeless bot on a language task
    }
    monkeypatch.setattr(
        client_mod, "get_value", lambda key, default=None: store.get(key, default)
    )
    client_mod._refused_overrides.clear()
    assert client_mod._resolve_policy_name("vizdoom_duel") == "bot"
    assert client_mod._resolve_policy_name("rag_race") == "agent"

    # The hatch is real: a turn-based coded game that declares `agent` keeps it.
    store["games.policy.tictactoe"] = "agent"
    assert client_mod._resolve_policy_name("tictactoe") == "agent"


def test_register_game_rejects_impossible_seats() -> None:
    """A category violation fails at import with a name and a reason, rather than
    being discovered mid-match."""
    import pytest

    from backend.games_engine.base import GameSpec, register_game

    def _factory(**_kw):  # never called — registration raises first
        raise AssertionError("factory should not run")

    def spec(**over):
        base = dict(
            id="_test_bad",
            name="Bad",
            min_players=2,
            max_players=2,
            factory=_factory,
        )
        return GameSpec(**{**base, **over})

    # An LLM on a real-time seat: refused, with no hatch at any pacing.
    with pytest.raises(ValueError, match="may not offer"):
        register_game(
            spec(
                pacing="realtime",
                declared_policies=("agent", "bot"),
                default_policy="bot",
            )
        )
    # A codeless bot on a language task: refused.
    with pytest.raises(ValueError, match="may not offer"):
        register_game(
            spec(
                decision_class="reasoner",
                declared_policies=("agent", "bot"),
                default_policy="agent",
            )
        )
    # A default nothing is allowed to select.
    with pytest.raises(ValueError, match="default_policy"):
        register_game(spec(default_policy="agent"))
    # A seat no one could occupy.
    with pytest.raises(ValueError, match="empty"):
        register_game(spec(declared_policies=(), default_policy="bot"))


def test_every_game_stays_inside_its_category() -> None:
    """The catalog invariant, checked over the real registry: nothing offers a
    policy its category+pacing doesn't permit, and every default is selectable."""
    from backend.games_engine.base import list_games, permitted_policies

    for spec in list_games():
        permitted = permitted_policies(spec.decision_class, spec.pacing)
        assert set(spec.allowed_policies) <= set(permitted), spec.id
        assert spec.default_policy in spec.allowed_policies, spec.id
        # Real-time games can never run the model loop, hatch or not.
        if spec.pacing == "realtime":
            assert "agent" not in spec.allowed_policies, spec.id


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
    assert vd.allowed_policies == ["bot", "random", "manual"]
    rr = by_id["rag_race"]
    assert rr.decision_class == "reasoner"
    assert rr.default_policy == "agent"
    assert rr.allowed_policies == ["agent", "manual"]
    # The escape hatch reaches the frontend, so the seat picker can offer it.
    assert "agent" in by_id["tictactoe"].allowed_policies
