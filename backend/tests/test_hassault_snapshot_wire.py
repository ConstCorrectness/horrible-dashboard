"""The snapshot wire after G3: the roster sent on change, sends that do not wait
on each other, and a tick clock that cannot step backwards.

Each is opt-in or invisible to an older client, and these tests pin both halves:
what a client that declared `roster` gets, and that one which did not still gets
the full rows it can read.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from backend.modules.hassault import match
from backend.modules.hassault.match import (
    CAP_ROSTER,
    ROSTER_FIELDS,
    MatchRoom,
    MatchServer,
    parse_caps,
    wire_clock,
)
from backend.modules.hassault.physics import flat_world


@pytest.fixture(autouse=True)
def unobserved(monkeypatch):
    """No `/ws` observer, so the pre-serialised path is the one under test."""
    from backend.modules import ws

    monkeypatch.setattr(ws, "_send_observer", None)


class Spawn:
    """The fields `physics.spawn_at` reads off a map entity."""

    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = 0.0
        self.yaw = 0.0
        self.attr2 = team


class TextConn:
    """A socket that takes pre-serialised frames, optionally declaring caps."""

    def __init__(self, caps: frozenset[str] = frozenset()) -> None:
        self.wire_caps = caps
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def snapshots(self) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == "snapshot"]


class StuckConn(TextConn):
    """A socket whose writes never finish until `release` is set."""

    def __init__(self, caps: frozenset[str] = frozenset()) -> None:
        super().__init__(caps)
        self.release = asyncio.Event()
        self.started = 0

    async def send_text(self, text: str) -> None:
        self.started += 1
        await self.release.wait()
        await super().send_text(text)


def make_room() -> MatchRoom:
    world = flat_world(32, floor=0, ceil=16)
    return MatchRoom(
        "r1", "testmap", world, [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    )


def broadcast(server: MatchServer, room: MatchRoom) -> None:
    asyncio.run(server._broadcast(room))


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------


def test_parse_caps_keeps_only_what_the_server_knows():
    assert parse_caps({"caps": ["roster", "telepathy", 7]}) == {CAP_ROSTER}
    assert parse_caps({"caps": "roster"}) == frozenset()
    assert parse_caps({}) == frozenset()


def test_a_client_without_caps_still_gets_full_rows():
    server = MatchServer()
    room = make_room()
    conn = TextConn()
    room.add("alice", conn)
    broadcast(server, room)
    snap = conn.snapshots()[0]
    assert "rv" not in snap and "roster" not in snap
    for field in ROSTER_FIELDS:
        assert field in snap["players"][0], field


def test_the_roster_is_sent_on_change_and_rows_go_slim():
    server = MatchServer()
    room = make_room()
    conn = TextConn(frozenset({CAP_ROSTER}))
    alice = room.add("alice", conn)
    room.add("bob", None)

    broadcast(server, room)
    first = conn.snapshots()[-1]
    assert [r["name"] for r in first["roster"]] == ["alice", "bob"]
    for row in first["players"]:
        assert not set(ROSTER_FIELDS) & set(row), row
        assert {"id", "x", "y", "z", "hp", "alive"} <= set(row)

    # Unchanged: the version only.
    broadcast(server, room)
    second = conn.snapshots()[-1]
    assert second["rv"] == first["rv"]
    assert "roster" not in second

    # A kill is a roster change.
    alice.kills += 1
    broadcast(server, room)
    third = conn.snapshots()[-1]
    assert third["rv"] == first["rv"] + 1
    assert third["roster"][0]["kills"] == 1


def test_a_late_joiner_gets_the_roster_even_when_it_did_not_change_for_others():
    server = MatchServer()
    room = make_room()
    first = TextConn(frozenset({CAP_ROSTER}))
    room.add("alice", first)
    broadcast(server, room)
    # Their roster version is current; a second socket on the same version
    # (as after a reconnect) must still be sent it once.
    second = TextConn(frozenset({CAP_ROSTER}))
    room.add("bob", second)
    broadcast(server, room)
    assert "roster" in second.snapshots()[0]


def test_slim_rows_plus_roster_rebuild_the_full_rows():
    """What a client merges must be exactly what an old client is sent."""
    server = MatchServer()
    room = make_room()
    full = TextConn()
    slim = TextConn(frozenset({CAP_ROSTER}))
    room.add("alice", full)
    room.add("bob", slim)
    room.add("carol", None)
    broadcast(server, room)
    roster = {r["id"]: r for r in slim.snapshots()[0]["roster"]}
    merged = [{**row, **roster[row["id"]]} for row in slim.snapshots()[0]["players"]]
    expected = full.snapshots()[0]["players"]
    assert [dict(sorted(r.items())) for r in merged] == [
        dict(sorted(r.items())) for r in expected
    ]


def test_the_slim_path_saves_bytes():
    room = make_room()
    for name in ("alice", "bob", "carol", "dave"):
        room.add(name, None)
    rows = [p.snapshot(0.0) for p in room.players.values()]
    full = len(json.dumps(rows))
    slim = len(json.dumps([match._slim(r) for r in rows]))
    assert slim < full * 0.8


# ---------------------------------------------------------------------------
# Sends that do not wait on each other
# ---------------------------------------------------------------------------


def test_a_stuck_socket_does_not_hold_up_the_room():
    async def go():
        server = MatchServer()
        room = make_room()
        stuck = StuckConn()
        fine = TextConn()
        room.add("slow", stuck)
        room.add("fast", fine)

        started = time.perf_counter()
        await server._broadcast(room)
        # Waited for at most half a tick, not for the stuck socket.
        assert time.perf_counter() - started < match.TICK_INTERVAL
        assert len(fine.snapshots()) == 1

        # Next tick: the stuck one is skipped, the other is not.
        await server._broadcast(room)
        assert len(fine.snapshots()) == 2
        assert stuck.started == 1
        assert room.stats.skipped_sends == 1
        assert room.stats.report()["skippedSends"] == 1

        stuck.release.set()
        await asyncio.sleep(0)
        await server._broadcast(room)
        assert stuck.started == 2

    asyncio.run(go())


def test_a_skipped_player_keeps_their_hitmarkers_for_the_next_frame():
    async def go():
        server = MatchServer()
        room = make_room()
        stuck = StuckConn()
        player = room.add("slow", stuck)
        await server._broadcast(room)  # in flight, never finishing yet
        player.pending_hits.append({"target": "x", "damage": 10})
        await server._broadcast(room)  # skipped
        assert player.pending_hits, "a skipped tick must not drain the hitmarkers"
        stuck.release.set()
        await asyncio.sleep(0)
        await server._broadcast(room)
        stuck.release.set()
        await asyncio.sleep(0)
        assert stuck.snapshots()[-1]["you"]["hits"] == [{"target": "x", "damage": 10}]

    asyncio.run(go())


def test_events_and_snapshots_do_not_interleave_on_a_game_server_seat():
    """`SeatConn` serialises its writes, now that snapshots are tasks."""
    from backend.games_server.hassault_rooms import SeatConn

    class Socket:
        def __init__(self) -> None:
            self.active = 0
            self.overlapped = False
            self.frames: list[str] = []

        async def send_text(self, text: str) -> None:
            self.active += 1
            self.overlapped |= self.active > 1
            await asyncio.sleep(0.001)
            self.frames.append(text)
            self.active -= 1

    async def go():
        sock = Socket()
        seat = SeatConn(sock, "acc", "alice")
        await asyncio.gather(
            seat.send_text("a"), seat.send_json({"b": 1}), seat.send_text("c")
        )
        assert not sock.overlapped
        assert len(sock.frames) == 3

    asyncio.run(go())


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def test_the_wire_clock_is_epoch_time_that_never_steps_back(monkeypatch):
    assert abs(wire_clock() - time.time()) < 1.0
    before = wire_clock()
    # A wall-clock correction mid-match does not move it.
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 3600)
    assert wire_clock() >= before


def test_snapshot_t_is_on_the_wire_clock(monkeypatch):
    monkeypatch.setattr(match, "wire_clock", lambda: 1234.5)
    server = MatchServer()
    room = make_room()
    conn = TextConn()
    room.add("alice", conn)
    broadcast(server, room)
    assert conn.snapshots()[0]["t"] == 1234500
