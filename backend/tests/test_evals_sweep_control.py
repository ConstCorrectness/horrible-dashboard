"""Stopping a sweep, and what a stopped sweep leaves behind.

A sweep is detached and outlives the request that started it, so the only handle on
one is the registry `start_sweep` keeps. The assertions worth having are about the
rows: a cancel must not leave a run reading `running` forever, and it must not
rewrite a target that had already finished — those are real measurements.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.evals import store, sweep
from backend.modules.evals.models import EvalCase, Expect, RunTarget, ToolCall


@pytest.fixture
def suite(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    store._initialized.clear()
    made = store.create_suite("Control")
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


@pytest.mark.anyio
async def test_a_cancel_closes_unfinished_runs_and_leaves_finished_ones(
    suite, monkeypatch
):
    ids: dict[str, str] = {}

    async def never_finishes(
        suite_id, targets, tools, case_ids=None, project="", started=None
    ):
        # Stands in for the real sweep: one target that got through, one still
        # going. Both ids are handed up the way `_run_one_target` hands them up.
        done = store.create_run(suite_id, "done", "ollama", "http://x", "m", 1)
        store.update_run(done.id, status="done")
        running = store.create_run(suite_id, "running", "ollama", "http://x", "m", 1)
        store.update_run(running.id, status="running")
        ids.update(done=done.id, running=running.id)
        if started is not None:
            started.extend([done.id, running.id])
        await asyncio.sleep(30)

    monkeypatch.setattr(sweep, "run_sweep", never_finishes)

    key = sweep.start_sweep(suite, [RunTarget(model="m")], [])
    # One turn of the loop is enough for `_go` to reach the sleep.
    await asyncio.sleep(0.05)
    assert [s["key"] for s in sweep.active_sweeps()] == [key]

    assert sweep.cancel_sweep(key) is True
    await asyncio.sleep(0.05)

    assert store.get_run(ids["running"]).status == "cancelled"
    # The one that finished keeps its verdict: "you stopped it" is not a reason to
    # throw away a measurement that was actually taken.
    assert store.get_run(ids["done"]).status == "done"
    assert sweep.active_sweeps() == []


@pytest.mark.anyio
async def test_cancelling_something_that_is_not_running_says_so(suite):
    # False rather than an exception: a pane pressing Stop on a sweep that just
    # finished is a race, not an error.
    assert sweep.cancel_sweep("no-such-sweep") is False


@pytest.mark.anyio
async def test_a_live_sweep_names_what_it_is_running(suite, monkeypatch):
    async def hang(suite_id, targets, tools, case_ids=None, project="", started=None):
        await asyncio.sleep(30)

    monkeypatch.setattr(sweep, "run_sweep", hang)
    key = sweep.start_sweep(
        suite, [RunTarget(model="m", label="gemma"), RunTarget(model="q")], []
    )
    await asyncio.sleep(0.05)
    try:
        live = sweep.active_sweeps()
        # Labels, not just a key: a pane offering to stop something has to be able
        # to say what it is stopping.
        assert live[0]["suiteId"] == suite
        assert live[0]["targets"] == ["gemma", "q"]
    finally:
        sweep.cancel_sweep(key)
        await asyncio.sleep(0.05)


def test_the_routes_answer_for_a_node_with_nothing_running(suite):
    from fastapi.testclient import TestClient

    from backend.app import app

    client = TestClient(app)
    assert client.get("/api/evals/sweeps").json() == {"sweeps": []}
    # The key contains a colon, which is why the route declares `:path`.
    assert client.delete("/api/evals/sweeps/some-suite:123").json() == {
        "cancelled": False
    }
