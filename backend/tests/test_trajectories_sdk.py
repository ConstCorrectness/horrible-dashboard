"""The external Python SDK.

Driven against the real ingest route over the in-process app, so the payload the
client builds is validated by the same Pydantic models a socket client would hit —
a test that stubbed `_send` would pass with a payload the server rejects.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.trajectories import store
from horrible_trajectories import TrajectoryRecorder


@pytest.fixture()
def recorder():
    """A recorder wired to the in-process app instead of a socket."""
    store._initialized.clear()
    store.init_trajectories_db()
    rec = TrajectoryRecorder("sdk-test", batch_size=1, flush_interval_sec=0.1)
    rec._client.close()
    # `TestClient` is a real synchronous httpx client over the ASGI app, so the
    # payload is validated by the same Pydantic models a socket client would hit.
    # (`httpx.ASGITransport` is async-only and cannot back a sync `Client`.)
    rec._client = TestClient(app)
    rec.base_url = "http://testserver"
    yield rec
    rec.close()


def test_a_run_round_trips_into_the_store(recorder):
    h = TrajectoryRecorder.harness(
        system_prompt="you are a coder", tools=[{"name": "bash"}], model="m"
    )
    with recorder.run("fix the failing test", harness=h, external_id="r1") as run:
        run.message("user", "fix it")
        run.action("bash", {"cmd": "pytest"}, {"rc": 0}, ok=True, ms=42)
        run.label("outcome", "success")
    recorder.flush()

    runs, total = store.list_runs(dataset_id="sdk-test")
    assert total == 1
    detail = store.get_run(runs[0].id)
    assert detail.goal == "fix the failing test"
    assert detail.source == "external"
    assert detail.outcome == "success"
    assert detail.status == "complete"

    action = [s for s in detail.step_list if s.kind == "action"][0]
    assert action.name == "bash"
    assert action.args == {"cmd": "pytest"}
    assert action.result == {"rc": 0}
    assert action.duration_ms == 42
    assert detail.harness_detail.system_prompt == "you are a coder"


def test_an_exception_in_the_block_seals_the_run_as_failed(recorder):
    """A crashed run is the most interesting kind — losing it would be the worst
    possible failure mode for this client."""
    with pytest.raises(ValueError):
        with recorder.run("do a thing", external_id="boom") as run:
            run.action("bash", {"cmd": "false"}, None, ok=False)
            raise ValueError("agent exploded")
    recorder.flush()

    runs, _ = store.list_runs(dataset_id="sdk-test")
    detail = store.get_run(runs[0].id)
    assert detail.status == "failed"
    assert "agent exploded" in detail.error
    # And the step it got to before dying is still there.
    assert [s.name for s in detail.step_list if s.kind == "action"] == ["bash"]


def test_ingest_is_idempotent_from_the_client(recorder):
    for _ in range(3):
        with recorder.run("same thing", external_id="fixed-id") as run:
            run.action("bash", {"c": "ls"}, {"rc": 0})
        recorder.flush()

    _, total = store.list_runs(dataset_id="sdk-test")
    assert total == 1


def test_a_dead_backend_never_raises():
    """The contract: your agent does not care that the dashboard is down."""
    rec = TrajectoryRecorder(
        "offline", base_url="http://127.0.0.1:9", batch_size=1, flush_interval_sec=0.1
    )
    try:
        with rec.run("something", external_id="x") as run:
            run.message("assistant", "hello")
            run.action("bash", {"c": "ls"}, {"rc": 0})
        rec.flush(timeout=2.0)
    finally:
        rec.close()
    # Reaching here without an exception is the assertion.


def test_steps_after_sealing_are_dropped_not_raised(recorder):
    run = recorder.run("thing", external_id="sealed")
    run.action("a", {}, {})
    run.finish()
    run.action("b", {}, {})  # must not raise
    recorder.flush()

    runs, _ = store.list_runs(dataset_id="sdk-test")
    detail = store.get_run(runs[0].id)
    assert [s.name for s in detail.step_list if s.kind == "action"] == ["a"]


def test_harness_accepts_both_tool_shapes():
    as_list = TrajectoryRecorder.harness(
        tools=[{"name": "a"}, {"function": {"name": "b"}}]
    )
    as_map = TrajectoryRecorder.harness(tools={"a": {"name": "a"}, "b": {"name": "b"}})
    assert as_list["tool_names"] == ["a", "b"]
    assert as_map["tool_names"] == ["a", "b"]


def test_client_module_imports_nothing_from_the_backend():
    """The distributability claim, enforced.

    This package is published on its own so it can drop into a codebase that has
    never heard of horrible-dashboard. One convenience import from `backend.*`
    would make it uninstallable there, and nothing else would notice — the tests
    run inside this repo, where `backend` happens to be importable.
    """
    source = Path(
        "sdk/horrible-trajectories/src/horrible_trajectories/client.py"
    ).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+backend", source, re.MULTILINE)
