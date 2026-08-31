"""The authoritative match server and its `/ws` channel.

Hermetic: every room here is built on a synthetic world, because AssaultCube
content is copyright and cannot live in this repo. `MatchRoom` takes a world and
its spawns rather than a parsed map for exactly that reason.

Async cases follow the repo convention of `asyncio.run` inside a sync test, since
there is no pytest-asyncio here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.modules.hassault import channel, grenades, match
from backend.modules.hassault.match import (
    BUDGET_CEILING,
    EMPTY_GRACE,
    MAX_QUEUED_COMMANDS,
    STALE_AFTER,
    Command,
    MatchRoom,
    MatchServer,
)
from backend.modules.hassault.noise import Noise
from backend.modules.hassault.physics import MOVE_SPEED, flat_world


@pytest.fixture
def signed_in(monkeypatch):
    """A player with an account. Joining is gated on one (see
    test_hassault_channel.py); these tests are about the wire path past that gate,
    so they stand an account up rather than exercise the refusal."""
    monkeypatch.setattr(channel, "_signed_in_username", lambda: "alice")


@pytest.fixture
def unobserved(monkeypatch):
    """No `/ws` observer for the duration of the test.

    `app.py` registers one at import, and importing the app anywhere in the
    session leaves it registered process-wide — so a test that asserts the
    pre-serialised send path is taken has to say so rather than inherit
    whichever modules happened to be imported first.
    """
    from backend.modules import ws

    monkeypatch.setattr(ws, "_send_observer", None)
    monkeypatch.setattr(ws, "_observer_wants", None)


class FakeConn:
    """Stands in for a `/ws` connection: records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]


class FakeTextConn(FakeConn):
    """A `FakeConn` that also takes pre-serialised frames.

    `_broadcast` picks its fast path on the presence of `send_text`, so the base
    `FakeConn` above exercises the `send_json` fallback and this one exercises
    the template. Both must produce the same frame — that is what
    `test_the_prebuilt_snapshot_is_byte_identical_to_the_dict_one` pins.
    """

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    async def send_text(self, text: str) -> None:
        self.texts.append(text)
        self.sent.append(json.loads(text))


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


def test_an_overflowed_command_is_acknowledged_rather_than_dropped_silently():
    """A dropped command the client keeps replaying is permanent rubber-banding.

    `ack` only moves when a command is *simulated*, so a command discarded by the
    overflow bound would never be acknowledged — and the client replays every
    unacknowledged command on top of each correction. Its prediction then sits
    permanently ahead of the server, which is the exact symptom the bound exists
    to prevent. Acknowledging the dropped one says "this is not coming back".
    """
    room = make_room()
    player = room.add("a", None)
    for seq in range(1, MAX_QUEUED_COMMANDS + 11):
        room.enqueue(player, walk(seq))
    assert len(player.queue) == MAX_QUEUED_COMMANDS
    # Ten were dropped from the front, so the client must be told the tenth is
    # done with — and nothing beyond it, which is still queued and still real.
    assert player.ack == 10
    assert player.queue[0].seq == 11


def test_the_ack_never_goes_backwards_when_a_command_is_dropped():
    """Monotonic by construction, and the client relies on it: a snapshot whose
    `ack` moved backwards would resurrect commands already retired."""
    room = make_room()
    player = room.add("a", None)
    for seq in range(1, 6):
        room.enqueue(player, walk(seq))
    room.simulate(1.0)
    simulated = player.ack
    assert simulated == 5
    for seq in range(6, MAX_QUEUED_COMMANDS + 20):
        room.enqueue(player, walk(seq))
    assert player.ack >= simulated


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


def test_the_prebuilt_snapshot_is_byte_identical_to_the_dict_one():
    """The entire correctness argument for the pre-serialised broadcast path.

    `snapshot_template` exists only to move work, never to change the wire, so
    the bytes it assembles must equal `json.dumps` of the envelope
    `snapshot_for` builds — key order and separators included. Without this the
    fast path has no independent spec, and a reordered key would be a silent
    protocol change that only the Rust client would notice.

    Loaded deliberately: grenades in the air, a zone burning and effects in the
    tick, because those are the fields that used to be rebuilt per recipient.
    """
    room = make_room()
    a = room.add("a", None)
    b = room.add("b", None)
    a.ack = 7
    b.ack = 3
    room.scores = [4, 2]
    room.fx.append({"kind": "shot", "id": "a", "origin": [1, 2, 3], "ends": [4, 5, 6]})
    room.nades.append(
        grenades.Grenade(
            id="n1",
            spec=grenades.GRENADES[0],
            owner="a",
            team=0,
            x=1.0,
            y=2.0,
            z=3.0,
            vx=0.5,
            vy=0.5,
            vz=1.0,
            fuse=1.2,
        )
    )
    room.zones.append(
        grenades.Zone(
            id="z1",
            kind="smoke",
            owner="b",
            team=1,
            x=4.0,
            y=5.0,
            z=6.0,
            radius=7.0,
            remaining=2.0,
            duration=8.0,
            damage_per_second=0.0,
        )
    )
    rows = [p.snapshot(0.0) for p in room.players.values()]
    shared = room.shared_view()

    head, mid, tail = room.snapshot_template(0.0, rows, shared)
    for player in (a, b):
        # Built first: both calls drain the player's hitmarkers, so the two
        # sides have to be compared against the *same* drain.
        you = room.private_view_for(player)
        expected = json.dumps(room.snapshot_message(0.0, rows, shared, player.ack, you))
        assert f"{head}{player.ack}{mid}{json.dumps(you)}{tail}" == expected


def test_broadcast_sends_the_same_frame_over_text_and_json(signed_in, unobserved):
    """The two send paths are chosen per connection, so a room can hold both."""
    server = MatchServer()
    room = make_room()
    server.rooms[room.id] = room
    text_conn = FakeTextConn()
    json_conn = FakeConn()
    room.add("a", text_conn)
    room.add("b", json_conn)

    asyncio.run(server._broadcast(room))

    assert text_conn.texts, "the text conn should have taken the fast path"
    sent_text = text_conn.events("snapshot")[0]
    sent_json = json_conn.events("snapshot")[0]
    # `ack` and `you` are per recipient; everything else is the shared body and
    # must match exactly across the two paths.
    for key in ("room", "tick", "t", "players", "scores", "nades", "zones", "fx"):
        assert sent_text[key] == sent_json[key], key


def test_broadcast_clears_the_tick_it_just_sent(signed_in, unobserved):
    """`fx` and `noises` are drained once everyone has their copy.

    A regression test with a specific history: refactoring the send path left
    these two lines stranded after a `return` in a helper, so nothing cleared
    them and every shot in a match accumulated in the packet forever. No
    existing test noticed, because a single-tick test never sees the second
    tick — hence this one, which broadcasts twice.
    """
    server = MatchServer()
    room = make_room()
    server.rooms[room.id] = room
    conn = FakeTextConn()
    room.add("a", conn)
    room.fx.append({"kind": "shot", "id": "a"})
    room.noises.append(
        Noise(kind="step", source="a", x=1.0, y=2.0, z=3.0, loudness=20.0)
    )

    asyncio.run(server._broadcast(room))
    assert room.fx == []
    assert room.noises == []

    # And the next tick carries none of the previous one's.
    asyncio.run(server._broadcast(room))
    assert conn.events("snapshot")[1]["fx"] == []


def test_tick_stats_report_nothing_before_the_first_tick():
    """A room that has not ticked and a room whose ticks are free are different
    facts, so the window reports `None` rather than `0`."""
    room = make_room()
    report = room.stats.report()
    assert report["simulateMs"] == {"mean": None, "max": None, "samples": 0}
    assert report["budgetMs"] == pytest.approx(50.0)

    room.stats.record(2.0, 8.0)
    room.stats.record(4.0, 12.0)
    assert room.stats.report()["broadcastMs"] == {
        "mean": 10.0,
        "max": 12.0,
        "samples": 2,
    }


def test_broadcast_falls_back_to_json_while_the_observer_is_watching(
    signed_in, monkeypatch
):
    """The observability panel sees a dict or the fast path does not run.

    A pre-serialised frame has nothing to hand `set_ws_send_observer`, so a
    registered observer disables the template rather than being shown a
    reconstruction of what went out.
    """
    from backend.modules import ws

    server = MatchServer()
    room = make_room()
    server.rooms[room.id] = room
    conn = FakeTextConn()
    room.add("a", conn)

    # An observer that wants everything, which is what `is_observed` assumes
    # when none was declared.
    monkeypatch.setattr(ws, "_send_observer", lambda direction, data: None)
    monkeypatch.setattr(ws, "_observer_wants", None)
    asyncio.run(server._broadcast(room))

    assert not conn.texts, "an observer must force the dict path"
    assert conn.events("snapshot")


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


def test_command_axes_are_clamped():
    """The obvious cheat is asking to move fifty times as fast."""
    parsed = match.parse_command({"seq": 1, "forward": 50, "strafe": -50, "dt": 1 / 60})
    assert parsed is not None
    assert parsed.forward == 1.0
    assert parsed.strafe == -1.0


def test_command_dt_is_clamped():
    parsed = match.parse_command({"seq": 1, "forward": 1, "dt": 99})
    assert parsed is not None
    assert parsed.dt == 0.25


def test_nan_and_infinity_are_rejected():
    """Both survive JSON and poison every comparison downstream, so they are
    caught here rather than at the first surprising position."""
    parsed = match.parse_command(
        {"seq": 1, "forward": float("nan"), "dt": float("inf")}
    )
    assert parsed is not None
    assert parsed.forward == 0.0
    assert parsed.dt == 0.0


def test_a_command_without_a_sequence_number_is_dropped():
    assert match.parse_command({"forward": 1, "dt": 0.016}) is None
    assert match.parse_command({"seq": 0, "dt": 0.016}) is None
    assert match.parse_command("not a dict") is None


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


# ---------------------------------------------------------------------------
# The spray pattern's index
# ---------------------------------------------------------------------------


def _rifleman(room):
    """A player holding the one weapon that has a recoil pattern, with time to
    spend and no spawn shield to forfeit."""
    from backend.modules.hassault import weapons

    player = room.add("Shooter", None)
    player.state.x, player.state.y, player.state.z = 8.0, 8.0, 0.0
    player.state.on_ground = True
    player.weapon = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["assault"])
    player.ammo[player.weapon] = 20
    player.budget = 10.0
    return player


def _shot(seq: int, dt: float) -> Command:
    return Command(
        seq=seq,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=0.0,
        pitch=0.0,
        dt=dt,
        fire=True,
    )


def _spend(room, player, seconds: float, seq: int) -> int:
    """Advance a player's simulated clock by `seconds`, doing nothing else.

    A helper rather than one long command, because two limits bite otherwise and
    both are silent: `dt` is clamped to `physics.MAX_STEP_DT` per command, and a
    tick only hands out `BUDGET_CEILING` of time however much a test assigns.
    Returns the next sequence number.
    """
    from backend.modules.hassault import physics

    left = seconds
    while left > 1e-9:
        step = min(left, physics.MAX_STEP_DT)
        left -= step
        room.enqueue(player, walk(seq, dt=step, forward=0.0))
        seq += 1
        # One command per tick, so the budget is replenished between them.
        room.simulate(step)
    return seq


def test_two_commands_drained_in_one_tick_do_not_reset_the_spray():
    """**The wall-clock trap, directly.**

    `simulate` drains a whole queue per tick, so two commands sent 20ms apart can
    be simulated microseconds apart in real time. A reset gated on
    `time.monotonic()` would see no gap between them, decide the burst had ended,
    and pin every shot at pattern index 0 — a rifle with no recoil at all, and
    nothing anywhere saying why.
    """
    from backend.modules.hassault import weapons

    room = make_room()
    player = _rifleman(room)
    interval = weapons.WEAPON_BY_ID["assault"].interval

    # Two trigger pulls, one tick, spaced by exactly the fire interval in
    # *simulated* time.
    room.enqueue(player, _shot(1, interval))
    room.enqueue(player, _shot(2, interval))
    room.simulate(1 / 60)

    assert player.spray_index == 2, "the burst restarted inside a single tick"


def test_letting_go_long_enough_puts_you_back_at_the_top_of_the_pattern():
    """The trade the whole mechanic is made of: hold it and the pattern climbs,
    let go and you get the first shot back."""
    from backend.modules.hassault import weapons

    room = make_room()
    player = _rifleman(room)
    rifle = weapons.WEAPON_BY_ID["assault"]

    room.enqueue(player, _shot(1, rifle.interval))
    room.enqueue(player, _shot(2, rifle.interval))
    room.simulate(1 / 60)
    assert player.spray_index == 2

    # A gap longer than the reset, spent walking rather than shooting.
    seq = _spend(room, player, rifle.spray_reset + 0.05, seq=3)
    room.enqueue(player, _shot(seq, rifle.interval))
    room.simulate(1 / 60)
    assert player.spray_index == 1, "the pattern did not restart after the gap"


def test_a_refused_trigger_pull_does_not_walk_the_pattern():
    """Every early return in `_fire` is a pull that produced nothing. Counting
    those would advance the pattern for a player who never fired — and the client
    adopts this number, so its camera would kick for a bullet that never left."""
    room = make_room()
    player = _rifleman(room)
    player.ammo[player.weapon] = 0

    room.enqueue(player, _shot(1, 1 / 60))
    room.simulate(1 / 60)

    assert player.spray_index == 0


def test_switching_and_reloading_both_start_the_pattern_again():
    """A weapon you just drew must not fire from halfway down someone else's
    recoil curve, and a magazine change is the end of a burst by definition."""
    from backend.modules.hassault import weapons

    rifle_slot = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["assault"])
    pistol_slot = weapons.WEAPONS.index(weapons.WEAPON_BY_ID["pistol"])

    room = make_room()
    player = _rifleman(room)
    room.enqueue(player, _shot(1, 1 / 60))
    room.simulate(1 / 60)
    assert player.spray_index == 1

    switch = walk(2, dt=1 / 60)
    switch.weapon = pistol_slot
    room.enqueue(player, switch)
    room.simulate(1 / 60)
    assert player.spray_index == 0

    # Enough simulated time that the next pull clears the rate gate — otherwise
    # the shot below is refused and the test would be reading a spray index that
    # never moved for a reason unrelated to switching.
    seq = _spend(room, player, weapons.WEAPON_BY_ID["assault"].interval * 2, seq=3)
    back = walk(seq, dt=1 / 60)
    back.weapon = rifle_slot
    room.enqueue(player, back)
    room.enqueue(player, _shot(seq + 1, 1 / 60))
    room.simulate(1 / 60)
    assert player.spray_index == 1

    player.ammo[player.weapon] = 5
    reload_cmd = walk(seq + 2, dt=1 / 60)
    reload_cmd.reload = True
    room.enqueue(player, reload_cmd)
    room.simulate(1 / 60)
    assert player.spray_index == 0


def test_the_index_reaches_the_client_that_has_to_predict_it():
    """Echoed in the private view, so the client adopts it rather than keeping a
    count that can drift out of phase for the rest of a magazine."""
    room = make_room()
    player = _rifleman(room)
    room.enqueue(player, _shot(1, 1 / 60))
    room.simulate(1 / 60)

    view = player.private_view(0.0)
    assert view["sprayIndex"] == player.spray_index


def test_respawning_starts_the_pattern_again():
    room = make_room()
    player = _rifleman(room)
    room.enqueue(player, _shot(1, 1 / 60))
    room.simulate(1 / 60)
    assert player.spray_index == 1

    player.reset_loadout()
    assert player.spray_index == 0
