"""Starter-harness templates: every registered game ships one, it seeds the default
active loadout, and each template's tools compile + run on a realistic observation."""

from __future__ import annotations

import asyncio

from backend.games_engine import list_games
from backend.modules.games.loadout import (
    STARTER_VERSION,
    HarnessRuntime,
    LlmHarness,
    active_version_id,
    get_llm_harness,
    list_versions,
    save_version,
)
from backend.modules.games.templates import loadout_templates

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


def test_every_starter_is_for_exactly_one_harness() -> None:
    """A template is either a coded policy or an LLM harness — never both, and the
    kind it claims is the kind `default_harness_for` will hand back."""
    from backend.modules.games.loadout import CODED, LLM
    from backend.modules.games.templates import default_harness_for, template_kind

    for t in loadout_templates():
        kind = template_kind(t)
        assert kind in (CODED, LLM), t["id"]
        body = default_harness_for(t["game_id"], kind)
        assert body is not None, t["id"]
        if kind == CODED:
            # A coded starter arrives as bot_code, not as a tool list.
            assert body["bot_code"].strip(), t["id"]
            assert "tools" not in body, t["id"]
        else:
            assert body["game_id"] == t["game_id"], t["id"]
        # The other side genuinely has nothing of this template's.
        assert (
            default_harness_for(t["game_id"], LLM if kind == CODED else CODED) != body
        )


def test_seeding_a_fresh_game_gives_the_right_harness() -> None:
    """No saved harness (isolated temp data dir) → the shipped starter is active, so
    a brand-new player already has a working one of whichever kind their seat runs."""
    from backend.modules.games.loadout import (
        DEFAULT_BOT_CODE,
        get_coded_harness,
    )
    from backend.modules.games.templates import default_harness_for

    for g in list_games():
        if default_harness_for(g.id, "llm") is not None:
            lo = get_llm_harness(g.id)
            assert lo.context or lo.tools, f"{g.id} seeded an empty LLM harness"
        if default_harness_for(g.id, "coded") is not None:
            coded = get_coded_harness(g.id)
            assert coded.bot_code.strip(), f"{g.id} seeded an empty bot"
            assert coded.bot_code != DEFAULT_BOT_CODE, f"{g.id} ignored its starter"
        # Either way a coded seat always has something to run.
        assert get_coded_harness(g.id).bot_code.strip(), g.id


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
    save_version("tictactoe", LlmHarness(game_id="tictactoe", context="mine"), "v1")
    assert active_version_id("tictactoe") == "v1"
    assert [v["id"] for v in list_versions("tictactoe")] == ["v1"]


def test_every_template_compiles_and_runs() -> None:
    """Each starter is exercised through the runtime that will actually run it: a
    coded one through `compile_bot` (the policy path), an LLM one tool-by-tool
    through `HarnessRuntime`. Running a coded starter through the tool runtime would
    pass while proving nothing about the seat that plays it."""
    from backend.modules.games.bot_sdk import build_info, compile_bot
    from backend.modules.games.loadout import CODED
    from backend.modules.games.templates import default_harness_for, template_kind

    for t in loadout_templates():
        game_id = t["game_id"]
        obs = _OBS[game_id]
        if template_kind(t) == CODED:
            body = default_harness_for(game_id, CODED) or {}
            bot = compile_bot(body["bot_code"], f"<starter:{t['id']}>")
            legal = list(obs.get("legal_actions") or [])
            action = bot.act(obs, build_info(obs, legal, game_id, 0))
            assert action in [a["id"] for a in legal], (t["id"], action)
            continue
        runtime = HarnessRuntime(LlmHarness.from_wire(game_id, t["loadout"]))
        for tool in t["loadout"]["tools"]:
            res = asyncio.run(
                runtime.call(tool["name"], _ARGS.get(tool["name"], {}), obs)
            )
            # A helper returns a dict; only a dict carrying "error" means it raised.
            assert not (isinstance(res, dict) and "error" in res), (
                t["id"],
                tool["name"],
                res,
            )
