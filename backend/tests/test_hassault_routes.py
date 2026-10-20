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


# --- Nuke -------------------------------------------------------------------
# Nuke was one hall in a bare yard, with B sealed under A (the server keeps one
# floor per column). It is stretched to 74 x 76 m, B is a reactor room of its own
# beside the hall with its floor 4.5 m down (the ramp from T's side, stairs from
# CT's, a vent dropped into from A), and a Lobby in front of A's south door makes
# T walk round to it. On the 62 m square T was 3.9 s from A and 4.3 s from B.

NUKE = MAP.with_name("hd_nuke.json")


@pytest.fixture(scope="module")
def nuke():
    return routes_tool.measure(str(NUKE))


def test_every_team_can_walk_to_every_site_on_nuke(nuke):
    routes, _, _ = nuke
    assert set(routes) == {(t, s) for t in (0, 1) for s in ("A", "B")}
    for pair, times in routes.items():
        assert times, f"{pair} cannot be reached on foot"


def test_t_has_several_ways_to_each_site_on_nuke(nuke):
    # A: the Lobby and the east door. B: the ramp, and the vent from A.
    routes, _, _ = nuke
    for site in ("A", "B"):
        assert len(routes[(1, site)]) >= 2, routes[(1, site)]


def test_the_attackers_walk_is_in_the_band_of_the_other_large_maps_on_nuke(nuke):
    routes, _, _ = nuke
    assert routes[(1, "A")][0] > 7.5
    assert routes[(1, "B")][0] > 6.0
    for site in ("A", "B"):
        assert routes[(1, site)][0] > routes[(0, site)][0]


def test_ct_reaches_b_first_on_nuke(nuke):
    routes, _, _ = nuke
    assert routes[(0, "B")][0] < 6.0


def test_b_stairs_climb_from_the_pit_to_the_north_door_on_nuke(nuke):
    # The stairs run north up B's east wall to the door; the vent and the stairs
    # end too close together to count as two routes, so each is pinned itself.
    _, world, cells = nuke
    pit = routes_tool.nearest(cells, 66, 132)
    outside = routes_tool.nearest(cells, 68, 168)
    assert world.floor_at(*pit) < -10 and world.floor_at(*outside) == 0
    up, _ = routes_tool.dijkstra(world, cells, [pit])
    assert up[outside] < 60, f"the way up is {up.get(outside, -1):.0f} cubes"


# (x, y) of a doorway's centre in cubes, and the axis its wall runs along.
NUKE_DOORS = {
    "the Lobby door": ((85, 96), "x"),
    "the east door": ((177, 144), "y"),
    "B's north door": ((68, 162), "x"),
}


@pytest.mark.parametrize("name", NUKE_DOORS)
def test_a_doorway_on_nuke_can_be_walked_through(nuke, name):
    _, world, cells = nuke
    (x, y), along = NUKE_DOORS[name]
    hop = 9
    dx, dy = (0, hop) if along == "x" else (hop, 0)
    a = routes_tool.nearest(cells, x - dx, y - dy)
    b = routes_tool.nearest(cells, x + dx, y + dy)
    dist, _ = routes_tool.dijkstra(world, cells, [a])
    assert b in dist, f"{name}: the two sides are not joined"
    assert dist[b] < 2.5 * hop, f"{name}: the way through is {dist[b]:.0f} cubes"


def test_the_vent_drops_from_a_into_b_and_does_not_climb_back_on_nuke(nuke):
    _, world, cells = nuke
    hall = routes_tool.nearest(cells, 84, 123)  # A's floor, beside the hatch
    pit = routes_tool.nearest(cells, 68, 123)  # B's floor, out of the crawl
    assert world.floor_at(*hall) == 0 and world.floor_at(*pit) < -10
    down, _ = routes_tool.dijkstra(world, cells, [hall])
    assert down[pit] < 30, f"the vent is {down[pit]:.0f} cubes, not a drop"
    up, _ = routes_tool.dijkstra(world, cells, [pit])
    assert up[hall] > 100, "B climbs back up into A through the vent"


# --- Office -----------------------------------------------------------------
# Office was one open floor plate with B in the cubicles beside T's spawn: T
# reached B in 0.7 s and both sides had one way to A. A is the boardroom and B
# the server room now, CT arrive by the elevators between them, T up the east
# stairwell, and a corridor along each glass façade is a third way to each site.
# Cut to 66 x 62 m.

OFFICE = MAP.with_name("hd_office.json")


@pytest.fixture(scope="module")
def office():
    return routes_tool.measure(str(OFFICE))


def test_every_team_can_walk_to_every_site_on_office(office):
    routes, _, _ = office
    assert set(routes) == {(t, s) for t in (0, 1) for s in ("A", "B")}
    for pair, times in routes.items():
        assert times, f"{pair} cannot be reached on foot"


def test_t_has_three_ways_to_each_site_on_office(office):
    # A: the north corridor, the mid lane's double doors, round by the elevators.
    # B: the south corridor's door, the mid door, round by the elevators.
    routes, _, _ = office
    for site in ("A", "B"):
        assert len(routes[(1, site)]) >= 3, routes[(1, site)]


def test_the_attackers_walk_is_in_the_band_of_the_other_large_maps_on_office(office):
    routes, _, _ = office
    for site in ("A", "B"):
        assert routes[(1, site)][0] > 7.0, routes[(1, site)]
        assert routes[(1, site)][0] > 2 * routes[(0, site)][0]


def test_ct_holds_both_sites_from_the_elevators_on_office(office):
    routes, _, _ = office
    assert routes[(0, "A")][0] < 4.0
    assert routes[(0, "B")][0] < 3.0


# (x, y) of a doorway's centre in cubes, and the axis its wall runs along.
OFFICE_DOORS = {
    "the boardroom's double doors": ((66, 162), "y"),
    "the boardroom's CT door": ((26, 132), "x"),
    "the gap by the north glass into A": ((66, 195), "y"),
    "the server room's south door": ((66, 21), "y"),
    "the server room's mid door": ((66, 72), "y"),
    "the server room's CT door": ((18, 87), "x"),
    "the north corridor's door": ((99, 182), "x"),
    "the south corridor's door": ((99, 28), "x"),
}


@pytest.mark.parametrize("name", OFFICE_DOORS)
def test_a_doorway_on_office_can_be_walked_through(office, name):
    _, world, cells = office
    (x, y), along = OFFICE_DOORS[name]
    hop = 9
    dx, dy = (0, hop) if along == "x" else (hop, 0)
    a = routes_tool.nearest(cells, x - dx, y - dy)
    b = routes_tool.nearest(cells, x + dx, y + dy)
    dist, _ = routes_tool.dijkstra(world, cells, [a])
    assert b in dist, f"{name}: the two sides are not joined"
    assert dist[b] < 2.5 * hop, f"{name}: the way through is {dist[b]:.0f} cubes"


# --- Bank -------------------------------------------------------------------
# Bank had its sides the wrong way round: T spawned beside the vault (A, 1.1 s
# away) and the front door was the only way in from the street. T come off the
# street now, CT hold the rear offices, B is the north-west offices, and the
# facade's two side doors and a breach in the vault's west wall give T a second
# way to each site. Not cut: the walk is already in the band.

BANK = MAP.with_name("hd_bank.json")


@pytest.fixture(scope="module")
def bank():
    return routes_tool.measure(str(BANK))


def test_every_team_can_walk_to_every_site_on_bank(bank):
    routes, _, _ = bank
    assert set(routes) == {(t, s) for t in (0, 1) for s in ("A", "B")}
    for pair, times in routes.items():
        assert times, f"{pair} cannot be reached on foot"


def test_t_has_two_ways_to_each_site_on_bank(bank):
    # A: the vault corridor, and the breach from the rear corridor.
    # B: the hall's west door, and round through the rear offices.
    routes, _, _ = bank
    for site in ("A", "B"):
        assert len(routes[(1, site)]) >= 2, routes[(1, site)]


def test_the_attackers_walk_is_in_the_band_of_the_other_large_maps_on_bank(bank):
    # 6.7 s to each, Nuke's B; it was 1.1 s to A and 2.5 s to B.
    routes, _, _ = bank
    for site in ("A", "B"):
        assert routes[(1, site)][0] > 6.0, routes[(1, site)]
        assert routes[(1, site)][0] > 2 * routes[(0, site)][0]


# (x, y) of a doorway's centre in cubes, and the axis its wall runs along.
BANK_DOORS = {
    "the front door": ((96, 60), "x"),
    "the facade's west door": ((40, 60), "x"),
    "the facade's east door": ((152, 60), "x"),
    "the hall's west door to B": ((39, 142), "x"),
    "the vault breach": ((120, 150), "y"),
}


@pytest.mark.parametrize("name", BANK_DOORS)
def test_a_doorway_on_bank_can_be_walked_through(bank, name):
    _, world, cells = bank
    (x, y), along = BANK_DOORS[name]
    hop = 9
    dx, dy = (0, hop) if along == "x" else (hop, 0)
    a = routes_tool.nearest(cells, x - dx, y - dy)
    b = routes_tool.nearest(cells, x + dx, y + dy)
    dist, _ = routes_tool.dijkstra(world, cells, [a])
    assert b in dist, f"{name}: the two sides are not joined"
    assert dist[b] < 2.5 * hop, f"{name}: the way through is {dist[b]:.0f} cubes"
