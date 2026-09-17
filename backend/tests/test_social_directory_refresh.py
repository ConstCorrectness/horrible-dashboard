"""A running node keeps its presence record current.

`lookup` treats a record older than `TTL_SECONDS` as stale, and the record used to
be published exactly once, at startup — so any node up for more than fifteen minutes
was invisible to `roster._dial`, and friend reconnects through the directory failed
silently. The collection here is a fake; nothing reaches Atlas (see
test_atlas_isolation.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.social import directory


class FakeCollection:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.writes: list[dict[str, Any]] = []
        self.fail_next = 0

    async def update_one(self, query, update, upsert=False):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("cluster unreachable")
        record = dict(update["$set"])
        self.records[query["person_id"]] = record
        self.writes.append(record)

    async def find_one(self, query):
        return self.records.get(query["person_id"])


@pytest.fixture()
def world(monkeypatch):
    from backend import atlas
    from backend.modules.network import ice

    fake = FakeCollection()
    hosts = ["ws://10.0.0.18:8100/peer-ws"]
    monkeypatch.setattr(atlas, "collection", lambda name: fake)
    monkeypatch.setattr(atlas, "is_configured", lambda: True)
    monkeypatch.setattr(ice, "host_candidates", lambda: list(hosts))

    async def gather() -> list[str]:
        return list(hosts)

    monkeypatch.setattr(ice, "gather_candidates", gather)
    monkeypatch.setattr(directory, "_last_published", None)
    monkeypatch.setattr(directory, "_last_hosts", None)
    monkeypatch.setattr(directory, "_refresh_task", None)
    # No relay transport unless a test adds one: the process-wide hub can carry
    # transports from an earlier test that booted the app.
    from backend.modules.network.hub import peer_hub

    monkeypatch.setattr(peer_hub, "transports", [])
    return fake, hosts


async def _run_loop_for(seconds: float, **intervals: float) -> asyncio.Task[None]:
    task = asyncio.create_task(directory.refresh_loop(**intervals))
    await asyncio.sleep(seconds)
    return task


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_the_refresh_interval_leaves_room_for_a_missed_round():
    assert directory.REFRESH_SECONDS * 2 < directory.TTL_SECONDS


def test_a_running_node_republishes_before_its_record_goes_stale(world):
    fake, hosts = world

    async def go():
        assert await directory.publish()
        task = await _run_loop_for(0.35, refresh_seconds=0.05, check_seconds=0.01)
        assert not task.done()
        await _stop(task)

    asyncio.run(go())
    stamps = [w["updated_at"] for w in fake.writes]
    assert len(stamps) >= 4, stamps
    assert stamps == sorted(stamps)
    # Refreshed well inside the TTL, scaled: no gap longer than a couple of intervals.
    assert max(b - a for a, b in zip(stamps, stamps[1:])) < 0.3
    assert asyncio.run(directory.lookup(fake.writes[-1]["person_id"])) == hosts


def test_a_failed_publish_does_not_end_the_loop(world, monkeypatch):
    fake, _hosts = world
    fake.fail_next = 3
    probe_failures = {"n": 1}
    from backend.modules.network import ice

    real_hosts = ice.host_candidates

    def flaky_hosts():
        if probe_failures["n"]:
            probe_failures["n"] -= 1
            raise OSError("interface enumeration failed")
        return real_hosts()

    monkeypatch.setattr(ice, "host_candidates", flaky_hosts)

    async def go():
        task = await _run_loop_for(0.3, refresh_seconds=0.02, check_seconds=0.01)
        alive = not task.done()
        await _stop(task)
        return alive

    assert asyncio.run(go()) is True
    assert fake.fail_next == 0, "the failing rounds were never retried"
    assert fake.writes, "the loop gave up after the cluster failed"


def test_an_unexpected_exception_in_a_round_does_not_end_the_loop(world, monkeypatch):
    """`publish` is written never to raise — but the loop must not depend on that. A
    loop that dies on its first surprise is the original bug again, only later."""
    fake, _hosts = world
    real_publish = directory.publish
    calls = {"n": 0}

    async def surprising() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a bug nobody anticipated")
        return await real_publish()

    monkeypatch.setattr(directory, "publish", surprising)

    async def go():
        task = await _run_loop_for(0.2, refresh_seconds=0.02, check_seconds=0.01)
        alive = not task.done()
        await _stop(task)
        return alive

    assert asyncio.run(go()) is True
    assert calls["n"] >= 2 and fake.writes


def test_a_failed_startup_publish_is_retried_on_the_first_check(world):
    fake, _hosts = world
    fake.fail_next = 1

    async def go():
        assert await directory.publish() is False
        # A full refresh is an hour away; only the retry can publish in time.
        task = await _run_loop_for(0.1, refresh_seconds=3600, check_seconds=0.01)
        await _stop(task)

    asyncio.run(go())
    assert len(fake.writes) == 1


def test_changed_local_addresses_republish_early(world):
    fake, hosts = world

    async def go():
        assert await directory.publish()
        task = asyncio.create_task(
            directory.refresh_loop(refresh_seconds=3600, check_seconds=0.01)
        )
        await asyncio.sleep(0.05)
        assert len(fake.writes) == 1  # nothing due, nothing changed
        hosts.append("ws://100.115.105.123:8100/peer-ws")  # Tailscale came up
        await asyncio.sleep(0.1)
        await _stop(task)

    asyncio.run(go())
    assert len(fake.writes) == 2
    assert "ws://100.115.105.123:8100/peer-ws" in fake.writes[-1]["addresses"]


def test_no_cluster_no_loop(monkeypatch):
    from backend import atlas

    monkeypatch.setattr(atlas, "is_configured", lambda: False)
    monkeypatch.setattr(directory, "_refresh_task", None)

    async def go():
        return directory.start_refresh()

    assert asyncio.run(go()) is False
    assert directory._refresh_task is None


def test_start_is_idempotent_and_stop_cancels(world):
    async def go():
        assert directory.start_refresh()
        first = directory._refresh_task
        assert directory.start_refresh()
        assert directory._refresh_task is first
        await directory.stop_refresh()
        assert first.cancelled() or first.done()
        assert directory._refresh_task is None
        await directory.stop_refresh()  # stopping twice is fine

    asyncio.run(go())
