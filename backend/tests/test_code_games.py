"""Code Golf + Test Duel engines: phases, WORK grading (fake runner), scoring,
and one integration pass through the referee's WORK loop."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.games_engine import verify
from backend.games_engine.base import TERMINAL, WORK
from backend.games_engine.code_golf import DEFAULT_TASKS as GOLF_TASKS
from backend.games_engine.code_golf import CodeGolf
from backend.games_engine.test_duel import DEFAULT_TASKS as DUEL_TASKS
from backend.games_engine.test_duel import TestDuel as DuelGame


def _fake_runner(green_when):
    """A verify.run_python_job stand-in: `green_when(files)` decides the verdict."""

    def run(files, **kwargs):
        green = green_when(files)
        return verify.JobResult(
            ok=True,
            green=green,
            passed=1 if green else 0,
            failed=0 if green else 1,
            stdout="",
            stderr="",
            duration_ms=1,
        )

    return run


def test_code_golf_correct_and_shorter_wins(monkeypatch) -> None:
    game = CodeGolf(task=GOLF_TASKS[0])
    assert sorted(game.current_players()) == [0, 1]
    game.apply_action(0, "submit", {"code": "def encode(s):..."})
    assert game.current_players() == [1]
    game.apply_action(1, "submit", {"code": "def encode(s):.........."})
    assert game.current_player() == WORK

    monkeypatch.setattr(verify, "run_python_job", _fake_runner(lambda f: True))
    game.run_work()
    assert game.current_player() == TERMINAL
    # Both green → fewer bytes (seat 0) wins.
    assert game._winner() == 0
    assert game.returns() == {0: 1.0, 1: -1.0}
    state = game.public_state()
    assert state["reports"][0]["green"] and state["reports"][1]["green"]
    assert state["solutions"][0].startswith("def encode")


def test_code_golf_only_correct_submission_wins(monkeypatch) -> None:
    game = CodeGolf(task=GOLF_TASKS[0])
    game.apply_action(0, "submit", {"code": "x" * 500})  # long but "correct"
    game.apply_action(1, "submit", {"code": "y"})  # short but broken

    def verdict(files: dict[str, Any]) -> bool:
        return files["solution.py"].startswith("x")

    monkeypatch.setattr(verify, "run_python_job", _fake_runner(verdict))
    game.run_work()
    assert game._winner() == 0


def test_code_golf_grading_disabled_is_a_draw(monkeypatch) -> None:
    monkeypatch.delenv("GAMES_ENABLE_CODE_EXEC", raising=False)
    game = CodeGolf(task=GOLF_TASKS[0])
    game.apply_action(0, "submit", {"code": "a"})
    game.apply_action(1, "submit", {"code": "b"})
    game.run_work()  # real runner, but the gate is off → both not green
    assert game._winner() is None
    assert game.returns() == {0: 0.0, 1: 0.0}


def test_test_duel_phases_and_scoring(monkeypatch) -> None:
    game = DuelGame(task=DUEL_TASKS[0])
    assert game.observation(0)["phase"] == "impl"
    game.apply_action(0, "submit_impl", {"code": "GOOD_IMPL"})
    game.apply_action(1, "submit_impl", {"code": "BAD_IMPL"})
    # Phase flips: both seats owe tests now.
    assert sorted(game.current_players()) == [0, 1]
    assert game.observation(0)["phase"] == "tests"
    game.apply_action(0, "submit_tests", {"code": "SHARP_TESTS"})
    game.apply_action(1, "submit_tests", {"code": "INVALID_TESTS"})
    assert game.current_player() == WORK

    def verdict(files: dict[str, Any]) -> bool:
        solution = files["solution.py"]
        suite = files.get("test_user.py") or ""
        if "test_ref.py" in files:
            return solution == "GOOD_IMPL"  # only seat 0's impl holds
        if suite == "SHARP_TESTS":
            return solution != "BAD_IMPL"  # valid vs reference; kills BAD_IMPL
        return False  # INVALID_TESTS fail everything, including the reference

    monkeypatch.setattr(verify, "run_python_job", _fake_runner(verdict))
    game.run_work()
    assert game.current_player() == TERMINAL
    r0, r1 = game.reports
    assert r0 == {"holds": True, "valid_tests": True, "kills": True, "score": 5}
    assert r1 == {"holds": False, "valid_tests": False, "kills": False, "score": 0}
    assert game._winner() == 0
    assert game.returns() == {0: 5.0, 1: -5.0}


def test_referee_runs_the_work_loop(monkeypatch) -> None:
    """End to end through the hub: submissions → WORK grading → game_over."""
    from backend.games_server import models
    from backend.games_server.hub import GameHub

    monkeypatch.setattr(verify, "run_python_job", _fake_runner(lambda f: True))

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

    async def go() -> None:
        hub = GameHub(move_timeout_s=0)
        a_conn, b_conn = FakeConn(), FakeConn()
        a = hub.connect(a_conn)
        b = hub.connect(b_conn)
        await hub.handle(a, {"type": models.AUTH, "token": "alice"})
        await hub.handle(b, {"type": models.AUTH, "token": "bob"})
        await hub.handle(a, {"type": models.CREATE_TABLE, "game_id": "code_golf"})
        table_id = a_conn.last(models.TABLE)["table"]["id"]
        await hub.handle(b, {"type": models.JOIN_TABLE, "table_id": table_id})
        for session, code in ((a, "short"), (b, "much longer submission")):
            await hub.handle(
                session,
                {
                    "type": models.ACTION,
                    "game_id": "code_golf",
                    "action_id": "submit",
                    "payload": {"code": code},
                },
            )
        # WORK ran on a thread inside the referee; give it a beat.
        for _ in range(100):
            if a_conn.last(models.GAME_OVER):
                break
            await asyncio.sleep(0.02)
        over = a_conn.last(models.GAME_OVER)
        assert over is not None
        assert over["winner"] == 0  # shorter green submission
        # Spectators saw the grading phase.
        assert any(
            (m.get("state") or {}).get("grading")
            for m in a_conn.messages
            if m.get("type") == models.PUBLIC_STATE
        )

    asyncio.run(go())
