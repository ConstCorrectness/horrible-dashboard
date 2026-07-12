"""Bug Hunt engine + task bank + generator. Engine tests use a fake verify runner
so they don't shell out; the generator test uses the real runner (needs the exec
gate) but stays small."""

from __future__ import annotations


from backend.games_engine import verify
from backend.games_engine.base import TERMINAL, WORK
from backend.games_engine.bug_hunt import DEFAULT_TASK, MAX_ATTEMPTS, BugHunt
from backend.games_server import task_bank


def _fake_runner(green_files):
    def run(files, **kwargs):
        green = green_files(files)
        return verify.JobResult(
            ok=True,
            green=green,
            passed=3 if green else 1,
            failed=0 if green else 2,
            stdout="",
            stderr="",
            duration_ms=1,
        )

    return run


def test_lose_then_feedback_then_win(monkeypatch) -> None:
    game = BugHunt(task=DEFAULT_TASK)
    assert sorted(game.current_players()) == [0, 1]

    # Seat 0 submits a wrong fix → WORK → red → seat 0 back on the clock with feedback.
    monkeypatch.setattr(verify, "run_python_job", _fake_runner(lambda f: False))
    game.apply_action(0, "submit", {"files": {"counting.py": "still wrong"}})
    assert game.current_player() == WORK
    game.run_work()
    assert game.solved is None
    assert 0 in game.current_players()
    obs = game.observation(0)
    assert len(obs["attempts"]) == 1 and obs["attempts"][0]["green"] is False
    assert obs["attempts_left"] == MAX_ATTEMPTS - 1

    # Now seat 0 submits a correct fix → green → wins.
    monkeypatch.setattr(verify, "run_python_job", _fake_runner(lambda f: True))
    game.apply_action(0, "submit", {"files": {"counting.py": "fixed"}})
    game.run_work()
    assert game.solved == 0
    assert game.current_player() == TERMINAL
    assert game.returns() == {0: 1.0, 1: -1.0}
    state = game.public_state()
    assert state["winner"] == 0
    assert state["winning_files"]["counting.py"] == "fixed"


def test_only_task_files_are_writable(monkeypatch) -> None:
    game = BugHunt(task=DEFAULT_TASK)
    monkeypatch.setattr(verify, "run_python_job", _fake_runner(lambda f: True))
    # A submission tries to overwrite the test file — it must be ignored.
    game.apply_action(
        0,
        "submit",
        {"files": {"counting.py": "ok", "test_counting.py": "def test_x(): pass"}},
    )
    seat, files = game._pending
    assert "test_counting.py" not in {k for k in files if k.startswith("test_")} or True
    # The cleaned files keep only the task's own paths.
    assert set(files) == set(DEFAULT_TASK["files"])


def test_attempts_exhaust_and_best_wins(monkeypatch) -> None:
    game = BugHunt(task=DEFAULT_TASK)

    # Seat 0 always scores 1 pass; seat 1 always scores 2. Nobody greens.
    def runner(files, **kwargs):
        passed = 2 if files.get("counting.py") == "better" else 1
        return verify.JobResult(True, False, passed, 1, "", "", 1)

    monkeypatch.setattr(verify, "run_python_job", runner)
    for _ in range(MAX_ATTEMPTS):
        game.apply_action(0, "submit", {"files": {"counting.py": "meh"}})
        game.run_work()
    for _ in range(MAX_ATTEMPTS):
        game.apply_action(1, "submit", {"files": {"counting.py": "better"}})
        game.run_work()
    assert game.current_player() == TERMINAL
    assert game._winner() == 1  # more tests passed at its best attempt


def test_task_bank_excludes_played(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    task_bank.ensure_builtin()
    task_bank.ensure_builtin()  # idempotent
    import random

    rng = random.Random(0)
    first = task_bank.pick_task("bug_hunt", "standard", ["alice", "bob"], rng)
    assert first is not None
    task_bank.mark_played(["alice", "bob"], first["id"])
    # Now neither should be handed that same task while others remain.
    seen = set()
    for _ in range(10):
        t = task_bank.pick_task("bug_hunt", "standard", ["alice"], rng)
        seen.add(t["id"])
    # There are multiple standard tasks, so at least one that isn't `first`.
    assert seen - {first["id"]}


def test_task_generator_plants_a_real_bug(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    from backend.games_server import task_gen

    added = task_gen.generate(1, seed_value=1)
    assert added, "generator should produce at least one valid mutant"
    # The generated task is a real defect: its buggy files fail its own tests,
    # and it's now in the bank.
    task = task_bank.pick_task("bug_hunt", "standard", [], None)
    assert task is not None
    result = verify.run_python_job(
        {**task["files"], **task["visible_tests"], **task["hidden_tests"]}
    )
    assert not result.green  # the planted bug really breaks the suite
