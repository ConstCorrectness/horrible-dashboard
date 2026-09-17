"""Durable agent I/O.

Everything pinned here fails *quietly* if it breaks: a table that silently keeps the
wrong rows, a key that silently collides across restarts, a header that silently
reaches disk with a token in it, or a run deleted from one table and left behind in
another. None of them raise, and none are visible without looking.
"""

from __future__ import annotations

import time

import pytest

from backend.modules.telemetry import drain, store
from backend.modules.telemetry.models import IoEvent
from backend.modules.telemetry.recorder import Recorder


@pytest.fixture()
def db():
    store._initialized.clear()
    store.init_telemetry_db()
    return store


def _event(**over) -> IoEvent:
    body = dict(
        id=1,
        ts=time.time(),
        source="outbound",
        method="POST",
        target="http://localhost:11434/api/chat",
        status=200,
        duration_ms=120.0,
        turn_id="t1",
        round=0,
    )
    body.update(over)
    return IoEvent(**body)


# ------------------------------------------------------------------ what is kept


def test_an_event_with_no_turn_is_not_stored(db):
    """The drain's whole filter. A node's background polling is not part of any
    agent postmortem, and a row that joins to nothing is noise in a table whose only
    purpose is the join."""
    assert db.persist([_event(turn_id=None)]) == 0
    assert db.count() == 0


def test_a_turns_events_come_back_in_time_order(db):
    now = time.time()
    db.persist(
        [
            _event(id=2, ts=now + 1, target="second"),
            _event(id=1, ts=now, target="first"),
        ]
    )
    assert [e["target"] for e in db.for_turn("t1")] == ["first", "second"]


def test_events_can_be_read_back_by_round(db):
    """`IoEvent.round` already existed, which is what lets the waterfall drop each
    request into the correct round band with no correlation work."""
    db.persist([_event(id=1, round=0), _event(id=2, round=1)])
    assert len(db.for_turn("t1", round_no=1)) == 1
    assert db.for_turn("t1", round_no=1)[0]["round"] == 1


def test_timing_survives_the_round_trip_as_a_dict(db):
    """The phase breakdown is what makes a waterfall possible; stored as JSON, it
    has to come back as numbers rather than a string nobody parses."""
    db.persist([_event(timing={"dns": 4.0, "connect": 12.5})])
    assert db.for_turn("t1")[0]["timing"] == {"dns": 4.0, "connect": 12.5}


# ------------------------------------------------------------------------- keys


def test_amend_updates_its_row_instead_of_colliding(db):
    """`Recorder.amend` re-emits under the *same* id to backfill a streamed body, so
    `record` and its amendment routinely land in one batch. `INSERT OR REPLACE` on
    `(boot_id, ev_id)` makes that idempotent with no dedupe logic anywhere."""
    db.persist([_event(id=7, status=None)])
    db.persist([_event(id=7, status=200, response_bytes=4096)])
    rows = db.for_turn("t1")
    assert len(rows) == 1
    assert rows[0]["status"] == 200
    assert rows[0]["response_bytes"] == 4096


def test_the_same_event_id_from_another_boot_is_a_different_row(db, monkeypatch):
    """`IoEvent.id` restarts at 1 every process. Without `boot_id` in the key, the
    first event after a restart would overwrite the first event before it — losing
    a row and doing it silently."""
    db.persist([_event(id=1, target="before-restart")])
    monkeypatch.setattr(db, "BOOT_ID", "second-boot")
    db.persist([_event(id=1, target="after-restart")])

    targets = {e["target"] for e in db.for_turn("t1")}
    assert targets == {"before-restart", "after-restart"}


# -------------------------------------------------------------------- redaction


def test_a_secret_shaped_header_never_reaches_disk(db):
    """Matched on the name's shape, not on a declaration — the same rule the settings
    blanking follows, and for the same reason."""
    event = _event(
        request_headers={"x-api-key": "sk-secret", "Accept": "application/json"}
    )
    db.persist([event])
    headers = db.for_turn("t1")[0]["detail"]["request_headers"]
    assert "sk-secret" not in str(headers)
    assert headers["Accept"] == "application/json"


@pytest.mark.parametrize(
    "header",
    ["Authorization", "Cookie", "Proxy-Authorization", "X-Auth-Token"],
)
def test_auth_headers_the_settings_shape_match_misses_are_still_blanked(db, header):
    """The gap that made `SENSITIVE_HEADER_PARTS` necessary.

    `trajectories.store.redact` matches `SECRET_KEY_SUFFIXES`, a dotted settings-key
    vocabulary (`github.token`). It matches neither `x-api-key` (no `.key`) nor
    `Authorization` — so the most common ways to send a credential would have gone to
    disk in full, with nothing to indicate it.
    """
    db.persist([_event(request_headers={header: "Bearer sk-live-do-not-log"})])
    stored = db.for_turn("t1")[0]["detail"]["request_headers"]
    assert "sk-live-do-not-log" not in str(stored)
    # Blanked, not dropped: "this request carried credentials" is worth knowing.
    assert header in stored


def test_a_blanked_header_is_still_listed(db):
    db.persist([_event(request_headers={"Authorization": "Bearer x"})])
    assert (
        db.for_turn("t1")[0]["detail"]["request_headers"]["Authorization"]
        == store.HEADER_REDACTED
    )


def test_bodies_are_not_stored_unless_asked_for(db, monkeypatch):
    """A body is opaque text redaction cannot help. Default off."""
    monkeypatch.setattr(db, "_setting", lambda key, default: default)
    db.persist([_event(request_body='{"prompt": "my private notes"}')])
    detail = db.for_turn("t1")[0]["detail"]
    assert detail is None or "request_body" not in detail


def test_an_opted_in_body_is_stored_and_capped(db, monkeypatch):
    monkeypatch.setattr(
        db,
        "_setting",
        lambda key, default: True if key == "telemetry.persistBodies" else default,
    )
    db.persist([_event(request_body="x" * (store.BODY_MAX * 3))])
    stored = db.for_turn("t1")[0]["detail"]["request_body"]
    assert len(stored) == store.BODY_MAX


# -------------------------------------------------------------------- retention


def test_prune_drops_events_past_the_age_limit(db, monkeypatch):
    monkeypatch.setattr(
        db,
        "_setting",
        lambda key, default: 1 if key == "telemetry.retentionDays" else default,
    )
    db.persist([_event(id=1, ts=time.time() - 86400 * 3), _event(id=2, ts=time.time())])
    assert db.prune() == 1
    assert db.count() == 1


def test_prune_enforces_the_hard_cap_oldest_first(db, monkeypatch):
    def setting(key, default):
        if key == "telemetry.retentionDays":
            return 0  # age limit off, so only the cap is under test
        if key == "telemetry.retentionEvents":
            return 2
        return default

    monkeypatch.setattr(db, "_setting", setting)
    now = time.time()
    db.persist([_event(id=i, ts=now + i, target=f"r{i}") for i in range(5)])
    db.prune()
    assert {e["target"] for e in db.for_turn("t1")} == {"r3", "r4"}


def test_deleting_a_run_takes_its_wire_traffic_with_it(db):
    """Otherwise the events sit unreachable until retention ages them out: nothing
    references them, and nothing says they are there."""
    from backend.modules.trajectories import store as traj

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d", turn_id="t1")
    db.persist([_event()])
    assert db.count() == 1

    traj.delete_run(run_id)
    assert db.count() == 0


# ------------------------------------------------------------------- the drain


@pytest.mark.anyio
async def test_the_drain_writes_only_turn_stamped_events():
    recorder = Recorder()
    store._initialized.clear()
    store.init_telemetry_db()
    drain.reset()

    import backend.modules.telemetry.drain as drain_mod

    original = drain_mod.recorder
    drain_mod.recorder = recorder
    try:
        drain.start()
        # Let the subscriber attach before anything is recorded, or the events go
        # nowhere — the same subscribe-before-you-produce ordering the trajectory
        # client follows.
        await _settle()
        recorder.record(source="outbound", method="GET", target="/no-turn")
        recorder.record(
            source="outbound", method="GET", target="/in-turn", turn_id="t9"
        )
        await _settle(rounds=30)
        await drain.stop()
    finally:
        drain_mod.recorder = original

    rows = store.for_turn("t9")
    assert [r["target"] for r in rows] == ["/in-turn"]


@pytest.mark.anyio
async def test_the_drain_flushes_what_it_holds_when_cancelled():
    """Those are the events of the turn that was very likely still running, which is
    exactly the one somebody will want to read."""
    recorder = Recorder()
    store._initialized.clear()
    store.init_telemetry_db()
    drain.reset()

    import backend.modules.telemetry.drain as drain_mod

    original = drain_mod.recorder
    drain_mod.recorder = recorder
    try:
        drain.start()
        await _settle()
        recorder.record(
            source="outbound", method="GET", target="/mid-turn", turn_id="t8"
        )
        # Stopped immediately, well inside the flush interval.
        await drain.stop()
    finally:
        drain_mod.recorder = original

    assert [r["target"] for r in store.for_turn("t8")] == ["/mid-turn"]


async def _settle(rounds: int = 5) -> None:
    import asyncio

    for _ in range(rounds):
        await asyncio.sleep(0)


# ------------------------------------------------------------------------ route


def test_run_io_route_joins_through_the_run(db):
    """The pane holds a run id, not a turn id. The route does the join so a schema
    detail stays out of the browser."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.trajectories import store as traj

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d", turn_id="t1")
    db.persist([_event(id=1, round=0), _event(id=2, round=1)])

    client = TestClient(app)
    body = client.get(f"/api/trajectories/runs/{run_id}/io").json()
    assert body["joinable"] is True
    assert len(body["events"]) == 2
    assert (
        len(client.get(f"/api/trajectories/runs/{run_id}/io?round=1").json()["events"])
        == 1
    )


def test_a_run_with_no_turn_is_unjoinable_not_empty(db):
    """An SDK or imported run has no `turn_id`. Returning `events: []` alone would
    read as "this run made no requests" — a claim we have no evidence for."""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.modules.trajectories import store as traj

    traj._initialized.clear()
    traj.init_trajectories_db()
    traj.create_dataset("d", "D")
    run_id = traj.start_run("d")

    body = TestClient(app).get(f"/api/trajectories/runs/{run_id}/io").json()
    assert body == {"events": [], "joinable": False}


def test_stats_route_reports_drops(db):
    from fastapi.testclient import TestClient

    from backend.app import app

    body = TestClient(app).get("/api/telemetry/stats").json()
    assert set(body) == {"written", "dropped", "stored"}


def test_the_drain_counts_an_id_gap_as_dropped():
    """A gap in `ev_id` means the ring's bounded queue discarded events before the
    drain saw them. That loss is reported, never hidden."""
    drain.reset()
    drain._note_gap(_event(id=3))
    drain._note_gap(_event(id=7))
    assert drain.stats()["dropped"] == 3
