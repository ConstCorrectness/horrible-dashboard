"""Dust II's routes: the walls that were opened, and what that did to the walk.

`tools/hassault_route_times.py` measures the baked grid the server simulates. The
larger Dust II had two unbroken walls (Mid's west wall and Long A's) and a B whose
only way in was the tunnels, so CT took 16.6 s to reach B and T had one route to
each site. The B Doors, the Long Doors and the Lower Dark door were opened to fix
that. These tests keep them open: a regeneration that closes one (a wall piece
moved, a prop placed in a doorway) fails here and not in somebody's match.
"""

import importlib.util
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "hassault_route_times.py"
_spec = importlib.util.spec_from_file_location("hassault_route_times", _TOOL)
routes_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routes_tool)

MAP = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "modules"
    / "hassault"
    / "maps"
    / "hd_dust2.json"
)


@pytest.fixture(scope="module")
def measured():
    return routes_tool.measure(str(MAP))


def test_every_team_can_walk_to_every_site(measured):
    routes, _, _ = measured
    assert set(routes) == {(t, s) for t in (0, 1) for s in ("A", "B")}
    for pair, times in routes.items():
        assert times, f"{pair} cannot be reached on foot"


def test_ct_reaches_b_through_the_b_doors_not_the_long_way_round(measured):
    # 16.6 s when B's only way in was the tunnels.
    routes, _, _ = measured
    assert routes[(0, "B")][0] < 6.0


def test_t_has_more_than_one_way_to_each_site(measured):
    routes, _, _ = measured
    for site in ("A", "B"):
        times = routes[(1, site)]
        assert len(times) >= 2, f"T has one route to {site}: {times}"
        # And the alternatives are worth taking: not twice as far.
        assert times[1] <= times[0] * 1.6


def test_the_approach_is_longer_for_t_than_for_ct(measured):
    # The whole point of a bigger map: the attackers have ground to cover.
    routes, _, _ = measured
    for site in ("A", "B"):
        assert routes[(1, site)][0] > routes[(0, site)][0] * 2


# A doorway is passable when the cells either side of its wall are joined by a path
# about as short as walking straight through. In cubes: (x, y) of the doorway's
# centre, the wall's axis, and a hop either side of it.
DOORS = {
    "the B Doors": ((87, 219), "x"),
    "the Long Doors": ((156, 102), "x"),
    "the Lower Dark door": ((87, 142), "x"),
}


@pytest.mark.parametrize("name", DOORS)
def test_a_doorway_opened_in_a_wall_can_be_walked_through(measured, name):
    _, world, cells = measured
    (x, y), _ = DOORS[name]
    hop = 9
    west = routes_tool.nearest(cells, x - hop, y)
    east = routes_tool.nearest(cells, x + hop, y)
    dist, _ = routes_tool.dijkstra(world, cells, [west])
    assert east in dist, f"{name}: the two sides are not joined"
    assert dist[east] < 2.5 * hop, f"{name}: the way through is {dist[east]:.0f} cubes"


# --- Mirage -----------------------------------------------------------------
# Mirage was one open 62 m square. It is stretched to 74 x 78 m, and it was never
# short of ways round (it is open), so what these keep is the length the stretch
# gave the attackers' walk and that no building cut across the middle closed a way.

MIRAGE = MAP.with_name("hd_mirage.json")


@pytest.fixture(scope="module")
def mirage():
    return routes_tool.measure(str(MIRAGE))


def test_every_team_can_walk_to_every_site_on_mirage(mirage):
    routes, _, _ = mirage
    assert set(routes) == {(t, s) for t in (0, 1) for s in ("A", "B")}
    for pair, times in routes.items():
        assert times, f"{pair} cannot be reached on foot"


def test_t_still_has_several_ways_to_each_site_on_mirage(mirage):
    routes, _, _ = mirage
    for site in ("A", "B"):
        assert len(routes[(1, site)]) >= 2, routes[(1, site)]


def test_the_stretch_lengthened_the_attackers_walk_on_mirage(mirage):
    # 5.7 s to A and 6.1 s to B on the 62 m square.
    routes, _, _ = mirage
    assert routes[(1, "A")][0] > 6.5
    assert routes[(1, "B")][0] > 7.0
    for site in ("A", "B"):
        assert routes[(1, site)][0] > routes[(0, site)][0] * 2


# --- Inferno ----------------------------------------------------------------
# Inferno is stretched to 74 x 78 m by cutting along clean lines, so Banana, mid and
# the alleys keep their shape and only get longer. What these keep is that nothing
# the stretch or the dressing put down closed a way to a site, and that the
# attackers' walk got longer (7.0 s to A and 5.7 s to B on the 62 m square).

INFERNO = MAP.with_name("hd_inferno.json")


@pytest.fixture(scope="module")
def inferno():
    return routes_tool.measure(str(INFERNO))


def test_every_team_can_walk_to_every_site_on_inferno(inferno):
    routes, _, _ = inferno
    assert set(routes) == {(t, s) for t in (0, 1) for s in ("A", "B")}
    for pair, times in routes.items():
        assert times, f"{pair} cannot be reached on foot"


def test_t_has_several_ways_to_each_site_on_inferno(inferno):
    routes, _, _ = inferno
    for site in ("A", "B"):
        assert len(routes[(1, site)]) >= 3, routes[(1, site)]


def test_the_stretch_lengthened_the_attackers_walk_on_inferno(inferno):
    routes, _, _ = inferno
    assert routes[(1, "A")][0] > 8.0
    assert routes[(1, "B")][0] > 6.8
    for site in ("A", "B"):
        assert routes[(1, site)][0] > routes[(0, site)][0] * 2
