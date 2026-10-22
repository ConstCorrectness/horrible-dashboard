"""Bot waypoint graphs (`nav.py`, `nav/<map>.json`).

The graph files are built by `tools/hassault_nav_build.py` and may be edited by
hand, so these hold any version of them to the rules the builder follows: every
link is walkable in the simulation, and every spawn reaches every site and every
cover node. Plus the two behaviours that are the point of the graph: a route
round a wall, and a bot that follows it to a door it never used to reach.
"""

from __future__ import annotations

import math

import pytest

from backend.modules.hassault import assets, bots, maplint, nav, physics
from backend.modules.hassault.match import MatchRoom
from backend.modules.hassault.modes import objectives

GRAPHS = sorted(p.stem for p in nav.NAV_DIR.glob("*.json"))


def _world(name: str) -> tuple[physics.World, object]:
    cgz = assets.load_map(name)
    return physics.World.from_map(cgz), cgz


def test_every_defuse_map_has_a_graph():
    defuse = {
        "hd_dust2",
        "hd_mirage",
        "hd_inferno",
        "hd_nuke",
        "hd_office",
        "hd_bank",
        "hd_assault",
    }
    assert defuse <= set(GRAPHS)


@pytest.mark.parametrize("name", GRAPHS)
def test_every_link_is_walkable(name):
    world, _ = _world(name)
    graph = nav.load(name)
    assert graph is not None
    bad = [
        (i, j)
        for i, succ in enumerate(graph.succ)
        for j in succ
        if not nav.walkable(world, *graph.node(i), *graph.node(j))
    ]
    assert not bad, f"{name}: {len(bad)} links a body cannot walk, e.g. {bad[:5]}"


@pytest.mark.parametrize("name", GRAPHS)
def test_every_spawn_reaches_every_site_and_cover_node(name):
    world, cgz = _world(name)
    graph = nav.load(name)
    assert graph is not None
    targets = [(s.x, s.y, f"site {s.id}") for s in objectives.place(world, cgz).sites]
    targets += [
        (x, y, label) for x, y, label in bots.TACTICAL_COVER_NODES.get(name, [])
    ]
    for spawn in cgz.spawns():
        start = (spawn.x + 0.5, spawn.y + 0.5)
        for x, y, label in targets:
            route = nav.path(world, graph, start, (x, y))
            assert route is not None, f"{name}: no route from spawn {start} to {label}"


def test_a_route_goes_round_a_wall_rather_than_through_it():
    world = physics.flat_world(48)
    # A wall across the room with a gap at the top end.
    wall = bytearray(world.type)
    for y in range(4, 40):
        wall[world.index(24, y)] = physics.SOLID
    world.type = bytes(wall)
    graph = nav.Graph.from_json(
        {
            "nodes": [[12, 20, ""], [20, 43, "gap_w"], [28, 43, "gap_e"], [36, 20, ""]],
            "links": [[0, 1], [1, 0], [1, 2], [2, 1], [2, 3], [3, 2]],
        }
    )
    assert not nav.walkable(world, 12, 20, 36, 20)
    route = nav.path(world, graph, (10.5, 20.5), (38.5, 20.5))
    assert route is not None
    assert route[-1] == (38.5, 20.5)
    assert (20.0, 43.0) in route and (28.0, 43.0) in route
    for a, b in zip(route, route[1:]):
        assert nav.walkable(world, *a, *b)


def test_a_drop_is_walkable_one_way_only():
    world = physics.flat_world(32)
    floor = bytearray(world.floor)
    for y in range(32):
        for x in range(16, 32):
            floor[world.index(x, y)] = 6  # a ledge well over a step high
    world.floor = bytes(floor)
    assert nav.walkable(world, 24.5, 16.5, 8.5, 16.5)
    assert not nav.walkable(world, 8.5, 16.5, 24.5, 16.5)


def test_a_map_without_a_graph_has_none():
    assert nav.load("testmap") is None


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("hd_office", "site_b_south_door"),
        ("hd_office", "site_a_north_gap"),
        ("hd_office", "site_a_back"),
    ],
)
def test_a_bot_walks_the_graph_to_a_door_it_used_to_miss(name, label):
    # Measured: steering straight, a bot from the attackers' end gets no closer
    # than 59 to 92 cubes to any of these. Over the graph it arrives in ~9 s.
    world, cgz = _world(name)
    attackers = [s for s in cgz.spawns() if s.attr2 == 1]
    room = MatchRoom("nav", name, world, attackers)
    (bot,) = bots.add_bots(room, 1, skill="normal", team=1)
    x, y, _ = next(n for n in bots.TACTICAL_COVER_NODES[name] if n[2] == label)
    best = math.inf
    for _ in range(int(45 / (1 / 20))):
        bot.brain.roam = (x, y)
        bot.brain.roam_in = 60.0
        room.simulate(1 / 20)
        best = min(best, math.hypot(bot.state.x - x, bot.state.y - y))
        if best < 4:
            break
    assert best < 4, (
        f"{name}: a bot heading for {label} got no closer than {best:.1f} cubes"
    )


def test_standable_cells_include_every_graph_node():
    for name in GRAPHS:
        world, _ = _world(name)
        graph = nav.load(name)
        assert graph is not None
        cells = maplint._standable(world)
        off = [n for n in graph.nodes if nav.floor_under(world, n[0], n[1]) is None]
        assert not off, f"{name}: nodes no body fits at: {off[:5]}"
        assert cells
