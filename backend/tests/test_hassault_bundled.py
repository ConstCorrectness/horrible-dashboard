"""The maps HorribleAssault ships itself, and the writer that exports them.

These run everywhere — that is the point of them. The install-gated tests in
`test_hassault_cgz.py` still prove the *reader* against real AssaultCube files,
but until now a machine without an install (CI, a fresh clone, the Fly image) had
no map at all, so nothing downstream of parsing was covered there.

Two things get pinned here. The **round trip** — source → `CgzMap` → `write_cgz`
→ `parse_cgz` — is what keeps the writer honest against the reader: the writer
has no independent specification, so agreement with a reader validated on 44 real
maps is the whole of its correctness argument. And **playability**, because a map
that builds is not the same as a map you can play: every spawn has to be somewhere
a body can stand, and has to be able to reach every other one.
"""

from __future__ import annotations

import math
from collections import deque

import pytest

from backend.modules.hassault import assets, mapsource, physics, pickups
from backend.modules.hassault.cgz import (
    PLANE_ORDER,
    SOLID,
    SPACE,
    CgzError,
    parse_cgz,
    write_cgz,
)

BUNDLED = mapsource.bundled_names()


def test_the_app_ships_maps():
    """The whole point: a fresh install can play without owning AssaultCube."""
    assert BUNDLED, "no bundled maps found — hassault would need an install to play"
    assert all(n.startswith(mapsource.BUNDLED_PREFIX) for n in BUNDLED)


@pytest.mark.parametrize("name", BUNDLED)
def test_bundled_map_builds(name):
    world = mapsource.load_bundled(name)
    assert world is not None
    for plane in PLANE_ORDER:
        assert len(getattr(world, plane)) == world.cubic_size, plane
    assert world.spawns(), "a playable map needs spawns"
    solid = world.type.count(SOLID)
    assert 0 < solid < world.cubic_size, "neither all rock nor all air"
    assert not world.truncated


@pytest.mark.parametrize("name", BUNDLED)
def test_bundled_map_has_a_solid_border(name):
    """The physics treats out of bounds as solid and nothing else stops a player
    leaving the map, so an open edge is a hole straight out of the world."""
    world = mapsource.load_bundled(name)
    assert world is not None
    n = world.ssize
    edge = (
        [world.type[i] for i in range(n)]
        + [world.type[(n - 1) * n + i] for i in range(n)]
        + [world.type[i * n] for i in range(n)]
        + [world.type[i * n + n - 1] for i in range(n)]
    )
    assert set(edge) == {SOLID}


# ---- the writer -------------------------------------------------------------------


@pytest.mark.parametrize("name", BUNDLED)
def test_round_trips_through_the_writer(name):
    """Written and read back, a map must be identical cube for cube."""
    world = mapsource.load_bundled(name)
    assert world is not None
    again = parse_cgz(write_cgz(world), name=name)

    for plane in PLANE_ORDER:
        assert getattr(again, plane) == getattr(world, plane), plane
    assert (again.sfactor, again.title) == (world.sfactor, world.title)
    assert again.waterlevel == world.waterlevel
    assert again.watercolor == world.watercolor
    assert (again.ambient, again.maprevision) == (world.ambient, world.maprevision)
    assert not again.truncated


@pytest.mark.parametrize("name", BUNDLED)
def test_round_trip_preserves_every_entity(name):
    world = mapsource.load_bundled(name)
    assert world is not None
    again = parse_cgz(write_cgz(world), name=name)

    assert len(again.entities) == len(world.entities)
    for wrote, read in zip(world.entities, again.entities, strict=True):
        assert (read.type, read.x, read.y, read.z) == (
            wrote.type,
            wrote.x,
            wrote.y,
            wrote.z,
        )
        assert read.yaw == wrote.yaw
        assert read.attr2 == wrote.attr2


def test_writer_scales_legacy_angles_to_v10():
    """A map parsed from a pre-v10 file holds whole degrees; v10 means tenths, so
    writing one through unscaled would silently turn 90° into 9°."""
    world = mapsource.load_bundled(BUNDLED[0])
    assert world is not None
    spawn = world.spawns()[0]
    spawn.attr1 = 90  # as a pre-v10 file would have stored it
    spawn.yaw = 90.0
    world.legacy_unscaled_attrs = True

    again = parse_cgz(write_cgz(world))
    assert again.spawns()[0].yaw == 90.0


def test_writer_refuses_a_solid_cube_it_cannot_represent():
    """A SOLID record stores only wtex and vdelta. Silently dropping the rest is
    exactly the kind of loss a round trip is supposed to catch."""
    world = mapsource.load_bundled(BUNDLED[0])
    assert world is not None
    floor = bytearray(world.floor)
    floor[world.type.index(SOLID)] = 5
    world.floor = bytes(floor)

    with pytest.raises(CgzError, match="solid cube"):
        write_cgz(world)


def test_run_lengths_longer_than_255_survive():
    """A run is a byte, so anything longer is split — and each continuation still
    repeats the same previous cube. Off-by-one here rewrites the whole grid."""
    source = {
        "sfactor": 6,
        "brushes": [{"op": "room", "rect": [1, 1, 62, 62], "floor": 0, "ceil": 16}],
    }
    world = mapsource.build(source, name="hd_open")
    again = parse_cgz(write_cgz(world))
    assert again.type.count(SPACE) == 62 * 62
    assert again.type == world.type


# ---- playability ------------------------------------------------------------------


def _standable(world: physics.World) -> set[tuple[int, int]]:
    """Cells a player's body actually fits in, standing on that cell's floor."""
    return {
        (cx, cy)
        for cy in range(world.ssize)
        for cx in range(world.ssize)
        if not world.is_solid(cx, cy)
        and physics.can_stand(world, cx + 0.5, cy + 0.5, world.floor_at(cx, cy))
    }


def _reachable(world: physics.World, start: tuple[int, int], cells: set) -> set:
    """Flood fill on foot: a step up costs nothing below `STEP_HEIGHT`, and any
    drop is free — which is what walking (and falling) can actually do."""
    seen = {start}
    queue = deque([start])
    while queue:
        cx, cy = queue.popleft()
        here = world.floor_at(cx, cy)
        for neighbour in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if neighbour in seen or neighbour not in cells:
                continue
            if world.floor_at(*neighbour) - here > physics.STEP_HEIGHT:
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return seen


@pytest.mark.parametrize("name", BUNDLED)
def test_every_spawn_is_somewhere_a_body_fits(name):
    """`spawn_at` resolves the height, but nothing resolves a spawn wedged in a
    wall — the player would simply be unable to move."""
    world = mapsource.load_bundled(name)
    assert world is not None
    sim = physics.World.from_map(world)
    for spawn in world.spawns():
        state = physics.spawn_at(sim, spawn)
        assert physics.can_stand(sim, state.x, state.y, state.z), (
            f"{name}: spawn at ({spawn.x}, {spawn.y}) is not standable"
        )


@pytest.mark.parametrize("name", BUNDLED)
def test_the_whole_map_is_reachable_on_foot(name):
    """Every standable cell connects to every other one. This is what catches a
    raised gallery whose stairs do not reach it, or a room walled off by an
    off-by-one rect — neither of which fails any other check."""
    world = mapsource.load_bundled(name)
    assert world is not None
    sim = physics.World.from_map(world)
    cells = _standable(sim)
    assert cells

    first = physics.spawn_at(sim, world.spawns()[0])
    reached = _reachable(sim, (int(first.x), int(first.y)), cells)
    assert reached == cells, f"{name}: {len(cells - reached)} cells are cut off"


@pytest.mark.parametrize("name", BUNDLED)
def test_every_map_carries_items(name):
    """A bundled map with no items would make pickups a feature only the people
    who own AssaultCube can see, which is the exact asymmetry this module keeps
    refusing: their content is optional, ours is the game."""
    world = mapsource.load_bundled(name)
    assert world is not None
    placed = pickups.place(physics.World.from_map(world), world.entities)
    kinds = {item.kind for item in placed}
    assert len(placed) >= 8, f"{name}: only {len(placed)} items"
    assert {"health", "ammo", "armour"} <= kinds, f"{name}: has only {sorted(kinds)}"


@pytest.mark.parametrize("name", BUNDLED)
def test_every_item_is_somewhere_a_body_can_reach(name):
    """The spawn test, for items — and it catches strictly more.

    `pickups.place` resolves an item onto the floor beneath it, so an item can
    never be *floating*; what it can be is resting inside a pillar, or in a
    sealed room. Either one is invisible until a player spends a match looking
    for an armour that cannot be picked up."""
    world = mapsource.load_bundled(name)
    assert world is not None
    sim = physics.World.from_map(world)
    cells = _standable(sim)
    first = physics.spawn_at(sim, world.spawns()[0])
    reached = _reachable(sim, (int(first.x), int(first.y)), cells)

    for item in pickups.place(sim, world.entities):
        assert physics.can_stand(sim, item.x, item.y, item.z), (
            f"{name}: {item.kind} at ({item.x}, {item.y}) is inside something"
        )
        assert (int(item.x), int(item.y)) in reached, (
            f"{name}: {item.kind} at ({item.x}, {item.y}) cannot be walked to"
        )


@pytest.mark.parametrize("name", BUNDLED)
def test_items_are_not_on_top_of_a_spawn(name):
    """An item within reach of a spawn is a free pickup for whoever died last,
    which turns dying into a resupply."""
    world = mapsource.load_bundled(name)
    assert world is not None
    sim = physics.World.from_map(world)
    spawns = [physics.spawn_at(sim, s) for s in world.spawns()]
    for item in pickups.place(sim, world.entities):
        for state in spawns:
            assert not pickups.in_reach(item, state.x, state.y, state.z), (
                f"{name}: {item.kind} at ({item.x}, {item.y}) is on a spawn"
            )


@pytest.mark.parametrize("name", BUNDLED)
def test_every_ladder_actually_gets_you_somewhere(name):
    """A ladder is only a route if climbing it ends with you standing somewhere.

    The failure this catches is quiet and specific: our body is 2.2 cubes wide, so
    a ladder placed flush against the lip it serves is already stood on by anyone
    at its foot (`_support` takes the highest floor the body overlaps), and one
    placed too far back drops you off the top before you reach the ledge. Both
    look fine in the map source. So the test climbs each one, the way a player
    would, and insists on arriving.
    """
    world = mapsource.load_bundled(name)
    assert world is not None
    sim = physics.World.from_map(world)

    for ladder in sim.ladders:
        arrived = False
        # Approached from either side: a mapper decides which way a ladder faces
        # by where they put it, and this asks only that *one* approach works.
        for sign in (1.0, -1.0):
            state = physics.PlayerState(
                x=ladder.x,
                y=ladder.y - sign,
                z=ladder.base,
                yaw=math.pi / 2 if sign > 0 else -math.pi / 2,
            )
            for _ in range(600):
                physics.step(
                    sim, state, physics.MoveInput(forward=1.0, dt=1 / 60), 1 / 60
                )
                if state.on_ground and state.z >= ladder.top - physics.STEP_HEIGHT:
                    arrived = True
                    break
            if arrived:
                break
        assert arrived, (
            f"{name}: the ladder at ({ladder.x}, {ladder.y}) cannot be climbed to "
            f"anywhere you can stand — it spans {ladder.base} to {ladder.top}"
        )


@pytest.mark.parametrize("name", BUNDLED)
def test_water_never_covers_the_whole_map(name):
    """The one water slip nothing else notices.

    A plane *below* every floor is how a map says it has no water — every
    official map ships one — so that is not an error. A plane above every floor
    is: the whole map becomes a swimming pool, nobody can jump, and the map
    source looks completely ordinary.
    """
    world = mapsource.load_bundled(name)
    assert world is not None
    sim = physics.World.from_map(world)
    floors = [
        sim.floor_at(x, y)
        for y in range(sim.ssize)
        for x in range(sim.ssize)
        if not sim.is_solid(x, y)
    ]
    assert max(floors) > sim.waterlevel, f"{name}: the water covers the whole map"


@pytest.mark.parametrize("name", BUNDLED)
def test_spawns_are_spread_out(name):
    """Two spawns in the same spot means two players telefragged into each other
    on the first frame of a match."""
    world = mapsource.load_bundled(name)
    assert world is not None
    spawns = world.spawns()
    assert len(spawns) >= 8, "enough spawns for a full match"
    places = {(s.x, s.y) for s in spawns}
    assert len(places) == len(spawns)
    for a in spawns:
        nearest = min(max(abs(a.x - b.x), abs(a.y - b.y)) for b in spawns if b is not a)
        assert nearest >= 4, (
            f"{name}: spawns at ({a.x}, {a.y}) are on top of each other"
        )


# ---- the catalog ------------------------------------------------------------------


def test_bundled_maps_are_listed_and_loadable_without_an_install():
    listed = {m["name"]: m for m in assets.list_maps()}
    for name in BUNDLED:
        assert listed[name]["source"] == "bundled"
        assert assets.load_map(name) is not None


@pytest.mark.parametrize(
    "name", ["hd_../../etc/passwd", "hd_a/b", "hd_nope", "../hd_pit", "hd_", ""]
)
def test_bundled_lookup_refuses_anything_that_is_not_a_bundled_name(name):
    assert mapsource.load_bundled(name) is None


def test_source_errors_name_the_problem():
    with pytest.raises(CgzError, match="border"):
        mapsource.build(
            {"sfactor": 6, "brushes": [{"op": "room", "rect": [0, 0, 8, 8]}]}
        )
    with pytest.raises(CgzError, match="unknown op"):
        mapsource.build(
            {"sfactor": 6, "brushes": [{"op": "carve", "rect": [1, 1, 8, 8]}]}
        )
    with pytest.raises(CgzError, match="not below its ceiling"):
        mapsource.build(
            {
                "sfactor": 6,
                "brushes": [{"op": "room", "rect": [1, 1, 8, 8], "floor": 20}],
            }
        )
