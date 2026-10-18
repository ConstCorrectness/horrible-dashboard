"""The AFK timer (`afk.py`): server-authoritative, and blind to keep-alives.

The clock is accrued from the `elapsed` the tick loop hands `simulate`, so the
fake clock here is just the number passed in — no sleeping, no monkeypatched
`time`. Async cases use `asyncio.run` inside a sync test, the repo convention.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from typing import Any

import pytest

from backend.modules.hassault import afk, channel, chat
from backend.modules.hassault.afk import AfkPolicy
from backend.modules.hassault.match import Command, MatchPlayer, MatchRoom, MatchServer
from backend.modules.hassault.modes import defuse
from backend.modules.hassault.modes.deathmatch import Deathmatch
from backend.modules.hassault.physics import flat_world

WARN = 90.0
KICK = 120.0


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x, self.y, self.z, self.yaw, self.attr2 = x, y, 0.0, 0.0, team


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]


class PausableMode(Deathmatch):
    """Deathmatch with a switch on the AFK clock, standing in for freeze time."""

    def __init__(self) -> None:
        super().__init__()
        self.clock_runs = True

    def afk_clock_runs(self, room: MatchRoom, player: MatchPlayer) -> bool:
        return self.clock_runs


@pytest.fixture(autouse=True)
def fresh_chat_limiter(monkeypatch):
    monkeypatch.setattr(chat, "limiter", chat.RateLimiter())


def make_room(mode=None) -> MatchRoom:
    room = MatchRoom(
        "afk",
        "testmap",
        flat_world(32, floor=0, ceil=16),
        [Spawn(8, 8, 0), Spawn(20, 20, 1)],
        mode=mode,
        record=False,
    )
    room.afk = AfkPolicy(warn_after=WARN, kick_after=KICK)
    return room


def seated(room: MatchRoom, server: MatchServer, name: str = "idler"):
    """A human with a socket, registered with the server the way `join` does."""
    conn = FakeConn()
    player = room.add(name, conn)
    server.rooms[room.id] = room
    server.membership[id(conn)] = (room.id, player.id)
    return player, conn


def idle_command(seq: int, yaw: float = 0.0, pitch: float = 0.0, **kw: Any) -> Command:
    """A well-formed frame with every key up: what a keep-alive looks like."""
    return Command(
        seq=seq,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=yaw,
        pitch=pitch,
        dt=1 / 60,
        **kw,
    )


def advance(room: MatchRoom, seconds: float, step: float = 1.0) -> None:
    """Tick the room for `seconds` of fake time in `step`-second ticks."""
    for _ in range(int(round(seconds / step))):
        room.simulate(step)


def run_ticks(room: MatchRoom, server: MatchServer, seconds: float, step: float = 1.0):
    """`advance`, plus what the tick loop does with the room's AFK events."""

    async def go() -> None:
        for _ in range(int(round(seconds / step))):
            room.simulate(step)
            await server._process_afk(room)

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Warning and removal
# ---------------------------------------------------------------------------


def test_an_idle_player_is_warned_then_removed():
    server = MatchServer()
    room = make_room()
    player, conn = seated(room, server)
    other, other_conn = seated(room, server, "watcher")

    # Keep the watcher active so only the idler times out.
    seq = 0

    async def go() -> None:
        nonlocal seq
        for _ in range(int(WARN) - 1):
            seq += 1
            room.enqueue(other, Command(seq, 1.0, 0.0, False, 0.0, 0.0, 1 / 60))
            room.simulate(1.0)
            await server._process_afk(room)

    asyncio.run(go())
    assert conn.events("afk_warning") == []
    assert player.id in room.players

    run_ticks(room, server, 1.0)
    warning = conn.events("afk_warning")
    assert len(warning) == 1
    assert warning[0]["kickIn"] == pytest.approx(KICK - WARN)
    lines = [c for c in conn.events("chat") if c["senderName"] == afk.SENDER]
    assert lines and "AFK" in lines[0]["text"]
    # Private: the watcher is not told somebody else is being warned.
    assert other_conn.events("afk_warning") == []

    run_ticks(room, server, KICK - WARN)
    assert player.id not in room.players
    assert conn.events("afk_kicked")
    assert id(conn) not in server.membership
    # The room hears about it, in chat and as an ordinary departure.
    assert any("removed" in c["text"] for c in other_conn.events("chat"))
    assert {"room": room.id, "playerId": player.id} in other_conn.events("left")


def test_the_warning_shows_a_countdown_in_the_private_view():
    room = make_room()
    player = room.add("idler", None)
    assert "afk" not in room.private_view_for(player)

    advance(room, WARN)
    assert room.private_view_for(player)["afk"]["kickIn"] == pytest.approx(KICK - WARN)
    advance(room, 10.0)
    assert room.private_view_for(player)["afk"]["kickIn"] == pytest.approx(
        KICK - WARN - 10
    )


def test_a_warning_always_comes_before_the_removal_even_after_a_stall():
    """One stalled tick jumping straight past the kick threshold still warns,
    and the whole grace window follows."""
    room = make_room()
    player = room.add("idler", None)
    room.simulate(KICK * 3)
    assert [k for k, _ in room.afk_events] == ["warn"]
    assert player.afk_idle == pytest.approx(WARN)

    room.afk_events.clear()
    advance(room, KICK - WARN - 1)
    assert room.afk_events == []
    advance(room, 1)
    assert [k for k, _ in room.afk_events] == ["kick"]


def test_a_removal_is_queued_once():
    room = make_room()
    room.add("idler", None)
    advance(room, KICK + 30)
    assert [k for k, _ in room.afk_events] == ["warn", "kick"]


# ---------------------------------------------------------------------------
# What counts as activity
# ---------------------------------------------------------------------------


def test_keep_alive_commands_do_not_reset_the_clock():
    """A client streaming empty frames — every key up, the view still — is idle,
    however many of them it sends."""
    room = make_room()
    player = room.add("idler", None)
    seq = 0
    for _ in range(int(WARN)):
        for _ in range(30):
            seq += 1
            room.enqueue(player, idle_command(seq))
        room.simulate(1.0)
    assert player.afk_warned
    assert player.high_seq == seq  # they were all accepted, and none counted


def test_sub_threshold_aim_jitter_is_not_aiming():
    room = make_room()
    player = room.add("idler", None)
    seq = 0
    for i in range(int(WARN)):
        seq += 1
        # Alternating by a hundredth of the threshold never accumulates to it
        # within one tick, and resets nothing.
        jitter = afk.AIM_TRAVEL / 400 * (1 if i % 2 else -1)
        room.enqueue(player, idle_command(seq, yaw=jitter))
        room.simulate(1.0)
    assert player.afk_idle >= WARN - 1


def test_pings_and_rtt_do_not_count(monkeypatch):
    """The `/ws` ping and the rtt on an input batch are network, not play."""
    monkeypatch.setattr(channel, "_signed_in_username", lambda: "alice")
    room = make_room()
    conn = FakeConn()
    player = room.add("alice", conn)
    monkeypatch.setattr(channel.match_server, "player_for", lambda c: (room, player))
    advance(room, 50)

    async def chatter() -> None:
        await channel.handle(conn, {"event": "ping", "data": {"t": 1}})
        await channel.handle(
            conn, {"event": "input", "data": {"commands": [], "rtt": 40}}
        )

    asyncio.run(chatter())
    room.simulate(0.0)
    assert player.afk_idle == pytest.approx(50)


@pytest.mark.parametrize(
    "field",
    [
        {"forward": 1.0},
        {"strafe": -1.0},
        {"jump": True},
        {"fire": True},
        {"alt_fire": True},
        {"reload": True},
        {"throw": True, "nade": 0},
        {"use": True},
        {"drop": True},
        {"buy": 2},
        {"sell": 1},
        {"weapon": 0},
    ],
    ids=lambda f: next(iter(f)),
)
def test_real_input_resets_the_clock_and_clears_the_warning(field):
    room = make_room()
    player = room.add("p", None)
    player.weapon = 2  # so `weapon: 0` is a switch
    advance(room, WARN + 5)
    assert player.afk_warned

    base = {"seq": 1, "forward": 0.0, "strafe": 0.0, "jump": False, "yaw": 0.0}
    room.enqueue(player, Command(**{**base, "pitch": 0.0, "dt": 1 / 60, **field}))
    room.simulate(0.0)
    assert player.afk_idle == 0.0
    assert not player.afk_warned
    assert "afk" not in room.private_view_for(player)


def test_holding_crouch_is_not_activity_but_toggling_it_is():
    room = make_room()
    player = room.add("p", None)
    room.enqueue(player, idle_command(1, crouch=True))
    room.simulate(0.0)
    advance(room, 30)
    for seq in range(2, 40):
        room.enqueue(player, idle_command(seq, crouch=True))
        room.simulate(1.0)
    assert player.afk_idle >= 30 + 38 - 1

    room.enqueue(player, idle_command(99, crouch=False))
    room.simulate(0.0)
    assert player.afk_idle == 0.0


def test_aiming_counts_and_a_yaw_wrap_is_a_small_turn():
    room = make_room()
    player = room.add("p", None)
    room.enqueue(player, idle_command(1, yaw=2 * math.pi - 0.0001))
    room.simulate(0.0)
    advance(room, 20)
    # Across the wrap by a hair: not a full circle of aim.
    room.enqueue(player, idle_command(2, yaw=0.0001))
    room.simulate(0.0)
    assert player.afk_idle == pytest.approx(20)
    # A real turn.
    room.enqueue(player, idle_command(3, yaw=0.3))
    room.simulate(0.0)
    assert player.afk_idle == 0.0


def test_a_respawn_facing_does_not_read_as_aiming():
    """Every defuse round respawns everybody; if the jump from the client's old
    view to the spawn's counted as a turn, nobody would ever time out."""
    room = make_room()
    player = room.add("p", None)
    room.enqueue(player, idle_command(1, yaw=1.0))
    room.simulate(0.0)
    advance(room, 20)
    room.respawn(player)
    room.enqueue(player, idle_command(2, yaw=2.5))
    room.simulate(0.0)
    assert player.afk_idle == pytest.approx(20)


def test_chat_counts_but_only_when_it_was_posted():
    room = make_room()
    server = MatchServer()
    player, _ = seated(room, server)
    advance(room, WARN)
    assert player.afk_warned

    refused = asyncio.run(chat.post(room, player, "   ", False, server=server))
    assert refused == "empty"
    assert player.afk_warned

    assert asyncio.run(chat.post(room, player, "brb", False, server=server)) is None
    assert player.afk_idle == 0.0
    assert not player.afk_warned


# ---------------------------------------------------------------------------
# When the clock does not run
# ---------------------------------------------------------------------------


def test_bots_are_exempt():
    from backend.modules.hassault import bots

    room = make_room()
    bot = bots.add_bots(room, 1, bots.DEFAULT_SKILL)[0]
    bot.brain.think = lambda *a, **k: None  # type: ignore[method-assign]
    advance(room, KICK * 2)
    assert bot.afk_idle == 0.0
    assert room.afk_events == []


def test_dead_time_is_paused_not_reset():
    room = make_room()
    player = room.add("p", None)
    advance(room, 40)
    player.alive = False
    player.respawn_at = float("inf")
    advance(room, KICK * 2)
    assert player.afk_idle == pytest.approx(40)
    player.alive = True
    advance(room, 10)
    assert player.afk_idle == pytest.approx(50)


def test_the_mode_can_stop_the_clock():
    mode = PausableMode()
    room = make_room(mode)
    player = room.add("p", None)
    advance(room, 30)
    mode.clock_runs = False
    advance(room, KICK * 2)
    assert player.afk_idle == pytest.approx(30)
    assert room.afk_events == []


@pytest.mark.parametrize(
    ("phase", "runs"),
    [
        (defuse.WARMUP, True),
        (defuse.FREEZE, False),
        (defuse.LIVE, True),
        (defuse.POST, False),
        (defuse.OVER, False),
    ],
)
def test_defuse_runs_the_clock_only_while_there_is_something_to_do(phase, runs):
    mode = defuse.Defuse()
    mode.state = replace(mode.state, phase=phase)
    assert mode.afk_clock_runs(None, None) is runs  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Policy and the hosts' removal paths
# ---------------------------------------------------------------------------


def test_policy_defaults_and_env_overrides(monkeypatch):
    monkeypatch.delenv("HASSAULT_AFK_WARN", raising=False)
    monkeypatch.delenv("HASSAULT_AFK_KICK", raising=False)
    assert AfkPolicy.from_env() == AfkPolicy(afk.WARN_AFTER, afk.KICK_AFTER)

    monkeypatch.setenv("HASSAULT_AFK_WARN", "300")
    monkeypatch.setenv("HASSAULT_AFK_KICK", "60")
    assert AfkPolicy.from_env() == AfkPolicy(60.0, 60.0)  # warns no later than it kicks

    monkeypatch.setenv("HASSAULT_AFK_KICK", "nonsense")
    assert AfkPolicy.from_env().kick_after == afk.KICK_AFTER

    monkeypatch.setenv("HASSAULT_AFK_KICK", "0")
    room = make_room()
    room.afk = AfkPolicy.from_env()
    player = room.add("p", None)
    advance(room, 10_000, step=100)
    assert player.afk_idle == 0.0 and room.afk_events == []


def test_removal_goes_through_the_hosts_kick_handler():
    server = MatchServer()
    room = make_room()
    player, conn = seated(room, server)
    kicked: list[Any] = []

    async def handler(c: Any) -> None:
        kicked.append(c)
        await server.leave(c)

    server.kick_handler = handler
    run_ticks(room, server, KICK + 1)
    assert kicked == [conn]
    assert player.id not in room.players


def test_the_node_channel_records_the_removed_players_result(monkeypatch):
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(channel, "_record_result", recorded.append)
    assert channel.match_server.kick_handler is channel._afk_kick

    server = channel.match_server
    room = make_room()
    player, conn = seated(room, server)
    try:
        asyncio.run(channel._afk_kick(conn))
    finally:
        server.rooms.pop(room.id, None)
        server.membership.pop(id(conn), None)
    assert player.id not in room.players
    assert [r["name"] for r in recorded] == ["idler"]


def test_the_game_server_referee_sends_the_result_back():
    from backend.games_server import hassault_rooms

    referee = hassault_rooms.HassaultReferee()
    assert referee.server.kick_handler == referee._kick
    conn = FakeConn()

    async def leave(c: Any) -> dict[str, Any]:
        return {"room": "r", "kills": 3}

    referee.leave = leave  # type: ignore[method-assign]
    asyncio.run(referee._kick(conn))  # type: ignore[arg-type]
    assert conn.events("result") == [{"room": "r", "kills": 3}]
