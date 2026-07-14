"""Starter-harness templates: every registered game ships one, it seeds the default
active loadout, and each template's tools compile + run on a realistic observation."""

from __future__ import annotations

import asyncio

from backend.games_engine import list_games
from backend.modules.games.loadout import (
    STARTER_VERSION,
    HarnessRuntime,
    Loadout,
    active_version_id,
    get_loadout,
    list_versions,
    save_version,
)
from backend.modules.games.templates import default_loadout_for, loadout_templates

# A minimal but realistic observation per game, carrying the fields each starter
# tool reads (keeps the test fast + hermetic — no engine instantiation, which for
# vizdoom would spin native processes).
_OBS: dict[str, dict] = {
    "tictactoe": {"board": [None] * 9},
    "connect_four": {"board": [[None] * 7 for _ in range(6)]},
    "holdem": {"hole": ["As", "Kd"], "board": [], "pot": 6, "to_call": 4},
    "rag_race": {"docs": [{"text": "The sky is blue."}]},
    "fighter": {
        "seat": 0,
        "p": [
            {"x": 0, "y": 0, "hp": 100, "meter": 0},
            {"x": 50, "y": 0, "hp": 100, "meter": 0},
        ],
        "legal_actions": [{"id": "idle"}, {"id": "right"}, {"id": "light"}],
    },
    "vizdoom_toy": {
        "tick": 0,
        "hud": {"ammo": 5},
        "legal_actions": [{"id": "idle"}, {"id": "attack"}, {"id": "turn_right"}],
    },
    "vizdoom_duel": {
        "tick": 0,
        "hud": {"ammo": 5},
        "legal_actions": [
            {"id": "idle"},
            {"id": "attack"},
            {"id": "move_left"},
            {"id": "move_right"},
            {"id": "turn_right"},
            {"id": "move_forward"},
        ],
    },
    "arena": {
        "seat": 0,
        "round": 2,
        "rounds": 3,
        "round_wins": [1, 2],
        "last_round": None,
        "starter_bot": "",
    },
    "bug_hunt": {
        "attempts": [{"green": False, "passed": 2, "failed": ["t_edge"]}],
        "attempts_left": 2,
    },
    "code_golf": {"signature": "def f(x):", "public_examples": [{"in": 1, "out": 1}]},
    "test_duel": {"signature": "def add(a, b):", "phase": "impl"},
    "tabular_fe": {"data_samples": [{"a": 1, "b": 2.0, "c": "x"}], "metric": "rmse"},
}

# Tools that take model-supplied args (everything else reads only the observation).
_ARGS: dict[str, dict] = {
    "byte_count": {"code": "lambda x:x"},
    "pot_odds": {"to_call": 4},
    "find_answer": {"question": "what color is the sky"},
}


def test_every_registered_game_has_a_starter_template() -> None:
    covered = {t["game_id"] for t in loadout_templates()}
    missing = sorted(g.id for g in list_games() if g.id not in covered)
    assert missing == [], f"games without a starter harness: {missing}"


def test_default_loadout_for_every_game() -> None:
    for g in list_games():
        body = default_loadout_for(g.id)
        assert body is not None, g.id
        assert body["game_id"] == g.id


def test_get_loadout_seeds_the_starter_for_a_fresh_game() -> None:
    # No saved loadout (isolated temp data dir) → the shipped starter is active,
    # so a brand-new player's agent already has a working harness.
    for g in list_games():
        lo = get_loadout(g.id)
        assert lo.context or lo.tools, f"{g.id} seeded empty"


def test_seeded_default_attributes_to_starter_version() -> None:
    # A fresh game running its shipped starter attributes to the synthetic "starter"
    # version (not a blank), and it's the one active entry in the version list.
    assert active_version_id("tictactoe") == STARTER_VERSION
    versions = list_versions("tictactoe")
    assert [v["id"] for v in versions] == [STARTER_VERSION]
    assert versions[0]["active"] is True
    # An unknown game (no starter) stays unattributed.
    assert active_version_id("no-such-game") is None
    assert list_versions("no-such-game") == []


def test_first_real_save_replaces_the_starter() -> None:
    save_version("tictactoe", Loadout(game_id="tictactoe", context="mine"), "v1")
    assert active_version_id("tictactoe") == "v1"
    assert [v["id"] for v in list_versions("tictactoe")] == ["v1"]


def test_every_template_tool_compiles_and_runs() -> None:
    for t in loadout_templates():
        obs = _OBS[t["game_id"]]
        runtime = HarnessRuntime(Loadout.from_wire(t["game_id"], t["loadout"]))
        for tool in t["loadout"]["tools"]:
            res = asyncio.run(
                runtime.call(tool["name"], _ARGS.get(tool["name"], {}), obs)
            )
            # A tool returns a dict (helpers) or a string (per-tick .bot actions);
            # only a dict carrying "error" means it raised.
            assert not (isinstance(res, dict) and "error" in res), (
                t["id"],
                tool["name"],
                res,
            )
