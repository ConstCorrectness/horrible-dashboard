"""Comparing runs: the ranking, the diff, and the two ways a comparison lies.

The interesting assertions here are the negative ones. A leaderboard that computes
percentages is easy; one that refuses to call something a fix when it cannot tell a
fix from an edited exam is the point.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.evals import leaderboard, store
from backend.modules.evals.models import CaseResult, EvalCase, Expect, ToolCall


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    return TestClient(app)


def case(case_id: str, tool: str = "show") -> EvalCase:
    return EvalCase(
        id=case_id,
        prompt="do the thing",
        expect=Expect(grade="subset", calls=[ToolCall(name=tool)]),
    )


def suite_with(cases: list[EvalCase]) -> str:
    suite = store.create_suite("Compare")
    store.write_cases(suite, cases)
    return suite.id


def run_with(
    suite_id: str,
    label: str,
    verdicts: dict[str, bool],
    hashes: dict[str, str] | None = None,
) -> str:
    run = store.create_run(suite_id, label, "ollama", "http://x", label, len(verdicts))
    for case_id, passed in verdicts.items():
        store.save_result(
            run.id,
            CaseResult(
                case_id=case_id,
                passed=passed,
                grade="subset",
                detail="ok" if passed else "called the wrong tool",
                case_hash=(hashes or {}).get(case_id, ""),
            ),
        )
    store.update_run(run.id, status="done")
    return run.id


# --- the ranking --------------------------------------------------------------


def test_runs_are_summarised_with_a_rate(node):
    suite = suite_with([case("a"), case("b")])
    run_with(suite, "weak", {"a": True, "b": False})

    board = leaderboard.build(suite)
    assert board["runs"][0]["passed"] == 1
    assert board["runs"][0]["rate"] == pytest.approx(0.5)


def test_an_unfinished_run_is_not_in_the_table(node):
    """A half-complete sweep would sit there looking like a model that failed
    everything it has not reached yet."""
    suite = suite_with([case("a")])
    run = store.create_run(suite, "running", "ollama", "http://x", "m", 1)
    store.update_run(run.id, status="running")

    assert leaderboard.build(suite)["runs"] == []


def test_rows_are_failure_first(node):
    """The rows you came to read are the ones something is wrong with; sorting
    alphabetically buries them."""
    suite = suite_with([case("aaa"), case("zzz")])
    run_with(suite, "m", {"aaa": True, "zzz": False})

    ids = [c["caseId"] for c in leaderboard.build(suite)["cases"]]
    assert ids == ["zzz", "aaa"]


# --- the signal this module keeps needing -------------------------------------


def test_a_case_every_model_fails_is_flagged(node):
    """Three times now a case here was wrong rather than the model. The signature
    is every model failing the same case, and it is a prompt to read the case."""
    suite = suite_with([case("suspect"), case("fine")])
    run_with(suite, "a", {"suspect": False, "fine": True})
    run_with(suite, "b", {"suspect": False, "fine": True})
    run_with(suite, "c", {"suspect": False, "fine": False})

    board = leaderboard.build(suite)
    assert board["universalFailures"] == ["suspect"]


def test_one_run_failing_a_case_is_not_evidence(node):
    """With a single run there is nothing to generalise from — flagging it would
    make the signal meaningless."""
    suite = suite_with([case("a")])
    run_with(suite, "only", {"a": False})

    assert leaderboard.build(suite)["universalFailures"] == []


# --- the diff -----------------------------------------------------------------


def test_the_diff_names_what_was_fixed_and_broken(node):
    """8/12 → 9/12 by fixing three and breaking two is a different event from
    fixing one and breaking none. The totals cannot tell them apart."""
    suite = suite_with([case(c) for c in ("a", "b", "c", "d")])
    base = run_with(suite, "base", {"a": True, "b": False, "c": False, "d": False})
    tuned = run_with(suite, "tuned", {"a": False, "b": True, "c": True, "d": False})

    out = leaderboard.diff(base, tuned)
    assert [f["caseId"] for f in out["fixed"]] == ["b", "c"]
    assert [b["caseId"] for b in out["broken"]] == ["a"]
    assert out["stillFailing"] == ["d"]


def test_the_diff_only_compares_cases_both_runs_attempted(node):
    """A run started with a case filter attempted fewer cases; 5/5 beats 8/12 on
    percentage and means nothing."""
    suite = suite_with([case(c) for c in ("a", "b", "c")])
    full = run_with(suite, "full", {"a": True, "b": False, "c": False})
    partial = run_with(suite, "partial", {"a": True})

    out = leaderboard.diff(full, partial)
    assert out["shared"] == 1
    # And it says what it left out rather than quietly dropping it.
    assert out["onlyInBase"] == ["b", "c"]
    assert out["onlyInOther"] == []


# --- the lie the case hash exists to prevent ----------------------------------


def test_an_edited_case_is_not_reported_as_a_fix(node):
    """Case ids survive an edit, so the same id before and after someone corrected
    its expectation is a different question. Calling that a fix would credit a
    model for a change made to the exam."""
    suite = suite_with([case("a")])
    base = run_with(suite, "before", {"a": False}, hashes={"a": "hash-old"})
    after = run_with(suite, "after", {"a": True}, hashes={"a": "hash-new"})

    out = leaderboard.diff(base, after)
    assert out["fixed"] == []
    assert [c["caseId"] for c in out["changed"]] == ["a"]
    assert "edited" in out["changed"][0]["detail"]


def test_the_same_hash_still_reports_a_real_fix(node):
    """The check must not swallow genuine progress."""
    suite = suite_with([case("a")])
    base = run_with(suite, "before", {"a": False}, hashes={"a": "same"})
    after = run_with(suite, "after", {"a": True}, hashes={"a": "same"})

    out = leaderboard.diff(base, after)
    assert [f["caseId"] for f in out["fixed"]] == ["a"]
    assert out["hashesUnknown"] is False


def test_missing_hashes_are_reported_as_unknown_not_as_agreement(node):
    """Rows written before the column existed cannot rule out an edit, and saying
    so is the difference between an honest comparison and a confident wrong one."""
    suite = suite_with([case("a")])
    base = run_with(suite, "before", {"a": False})
    after = run_with(suite, "after", {"a": True})

    out = leaderboard.diff(base, after)
    assert out["hashesUnknown"] is True


def test_an_edited_case_is_flagged_in_the_table_too(node):
    suite = suite_with([case("a")])
    run_with(suite, "before", {"a": False}, hashes={"a": "h1"})
    run_with(suite, "after", {"a": True}, hashes={"a": "h2"})

    board = leaderboard.build(suite)
    assert board["editedCases"] == ["a"]


# --- the content hash itself ---------------------------------------------------


def test_the_hash_tracks_what_decides_correctness(node):
    a = case("x", tool="show")
    assert a.content_hash() == case("x", tool="show").content_hash()
    # A different expectation is a different question.
    assert a.content_hash() != case("x", tool="open_pane").content_hash()
    # Rewording why the case exists is not.
    assert a.content_hash() == a.model_copy(update={"note": "explained"}).content_hash()
    # Nor is renaming it — the id is the join key, not part of the question.
    assert a.content_hash() == a.model_copy(update={"id": "y"}).content_hash()


def test_a_sweep_stamps_the_hash(node):
    """If the sweep forgets, every comparison silently degrades to "cannot tell"."""
    import inspect

    from backend.modules.evals import sweep

    assert "content_hash()" in inspect.getsource(sweep)


# --- routes -------------------------------------------------------------------


def test_the_routes_serve_the_board_and_the_diff(node):
    suite = suite_with([case("a"), case("b")])
    base = run_with(suite, "base", {"a": True, "b": False})
    other = run_with(suite, "other", {"a": True, "b": True})

    board = node.get(f"/api/evals/leaderboard?suite_id={suite}").json()
    assert len(board["runs"]) == 2

    out = node.get(f"/api/evals/leaderboard/diff?base={base}&other={other}").json()
    assert [f["caseId"] for f in out["fixed"]] == ["b"]


def test_an_unknown_suite_is_a_404(node):
    assert node.get("/api/evals/leaderboard?suite_id=nope").status_code == 404


def test_an_errored_case_is_not_reported_as_a_regression(node):
    """A 500 from the model server is not the model getting worse. Sitting in the
    "broke" column is how an infrastructure hiccup gets read as a regression — and
    this is a real one: LM Studio 500'd on one case of a two-run sweep and the diff
    called it a break until this existed."""
    suite = suite_with([case("a")])
    base = run_with(suite, "before", {"a": True}, hashes={"a": "h"})
    after = store.create_run(suite, "after", "ollama", "http://x", "m", 1)
    store.save_result(
        after.id,
        CaseResult(
            case_id="a",
            passed=False,
            grade="subset",
            detail="",
            error="HTTPStatusError: 500",
            case_hash="h",
        ),
    )
    store.update_run(after.id, status="done")

    out = leaderboard.diff(base, after.id)
    assert out["broken"] == []
    assert [e["caseId"] for e in out["errored"]] == ["a"]
    assert "500" in out["errored"][0]["detail"]
