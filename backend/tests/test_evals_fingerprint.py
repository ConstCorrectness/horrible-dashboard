"""The harness recorded alongside a run, and the two ways it must not lie.

The positive assertions here are cheap — a hash changes when its input changes.
The ones worth having are the negative ones: that a *missing* hash reads as
"cannot tell" and never as agreement, and that the hash is stable across a re-read
so the banner does not fire on every second comparison and get ignored.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.evals import fingerprint, leaderboard, store
from backend.modules.evals.models import CaseResult, EvalCase, Expect, ToolCall
from backend.modules.skills.store import Skill


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    return tmp_path


def _skills(monkeypatch, skills: list[Skill]) -> None:
    monkeypatch.setattr(
        "backend.modules.skills.store.active_skills", lambda: list(skills)
    )


def _no_mcp(monkeypatch) -> None:
    monkeypatch.setattr("backend.modules.mcp.client.manager.runtimes", lambda: [])


def skill(name: str, body: str = "do the thing", description: str = "a skill") -> Skill:
    return Skill(name=name, description=description, body=body)


# --- computing --------------------------------------------------------------


def test_the_same_harness_hashes_the_same_way_twice(node, monkeypatch):
    # Stability is the whole reason it is canonical JSON: a hash that moved on a
    # re-read would flag every comparison and the warning would stop being read.
    _skills(monkeypatch, [skill("b"), skill("a")])
    _no_mcp(monkeypatch)
    first, _ = fingerprint.compute()
    _skills(monkeypatch, [skill("a"), skill("b")])
    second, _ = fingerprint.compute()
    assert first == second != ""


def test_a_skill_switched_on_changes_the_harness(node, monkeypatch):
    _no_mcp(monkeypatch)
    _skills(monkeypatch, [skill("a")])
    before, _ = fingerprint.compute()
    _skills(monkeypatch, [skill("a"), skill("b")])
    after, _ = fingerprint.compute()
    assert before != after


def test_an_edited_body_changes_the_harness_under_a_stable_name(node, monkeypatch):
    # The reason this hashes content rather than the catalog listing: `use_skill`
    # pastes the body into the turn, so a rewritten body is a different harness
    # even though nothing about the name or the tool count moved.
    _no_mcp(monkeypatch)
    _skills(monkeypatch, [skill("a", body="first")])
    before, _ = fingerprint.compute()
    _skills(monkeypatch, [skill("a", body="second")])
    after, _ = fingerprint.compute()
    assert before != after


def test_an_unreadable_harness_is_empty_rather_than_wrong(node, monkeypatch):
    # Not a crash and not a partial hash: losing a twenty-minute sweep because a
    # skill file would not read is a bad trade, and a hash computed from half the
    # harness would collide with a genuinely different one.
    def boom() -> list[Skill]:
        raise OSError("the skills directory is gone")

    monkeypatch.setattr("backend.modules.skills.store.active_skills", boom)
    _no_mcp(monkeypatch)
    assert fingerprint.compute() == ("", "")


def test_a_genuinely_empty_harness_still_hashes(node, monkeypatch):
    # "No skills and no servers" is a real, recordable state — it must not be
    # confused with "could not tell", which is the empty hash.
    _skills(monkeypatch, [])
    _no_mcp(monkeypatch)
    hashed, blob = fingerprint.compute()
    assert hashed
    assert json.loads(blob) == {"skills": [], "mcp": []}


# --- describing a difference ------------------------------------------------


def test_the_difference_names_what_moved(node, monkeypatch):
    _no_mcp(monkeypatch)
    _skills(monkeypatch, [skill("kept"), skill("dropped"), skill("edited", body="x")])
    _, before = fingerprint.compute()
    _skills(monkeypatch, [skill("kept"), skill("added"), skill("edited", body="y")])
    _, after = fingerprint.compute()

    lines = fingerprint.describe_difference(before, after)
    assert "skill added was added" in lines
    assert "skill dropped was removed" in lines
    assert "skill edited changed" in lines
    assert not any("kept" in line for line in lines)


def test_an_unreadable_record_describes_nothing_rather_than_guessing(node):
    assert fingerprint.describe_difference("not json", "{}") == []
    assert fingerprint.describe_difference("", "") == []


# --- what Compare does with it ----------------------------------------------


def _run(suite_id: str, label: str, harness: tuple[str, str]) -> str:
    run = store.create_run(
        suite_id,
        label,
        "ollama",
        "http://x",
        label,
        1,
        harness_hash=harness[0],
        harness_json=harness[1],
    )
    store.save_result(
        run.id,
        CaseResult(case_id="c1", passed=True, grade="subset", case_hash="h"),
    )
    store.update_run(run.id, status="done")
    return run.id


@pytest.fixture
def suite(node):
    made = store.create_suite("Harness")
    store.write_cases(
        made,
        [
            EvalCase(
                id="c1",
                prompt="do it",
                expect=Expect(grade="subset", calls=[ToolCall(name="show")]),
            )
        ],
    )
    return made.id


def test_two_runs_of_one_harness_agree(suite):
    harness = ("abc123", '{"skills":[],"mcp":[]}')
    diff = leaderboard.diff(_run(suite, "a", harness), _run(suite, "b", harness))
    assert diff["harness"] == {
        "unknown": False,
        "differs": False,
        "base": "abc123",
        "other": "abc123",
        "changes": [],
    }


def test_a_differing_harness_is_flagged_and_explained(suite):
    before = json.dumps({"skills": [{"name": "a", "hash": "1"}], "mcp": []})
    after = json.dumps({"skills": [{"name": "a", "hash": "2"}], "mcp": []})
    diff = leaderboard.diff(
        _run(suite, "a", ("aaa", before)), _run(suite, "b", ("bbb", after))
    )
    assert diff["harness"]["differs"] is True
    assert diff["harness"]["unknown"] is False
    assert diff["harness"]["changes"] == ["skill a changed"]


def test_a_missing_harness_is_unknown_not_agreement(suite):
    # The whole point of the column. A run written before it existed carries no
    # hash, and reporting that as "same harness" would be an assertion nobody made.
    diff = leaderboard.diff(
        _run(suite, "old", ("", "")), _run(suite, "new", ("bbb", "{}"))
    )
    assert diff["harness"]["unknown"] is True
    assert diff["harness"]["differs"] is False
    assert diff["harness"]["changes"] == []


def test_the_run_the_api_serves_carries_the_harness(suite, monkeypatch):
    # Per the response_model trap: a field the store writes can still be filtered
    # out on the way to the browser, so this asserts the HTTP body.
    from fastapi.testclient import TestClient

    from backend.app import app

    run_id = _run(suite, "a", ("abc123", '{"skills":[],"mcp":[]}'))
    body = TestClient(app).get("/api/evals/runs").json()
    row = next(r for r in body["runs"] if r["id"] == run_id)
    assert row["harness_hash"] == "abc123"
