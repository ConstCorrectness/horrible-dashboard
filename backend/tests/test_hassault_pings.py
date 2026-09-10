"""Tests for the tactical ping & callout system in HorribleAssault."""

from __future__ import annotations

import time
import pytest

from backend.modules.hassault.match import (
    Command,
    MatchRoom,
    Ping,
    parse_command,
)
from backend.modules.hassault.modes.deathmatch import Deathmatch
from backend.modules.hassault.modes.defuse import Defuse
from backend.modules.hassault.physics import flat_world


class Spawn:
    def __init__(self, x: float, y: float, z: float = 0.0, yaw: float = 0.0, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.attr2 = team


def make_room(mode=None, room_id="test_room") -> MatchRoom:
    world = flat_world(32, floor=0, ceil=16)
    spawns = [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    return MatchRoom(room_id, "testmap", world, spawns, mode=mode)


def test_parse_command_ping():
    # Valid ping
    cmd = parse_command({
        "seq": 1,
        "forward": 1.0,
        "strafe": 0.0,
        "jump": False,
        "crouch": False,
        "yaw": 0.0,
        "pitch": 0.0,
        "dt": 0.016,
        "ping": {
            "kind": "spotted",
            "x": 12.5,
            "y": -8.0,
            "z": 1.2,
        },
    })
    assert cmd is not None
    assert cmd.ping == (12.5, -8.0, 1.2, "spotted")

    # Invalid ping kind ignored
    cmd2 = parse_command({
        "seq": 2,
        "forward": 0.0,
        "strafe": 0.0,
        "jump": False,
        "crouch": False,
        "yaw": 0.0,
        "pitch": 0.0,
        "dt": 0.016,
        "ping": {
            "kind": "illegal_cheat_kind",
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
        },
    })
    assert cmd2 is not None
    assert cmd2.ping is None

    # Out of bounds coordinates ignored
    cmd3 = parse_command({
        "seq": 3,
        "forward": 0.0,
        "strafe": 0.0,
        "jump": False,
        "crouch": False,
        "yaw": 0.0,
        "pitch": 0.0,
        "dt": 0.016,
        "ping": {
            "kind": "danger",
            "x": 9999.0,
            "y": 0.0,
            "z": 0.0,
        },
    })
    assert cmd3 is not None
    assert cmd3.ping is None


def test_ping_rate_limit_and_cap():
    room = make_room(mode=Deathmatch(teams=True))
    p1 = room.add("Alpha", None, team=0)

    # 1st ping succeeds
    cmd1 = Command(seq=1, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(10.0, 20.0, 0.0, "spotted"))
    room.enqueue(p1, cmd1)
    room.simulate(0.05)
    assert len(room.pings) == 1
    assert room.pings[0].kind == "spotted"
    assert room.pings[0].owner == p1.id
    assert room.pings[0].owner_name == "Alpha"

    # Immediate second ping within 0.8s is rate-limited
    cmd2 = Command(seq=2, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(12.0, 22.0, 0.0, "danger"))
    room.enqueue(p1, cmd2)
    room.simulate(0.05)
    assert len(room.pings) == 1  # unchanged

    # Advance time by 0.85s, ping 2 succeeds
    p1.last_ping_at -= 0.85
    cmd3 = Command(seq=3, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(14.0, 24.0, 0.0, "watch"))
    room.enqueue(p1, cmd3)
    room.simulate(0.05)
    assert len(room.pings) == 2

    # Advance time, ping 3 succeeds (reaching cap of 3)
    p1.last_ping_at -= 0.85
    cmd4 = Command(seq=4, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(16.0, 26.0, 0.0, "utility"))
    room.enqueue(p1, cmd4)
    room.simulate(0.05)
    assert len(room.pings) == 3

    # Advance time, ping 4 succeeds and removes oldest ping (cap remains 3)
    p1.last_ping_at -= 0.85
    cmd5 = Command(seq=5, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(18.0, 28.0, 0.0, "spotted"))
    room.enqueue(p1, cmd5)
    room.simulate(0.05)
    assert len(room.pings) == 3
    # The oldest (cmd1, spotted at 10, 20) was evicted, so kinds are watch, utility, spotted
    assert [p.kind for p in room.pings] == ["watch", "utility", "spotted"]


def test_ping_ttl_pruning():
    room = make_room(mode=Deathmatch(teams=True))
    p1 = room.add("Alpha", None, team=0)

    cmd = Command(seq=1, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(10.0, 20.0, 0.0, "spotted"))
    room.enqueue(p1, cmd)
    room.simulate(0.05)
    assert len(room.pings) == 1

    # Before TTL expires (e.g. 3s later)
    now = time.monotonic()
    room._step_pings(now + 3.0)
    assert len(room.pings) == 1

    # After TTL expires (> 5s later)
    room._step_pings(now + 6.0)
    assert len(room.pings) == 0


def test_ping_team_privacy():
    room = make_room(mode=Defuse())
    p_team0 = room.add("CT_Player", None, team=0)
    p_team0_mate = room.add("CT_Mate", None, team=0)
    p_team1 = room.add("T_Player", None, team=1)

    # CT_Player places a ping
    cmd = Command(seq=1, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.05, ping=(5.0, 5.0, 0.0, "danger"))
    room.enqueue(p_team0, cmd)
    room.simulate(0.05)
    assert len(room.pings) == 1

    # Check private views
    view_owner = room.private_view_for(p_team0)
    assert len(view_owner["pings"]) == 1
    assert view_owner["pings"][0]["kind"] == "danger"
    assert view_owner["pings"][0]["owner"] == p_team0.id

    view_mate = room.private_view_for(p_team0_mate)
    assert len(view_mate["pings"]) == 1
    assert view_mate["pings"][0]["kind"] == "danger"

    # Opponent (team 1) MUST NOT receive team 0's ping!
    view_enemy = room.private_view_for(p_team1)
    assert len(view_enemy["pings"]) == 0
