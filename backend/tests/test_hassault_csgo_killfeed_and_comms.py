from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.hassault import channel, grenades, match, weapons
from backend.modules.hassault.match import MatchRoom, MatchServer, PseudoWeapon, _segment_intersects_zone
from backend.modules.hassault.physics import flat_world


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class Spawn:
    def __init__(self, x: float, y: float, z: float = 0.0, yaw: float = 0.0, team: int = 0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.attr2 = team


def make_room(room_id: str = "r1") -> MatchRoom:
    world = flat_world(32, floor=0, ceil=16)
    spawns = [Spawn(8, 8, team=0), Spawn(20, 20, team=1)]
    return MatchRoom(room_id, "testmap", world, spawns)


def test_segment_intersects_zone():
    zone = grenades.Zone(
        id="z1",
        kind="smoke",
        owner="p1",
        team=0,
        x=10.0,
        y=10.0,
        z=5.0,
        radius=3.0,
        remaining=15.0,
        duration=15.0,
        damage_per_second=0.0,
    )
    assert _segment_intersects_zone((0.0, 10.0, 5.0), (20.0, 10.0, 5.0), zone)
    assert _segment_intersects_zone((0.0, 11.5, 5.0), (20.0, 11.5, 5.0), zone)
    assert not _segment_intersects_zone((0.0, 15.0, 5.0), (20.0, 15.0, 5.0), zone)


def test_kill_event_includes_csgo_metadata():
    room = make_room()
    killer = room.add("killer", None)
    victim = room.add("victim", None)
    killer.team = 0
    victim.team = 1
    killer.state.on_ground = False

    weapon = weapons.weapon_at(2)
    now = 100.0
    victim.health = 10.0

    room._apply_damage(
        victim=victim,
        attacker=killer,
        amount=50.0,
        head=True,
        weapon=weapon,
        now=now,
        nutshot=False,
        through_smoke=True,
        airborne=True,
    )

    assert not victim.alive
    kill_fx = [fx for fx in room.fx if fx.get("kind") == "kill"]
    assert len(kill_fx) == 1
    event = kill_fx[0]
    assert event["killer"] == killer.id
    assert event["victim"] == victim.id
    assert event["head"] is True
    assert event["nutshot"] is False
    assert event["smoke"] is True
    assert event["airborne"] is True
    assert event["killerTeam"] == 0
    assert event["victimTeam"] == 1


def test_kill_event_with_assist_and_cs2_modifiers():
    room = make_room()
    killer = room.add("killer", None)
    assister = room.add("assister", None)
    victim = room.add("victim", None)
    killer.team = 0
    assister.team = 0
    victim.team = 1

    weapon = weapons.weapon_at(2)
    now = 200.0

    # Assister deals 45 damage 2 seconds earlier
    room._apply_damage(
        victim=victim,
        attacker=assister,
        amount=45.0,
        head=False,
        weapon=weapon,
        now=now - 2.0,
    )
    assert victim.alive
    assert victim.health == 55.0

    # Killer finishes victim with noscope, wallbang, and blind
    room._apply_damage(
        victim=victim,
        attacker=killer,
        amount=60.0,
        head=True,
        weapon=weapon,
        now=now,
        nutshot=False,
        through_smoke=False,
        airborne=False,
        wallbang=True,
        noscope=True,
        blind=True,
    )

    assert not victim.alive
    kill_fx = [fx for fx in room.fx if fx.get("kind") == "kill"]
    assert len(kill_fx) == 1
    event = kill_fx[0]
    assert event["killer"] == killer.id
    assert event["victim"] == victim.id
    assert event["assister"] == assister.id
    assert event["assisterName"] == assister.name
    assert event["assisterTeam"] == 0
    assert event["wallbang"] is True
    assert event["noscope"] is True
    assert event["blind"] is True
    assert assister.assists == 1


def test_chat_and_voice_routing(monkeypatch):
    server = MatchServer()
    room = make_room()
    server.rooms[room.id] = room
    monkeypatch.setattr(channel, "match_server", server)

    conn_t1 = FakeConn()
    conn_t2 = FakeConn()
    conn_ct = FakeConn()

    p_t1 = room.add("t1", conn_t1)
    p_t2 = room.add("t2", conn_t2)
    p_ct = room.add("ct", conn_ct)
    p_t1.team = 0
    p_t2.team = 0
    p_ct.team = 1

    server.membership[id(conn_t1)] = (room.id, p_t1.id)
    server.membership[id(conn_t2)] = (room.id, p_t2.id)
    server.membership[id(conn_ct)] = (room.id, p_ct.id)

    async def run_chat_and_voice():
        await channel.handle(
            conn_t1,
            {"event": "chat", "data": {"text": "gg wp", "team": False}},
        )
        await channel.handle(
            conn_t1,
            {"event": "chat", "data": {"text": "rush B", "team": True}},
        )
        await channel.handle(
            conn_t1,
            {"event": "voice", "data": {"transmitting": True}},
        )

    asyncio.run(run_chat_and_voice())

    all_chat_ct = [m for m in conn_ct.sent if m.get("event") == "chat" and not m["data"]["isTeam"]]
    assert len(all_chat_ct) == 1
    assert all_chat_ct[0]["data"]["text"] == "gg wp"

    team_chat_ct = [m for m in conn_ct.sent if m.get("event") == "chat" and m["data"]["isTeam"]]
    assert len(team_chat_ct) == 0
    team_chat_t2 = [m for m in conn_t2.sent if m.get("event") == "chat" and m["data"]["isTeam"]]
    assert len(team_chat_t2) == 1
    assert team_chat_t2[0]["data"]["text"] == "rush B"

    voice_ct = [m for m in conn_ct.sent if m.get("event") == "voice"]
    assert len(voice_ct) == 0
    voice_t1 = [m for m in conn_t1.sent if m.get("event") == "voice"]
    assert len(voice_t1) == 0
    voice_t2 = [m for m in conn_t2.sent if m.get("event") == "voice"]
    assert len(voice_t2) == 1
    assert voice_t2[0]["data"]["transmitting"] is True
    assert voice_t2[0]["data"]["playerId"] == p_t1.id


def test_knife_backstab_detection_and_kill_event():
    room = make_room()
    killer = room.add("killer", None)
    victim = room.add("victim", None)
    killer.team = 0
    victim.team = 1

    killer.protected_until = 0.0
    victim.protected_until = 0.0

    # Position: killer behind victim.
    # Killer at (10, 10), looking in +X direction (yaw = 0)
    killer.state.x = 10.0
    killer.state.y = 10.0
    killer.state.z = 0.0
    killer.state.yaw = 0.0

    # Victim in front of killer at (11.0, 10.0), also looking in +X direction (yaw = 0)
    # So killer is behind victim looking at victim's back!
    victim.state.x = 11.0
    victim.state.y = 10.0
    victim.state.z = 0.0
    victim.state.yaw = 0.0
    victim.health = 100.0

    killer.weapon = 0  # Knife
    cmd = match.Command(
        seq=1,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=0.0,
        pitch=0.0,
        dt=0.016,
        fire=True,
        alt_fire=True,
        view_t=100.0,
    )

    room._fire(killer, cmd, now=100.0, now_ms=100000.0)

    # Backstab should deal 150 damage, eliminating full-health victim instantly
    assert not victim.alive
    assert victim.health == 0
    kill_events = [fx for fx in room.fx if fx.get("kind") == "kill"]
    assert len(kill_events) == 1
    ev = kill_events[0]
    assert ev["killer"] == killer.id
    assert ev["victim"] == victim.id
    assert ev["weapon"] == "knife"
    assert ev["backstab"] is True


def test_knife_slash_and_front_stab_damage():
    room = make_room()
    killer = room.add("killer", None)
    victim = room.add("victim", None)
    killer.team = 0
    victim.team = 1
    killer.protected_until = 0.0
    victim.protected_until = 0.0

    # Facing each other (killer looking +X yaw=0, victim looking -X yaw=pi)
    killer.state.x = 10.0
    killer.state.y = 10.0
    killer.state.z = 0.0
    killer.state.yaw = 0.0

    victim.state.x = 11.0
    victim.state.y = 10.0
    victim.state.z = 0.0
    victim.state.yaw = 3.14159
    victim.health = 100.0

    killer.weapon = 0  # Knife

    # Quick slash: deals 45 damage, non-lethal
    cmd_slash = match.Command(
        seq=1,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=0.0,
        pitch=0.0,
        dt=0.016,
        fire=True,
        alt_fire=False,
        view_t=100.0,
    )
    room._fire(killer, cmd_slash, now=100.0, now_ms=100000.0)
    assert victim.alive
    assert victim.health == 55.0

    # Advance time beyond fire interval
    killer.sim_time = 102.0
    # Heavy frontal stab: deals 70 damage (lethal here since health is 55.0)
    cmd_stab = match.Command(
        seq=2,
        forward=0.0,
        strafe=0.0,
        jump=False,
        yaw=0.0,
        pitch=0.0,
        dt=0.016,
        fire=True,
        alt_fire=True,
        view_t=102.0,
    )
    room._fire(killer, cmd_stab, now=102.0, now_ms=102000.0)
    assert not victim.alive
    assert victim.health == 0
    kill_events = [fx for fx in room.fx if fx.get("kind") == "kill"]
    assert len(kill_events) == 1
    ev = kill_events[0]
    # Not a backstab because they were facing each other!
    assert ev["backstab"] is False


