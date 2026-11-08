"""Bots playing defuse as a team (`bots.Tactics`, `bots.defuse_goal`, lineups).

The attackers pick one site a round and go together (execute, a split over two
approaches, a sniper lurking the other site); defenders hold spread over the
sites and rotate on a teammate's call; smokes and flashes are thrown from the
map JSON's `lineups` through `Command.throw`, like a player's. These hold the
rules each of those rests on, against real maps.
"""

from __future__ import annotations

import importlib.util
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from backend.modules.hassault import bots, mapsource, nav
from backend.modules.hassault.match import MatchRoom, MatchServer
from backend.modules.hassault.modes import defuse
from backend.modules.hassault.modes.base import Goal
from backend.modules.hassault.noise import Noise
from backend.modules.hassault.physics import flat_world

ROOT = Path(__file__).resolve().parents[2]
_TOOL = ROOT / "tools" / "hassault_lineups.py"
_spec = importlib.util.spec_from_file_location("hassault_lineups", _TOOL)
lineups_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lineups_tool)

DEFUSE_MAPS = [
    "hd_dust2",
    "hd_mirage",
    "hd_inferno",
    "hd_nuke",
    "hd_office",
    "hd_bank",
    "hd_assault",
]


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))


def bot_room(name: str = "hd_dust2", count: int = 10):
    room = MatchServer(record_replays=False).create(name, mode="defuse")
    members = bots.add_bots(room, count, skill="normal")
    return room, members


def planned(room):
    t = bots.tactics(room)
    t.plan(room, room.mode.state, room.mode.sites)
    return t


# ---------------------------------------------------------------------------
# Lineups
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEFUSE_MAPS)
def test_every_defuse_map_declares_lineups_for_its_sites(name):
    rows = bots.lineups(name)
    assert len(rows) >= 3, f"{name}: only {len(rows)} lineups"
    room, _ = bot_room(name, 0)
    sites = {s.id for s in room.mode.sites}
    for lu in rows:
        assert lu.site in sites, f"{name}: {lu.id} names a site the map lacks"
        assert lu.kind in ("smoke", "flash")
        assert len(lu.route) > 0


@pytest.mark.parametrize("name", DEFUSE_MAPS)
def test_every_lineup_lands_where_it_says(name):
    # The server's own grenade physics at the live tick, from the lineup's spot
    # and angle: a re-bake that moves a wall shows up here, not in a match.
    bad = lineups_tool.check(name)
    assert not bad, f"{name}: lineups landing over {lineups_tool.TOLERANCE} off: {bad}"


def test_lineups_are_read_from_the_map_source_in_radians():
    raw = json.loads(
        mapsource.source_path("hd_dust2").read_text(encoding="utf-8")
    )["lineups"]
    rows = bots.lineups("hd_dust2")
    assert len(rows) == len(raw)
    for row, lu in zip(raw, rows):
        assert lu.yaw == pytest.approx(math.radians(row["yaw"]))
        assert lu.pitch == pytest.approx(math.radians(row["pitch"]))


def test_a_map_with_no_lineups_has_none():
    assert bots.lineups("testmap") == ()


# ---------------------------------------------------------------------------
# The attackers' plan
# ---------------------------------------------------------------------------


def test_the_attackers_pick_one_site_and_all_go_to_it():
    room, members = bot_room()
    t = planned(room)
    state = room.mode.state
    assert t.site in {s.id for s in room.mode.sites}
    attackers = [b for b in members if b.team == state.attackers]
    assert set(t.roles) == {b.id for b in attackers}
    t.go = True
    site = next(s for s in room.mode.sites if s.id == t.site)
    spot = bots.plant_spot(room, site)
    room.mode.state = replace(state, phase=defuse.LIVE, remaining=defuse.ROUND_TIME)
    for b in attackers:
        if t.roles[b.id] == "lurk":
            continue
        goal = bots.defuse_goal(room, b, room.mode)
        assert goal is not None
        assert (goal.x, goal.y) == spot, f"{b.name} was sent somewhere else"


def test_the_plan_is_made_once_a_round():
    room, _ = bot_room()
    t = planned(room)
    first = (t.site, dict(t.roles))
    t.plan(room, room.mode.state, room.mode.sites)
    assert (t.site, dict(t.roles)) == first
    room.mode.state = replace(room.mode.state, round=room.mode.state.round + 1)
    t.plan(room, room.mode.state, room.mode.sites)
    assert t.key == (room.mode.state.round, room.mode.state.attackers)


def test_a_lurker_holds_away_from_the_target_early_and_joins_later():
    room, members = bot_room()
    t = planned(room)
    state = room.mode.state
    attackers = [b for b in members if b.team == state.attackers]
    lurker = attackers[0]
    t.roles = {b.id: "execute" for b in attackers}
    t.roles[lurker.id] = "lurk"
    t.lurk = (10.0, 10.0)
    t.go = True
    room.mode.state = replace(
        state,
        phase=defuse.LIVE,
        remaining=defuse.ROUND_TIME - 5.0,
        bomb=replace(state.bomb, carrier=""),
    )
    early = bots.defuse_goal(room, lurker, room.mode)
    assert early is not None and (early.x, early.y) == (10.0, 10.0) and early.hold
    room.mode.state = replace(
        room.mode.state, remaining=defuse.ROUND_TIME - bots.LURK_UNTIL - 1.0
    )
    late = bots.defuse_goal(room, lurker, room.mode)
    site = next(s for s in room.mode.sites if s.id == t.site)
    assert late is not None and (late.x, late.y) == bots.plant_spot(room, site)


def test_the_bomb_never_lurks():
    # Measured on Inferno: a lurker handed the bomb held it at the other site
    # for most of the round.
    room, members = bot_room()
    t = planned(room)
    state = room.mode.state
    carrier = next(b for b in members if b.team == state.attackers)
    t.roles[carrier.id] = "lurk"
    t.lurk = (10.0, 10.0)
    t.go = True
    room.mode.state = replace(
        state,
        phase=defuse.LIVE,
        remaining=defuse.ROUND_TIME - 5.0,
        bomb=replace(state.bomb, carrier=carrier.id),
    )
    goal = bots.defuse_goal(room, carrier, room.mode)
    assert goal is not None
    assert goal.use, "the carrier should be sent to plant"
    assert (goal.x, goal.y) != (10.0, 10.0)


def test_attackers_wait_staged_until_the_utility_is_out():
    room, members = bot_room()
    t = planned(room)
    assert t.lineup, "a defuse map with lineups should hand some out"
    thrower_id, lu = next(iter(t.lineup.items()))
    thrower = room.players[thrower_id]
    for b in members:
        if b.id in t.stage:
            b.state.x, b.state.y = t.stage[b.id]
    t.update(room, 5.0)
    assert not t.go, "should not go with a lineup still in a living hand"
    t.thrown.update(x.id for x in t.lineup.values())
    t.update(room, 5.0)
    assert t.go
    assert thrower.alive


def test_attackers_go_at_execute_by_whatever_happens():
    room, _ = bot_room()
    t = planned(room)
    t.update(room, bots.EXECUTE_BY - 0.1)
    if not t.go:
        t.update(room, bots.EXECUTE_BY)
    assert t.go


# ---------------------------------------------------------------------------
# The defenders
# ---------------------------------------------------------------------------


def test_defenders_hold_both_sites_with_an_anchor_each():
    room, members = bot_room()
    t = planned(room)
    state = room.mode.state
    defenders = [b for b in members if b.team != state.attackers]
    held = {t.holds[b.id][2] for b in defenders}
    assert held == {s.id for s in room.mode.sites}
    assert len(t.anchors) == len(held)


def test_defenders_rotate_on_a_call_but_the_anchor_stays():
    room, members = bot_room()
    t = planned(room)
    state = room.mode.state
    defenders = [b for b in members if b.team != state.attackers]
    a, b = room.mode.sites[0], room.mode.sites[1]
    t.report(room, defenders[0].team, b.x + 2.0, b.y)
    assert t.call(defenders[0].team).site == b.id
    room.mode.state = replace(state, phase=defuse.LIVE, remaining=90.0)
    for d in defenders:
        goal = bots.defuse_goal(room, d, room.mode)
        assert goal is not None
        _, _, holding = t.holds[d.id]
        to_b = math.hypot(goal.x - b.x, goal.y - b.y) <= b.radius
        if holding == a.id and d.id in t.anchors:
            assert not to_b, f"{d.name}, A's anchor, left A on a call"
        elif holding == a.id:
            assert to_b, f"{d.name} did not rotate to B on the call"


def test_a_call_needs_a_site_nearby_and_expires():
    room, _ = bot_room()
    t = bots.tactics(room)
    t.report(room, 1, -500.0, -500.0)
    assert t.call(1) is None
    s = room.mode.sites[0]
    t.report(room, 1, s.x, s.y)
    assert t.call(1) is not None
    t.clock += bots.CALL_TTL + 0.1
    assert t.call(1) is None


def test_noises_outlive_the_broadcast_that_clears_them():
    # The room's list is emptied after every tick, before any bot thinks, so a
    # bot reading only `room.noises` never heard a thing on a live server.
    room, members = bot_room()
    state = room.mode.state
    defender = next(b for b in members if b.team != state.attackers)
    attacker = next(b for b in members if b.team == state.attackers)
    s = room.mode.sites[0]
    defender.state.x, defender.state.y = s.x, s.y
    room.noises.append(
        Noise(
            kind="footstep",
            source=attacker.id,
            x=s.x + 3.0,
            y=s.y,
            z=s.z,
            loudness=20.0,
            source_team=attacker.team,
        )
    )
    bots.hear(room, 1 / 20)
    room.noises.clear()
    t = bots.tactics(room)
    assert t.heard and t.heard[0].source == attacker.id
    assert t.call(defender.team) is not None, "a heard step on a site is a call"


# ---------------------------------------------------------------------------
# Doing it: a hold, and a throw
# ---------------------------------------------------------------------------


def test_a_hold_goal_stands_still_and_faces_where_it_says():
    room = MatchRoom("h", "testmap", flat_world(48), [])
    (bot,) = bots.add_bots(room, 1, team=0)
    bot.state.x, bot.state.y, bot.state.z, bot.state.yaw = 20.0, 20.0, 0.0, 0.0
    room.mode.bot_goal = lambda room, me: Goal(
        x=20.0, y=20.0, z=0.0, radius=2.0, hold=True, face=(20.0, 40.0)
    )
    cmd = None
    for _ in range(40):
        cmd = bot.brain.think(room, bot, 1 / 20)
        bot.state.yaw = cmd.yaw
    assert cmd is not None
    assert cmd.forward == 0 and cmd.strafe == 0
    assert abs(bots._wrap(cmd.yaw - math.pi / 2)) < 0.05


def test_a_bot_throws_its_lineup_through_command_throw():
    room = MatchServer(record_replays=False).create("hd_dust2", mode="defuse")
    state = room.mode.state
    (bot,) = bots.add_bots(room, 1, skill="normal", team=state.attackers)
    room.add("far", None, team=1 - state.attackers)
    lu = next(x for x in bots.lineups("hd_dust2") if x.kind == "smoke")
    t = planned(room)
    t.roles = {bot.id: "execute"}
    t.lineup = {bot.id: lu}
    t.stage = {bot.id: (lu.x, lu.y)}
    t.site = lu.site
    t.go = False
    room.mode.state = replace(state, phase=defuse.LIVE, remaining=defuse.ROUND_TIME)
    bot.state.x, bot.state.y = lu.x, lu.y
    bot.state.z = nav.floor_under(room.world, lu.x, lu.y)
    bot.nades.counts[lu.slot] = 1
    for _ in range(int(4.0 * 20)):
        room.simulate(1 / 20)
        room.noises.clear()
        room.fx.clear()
        if lu.id in t.thrown:
            break
    assert lu.id in t.thrown, "the bot never threw its lineup"
    assert bot.nades.counts.get(lu.slot, 0) == 0, "thrown, so the smoke is gone"


def test_a_bot_with_a_lineup_buys_that_grenade_first():
    room, members = bot_room()
    room.simulate(1 / 20)  # into the first round's freeze, when buying opens
    t = planned(room)
    thrower_id, lu = next(iter(t.lineup.items()))
    bot = room.players[thrower_id]
    bot.money = 800
    bot.nades.counts = {}
    assert room.mode.can_buy(room, bot), "at its spawn in the freeze"
    bought = False
    for _ in range(40):
        cmd = bot.brain.think(room, bot, 1 / 20)
        if cmd is not None and cmd.buy == room.mode.catalog_index(lu.kind):
            bought = True
            break
    assert bought, f"{bot.name} should buy its lineup's {lu.kind}"


# ---------------------------------------------------------------------------
# A site you can plant on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEFUSE_MAPS)
def test_most_of_every_sites_floor_can_be_planted_on(name):
    # A site's height used to be the floor under its centre cell, and Inferno's
    # A centre is a 7-cube prop: 33 of its 384 cells were plantable, every one
    # of them on top of a box, and every bot round there timed out.
    from backend.modules.hassault import maplint
    from backend.modules.hassault.modes import objectives

    room, _ = bot_room(name, 0)
    world = room.world
    standable = maplint._standable(world)
    for site in room.mode.sites:
        cells = [
            (x + 0.5, y + 0.5)
            for x, y in standable
            if math.hypot(x + 0.5 - site.x, y + 0.5 - site.y) <= site.radius
        ]
        ok = sum(
            site.contains(x, y, objectives._on_floor(world, x, y)) for x, y in cells
        )
        assert ok >= 0.6 * len(cells), (
            f"{name} {site.id}: only {ok} of {len(cells)} cells plantable "
            f"(site z {site.z})"
        )
