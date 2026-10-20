"""Defuse spawns players by role, not by team.

A spawn belongs to an end of the map: the team-1 spawns are the attackers' end,
far from the sites, and the team-0 spawns are the defenders', beside them. The
half-time swap flips which team attacks, so it moves both teams to the other
end, and the buy zones go with them. Before this, everyone kept their spawn
across the swap, and on the rebuilt maps every first half began with the
attackers two seconds from a site.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from backend.modules.hassault.match import MatchServer
from backend.modules.hassault.modes import defuse

ROOT = Path(__file__).resolve().parents[2]
MAPS = ROOT / "backend" / "modules" / "hassault" / "maps"

_TOOL = ROOT / "tools" / "hassault_route_times.py"
_spec = importlib.util.spec_from_file_location("hassault_route_times", _TOOL)
routes_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routes_tool)


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))


def defuse_room(name="hd_dust2"):
    room = MatchServer(record_replays=False).create(name, mode="defuse")
    a = room.add("a", None, team=0)
    b = room.add("b", None, team=1)
    return room, a, b


def end_cells(room, end):
    """Where a body spawned at each of the map's `end` spawns stands."""
    return {
        (round(e.x + 0.5, 1), round(e.y + 0.5, 1))
        for e in room.spawns
        if e.attr2 == end
    }


def stands_at(player):
    return (round(player.state.x, 1), round(player.state.y, 1))


def test_the_attacking_team_spawns_at_the_attackers_end_in_both_halves():
    room, a, b = defuse_room()
    far, near = (
        end_cells(room, defuse.ATTACKER_SPAWNS),
        end_cells(room, 1 - defuse.ATTACKER_SPAWNS),
    )
    assert room.mode.state.attackers == 0, "team 0 attacks the first half"
    for _ in range(10):
        room.respawn(a)
        room.respawn(b)
        assert stands_at(a) in far, "the first half's attackers start at the far end"
        assert stands_at(b) in near
    # Half time: the roles flip, and with them the ends.
    room.mode.state = replace(room.mode.state, swapped=True, attackers=1)
    for _ in range(10):
        room.respawn(a)
        room.respawn(b)
        assert stands_at(a) in near, "the same team now defends, beside the sites"
        assert stands_at(b) in far


def test_the_buy_zones_follow_the_role_not_the_team():
    room, a, b = defuse_room()
    room.respawn(a)  # attacking: at the far end
    assert room.mode.in_buy_zone(a)
    far_spawn = (a.state.x, a.state.y)
    room.respawn(b)  # defending: beside the sites
    assert room.mode.in_buy_zone(b)
    # The defender walked to the attackers' end: no buying there.
    b.state.x, b.state.y = far_spawn
    assert not room.mode.in_buy_zone(b)
    # After the swap that same end is theirs to buy at.
    room.mode.state = replace(room.mode.state, swapped=True, attackers=1)
    assert room.mode.in_buy_zone(b)


def _defuse_maps():
    out = []
    for path in sorted(MAPS.glob("hd_*.json")):
        source = json.loads(path.read_text(encoding="utf-8"))
        if "defuse" in source.get("modes", []) and source.get("objectives", {}).get(
            "sites"
        ):
            out.append(path)
    return out


@pytest.mark.parametrize("path", _defuse_maps(), ids=lambda p: p.stem)
def test_every_defuse_map_puts_its_attackers_end_farther_from_each_site(path):
    """`ATTACKER_SPAWNS` is the far end on every map, or the role spawn puts the
    attackers beside the sites. Assault had its two sets the other way round."""
    routes, _, _ = routes_tool.measure(str(path))
    attackers, defenders = defuse.ATTACKER_SPAWNS, 1 - defuse.ATTACKER_SPAWNS
    for site in ("A", "B"):
        assert routes[(attackers, site)] and routes[(defenders, site)], (
            path.stem,
            site,
        )
        assert routes[(attackers, site)][0] > routes[(defenders, site)][0], (
            path.stem,
            site,
            routes[(attackers, site)][0],
            routes[(defenders, site)][0],
        )
