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
    assert isinstance(make_policy("random"), RandomPolicy)
    assert isinstance(make_policy("agent"), AgentPolicy)
    # Unknown names fall back to random rather than erroring.
    assert isinstance(make_policy("bogus"), RandomPolicy)


def test_agent_policy_falls_back_to_random_without_a_model(monkeypatch) -> None:
    # No agent config on disk -> _load_config returns None -> fallback to random.
    import backend.modules.agent.routes as agent_routes

    monkeypatch.setattr(agent_routes, "_load_config", lambda: None)
    policy = AgentPolicy(fallback=RandomPolicy(random.Random(1)))
    chosen = asyncio.run(policy.choose({"x": 1}, LEGAL))
    assert chosen in IDS
