"""The authoritative match server and its `/ws` channel.

Hermetic: every room here is built on a synthetic world, because AssaultCube
content is copyright and cannot live in this repo. `MatchRoom` takes a world and
its spawns rather than a parsed map for exactly that reason.

Async cases follow the repo convention of `asyncio.run` inside a sync test, since
there is no pytest-asyncio here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.hassault import channel
from backend.modules.hassault.match import (
    BUDGET_CEILING,
    EMPTY_GRACE,
    MAX_QUEUED_COMMANDS,
    STALE_AFTER,
    Command,
    MatchRoom,
    MatchServer,
)
from backend.modules.hassault.physics import MOVE_SPEED, flat_world


@pytest.fixture
def signed_in(monkeypatch):
    """A player with an account. Joining is gated on one (see
    test_hassault_channel.py); these tests are about the wire path past that gate,
    so they stand an account up rather than exercise the refusal."""
    monkeypatch.setattr(channel, "_signed_in_callsign", lambda: "alice")


class FakeConn:
    """Stands in for a `/ws` connection: records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]


class Spawn:
    """The two fields `physics.spawn_at` reads off a map entity."""

    def __init__(
        self, x: float, y: float, z: float = 0.0, yaw: float = 0.0, team: int = 0
    ) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.attr2 = team


def make_room(room_id: str = "r1") -> MatchRoom:
    world = flat_world(32, floor=0, ceil=16)
    spawns = [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    return MatchRoom(room_id, "testmap", world, spawns)


def walk(seq: int, dt: float = 1 / 60, forward: float = 1.0) -> Command:
    return Command(
        seq=seq, forward=forward, strafe=0.0, jump=False, yaw=0.0, pitch=0.0, dt=dt
    )


# ---------------------------------------------------------------------------
# Rooms and membership
# ---------------------------------------------------------------------------


def test_teams_are_balanced_as_players_arrive():
    room = make_room()
    teams = [room.add(f"p{i}", None).team for i in range(4)]
    assert teams.count(0) == 2
    assert teams.count(1) == 2


def test_a_player_spawns_on_their_own_team_point():
    room = make_room()
    first = room.add("a", None)
    assert first.team == 0
    assert first.state.x == pytest.approx(8.5)  # spawn_at centres in the cell


def test_a_room_with_no_spawns_still_places_players():
    """Some community maps ship without spawn entities. Refusing to place anyone
    would turn that into an unjoinable match rather than a slightly odd one."""
    room = MatchRoom("r", "bare", flat_world(32), [])
    player = room.add("a", None)
    assert 0 < player.state.x < 32


def test_removing_the_last_player_starts_the_empty_clock():
    room = make_room()
    player = room.add("a", None)
    assert room.empty_since is None
    room.remove(player.id)
    assert room.empty_since is not None


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_duplicate_and_reordered_commands_are_ignored():
    """Normal on any real link, and the sequence number is what makes them cheap
    to drop — replaying one would move the player twice for one input."""
    room = make_room()
    player = room.add("a", None)
    room.enqueue(player, walk(1))
    room.enqueue(player, walk(2))
    room.enqueue(player, walk(1))  # duplicate
    room.enqueue(player, walk(2))  # reorder
    assert len(player.queue) == 2


def test_the_queue_is_bounded():
    """An unbounded queue turns a lagging client into unbounded memory."""
    room = make_room()
    player = room.add("a", None)
    for seq in range(1, MAX_QUEUED_COMMANDS + 50):
        room.enqueue(player, walk(seq))
    assert len(player.queue) == MAX_QUEUED_COMMANDS


def test_simulating_moves_the_player_and_advances_the_ack():
    room = make_room()
    player = room.add("a", None)
    start = player.state.x
    for seq in range(1, 11):
        room.enqueue(player, walk(seq))
    room.simulate(1.0)  # plenty of budget
    assert player.state.x > start
    assert player.ack == 10
    assert not player.queue


def test_the_ack_only_moves_for_commands_actually_simulated():
    room = make_room()
    player = room.add("a", None)
    for seq in range(1, 11):
        room.enqueue(player, walk(seq, dt=0.1))
    # One tick's worth of credit buys one 0.1 s command, not ten.
    room.simulate(0.1)
    assert player.ack == 1
    assert len(player.queue) == 9


def test_claimed_time_cannot_exceed_real_time_for_long():
    """The actual speed cap. A client is free to lie about `dt`; it is not free
    to spend more simulated seconds than have passed (plus a jitter allowance)."""
    room = make_room()
    player = room.add("a", None)
    start = player.state.x
    seq = 0
    # Two seconds of ticks, but a hundred seconds of claimed input.
    for _ in range(40):
        for _ in range(25):
            seq += 1
            room.enqueue(player, walk(seq, dt=0.1))
        room.simulate(0.05)
    travelled = player.state.x - start
    ceiling = MOVE_SPEED * (2.0 * 1.2 + BUDGET_CEILING)
    assert travelled <= ceiling


def test_budget_never_banks_more_than_the_ceiling():
    room = make_room()
    player = room.add("a", None)
    room.simulate(60.0)  # a very long stall
    assert player.budget <= BUDGET_CEILING


def test_respawn_clears_queued_commands():
    """They were predicted against the old position; simulating them after a
    teleport walks the player straight back off the spawn."""
    room = make_room()
    player = room.add("a", None)
    for seq in range(1, 6):
        room.enqueue(player, walk(seq))
    room.respawn(player)
    assert not player.queue


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_snapshot_rounds_positions():
    room = make_room()
    player = room.add("a", None)
    player.state.x = 1.23456789
    row = player.snapshot(0.0)
    assert row["x"] == pytest.approx(1.235)


def test_a_silent_player_is_marked_stale_not_removed():
    room = make_room()
    player = room.add("a", None)
    row = player.snapshot(player.last_command_at + STALE_AFTER + 1)
    assert row["stale"] is True


def test_each_player_gets_their_own_ack():
    room = make_room()
    a = room.add("a", None)
    b = room.add("b", None)
    a.ack = 7
    b.ack = 3
    rows = [p.snapshot(0.0) for p in room.players.values()]
    assert room.snapshot_for(a, 0.0, rows)["data"]["ack"] == 7
    assert room.snapshot_for(b, 0.0, rows)["data"]["ack"] == 3


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


def test_command_axes_are_clamped():
    """The obvious cheat is asking to move fifty times as fast."""
    parsed = channel._parse_command(
        {"seq": 1, "forward": 50, "strafe": -50, "dt": 1 / 60}
    )
    assert parsed is not None
    assert parsed.forward == 1.0
    assert parsed.strafe == -1.0


def test_command_dt_is_clamped():
    parsed = channel._parse_command({"seq": 1, "forward": 1, "dt": 99})
    assert parsed is not None
    assert parsed.dt == 0.25


def test_nan_and_infinity_are_rejected():
    """Both survive JSON and poison every comparison downstream, so they are
    caught here rather than at the first surprising position."""
    parsed = channel._parse_command(
        {"seq": 1, "forward": float("nan"), "dt": float("inf")}
    )
    assert parsed is not None
    assert parsed.forward == 0.0
    assert parsed.dt == 0.0


def test_a_command_without_a_sequence_number_is_dropped():
    assert channel._parse_command({"forward": 1, "dt": 0.016}) is None
    assert channel._parse_command({"seq": 0, "dt": 0.016}) is None
    assert channel._parse_command("not a dict") is None


def test_join_and_input_round_trip_over_the_channel(signed_in):
    """The real wire path: `join` then `input`, through `channel.handle`."""

    async def go():
        room = MatchRoom("r1", "testmap", flat_world(32), [Spawn(8, 8)])
        channel.match_server.rooms["r1"] = room
        conn = FakeConn()
        try:
            await channel.handle(
                conn,
                {
                    "event": "join",
                    "data": {"map": "testmap", "name": "alice", "room": "r1"},
                },
            )
            welcome = conn.events("welcome")
            assert len(welcome) == 1
            assert welcome[0]["map"] == "testmap"
            player_id = welcome[0]["playerId"]
            player = room.players[player_id]
            start_x = player.state.x

            await channel.handle(
                conn,
                {
                    "event": "input",
                    "data": {
                        "commands": [
                            {"seq": 1, "forward": 1, "dt": 1 / 60},
                            {"seq": 2, "forward": 1, "dt": 1 / 60},
                        ],
                        "rtt": 12,
                    },
                },
            )
            assert len(player.queue) == 2
            assert player.rtt_ms == pytest.approx(12)

            room.simulate(1.0)
            assert player.ack == 2
            assert player.state.x > start_x
        finally:
            await channel.match_server.shutdown()

    asyncio.run(go())


def test_joining_a_missing_room_sends_an_error_rather_than_raising(signed_in):
    async def go():
        conn = FakeConn()
        try:
            await channel.handle(
                conn, {"event": "join", "data": {"map": "testmap", "room": "nope"}}
            )
            errors = conn.events("error")
            assert errors and "nope" in errors[0]["message"]
        finally:
            await channel.match_server.shutdown()

    asyncio.run(go())


def test_input_from_a_socket_in_no_match_is_ignored():
    async def go():
        conn = FakeConn()
        await channel.handle(
            conn, {"event": "input", "data": {"commands": [{"seq": 1, "dt": 0.016}]}}
        )
        assert conn.sent == []

    asyncio.run(go())


def test_joining_an_unknown_room_reports_an_error():
    async def go():
        server = MatchServer()
        conn = FakeConn()
        with pytest.raises(LookupError):
            await server.join(conn, "testmap", "alice", "nope")

    asyncio.run(go())


def test_leaving_tells_the_others():
    async def go():
        server = MatchServer()
        room = MatchRoom("r1", "testmap", flat_world(32), [Spawn(8, 8)])
        server.rooms["r1"] = room
        a, b = FakeConn(), FakeConn()
        _, pa = await server.join(a, "testmap", "alice", "r1")
        await server.join(b, "testmap", "bob", "r1")

        await server.leave(a)
        left = b.events("left")
        assert left and left[0]["playerId"] == pa.id
        assert pa.id not in room.players
        await server.shutdown()

    asyncio.run(go())


def test_a_disconnect_removes_the_player():
    """A closed tab has to leave the match, not stand there forever."""

    async def go():
        server = MatchServer()
        room = MatchRoom("r1", "testmap", flat_world(32), [Spawn(8, 8)])
        server.rooms["r1"] = room
        conn = FakeConn()
        await server.join(conn, "testmap", "alice", "r1")
        assert len(room.players) == 1
        await server.leave(conn)
        assert not room.players
        await server.shutdown()

    asyncio.run(go())


def test_rejoining_replaces_the_previous_player():
    """One socket is one player. Joining twice without leaving would otherwise
    leave a body behind that nothing can ever remove."""

    async def go():
        server = MatchServer()
        room = MatchRoom("r1", "testmap", flat_world(32), [Spawn(8, 8)])
        server.rooms["r1"] = room
        conn = FakeConn()
        await server.join(conn, "testmap", "alice", "r1")
        await server.join(conn, "testmap", "alice", "r1")
        assert len(room.players) == 1
        await server.shutdown()

    asyncio.run(go())


def test_an_empty_room_is_kept_for_the_grace_period():
    """A room opened for a friend who has not clicked the invite yet is empty and
    must survive; one everyone has left should not."""
    room = make_room()
    assert room.empty_since is not None
    assert EMPTY_GRACE > 10
    player = room.add("a", None)
    assert room.empty_since is None
    room.remove(player.id)
    assert room.empty_since is not None


def test_ping_echoes_the_client_clock_untouched():
    async def go():
        conn = FakeConn()
        await channel.handle(
            conn, {"channel": "hassault", "event": "ping", "data": {"t": 12345}}
        )
        pong = conn.events("pong")
        assert pong and pong[0]["t"] == 12345
        assert pong[0]["serverT"] > 0

    asyncio.run(go())
