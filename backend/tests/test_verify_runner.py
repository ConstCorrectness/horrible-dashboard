"""The verification runner: green/red/timeout, the exec gate, and output caps.
Real subprocesses (kept small so the whole file stays fast)."""

from __future__ import annotations

from backend.games_engine import verify

GREEN = {
    "solution.py": "def add(a, b):\n    return a + b\n",
    "test_it.py": "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
}

RED = {
    "solution.py": "def add(a, b):\n    return a - b\n",
    "test_it.py": (
        "from solution import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_zero():\n    assert add(0, 0) == 0\n"
    ),
}


def test_disabled_without_the_env_gate(monkeypatch) -> None:
    monkeypatch.delenv("GAMES_ENABLE_CODE_EXEC", raising=False)
    result = verify.run_python_job(GREEN)
    assert not result.ok and not result.green
    assert "disabled" in result.stderr


def test_green_job(monkeypatch) -> None:
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    result = verify.run_python_job(GREEN, timeout_s=60)
    assert result.ok
    assert result.green
    assert result.passed == 1 and result.failed == 0


def test_red_job_reports_failures(monkeypatch) -> None:
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    result = verify.run_python_job(RED, timeout_s=60)
    assert result.ok  # the job ran fine…
    assert not result.green  # …but the submission is wrong
    assert result.failed == 1 and result.passed == 1


def test_timeout_kills_the_tree(monkeypatch) -> None:
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    result = verify.run_python_job(
        {"loop.py": "while True:\n    pass\n"},
        entry=["loop.py"],
        timeout_s=2,
    )
    assert not result.ok and not result.green
    assert "killed" in result.stderr


def test_path_escapes_are_refused(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAMES_ENABLE_CODE_EXEC", "1")
    evil = tmp_path / "evil.txt"
    result = verify.run_python_job(
        {
            f"..\\..\\{evil.name}": "boom",
            "test_ok.py": "def test_ok():\n    assert True\n",
        },
        timeout_s=60,
    )
    assert result.green  # the legit test ran; the escape was silently dropped
    assert not evil.exists()
