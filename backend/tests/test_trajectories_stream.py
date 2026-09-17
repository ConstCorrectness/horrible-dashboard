"""The live trajectory channel.

Three properties, each of which fails silently rather than loudly if it breaks:

- **Every source streams**, because the broadcast hangs off `store.append_step`
  rather than off the orchestrator's recorder. Hooking the recorder would have
  streamed chat turns and left the evals runner, the SDK and file import static,
  with nothing to indicate that the pane was showing a partial picture.
- **A bulk write is one event.** Importing a 200-step file must not animate a run
  that finished last week.
- **Large payloads are elided but their size is still reported.** "There is a 400 KB
  result here you have not fetched" and "there is no result" must not render the same.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.trajectories import store, stream
from backend.modules.trajectories.models import StepWrite


@pytest.fixture()
async def bus(monkeypatch):
    """Capture what reaches the socket, and give `publish` a loop to run on.

    Async on purpose: `init_loop` calls `asyncio.get_running_loop()`, so a sync
    fixture captures nothing, every publish is dropped as "no loop", and each test
    below fails on an empty list rather than on what it meant to assert.
    """
    sent: list[tuple[str, str, dict]] = []

    async def fake_broadcast(channel, event, data):
        sent.append((channel, event, data))

    import backend.modules.ws as ws_mod

    monkeypatch.setattr(ws_mod, "broadcast_event", fake_broadcast)
    stream.init_loop()
    yield sent
    stream.reset()


@pytest.fixture()
def db():
    store._initialized.clear()
    store.init_trajectories_db()
    store.create_dataset("d", "D")
    return store


async def _settle():
    """`publish` schedules onto the loop, so let the scheduled sends actually run."""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.anyio
async def test_a_run_and_its_steps_reach_the_channel(bus, db):
    run_id = db.start_run("d", goal="do the thing")
    db.append_step(run_id, StepWrite(kind="action", name="open_pane", args={"id": "t"}))
    db.finish_run(run_id)
    await _settle()

    assert [e for _, e, _ in bus] == ["run", "step", "seal"]
    assert all(c == "trajectories" for c, _, _ in bus)
    assert bus[0][2]["goal"] == "do the thing"
    assert bus[1][2]["runId"] == run_id
    assert bus[1][2]["step"]["name"] == "open_pane"
    assert bus[2][2]["status"] == "complete"
    assert bus[2][2]["steps"] == 1


@pytest.mark.anyio
async def test_a_step_carries_the_seq_the_pane_reconciles_on(bus, db):
    """`traj_steps.seq` is the cursor a reconnecting pane merges against, so it has
    to be on the wire. Without it a refetch cannot tell which buffered live steps it
    already has, and the run renders with every step twice."""
    run_id = db.start_run("d")
    for i in range(3):
        db.append_step(run_id, StepWrite(kind="action", name=f"t{i}"))
    await _settle()

    steps = [d["step"] for _, e, d in bus if e == "step"]
    assert [s["seq"] for s in steps] == [0, 1, 2]


@pytest.mark.anyio
async def test_a_bulk_import_is_one_event(bus, db):
    """The whole reason `notify` exists."""
    from backend.modules.trajectories.models import TrajectoryWrite

    db.ingest_run(
        TrajectoryWrite(
            dataset_id="d",
            external_id="x1",
            status="complete",
            step_list=[StepWrite(kind="action", name=f"t{i}") for i in range(200)],
        )
    )
    await _settle()

    assert [e for _, e, _ in bus] == ["run"]
    # And the one frame describes the finished run, not a headline already stale.
    assert bus[0][2]["status"] == "complete"
    assert bus[0][2]["steps"] == 200


@pytest.mark.anyio
async def test_a_large_payload_is_elided_but_its_size_is_reported(bus, db):
    run_id = db.start_run("d")
    big = {"text": "x" * (stream.STREAM_PAYLOAD_MAX * 2)}
    db.append_step(run_id, StepWrite(kind="action", name="read", result=big))
    await _settle()

    step = [d["step"] for _, e, d in bus if e == "step"][0]
    assert step["result"] is None
    assert step["result_bytes"] > stream.STREAM_PAYLOAD_MAX
    # The store still holds the real thing — elision is a wire decision only.
    assert db.get_run(run_id).step_list[0].result == big


@pytest.mark.anyio
async def test_a_small_payload_rides_along(bus, db):
    """Most steps are small, and a round trip per row to render three words would
    make the live view slower than the polled one it replaces."""
    run_id = db.start_run("d")
    db.append_step(run_id, StepWrite(kind="action", name="t", result={"ok": True}))
    await _settle()

    step = [d["step"] for _, e, d in bus if e == "step"][0]
    assert step["result"] == {"ok": True}
    assert step["result_bytes"] is not None


@pytest.mark.anyio
async def test_an_absent_payload_is_not_a_withheld_one(bus, db):
    """`None` bytes means there was nothing; a number means there is something you
    have not fetched. Collapsing the two is the bug elision would otherwise cause."""
    run_id = db.start_run("d")
    db.append_step(run_id, StepWrite(kind="message", role="assistant", content="hi"))
    await _settle()

    step = [d["step"] for _, e, d in bus if e == "step"][0]
    assert step["result"] is None
    assert step["result_bytes"] is None


@pytest.mark.anyio
async def test_publishing_without_a_loop_never_raises(db, monkeypatch):
    """The SDK, the CLI and every test write to this store with no app running. A
    store that raised there would make the module unusable outside the server."""
    stream.reset()
    run_id = db.start_run("d")
    db.append_step(run_id, StepWrite(kind="action", name="t"))
    db.finish_run(run_id)
    assert db.get_run(run_id).status == "complete"


@pytest.mark.anyio
async def test_a_broken_socket_never_reaches_the_writer(db, monkeypatch):
    """Observation must not break the thing it observes — the rule the recorder is
    written around, applied one layer down."""
    import backend.modules.ws as ws_mod

    async def boom(*a, **kw):
        raise RuntimeError("socket is gone")

    monkeypatch.setattr(ws_mod, "broadcast_event", boom)
    stream.init_loop()
    try:
        run_id = db.start_run("d")
        db.append_step(run_id, StepWrite(kind="action", name="t"))
        await _settle()
        assert db.get_run(run_id).steps == 1
    finally:
        stream.reset()


def test_a_step_reaches_a_real_socket(monkeypatch):
    """End to end over the actual `/ws` handler, not the publish function alone.

    The unit tests above stub `broadcast_event`, so they would still pass if the
    channel were never reachable from a browser — a wrong envelope, a handler that
    drops unknown channels, or a loop that was never captured all look identical from
    inside `stream.py`. This drives the real app: real lifespan, real socket, real
    fan-out.
    """
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.telemetry.recorder import recorder as telemetry_recorder
    from backend.modules.trajectories.models import StepWrite

    store._initialized.clear()

    # `with TestClient(...)` runs the lifespan, which is what calls `init_loop` — the
    # single line whose absence would silently disable the whole channel.
    # The telemetry recorder is a process-global 500-event ring that outlives a test,
    # and `push_telemetry` sends its whole backlog on connect. Run in a full suite,
    # that backlog arrives ahead of anything this test publishes and drowns it — which
    # is a fact about the shared recorder, not about this channel.
    telemetry_recorder.clear()

    with TestClient(app) as client:
        store.create_dataset("live", "Live")
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["channel"] == "system"  # hello

            run_id = store.start_run("live", goal="watch me")
            store.append_step(run_id, StepWrite(kind="action", name="open_pane"))
            store.finish_run(run_id)

            seen = []
            # The socket is shared, so other channels interleave. Take frames until
            # ours have arrived rather than assuming we are the only publisher.
            for _ in range(200):
                msg = ws.receive_json()
                if msg.get("channel") == "trajectories":
                    seen.append(msg["event"])
                    if seen == ["run", "step", "seal"]:
                        break

    assert seen == ["run", "step", "seal"]
