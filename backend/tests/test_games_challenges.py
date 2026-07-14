"""The challenge track: scenario grading, best-score persistence, and the hub's
anti-cheat exchange (solutions never leave the server)."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_engine.connect_four import COLS, ROWS, ConnectFour
from backend.games_server import challenges, models, store
from backend.games_server.hub import GameHub

# The known-correct answer for every bundled tic-tac-toe challenge (test-only).
PERFECT = {c.id: c.solution[0] for c in challenges._TTT_CHALLENGES}
C4_PERFECT = {c.id: c.solution[0] for c in challenges._C4_CHALLENGES}


def test_scenarios_never_include_solutions() -> None:
    for sc in challenges.scenarios_for("tictactoe"):
        assert "solution" not in sc
        assert {"id", "category", "observation", "legal_actions"} <= set(sc)


def test_grade_perfect_and_empty() -> None:
    perfect = challenges.grade("tictactoe", PERFECT)
    assert perfect["correct"] == perfect["total"] == len(challenges._TTT_CHALLENGES)
    assert perfect["covered"] == perfect["category_count"]
    assert perfect["score"] == 1.0

    empty = challenges.grade("tictactoe", {})
    assert empty["correct"] == 0 and empty["covered"] == 0
    # Categories are still reported (with 0 passed) so a report shows coverage gaps.
    assert set(empty["categories"]) == {"win", "block", "center"}


def test_grade_partial_counts_per_category() -> None:
    # Only the two 'win' scenarios answered correctly.
    answers = {"ttt-win-row": "2", "ttt-win-diag": "8"}
    report = challenges.grade("tictactoe", answers)
    assert report["correct"] == 2
    assert report["categories"]["win"] == {"passed": 2, "total": 2}
    assert report["categories"]["block"]["passed"] == 0
    assert report["covered"] == 1


def test_record_challenge_keeps_best() -> None:
    good = challenges.grade("tictactoe", PERFECT)
    weak = challenges.grade("tictactoe", {"ttt-win-row": "2"})
    assert store.record_challenge("alice", "tictactoe", good) is True
    # A worse later attempt does not overwrite the best.
    assert store.record_challenge("alice", "tictactoe", weak) is False
    board = store.challenge_leaderboard("tictactoe")
    assert board[0]["account_id"] == "alice"
    assert board[0]["correct"] == good["correct"]


# ---- connect four challenge set --------------------------------------------


def _c4_from_obs(obs: dict[str, Any]) -> ConnectFour:
    """Rebuild the engine state a challenge was generated from (perfect info, so the
    observation carries the whole board and whose turn it is)."""
    seat = {"R": 0, "Y": 1}
    st = ConnectFour()
    board = obs["board"]  # top row first
    for r in range(ROWS):
        line = board[ROWS - 1 - r]
        for c in range(COLS):
            st.grid[r][c] = None if line[c] is None else seat[line[c]]
    st.turn = int(obs["turn"])
    return st


def _drop_wins(st: ConnectFour, seat: int, col: int) -> bool:
    """Would dropping `seat`'s disc in `col` win right now? (Ignores whose turn it is.)"""
    row = st._drop_row(col)
    if row is None:
        return False
    st.grid[row][col] = seat
    won = st._winner() == seat
    st.grid[row][col] = None
    return won


def _winning_cols(st: ConnectFour, seat: int) -> set[str]:
    return {str(c) for c in range(COLS) if _drop_wins(st, seat, c)}


def test_c4_scenarios_never_include_solutions() -> None:
    scenarios = challenges.scenarios_for("connect_four")
    assert scenarios, "connect four should have challenge scenarios"
    for sc in scenarios:
        assert "solution" not in sc
        assert sc["observation"]["game"] == "connect_four"


def test_c4_grade_perfect_covers_every_category() -> None:
    report = challenges.grade("connect_four", C4_PERFECT)
    assert report["correct"] == report["total"] == len(challenges._C4_CHALLENGES)
    assert report["score"] == 1.0
    # More categories than tic-tac-toe (which has win/block/center) — 'double' is new.
    assert set(report["categories"]) == {"win", "block", "center", "double"}
    assert report["covered"] == report["category_count"]


def test_c4_every_solution_is_legal() -> None:
    for ch in challenges._C4_CHALLENGES:
        legal = {a["id"] for a in ch.legal_actions}
        assert set(ch.solution) <= legal, ch.id


def test_c4_solutions_are_semantically_correct() -> None:
    """Replay each scenario through the engine to prove the hand-authored solution
    actually does what its category claims — so a mistyped board can't slip through."""
    for ch in challenges._C4_CHALLENGES:
        st = _c4_from_obs(ch.observation)
        mover = st.turn
        opp = 1 - mover
        sol = ch.solution[0]
        if ch.category == "win":
            # The solution wins immediately, and it's the only move that does.
            assert _winning_cols(st, mover) == set(ch.solution), ch.id
        elif ch.category == "block":
            # The opponent has a live threat, and the solution covers exactly it.
            assert _winning_cols(st, opp) == set(ch.solution), ch.id
            assert _winning_cols(st, mover) == set(), ch.id  # not a 'win' in disguise
        elif ch.category == "center":
            assert sol == "3" and all(
                c is None for row in ch.observation["board"] for c in row
            )
        elif ch.category == "double":
            # After the solution the mover has two+ ways to win — an unstoppable fork.
            row = st._drop_row(int(sol))
            assert row is not None
            st.grid[row][int(sol)] = mover
            assert len(_winning_cols(st, mover)) >= 2, ch.id
        else:  # pragma: no cover - guards against an unhandled new category
            raise AssertionError(f"unclassified category {ch.category!r} in {ch.id}")


# ---- hold'em challenge set ---------------------------------------------------


def test_holdem_scenarios_shape_and_hidden_solutions() -> None:
    scenarios = challenges.scenarios_for("holdem")
    assert scenarios, "hold'em should have challenge scenarios"
    for sc in scenarios:
        assert "solution" not in sc
        obs = sc["observation"]
        assert obs["game"] == "holdem"
        # A hold'em scenario carries the seat's own hole cards but never reveals any.
        assert len(obs["hole"]) == 2
        assert obs["revealed"] == [None, None]


def test_holdem_every_solution_is_legal() -> None:
    for ch in challenges._HD_CHALLENGES:
        legal = {a["id"] for a in ch.legal_actions}
        assert set(ch.solution) <= legal, ch.id


def test_holdem_grade_perfect_covers_every_category() -> None:
    perfect = {c.id: c.solution[0] for c in challenges._HD_CHALLENGES}
    report = challenges.grade("holdem", perfect)
    assert report["correct"] == report["total"] == len(challenges._HD_CHALLENGES)
    assert set(report["categories"]) == {"discipline", "value", "aggression"}
    assert report["covered"] == report["category_count"]


# ---- vizdoom duel challenge set --------------------------------------------


def test_vd_scenarios_shape_and_hidden_solutions() -> None:
    scenarios = challenges.scenarios_for("vizdoom_duel")
    assert scenarios, "vizdoom duel should have challenge scenarios"
    for sc in scenarios:
        assert "solution" not in sc
        obs = sc["observation"]
        assert obs["game"] == "vizdoom_duel"
        # The opaque frame carries no gradable aim — the HUD is what's tested.
        assert set(obs["hud"]) == {"health", "ammo", "score"}


def test_vd_every_solution_is_legal() -> None:
    for ch in challenges._VD_CHALLENGES:
        legal = {a["id"] for a in ch.legal_actions}
        assert set(ch.solution) <= legal, ch.id


def test_vd_grade_perfect_covers_every_category() -> None:
    perfect = {c.id: c.solution[0] for c in challenges._VD_CHALLENGES}
    report = challenges.grade("vizdoom_duel", perfect)
    assert report["correct"] == report["total"] == len(challenges._VD_CHALLENGES)
    assert set(report["categories"]) == {"pressure", "conserve"}
    assert report["covered"] == report["category_count"]


def test_vd_solutions_exclude_the_wrong_action() -> None:
    """Each spot's solution is 'everything but the objectively-wrong action', so the
    category is exactly the failure it guards against."""
    for ch in challenges._VD_CHALLENGES:
        legal = {a["id"] for a in ch.legal_actions}
        wrong = legal - set(ch.solution)
        if ch.category == "pressure":
            assert wrong == {"idle"}, ch.id  # ammo in the tank: never freeze
            assert ch.observation["hud"]["ammo"] > 0, ch.id
        elif ch.category == "conserve":
            assert wrong == {"attack"}, ch.id  # empty gun: never fire
            assert ch.observation["hud"]["ammo"] == 0, ch.id
        else:  # pragma: no cover - guards against an unhandled new category
            raise AssertionError(f"unclassified category {ch.category!r} in {ch.id}")


def test_vd_brawler_template_passes_the_track() -> None:
    """The bundled `vizdoom-brawler` loadout template should solve its own onboarding
    track — ship it, pass, then improve."""
    from backend.modules.games.templates import _VIZDOOM_DUEL_BOT

    ns: dict[str, Any] = {}
    exec(_VIZDOOM_DUEL_BOT, ns)
    run = ns["run"]
    answers = {ch.id: run({}, ch.observation) for ch in challenges._VD_CHALLENGES}
    report = challenges.grade("vizdoom_duel", answers)
    assert report["correct"] == report["total"], answers


# ---- hub exchange ----------------------------------------------------------


class FakeConn:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)

    def last(self, mtype: str) -> dict[str, Any] | None:
        for m in reversed(self.messages):
            if m.get("type") == mtype:
                return m
        return None


async def _run_challenge_exchange() -> dict[str, Any]:
    hub = GameHub()
    conn = FakeConn()
    session = hub.connect(conn)
    await hub.handle(session, {"type": models.AUTH, "token": "alice"})

    await hub.handle(session, {"type": models.CHALLENGE_START, "game_id": "tictactoe"})
    scenarios_msg = conn.last(models.CHALLENGE_SCENARIOS)
    assert scenarios_msg is not None
    # The node sees positions but never the answers.
    assert all("solution" not in s for s in scenarios_msg["scenarios"])

    # Answer perfectly (a real node would run its harness here).
    await hub.handle(
        session,
        {
            "type": models.CHALLENGE_ANSWERS,
            "run_id": scenarios_msg["run_id"],
            "game_id": "tictactoe",
            "answers": PERFECT,
        },
    )
    return conn.last(models.CHALLENGE_REPORT)


def test_hub_grades_and_records_a_run() -> None:
    report = asyncio.run(_run_challenge_exchange())
    assert report is not None
    assert report["correct"] == report["total"]
    assert report["best"] is True
    # The run was persisted to the challenge leaderboard.
    assert store.challenge_leaderboard("tictactoe")[0]["account_id"] == "alice"
