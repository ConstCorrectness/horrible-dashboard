"""The challenge track: scenario grading, best-score persistence, and the hub's
anti-cheat exchange (solutions never leave the server)."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_server import challenges, models, store
from backend.games_server.hub import GameHub

# The known-correct answer for every bundled tic-tac-toe challenge (test-only).
PERFECT = {c.id: c.solution[0] for c in challenges._TTT_CHALLENGES}


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
