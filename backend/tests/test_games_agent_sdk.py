"""The code-first agent entrypoint (`my_agent(obs, config)`) + the agent SDK.

Covers: the default agent (empty agent_code) still runs the declarative context+tools
loop; a custom `my_agent` overrides it and needs no model unless it calls
`config.decide`; return-value coercion; compile diagnostics; per-game starters compile;
and a broken agent degrades to a legal fallback in a live match.
"""

from __future__ import annotations

import asyncio

from backend.games_engine import list_games
from backend.modules.games.agent_sdk import (
    DEFAULT_AGENT_SOURCE,
    REFLEX_AGENT_SOURCE,
    agent_compile_error,
    coerce_action_id,
    compile_agent,
    starter_agent_source,
)
from backend.modules.games.loadout import LlmHarness
from backend.modules.games.policy import AgentPolicy, RandomPolicy

LEGAL = [
    {"id": "0", "label": "a"},
    {"id": "4", "label": "b"},
    {"id": "8", "label": "c"},
]
IDS = {a["id"] for a in LEGAL}


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


def _commit_chat(action_id: str):
    """A chat_fn that immediately commits `action_id` (the declarative loop)."""

    async def chat(messages, tools):
        return _Result(
            "choosing", [_Call("game.chooseAction", {"action_id": action_id})]
        )

    return chat


# ---- coercion + diagnostics (pure) ----------------------------------------


def test_coerce_action_id_shapes() -> None:
    ids = ["0", "4", "8"]
    assert coerce_action_id("4", ids) == "4"
    assert coerce_action_id(8, ids) == "8"  # int → str
    assert coerce_action_id({"id": "4"}, ids) == "4"  # action dict
    assert coerce_action_id({"action_id": "8"}, ids) == "8"
    assert coerce_action_id({"action": "0"}, ids) == "0"
    assert coerce_action_id("99", ids) is None  # illegal
    assert coerce_action_id(["4"], ids) is None  # ambiguous
    assert coerce_action_id(None, ids) is None


def test_agent_compile_error() -> None:
    assert agent_compile_error("") is None  # empty = default agent, fine
    assert agent_compile_error(REFLEX_AGENT_SOURCE) is None
    err = agent_compile_error("def my_agent(obs, config)\n    return 1\n")  # syntax
    assert err and "SyntaxError" in err
    # Defining the wrong function is caught too.
    assert agent_compile_error("def nope(obs, config):\n    return 1\n") is not None


def test_starter_sources_compile_for_every_game() -> None:
    for g in list_games():
        src = starter_agent_source(g.id)
        assert callable(compile_agent(src)), g.id
    # rag_race gets a retrieval-shaped starter, not the default.
    assert starter_agent_source("rag_race") != DEFAULT_AGENT_SOURCE


# ---- the policy path -------------------------------------------------------


def _run(loadout: LlmHarness, chat_fn=None):
    steps: list[dict] = []
    policy = AgentPolicy(
        fallback=RandomPolicy(),
        chat_fn=chat_fn,
        load_harness=lambda _g: loadout,
        trace=steps.append,
    )
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, loadout.game_id))
    return chosen, steps


def test_default_agent_runs_the_declarative_loop() -> None:
    # Empty agent_code ⇒ unchanged behavior: the model drives context+tools and commits.
    loadout = LlmHarness(game_id="t", context="think")
    chosen, steps = _run(loadout, _commit_chat("8"))
    assert chosen == "8"
    assert steps[-1] == {"kind": "chose", "action_id": "8"}


def test_custom_agent_overrides_without_touching_the_model() -> None:
    # A pure-code agent returns a legal id directly; the model must never be called.
    async def boom(messages, tools):  # would raise if the declarative loop ran
        raise AssertionError("the model should not be used by a pure-code agent")

    loadout = LlmHarness(
        game_id="t",
        agent_code="def my_agent(obs, config):\n    return obs['legal_actions'][1]['id']\n",
    )
    chosen, steps = _run(loadout, boom)
    assert chosen == "4"
    assert steps[-1]["kind"] == "chose"
    assert any(s.get("content", "").startswith("Running my_agent") for s in steps)


def test_agent_can_delegate_to_config_decide() -> None:
    # `return await config.decide(obs)` re-enters the declarative loop.
    loadout = LlmHarness(
        game_id="t",
        context="ctx",
        agent_code="async def my_agent(obs, config):\n    return await config.decide(obs)\n",
    )
    chosen, steps = _run(loadout, _commit_chat("0"))
    assert chosen == "0"
    kinds = [s["kind"] for s in steps]
    assert "chose" in kinds


def test_broken_agent_falls_back_to_a_legal_move() -> None:
    # A syntax error in agent_code degrades to the random fallback (never hangs a table).
    loadout = LlmHarness(
        game_id="t", agent_code="def my_agent(obs, config)\n    return 1\n"
    )
    chosen, steps = _run(loadout, _commit_chat("8"))
    assert chosen in IDS
    assert steps[-1]["kind"] == "fallback"


def test_default_source_is_the_reflex_free_default() -> None:
    # The seeded default source is valid and defines my_agent.
    fn = compile_agent(DEFAULT_AGENT_SOURCE)
    assert callable(fn)


def test_pure_code_agent_runs_without_any_model(monkeypatch) -> None:
    # No loadout model AND no agent module config: the declarative agent can't run, but a
    # code-first agent that never calls config.decide() still plays.
    import backend.modules.agent.routes as agent_routes

    monkeypatch.setattr(agent_routes, "_load_config", lambda: None)
    loadout = LlmHarness(game_id="t", agent_code=REFLEX_AGENT_SOURCE)
    policy = AgentPolicy(fallback=RandomPolicy(), load_harness=lambda _g: loadout)
    chosen = asyncio.run(policy.choose({"board": []}, LEGAL, "t"))
    assert chosen in IDS
