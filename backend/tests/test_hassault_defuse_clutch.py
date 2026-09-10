"""Competitive bomb defusal mechanics: defuse kits, drop/pickup, and clutch accolades."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from backend.modules.hassault import modes, physics, pickups, weapons
from backend.modules.hassault.match import Command, MatchRoom
from backend.modules.hassault.modes import objectives
from backend.modules.hassault.modes.defuse import (
    CATALOG,
    DEFUSE_TIME_KIT,
    DEFUSE_TIME_STANDARD,
    DEFUSER_PRICE,
    FUSE_TIME,
    LIVE,
    PLANT_TIME,
    POST,
    ROUNDS_TO_WIN,
    START_MONEY,
    Defuse,
    DroppedKit,
    Facts,
    RoundState,
    advance,
)
from backend.tests.test_hassault_defuse import defuse_room, go_live, hold_use


@pytest.fixture
def defuse_match():
    room = defuse_room()
    att1 = room.add("att1", None, team=0)
    att2 = room.add("att2", None, team=0)
    dfn1 = room.add("dfn1", None, team=1)
    dfn2 = room.add("dfn2", None, team=1)
    room.simulate(0.05)  # warmup -> round 1
    return room, att1, att2, dfn1, dfn2, room.mode


def test_defuse_kit_in_catalog():
    kit_item = next((item for item in CATALOG if item.id == "defuser"), None)
    assert kit_item is not None
    assert kit_item.price == DEFUSER_PRICE
    assert kit_item.kind == "kit"


def test_defender_can_buy_defuse_kit_attacker_cannot(defuse_match):
    room, att1, _att2, dfn1, _dfn2, mode = defuse_match
    assert mode.state.phase == "freeze"
    dfn1.money = 1000
    att1.money = 1000

    defuser_index = next(i for i, item in enumerate(CATALOG) if item.id == "defuser")

    # Attacker tries to buy defuser
    mode.on_command(
        room,
        att1,
        Command(seq=1, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.016, buy=defuser_index),
        time.monotonic(),
    )
    assert "defuser" not in att1.owned_extras
    assert att1.money == 1000

    # Defender buys defuser
    mode.on_command(
        room,
        dfn1,
        Command(seq=1, forward=0, strafe=0, jump=False, yaw=0, pitch=0, dt=0.016, buy=defuser_index),
        time.monotonic(),
    )
    assert "defuser" in dfn1.owned_extras
    assert dfn1.money == 1000 - DEFUSER_PRICE

    # Private state reflects kit
    priv = mode.private_state(room, dfn1)
    assert priv["hasKit"] is True
    assert priv["defuseTime"] == DEFUSE_TIME_KIT

    priv_att = mode.private_state(room, att1)
    assert priv_att["hasKit"] is False
    assert priv_att["defuseTime"] == DEFUSE_TIME_STANDARD


def test_defusal_time_comparison(defuse_match):
    room, att1, _att2, dfn1, dfn2, mode = defuse_match
    go_live(room)

    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att1.state.x, att1.state.y, att1.state.z = where
    hold_use(room, att1, where, 1, 250, until=lambda: mode.state.bomb.state == "planted")
    assert mode.state.bomb.state == "planted"

    bomb = mode.state.bomb
    at_bomb = (bomb.x, bomb.y, bomb.z)

    # Without kit: 2.5 seconds of defuse (2.5 / 10.0 = 0.25 progress)
    dfn1.state.x, dfn1.state.y, dfn1.state.z = at_bomb
    seq = hold_use(room, dfn1, at_bomb, 100, int(2.5 / 0.016))
    assert dfn1.action_progress == pytest.approx(0.25, abs=0.05)

    # Clear dfn1 action
    mode._clear_action(dfn1)

    # With kit: 2.5 seconds of defuse (2.5 / 5.0 = 0.50 progress)
    dfn2.owned_extras.add("defuser")
    dfn2.state.x, dfn2.state.y, dfn2.state.z = at_bomb
    hold_use(room, dfn2, at_bomb, seq, int(2.5 / 0.016))
    assert dfn2.action_progress == pytest.approx(0.50, abs=0.05)


def test_kit_drops_on_death_and_can_be_picked_up(defuse_match):
    room, _att1, _att2, dfn1, dfn2, mode = defuse_match
    go_live(room)

    dfn1.owned_extras.add("defuser")
    dfn1.state.x, dfn1.state.y, dfn1.state.z = 10.0, 15.0, 2.0

    # Dfn1 dies
    dfn1.alive = False
    mode.on_death(room, dfn1)
    assert "defuser" not in dfn1.owned_extras
    assert len(mode.dropped_kits) == 1
    kit = mode.dropped_kits[0]
    assert kit.x == 10.0 and kit.y == 15.0 and kit.z == 2.0

    shared = mode.shared_state(room)
    assert len(shared["kits"]) == 1
    assert shared["kits"][0]["x"] == 10.0

    # Dfn2 walks over the dropped kit
    dfn2.state.x, dfn2.state.y, dfn2.state.z = 10.5, 15.2, 2.0
    mode.tick(room, 0.05, time.monotonic())

    assert "defuser" in dfn2.owned_extras
    assert len(mode.dropped_kits) == 0
    assert len(mode.shared_state(room)["kits"]) == 0


def test_ninja_defuse_and_clutch_detection(defuse_match):
    room, att1, att2, dfn1, _dfn2, mode = defuse_match
    go_live(room)

    site = mode.sites[0]
    where = (site.x, site.y, site.z)
    att1.state.x, att1.state.y, att1.state.z = where
    hold_use(room, att1, where, 1, 250, until=lambda: mode.state.bomb.state == "planted")

    # Ensure both attackers are alive (ninja defuse condition >= 2 alive Ts)
    assert att1.alive and att2.alive

    # Give dfn1 a defuse kit so it finishes in 5s
    dfn1.owned_extras.add("defuser")
    at_bomb = (mode.state.bomb.x, mode.state.bomb.y, mode.state.bomb.z)
    dfn1.state.x, dfn1.state.y, dfn1.state.z = at_bomb

    # Kit defuse takes 5.0s (313 ticks of dt=0.016). In room simulation, 313 * 0.05 = 15.65s of fuse burns.
    # Set fuse to 16.15s so remaining fuse when defuse finishes is ~0.5s (< 1.0s sub-second clutch)
    mode.state = replace(mode.state, bomb=replace(mode.state.bomb, fuse=16.15))

    events = []
    orig_emit = room._emit
    def intercept(payload):
        events.append(payload)
        orig_emit(payload)
    room._emit = intercept

    hold_use(room, dfn1, at_bomb, 100, 350, until=lambda: mode.state.phase != LIVE)

    assert mode.state.phase in (POST, "over")
    assert room.scores[dfn1.team] == 1

    # Check bomb_defused event payload
    defuse_events = [e for e in events if e.get("kind") == "bomb_defused"]
    assert len(defuse_events) == 1
    evt = defuse_events[0]
    assert evt["ninja"] is True
    assert evt["clutch"] is True
    assert 0.0 <= evt["clutchTime"] < 1.0
    assert evt["by"] == dfn1.id
    assert evt["hasKit"] is True

    # Check shared state carries accolades
    shared_bomb = mode.shared_state(room)["bomb"]
    assert shared_bomb["ninja"] is True
    assert shared_bomb["clutch"] is True
    assert shared_bomb["clutchTime"] == evt["clutchTime"]
