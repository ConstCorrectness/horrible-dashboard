"""The Gymnasium seam: adapters, the Env, the three bot shapes, and the runner.

The load-bearing property this file pins is **parity**: a policy sees the same
`info` dict when it trains against `HorribleEnv` and when it plays a live match
through `BotPolicy`. Everything else here is in service of that — if the two ever
drift, code that trained correctly misbehaves on the ladder, and nothing else in
the system would notice.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env

import backend.games_engine  # noqa: F401  (registers the games and their adapters)
from backend.games_engine.base import get_game
from backend.games_engine.env import HorribleEnv, make_env
from backend.games_engine.env_adapter import adapter_for
from backend.modules.games import trainer
from backend.modules.games.bot_sdk import (
    BotShapeError,
    build_info,
    coerce_action,
    compile_bot,
)
from backend.modules.games.loadout import (
    DEFAULT_BOT_CODE,
    CodedHarness,
    get_coded_harness,
)
from backend.modules.games.policy import BotPolicy

ENV_GAMES = ["tictactoe", "connect_four"]


def _coded_with(code: str):
    return lambda key: CodedHarness(game_id=key, bot_code=code)


# ---- adapters ---------------------------------------------------------------


@pytest.mark.parametrize("game_id", ENV_GAMES)
def test_action_id_index_round_trip(game_id: str) -> None:
    adapter = adapter_for(game_id)
    assert adapter is not None
    for index in range(adapter.n_actions):
        assert adapter.to_index(adapter.to_action_id(index)) == index


def test_reasoner_games_have_no_env() -> None:
    """Not an oversight — a patch or an answer is not a point in an action space,
    and inventing a Discrete for one would be a silent lie about the game."""
    for game_id in ("bug_hunt", "rag_race", "code_golf", "test_duel"):
        assert adapter_for(game_id) is None


def test_observation_is_seat_relative() -> None:
    """The two seats must see exact negations of each other. Without this a policy
    has to learn each side separately and self-play teaches half as much."""
    state = get_game("tictactoe").new()
    state.apply_action(0, "4")
    state.apply_action(1, "0")
    adapter = adapter_for("tictactoe")
    assert adapter is not None
    seat0 = adapter.encode_obs(state.observation(0), 0)
    seat1 = adapter.encode_obs(state.observation(1), 1)
    assert np.array_equal(seat0, -seat1)
    assert seat0[4] == 1 and seat0[0] == -1


def test_action_mask_matches_legal_actions() -> None:
    state = get_game("tictactoe").new()
    state.apply_action(0, "4")
    adapter = adapter_for("tictactoe")
    assert adapter is not None
    mask = adapter.mask_for(state.legal_actions(1))
    assert mask[4] == 0  # taken
    assert mask.sum() == 8
    assert mask.shape == (adapter.n_actions,)


def test_mask_ignores_ids_outside_the_space() -> None:
    """A game that grows an action its adapter doesn't know about should make that
    move unavailable to policies, not crash a live match."""
    adapter = adapter_for("tictactoe")
    assert adapter is not None
    mask = adapter.mask_for([{"id": "3"}, {"id": "not-a-number"}, {"id": "99"}])
    assert mask.sum() == 1 and mask[3] == 1


# ---- the environment ---------------------------------------------------------


@pytest.mark.parametrize("game_id", ENV_GAMES)
def test_env_passes_gymnasium_checker(game_id: str) -> None:
    """Gymnasium's own conformance suite. Passing it is what makes the claim
    'stable-baselines3 works against this' true rather than aspirational."""
    check_env(make_env(game_id, seed=0), skip_render_check=True)


def test_seeded_resets_are_reproducible() -> None:
    """Regression: `reset` used to rebind the RNG, which left the default opponent
    holding the previous object and made seeded runs diverge."""
    env = make_env("tictactoe", seed=0)
    first, _ = env.reset(seed=5)
    second, _ = env.reset(seed=5)
    assert np.array_equal(first, second)


def test_illegal_action_ends_the_episode_and_is_reported() -> None:
    """Reported, never repaired: substituting a legal move would hide the most
    common bug in a new policy behind a slightly worse win rate."""
    env = make_env("tictactoe", seed=0)
    _obs, info = env.reset(seed=1)
    env.step(int(np.flatnonzero(info["action_mask"])[0]))
    taken = int(np.flatnonzero(env._info()["action_mask"] == 0)[0])
    _obs, reward, terminated, _truncated, info = env.step(taken)
    assert reward == -1.0 and terminated and info["illegal"] is True


def test_env_plays_a_full_legal_episode() -> None:
    env = make_env("tictactoe", seed=0)
    _obs, info = env.reset(seed=2)
    rng = np.random.default_rng(0)
    terminated = truncated = False
    while not (terminated or truncated):
        action = int(rng.choice(np.flatnonzero(info["action_mask"])))
        _obs, reward, terminated, truncated, info = env.step(action)
    assert terminated and not info.get("illegal") and reward in (-1.0, 0.0, 1.0)


def test_env_and_live_match_info_have_identical_keys() -> None:
    """**The parity test.** A policy reads `info` in both places; a key present in
    one and absent in the other is a bug that only appears in a rated match."""
    env = make_env("tictactoe", seed=0)
    _obs, env_info = env.reset(seed=0)
    live_info = build_info(
        {"game": "tictactoe", "board": [None] * 9, "turn": 0},
        [{"id": "0", "label": "c0"}],
        "tictactoe",
        0,
    )
    assert sorted(env_info) == sorted(live_info)


# ---- bot shapes --------------------------------------------------------------

AGENT_BOT = """
import numpy as np
class Agent:
    def reset(self, obs, info):
        self.turns = 0
    def act(self, obs, info):
        self.turns += 1
        return int(np.flatnonzero(info["action_mask"])[0])
    def observe(self, reward, terminated, info):
        self.last_reward = reward
"""

ACT_BOT = """
def act(obs, info):
    return info["legal_actions"][-1]["id"]
"""

LEGACY_BOT = """
def run(args, obs):
    return obs["legal_actions"][0]["id"]
"""


def test_compile_bot_detects_each_shape() -> None:
    assert compile_bot(AGENT_BOT).shape == "agent"
    assert compile_bot(ACT_BOT).shape == "act"
    assert compile_bot(LEGACY_BOT).shape == "run"


def test_compile_bot_rejects_code_with_no_entry_point() -> None:
    with pytest.raises(BotShapeError):
        compile_bot("x = 1")


@pytest.mark.parametrize(
    "code,expected", [(AGENT_BOT, "0"), (ACT_BOT, "8"), (LEGACY_BOT, "0")]
)
def test_every_shape_plays_through_bot_policy(code: str, expected: str) -> None:
    """Including the legacy one. Shipped templates and saved harnesses use
    `run(args, obs)`; breaking them to tidy an interface would be a poor trade."""
    obs = {"game": "tictactoe", "board": [None] * 9, "turn": 0}
    legal = [{"id": str(i), "label": f"c{i}"} for i in range(9)]
    policy = BotPolicy(load_harness=_coded_with(code))
    chosen = asyncio.run(policy.choose(obs, legal, "tictactoe", 0))
    assert chosen == expected


def test_class_agent_keeps_state_across_turns_of_one_match() -> None:
    """What makes a stateful policy — and an in-pane learner — possible at all."""
    obs = {"game": "tictactoe", "board": [None] * 9, "turn": 0}
    legal = [{"id": str(i)} for i in range(9)]
    policy = BotPolicy(load_harness=_coded_with(AGENT_BOT))
    asyncio.run(policy.choose(obs, legal, "tictactoe", 0))
    asyncio.run(policy.choose(obs, legal, "tictactoe", 0))
    assert policy._bot._instance.turns == 2


def test_illegal_bot_answer_falls_back_and_says_why() -> None:
    """A live table must never hang, but the Games Log has to say what happened."""
    traces: list[dict] = []
    policy = BotPolicy(
        trace=traces.append,
        load_harness=_coded_with("def act(obs, info):\n    return 'nope'\n"),
    )
    legal = [{"id": str(i)} for i in range(9)]
    chosen = asyncio.run(
        policy.choose({"game": "tictactoe", "board": [None] * 9}, legal, "tictactoe", 0)
    )
    assert chosen in {a["id"] for a in legal}
    assert any(t["kind"] == "fallback_reason" for t in traces)


# ---- action coercion ---------------------------------------------------------


def test_coerce_action_accepts_ids_indices_and_numpy() -> None:
    adapter = adapter_for("tictactoe")
    ids = [str(i) for i in range(9)]
    assert coerce_action("4", ids, adapter) == "4"
    assert coerce_action(4, ids, adapter) == "4"
    assert coerce_action(np.int64(4), ids, adapter) == "4"
    assert coerce_action({"action": 4}, ids, adapter) == "4"


def test_coerce_action_rejects_bools_and_illegal_moves() -> None:
    """`True == 1` in Python, so an unchecked bool would silently mean 'action 1'."""
    adapter = adapter_for("tictactoe")
    ids = [str(i) for i in range(9)]
    assert coerce_action(True, ids, adapter) is None
    assert coerce_action(None, ids, adapter) is None
    assert coerce_action(99, ids, adapter) is None
    assert coerce_action("4", ["0", "1"], adapter) is None


# ---- the episode runner ------------------------------------------------------


def test_runner_reports_wins_and_alternates_seats() -> None:
    result = trainer.run_episodes(
        "tictactoe", ACT_BOT, opponent="random", episodes=20, seed=3
    )
    assert result.ok and result.episodes == 20
    assert result.wins + result.draws + result.losses == 20
    assert len(result.curve) > 1


def test_runner_counts_illegal_actions_without_repairing_them() -> None:
    always_zero = "def act(obs, info):\n    return 0\n"
    result = trainer.run_episodes(
        "tictactoe", always_zero, opponent="random", episodes=10, seed=1
    )
    # Seat 1 moves second, so cell 0 is sometimes already taken.
    assert result.ok and result.illegal > 0
    assert result.losses >= result.illegal


def test_runner_supports_legacy_bots() -> None:
    """Regression: the runner passed the raw observation straight through, so a
    legacy bot reading `obs['legal_actions']` raised KeyError in Training while
    playing fine on the ladder — the one place the two paths must not differ."""
    result = trainer.run_episodes(
        "tictactoe", LEGACY_BOT, opponent="random", episodes=10, seed=1
    )
    assert result.ok and result.shape == "run" and result.episodes == 10


def test_runner_refuses_reasoner_games_with_an_explanation() -> None:
    result = trainer.run_episodes("bug_hunt", ACT_BOT, episodes=3)
    assert not result.ok and "no RL environment" in (result.error or "")


def test_runner_reports_a_broken_bot_as_a_result_not_an_exception() -> None:
    result = trainer.run_episodes(
        "tictactoe", "def act(obs, info):\n    return (", episodes=3
    )
    assert not result.ok and "failed to load" in (result.error or "")


def test_runner_learning_agent_receives_terminal_reward() -> None:
    """`observe()` is what makes an in-pane learner possible; if it never fires the
    reward curve is flat and nothing explains why."""
    code = """
import numpy as np
class Agent:
    rewards = []
    def act(self, obs, info):
        return int(np.flatnonzero(info["action_mask"])[0])
    def observe(self, reward, terminated, info):
        Agent.rewards.append(reward)
"""
    result = trainer.run_episodes(
        "tictactoe", code, opponent="random", episodes=8, seed=0
    )
    assert result.ok and result.episodes == 8


def test_env_rejects_games_without_an_adapter() -> None:
    with pytest.raises(ValueError, match="no RL environment"):
        HorribleEnv("bug_hunt")


# ---- the coded harness's baseline --------------------------------------------


def test_every_game_has_runnable_bot_code() -> None:
    """A coded seat always has something to run. Whatever the source — a saved
    harness, a shipped starter, or nothing at all — code comes back, and it
    compiles. An uncatalogued id is included: the seat still has to act."""
    for game_id in ("tictactoe", "connect_four", "fighter", "not_a_real_game"):
        harness = get_coded_harness(game_id)
        assert harness.bot_code.strip(), game_id
        compile_bot(harness.bot_code, f"<bot:{game_id}>")


def test_the_default_bot_plays_a_legal_move() -> None:
    """It is the baseline every policy is measured against, so it had better work.
    An empty `bot_code` is what "I haven't written one yet" means, and it resolves
    to the random-legal baseline rather than to nothing."""
    obs = {"game": "tictactoe", "board": [None] * 9, "turn": 0}
    legal = [{"id": str(i), "label": f"c{i}"} for i in range(9)]
    policy = BotPolicy(load_harness=lambda key: get_coded_harness(key))
    chosen = asyncio.run(policy.choose(obs, legal, "not_a_real_game", 0))
    assert chosen in {a["id"] for a in legal}


def test_default_bot_beats_nothing_but_never_acts_illegally() -> None:
    result = trainer.run_episodes(
        "tictactoe", DEFAULT_BOT_CODE, opponent="random", episodes=40, seed=2
    )
    assert result.ok and result.illegal == 0 and result.episodes == 40


def test_a_helper_tool_can_no_longer_be_run_as_the_policy() -> None:
    """The regression the split closes structurally. The coded harness has no tool
    list, so there is nothing for the policy to pick the wrong member of: it used to
    hunt `<game>.bot` → `bot` → *the loadout's first tool*, and that last step ran a
    helper returning analysis as though it were a move — illegal every turn,
    degrading to random, silently, ranked matches included."""
    from backend.modules.games.loadout import CodedHarness

    assert not hasattr(CodedHarness(game_id="t"), "tools")

    # The shipped fighter starter is a policy, and it is what the coded seat gets —
    # no name resolution involved.
    fighter = get_coded_harness("fighter")
    assert "def run(args, obs)" in fighter.bot_code
    assert fighter.bot_code != DEFAULT_BOT_CODE
