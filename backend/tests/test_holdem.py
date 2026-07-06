"""Heads-up No-Limit Hold'em engine tests: the hand evaluator, the discretized
betting state machine, chance-node dealing, and hub integration.

Follows the repo convention of `asyncio.run` inside synchronous test functions for
the hub test (see test_games_server.py).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from backend.games_engine.base import CHANCE, TERMINAL
from backend.games_engine.holdem import BB, SB, STACK, Holdem, best_rank
from backend.games_server import models
from backend.games_server.hub import GameHub


# ---- hand evaluator ---------------------------------------------------------


def test_evaluator_category_ordering() -> None:
    straight_flush = best_rank(["9s", "8s", "7s", "6s", "5s"])
    quads = best_rank(["Ah", "Ad", "As", "Ac", "Kd"])
    full_house = best_rank(["Kh", "Kd", "Ks", "2c", "2d"])
    flush = best_rank(["Ah", "Jh", "9h", "6h", "2h"])
    straight = best_rank(["9s", "8d", "7h", "6c", "5s"])
    trips = best_rank(["Qh", "Qd", "Qs", "7c", "2d"])
    two_pair = best_rank(["Jh", "Jd", "4s", "4c", "9d"])
    pair = best_rank(["Th", "Td", "8s", "6c", "2d"])
    high = best_rank(["Ah", "Jd", "9s", "6c", "2d"])
    ladder = [
        straight_flush,
        quads,
        full_house,
        flush,
        straight,
        trips,
        two_pair,
        pair,
        high,
    ]
    assert ladder == sorted(ladder, reverse=True)


def test_evaluator_wheel_and_kickers() -> None:
    # The wheel (A-2-3-4-5) is a straight with high card 5, below a 6-high straight.
    wheel = best_rank(["Ah", "2d", "3s", "4c", "5d"])
    six_high = best_rank(["6h", "5d", "4s", "3c", "2d"])
    assert wheel[0] == 4 and six_high > wheel
    # Same two pair, better kicker wins.
    assert best_rank(["Jh", "Jd", "4s", "4c", "Ad"]) > best_rank(
        ["Js", "Jc", "4h", "4d", "9d"]
    )
    # Best-of-7: hole pair + board trips = full house.
    assert best_rank(["Ah", "Ad", "Ks", "Kc", "Kd", "3s", "2h"])[0] == 6


# ---- betting state machine ----------------------------------------------------


def _deal(state: Holdem, seed: int = 7) -> random.Random:
    rng = random.Random(seed)
    assert state.current_player() == CHANCE
    state.resolve_chance(rng)
    return rng


def _run_out(state: Holdem, rng: random.Random) -> None:
    """Resolve every pending chance node until a player acts or the hand ends."""
    while state.current_player() == CHANCE:
        state.resolve_chance(rng)


def test_blinds_and_fold_preflop() -> None:
    st = Holdem()
    assert st.committed == [SB, BB]
    _deal(st)
    assert st.current_player() == 0  # button acts first preflop
    ids = [a.id for a in st.legal_actions(0)]
    assert ids[:2] == ["fold", "call"]
    st.apply_action(0, "fold")
    assert st.current_player() == TERMINAL
    assert st.returns() == {0: -1.0, 1: 1.0}  # the folder forfeits the small blind


def test_limp_gives_big_blind_an_option() -> None:
    st = Holdem()
    _deal(st)
    st.apply_action(0, "call")  # limp: bets equal, but the BB has not acted
    assert st.current_player() == 1
    assert {a.id for a in st.legal_actions(1)} >= {"check", "raise_min"}
    st.apply_action(1, "check")
    assert st.pending_deal == "flop"


def test_check_down_to_showdown_conserves_chips() -> None:
    st = Holdem()
    rng = _deal(st)
    st.apply_action(0, "call")
    st.apply_action(1, "check")
    for _ in range(3):  # flop, turn, river: BB checks first postflop
        _run_out(st, rng)
        st.apply_action(1, "check")
        st.apply_action(0, "check")
    assert st.current_player() == TERMINAL
    assert len(st.board) == 5
    returns = st.returns()
    assert sum(returns.values()) == 0.0
    assert {abs(v) for v in returns.values()} <= {0.0, 2.0}  # pot was 2 BB
    # Showdown reveals both hands and names them.
    pub = st.public_state()
    assert all(pub["revealed"]) and all(pub["hand_names"])


def test_min_raise_ladder() -> None:
    st = Holdem()
    _deal(st)
    targets = dict(st._raise_targets(0))
    assert targets["raise_min"] == BB + BB  # raise to 4 over the big blind
    st.apply_action(0, "raise_min")
    assert st.bets[0] == 4 and st.stacks[0] == STACK - 4
    # BB's min re-raise is 4 + the last raise size (2) = 6.
    assert dict(st._raise_targets(1))["raise_min"] == 6


def test_all_in_call_runs_out_the_board() -> None:
    st = Holdem()
    rng = _deal(st)
    st.apply_action(0, "all_in")
    assert st.bets[0] == STACK and st.stacks[0] == 0
    st.apply_action(1, "call")
    _run_out(st, rng)  # no more betting: flop, turn, river chain through chance
    assert st.current_player() == TERMINAL
    assert len(st.board) == 5
    returns = st.returns()
    assert sum(returns.values()) == 0.0
    assert {abs(v) for v in returns.values()} <= {0.0, float(STACK)}


def test_pot_raise_and_fold_transfers_committed() -> None:
    st = Holdem()
    _deal(st)
    st.apply_action(0, "raise_pot")  # pot raise over the blinds: to 2 + 3 + 1 = 6
    assert st.bets[0] == 6
    st.apply_action(1, "fold")
    assert st.returns() == {0: 2.0, 1: -2.0}  # BB forfeits the big blind


def test_observation_hides_opponent_cards() -> None:
    st = Holdem()
    _deal(st)
    assert st.hole is not None
    obs = st.observation(0)
    assert obs["hole"] == st.hole[0]
    assert "revealed" in obs and obs["revealed"] == [None, None]
    assert st.public_state()["revealed"] == [None, None]


# ---- hub integration -----------------------------------------------------------


class FakeConn:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)

    def last(self, mtype: str) -> dict[str, Any] | None:
        for msg in reversed(self.messages):
            if msg.get("type") == mtype:
                return msg
        return None


def test_holdem_table_starts_and_prompts_the_button() -> None:
    async def go() -> None:
        hub = GameHub(rng=random.Random(42), move_timeout_s=0)
        a_conn, b_conn = FakeConn(), FakeConn()
        a, b = hub.connect(a_conn), hub.connect(b_conn)
        await hub.handle(a, {"type": models.AUTH, "token": "alice"})
        await hub.handle(b, {"type": models.AUTH, "token": "bob"})
        await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "holdem"})
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        # The referee resolved the deal (a chance node) and prompted seat 0.
        turn = a_conn.last(models.YOUR_TURN)
        assert turn is not None and turn["seat"] == 0
        assert len(turn["observation"]["hole"]) == 2
        ids = [act["id"] for act in turn["legal_actions"]]
        assert "fold" in ids and "call" in ids and "all_in" in ids
        # The spectator state hides both hands.
        pub = a_conn.last(models.PUBLIC_STATE)["state"]
        assert pub["game"] == "holdem" and pub["revealed"] == [None, None]

    asyncio.run(go())
