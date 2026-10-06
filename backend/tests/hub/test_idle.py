"""Idle handling: an open tab is not use. Only input (POST /hub/activity) keeps an
instance running; the reaper spares one that is in the middle of work; and a
stopped instance stays stopped until its user touches something."""

from __future__ import annotations

import asyncio
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.tests.hub.conftest import MachineSpawner, sign_in

ALICE = "acc-alice"


@pytest.fixture
def setup(make_hub, upstream):
    def _make(**overrides):
        spawner = MachineSpawner(upstream.url)
        client = make_hub(spawner=spawner, idle_minutes=1, **overrides)
        sign_in(client)
        hub = client.app.state.hub
        # The post-sign-in warm-up starts the machine; let it finish.
        deadline = time.monotonic() + 10
        while (hub._tasks or ALICE not in hub._ready) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert spawner.up and ALICE in hub._ready
        return client, hub, spawner

    return _make


def go_idle(hub, seconds: float = 120) -> None:
    hub._last_seen[ALICE] -= seconds


def reap(client, hub) -> None:
    client.portal.call(hub.reap_idle)


def test_traffic_from_an_open_tab_is_not_activity(setup) -> None:
    client, hub, spawner = setup()
    go_idle(hub)
    before = hub._last_seen[ALICE]
    # What an unattended tab sends: health polls, a socket.
    for _ in range(3):
        assert client.get("/api/health").status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
    assert hub._last_seen[ALICE] == before
    assert hub.idle_users() == [ALICE]


def test_an_idle_instance_is_stopped_even_with_its_tab_open(setup) -> None:
    client, hub, spawner = setup()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        go_idle(hub)
        reap(client, hub)
    assert spawner.stops == 1 and not spawner.up
    assert ALICE not in hub._ready


def test_a_parked_tab_does_not_wake_its_machine(setup) -> None:
    client, hub, spawner = setup()
    go_idle(hub)
    reap(client, hub)
    ensures = spawner.ensures
    res = client.get("/api/health")
    assert res.status_code == 503 and res.json() == {"parked": True}
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert exc.value.code == 4408
    assert spawner.ensures == ensures and not spawner.up


def test_input_wakes_a_parked_machine(setup) -> None:
    client, hub, spawner = setup()
    go_idle(hub)
    reap(client, hub)
    res = client.post("/hub/activity")
    assert res.json() == {"resumed": True}
    deadline = time.monotonic() + 10
    while ALICE not in hub._ready and time.monotonic() < deadline:
        time.sleep(0.05)
    assert spawner.up
    assert client.get("/api/health").status_code == 200
    # Input while up just keeps it up.
    assert client.post("/hub/activity").json() == {"resumed": False}
    assert hub.recently_active(ALICE)


def test_a_busy_instance_is_kept_until_its_work_ends(setup, upstream) -> None:
    client, hub, spawner = setup()
    upstream.seen["busy"] = ["training run"]
    go_idle(hub)
    reap(client, hub)
    assert spawner.stops == 0 and spawner.up
    upstream.seen["busy"] = []
    reap(client, hub)
    assert spawner.stops == 1


def test_busy_does_not_keep_it_up_past_the_cap(setup, upstream) -> None:
    client, hub, spawner = setup(busy_max_hours=1)
    upstream.seen["busy"] = ["kernel executing"]
    go_idle(hub)
    reap(client, hub)
    assert spawner.up
    hub._busy_since[ALICE] -= 3601
    reap(client, hub)
    assert spawner.stops == 1


def test_input_resets_the_busy_clock(setup, upstream) -> None:
    client, hub, spawner = setup(busy_max_hours=1)
    upstream.seen["busy"] = ["agent turn"]
    go_idle(hub)
    reap(client, hub)
    assert ALICE in hub._busy_since
    client.post("/hub/activity")
    assert ALICE not in hub._busy_since


def test_a_hub_restart_still_reaps_and_still_serves(setup) -> None:
    """A machine left running when the hub restarted is neither orphaned (never
    reaped) nor reported parked to the tab that is using it."""
    client, hub, spawner = setup()
    # The real spawners record each user's instance in the store; the fake does not.
    hub.store.set_instance_token(ALICE, "instance-secret")
    # The hub restarts: in-memory state is gone, the machine is not.
    hub._last_seen.clear()
    hub.mark_unready(ALICE)
    hub.seed_known_instances()
    assert ALICE in hub._seeded and not hub.recently_active(ALICE)
    # The open tab's next poll reaches the running machine without waking anything.
    ensures = spawner.ensures
    assert client.get("/api/health").status_code == 200
    assert spawner.ensures == ensures
    # Within the first idle window it is left alone...
    reap(client, hub)
    assert spawner.stops == 0
    # ...and when nobody returns, one window later it is stopped.
    hub._seeded[ALICE] -= 120
    reap(client, hub)
    assert spawner.stops == 1


def test_a_hub_restart_does_not_wake_parked_machines(setup) -> None:
    client, hub, spawner = setup()
    go_idle(hub)
    reap(client, hub)
    assert not spawner.up
    hub.store.set_instance_token(ALICE, "instance-secret")
    # Restart, with this user's tab still open and polling.
    hub._last_seen.clear()
    hub._parked_checked.clear()
    hub.seed_known_instances()
    ensures = spawner.ensures
    for _ in range(3):
        assert client.get("/api/health").json() == {"parked": True}
    assert spawner.ensures == ensures and not spawner.up


def test_parked_checks_are_rate_limited(setup) -> None:
    client, hub, spawner = setup()
    go_idle(hub)
    reap(client, hub)
    calls = 0
    real_running = spawner.running

    async def counting(user):
        nonlocal calls
        calls += 1
        return await real_running(user)

    spawner.running = counting
    for _ in range(5):
        assert client.get("/api/health").status_code == 503
    assert calls == 1


def test_activity_requires_a_session(make_hub) -> None:
    client = make_hub()
    assert client.post("/hub/activity").status_code == 401
    asyncio.run(asyncio.sleep(0))
