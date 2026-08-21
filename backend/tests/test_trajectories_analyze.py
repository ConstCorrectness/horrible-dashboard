"""Aggregates, the harness comparison, and the agent tools.

The comparison is the module's headline feature and also the easiest thing to get
quietly wrong, so most of this file is about what it *refuses* to claim.
"""

from __future__ import annotations

import pytest

from backend.modules.trajectories import agent_tools, analyze, store
from backend.modules.trajectories.models import (
    HarnessWrite,
    StepWrite,
    TrajectoryWrite,
)


@pytest.fixture()
def db():
    store._initialized.clear()
    store.init_trajectories_db()
    return store


def _harness(prompt: str) -> HarnessWrite:
    return HarnessWrite(agent_id="coder", model="m", system_prompt=prompt)


def _run(db, *, prompt: str, goal: str, outcome: str | None, calls=()) -> str:
    run_id, _ = db.ingest_run(
        TrajectoryWrite(
            dataset_id="d",
            goal=goal,
            status="complete",
            outcome=outcome,
            harness=_harness(prompt),
            step_list=[
                StepWrite(kind="action", name=name, args={}, result=res, ok=ok)
                for name, res, ok in calls
            ],
        )
    )
    return run_id


# --- tool stats -------------------------------------------------------------


def test_tool_stats_separates_failures_from_gated(db):
    """A broken tool and a harness that will not let the agent work look identical
    in `ok`. Conflating them is the finding this split exists to prevent."""
    run_id = db.start_run("d", goal="g")
    db.append_step(run_id, StepWrite(kind="action", name="bash", ok=True))
    db.append_step(
        run_id,
        StepWrite(kind="action", name="bash", ok=False, result={"error": "boom"}),
    )
    db.append_step(
        run_id,
        StepWrite(
            kind="action",
            name="bash",
            ok=False,
            gated=True,
            result={"error": "denied by permission policy"},
        ),
    )
    db.finish_run(run_id)

    stats = {row["name"]: row for row in analyze.tool_stats()}
    assert stats["bash"]["calls"] == 3
    assert stats["bash"]["failures"] == 2
    assert stats["bash"]["gated"] == 1


def test_dataset_stats_counts_ungraded_separately(db):
    _run(db, prompt="p", goal="a", outcome="success")
    _run(db, prompt="p", goal="b", outcome=None)
    stats = analyze.dataset_stats("d")
    assert stats["runs"] == 2
    assert stats["outcomes"] == {"success": 1, "ungraded": 1}


# --- comparison -------------------------------------------------------------


def test_compare_refuses_to_headline_unpaired_workloads(db):
    """Harness A ran easy goals, B ran different ones. Any rate difference here
    describes the workloads, not the harnesses — and the report must say so."""
    for i in range(4):
        _run(db, prompt="A", goal=f"easy-{i}", outcome="success")
    for i in range(4):
        _run(db, prompt="B", goal=f"hard-{i}", outcome="failure")

    a = db.fingerprint_harness(_harness("A"))
    b = db.fingerprint_harness(_harness("B"))
    report = analyze.compare(a, b)

    assert report["pairedGoals"] == 0
    assert report["comparable"] is False
    assert "not a comparison" in report["note"]
    # The marginal rates are still reported — they are just not a verdict.
    assert report["a"]["successRate"] == 1.0
    assert report["b"]["successRate"] == 0.0


def test_compare_names_regressions_and_fixes_on_shared_goals(db):
    goals = [f"goal-{i}" for i in range(6)]
    for goal in goals:
        _run(
            db,
            prompt="A",
            goal=goal,
            outcome="success" if goal != "goal-5" else "failure",
        )
    for goal in goals:
        _run(
            db,
            prompt="B",
            goal=goal,
            outcome="success" if goal != "goal-0" else "failure",
        )

    a = db.fingerprint_harness(_harness("A"))
    b = db.fingerprint_harness(_harness("B"))
    report = analyze.compare(a, b)

    assert report["pairedGoals"] == 6
    assert report["comparable"] is True
    # A did goal-0, B does not: a regression. The reverse for goal-5.
    assert report["regressions"] == ["goal-0"]
    assert report["fixes"] == ["goal-5"]
    assert report["pairedSuccess"] == {"a": 5, "b": 5, "of": 6}


def test_success_rate_is_none_when_nothing_is_graded(db):
    """A rate over zero graded runs is unknown, not 0% — rendering it as 0%
    invents a finding out of an absence of data."""
    _run(db, prompt="A", goal="a", outcome=None)
    a = db.fingerprint_harness(_harness("A"))
    report = analyze.compare(a, a)
    assert report["a"]["successRate"] is None


def test_a_goal_passed_once_counts_as_passed(db):
    """Otherwise the comparison depends on which run happened to be last."""
    _run(db, prompt="A", goal="flaky", outcome="failure")
    _run(db, prompt="A", goal="flaky", outcome="success")
    _run(db, prompt="A", goal="flaky", outcome="failure")
    assert (
        analyze._goal_outcomes(db.fingerprint_harness(_harness("A")))["flaky"]
        == "success"
    )


def test_tool_delta_is_normalised_per_run(db):
    """A harness with more runs must not look like one that calls everything more."""
    for i in range(4):
        _run(
            db, prompt="A", goal=f"g{i}", outcome="success", calls=[("bash", {}, True)]
        )
    _run(db, prompt="B", goal="g0", outcome="success", calls=[("bash", {}, True)])

    a = db.fingerprint_harness(_harness("A"))
    b = db.fingerprint_harness(_harness("B"))
    delta = {row["name"]: row for row in analyze.compare(a, b)["toolDelta"]}
    # One call per run on both sides, despite 4 runs vs 1.
    assert delta["bash"]["a"] == 1.0
    assert delta["bash"]["b"] == 1.0
    assert delta["bash"]["delta"] == 0.0


# --- agent tools ------------------------------------------------------------


@pytest.mark.anyio
async def test_search_returns_successes_by_default(db):
    """Handing a model its own failures as examples teaches the failure."""
    _run(db, prompt="A", goal="deploy the thing", outcome="success")
    _run(db, prompt="A", goal="deploy the other thing", outcome="failure")

    out = await agent_tools._search({"query": "deploy"})
    assert [r["outcome"] for r in out["runs"]] == ["success"]
    assert "Successful runs only" in out["note"]

    both = await agent_tools._search({"query": "deploy", "include_failures": True})
    assert len(both["runs"]) == 2
    assert "counter-examples" in both["note"]


@pytest.mark.anyio
async def test_get_trims_a_long_run_to_head_and_tail(db):
    run_id = db.start_run("d", goal="long")
    for i in range(80):
        db.append_step(run_id, StepWrite(kind="action", name=f"t{i}"))
    db.finish_run(run_id)

    out = await agent_tools._get({"run_id": run_id})
    assert out["truncated"] is True
    assert len(out["steps"]) == agent_tools.MAX_STEPS_INLINE
    # How it started and how it ended, not the middle of the loop.
    assert out["steps"][0]["name"] == "t0"
    assert out["steps"][-1]["name"] == "t79"


@pytest.mark.anyio
async def test_get_caps_a_huge_result_payload(db):
    run_id = db.start_run("d", goal="g")
    db.append_step(
        run_id, StepWrite(kind="action", name="read", result={"body": "x" * 50_000})
    )
    db.finish_run(run_id)

    out = await agent_tools._get({"run_id": run_id})
    assert len(out["steps"][0]["result"]) < 500


@pytest.mark.anyio
async def test_agent_label_is_always_recorded_as_a_critique(db):
    """A model that could stamp its own guess `human` would launder it into
    evidence."""
    run_id = _run(db, prompt="A", goal="g", outcome=None)
    await agent_tools._label(
        {"run_id": run_id, "key": "outcome", "value": "failure", "source": "human"}
    )
    run = db.get_run(run_id)
    assert run.labels[0].source == "agent-critic"
    assert run.outcome == "failure"


@pytest.mark.anyio
async def test_agent_tools_report_missing_things_rather_than_raising(db):
    assert "error" in await agent_tools._get({"run_id": "nope"})
    assert "error" in await agent_tools._compare({"a": "x", "b": "y"})
    assert "error" in await agent_tools._label({"run_id": "nope", "key": "k"})
