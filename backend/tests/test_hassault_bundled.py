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

The playability checks themselves now live in `maplint`, and these tests call it.
They were written here and they still run here, but the map designer has to ask
the same questions of a document that is not on disk yet — and a second copy of
them is how an editor ends up happily saving a map this suite would reject. One
definition, two callers; the test names below are what makes a failure say which
rule broke.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.hassault import assets, maplint, mapsource
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


def _own_copy(name: str):
    """A bundled map nobody else is holding.

    `mapsource.load_bundled` is `lru_cache`d and a `CgzMap` is mutable, so a test
    that poked the map it got back was poking the one every later test would be
    handed. Two of them did: one left `hd_atrium` with a solid cube carrying a
    floor of 5, and the other left `legacy_unscaled_attrs` set on it. Nothing
    downstream read those fields, so it stayed invisible until `maplint` started
    asking whether every cube was one the writer could store — and reported a map
    that is perfectly fine on disk.
    """
    return mapsource.build(_read_source(name), name=name)


def _read_source(name: str) -> dict:
    path = mapsource.MAPS_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_writer_scales_legacy_angles_to_v10():
    """A map parsed from a pre-v10 file holds whole degrees; v10 means tenths, so
    writing one through unscaled would silently turn 90° into 9°."""
    world = _own_copy(BUNDLED[0])
    spawn = world.spawns()[0]
    spawn.attr1 = 90  # as a pre-v10 file would have stored it
    spawn.yaw = 90.0
    world.legacy_unscaled_attrs = True

    again = parse_cgz(write_cgz(world))
    assert again.spawns()[0].yaw == 90.0


def test_writer_refuses_a_solid_cube_it_cannot_represent():
    """A SOLID record stores only wtex and vdelta. Silently dropping the rest is
    exactly the kind of loss a round trip is supposed to catch."""
    world = _own_copy(BUNDLED[0])
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


def _findings(name: str) -> dict[str, "maplint.Finding"]:
    """Every playability complaint about a bundled map, keyed by code.

    The checks themselves live in `maplint`, not here. They were written as these
    tests and they still run as these tests, but the map *designer* needs to ask
    the same questions of a document that is not on disk yet — and asking them
    twice is how the editor ends up accepting a map this suite would reject. So
    there is one definition, with two callers.
    """
    world = mapsource.load_bundled(name)
    assert world is not None
    return {f.code: f for f in maplint.lint(world)}


@pytest.mark.parametrize("name", BUNDLED)
def test_bundled_map_is_playable(name):
    """The bar every bundled map clears, in one assertion.

    Named individually below so a failure says *which* rule broke, but this is
    the one that matters: these three maps are the worked examples, and the
    editor refuses to be more permissive than they are.
    """
    complaints = _findings(name)
    assert not complaints, f"{name}: " + "; ".join(
        f"[{f.severity}] {f.message}" for f in complaints.values()
    )


@pytest.mark.parametrize("name", BUNDLED)
def test_every_spawn_is_somewhere_a_body_fits(name):
    """`spawn_at` resolves the height, but nothing resolves a spawn wedged in a
    wall — the player would simply be unable to move."""
    assert "spawn.blocked" not in _findings(name)


@pytest.mark.parametrize("name", BUNDLED)
def test_the_whole_map_is_reachable_on_foot(name):
    """Every standable cell connects to every other one. This is what catches a
    raised gallery whose stairs do not reach it, or a room walled off by an
    off-by-one rect — neither of which fails any other check."""
    assert "world.cutoff" not in _findings(name)


@pytest.mark.parametrize("name", BUNDLED)
def test_every_map_carries_items(name):
    """A bundled map with no items would make pickups a feature only the people
    who own AssaultCube can see, which is the exact asymmetry this module keeps
    refusing: their content is optional, ours is the game."""
    complaints = _findings(name)
    assert "item.few" not in complaints and "item.kinds" not in complaints


@pytest.mark.parametrize("name", BUNDLED)
def test_every_item_is_somewhere_a_body_can_reach(name):
    """The spawn test, for items — and it catches strictly more.

    `pickups.place` resolves an item onto the floor beneath it, so an item can
    never be *floating*; what it can be is resting inside a pillar, or in a
    sealed room. Either one is invisible until a player spends a match looking
    for an armour that cannot be picked up."""
    complaints = _findings(name)
    assert "item.buried" not in complaints and "item.stranded" not in complaints


@pytest.mark.parametrize("name", BUNDLED)
def test_items_are_not_on_top_of_a_spawn(name):
    """An item within reach of a spawn is a free pickup for whoever died last,
    which turns dying into a resupply."""
    assert "item.on_spawn" not in _findings(name)


@pytest.mark.parametrize("name", BUNDLED)
def test_every_ladder_actually_gets_you_somewhere(name):
    """A ladder is only a route if climbing it ends with you standing somewhere.

    The failure this catches is quiet and specific: our body is 2.2 cubes wide, so
    a ladder placed flush against the lip it serves is already stood on by anyone
    at its foot (`_support` takes the highest floor the body overlaps), and one
    placed too far back drops you off the top before you reach the ledge. Both
    look fine in the map source. So the check climbs each one, the way a player
    would, and insists on arriving.
    """
    assert "ladder.dead_end" not in _findings(name)


@pytest.mark.parametrize("name", BUNDLED)
def test_water_never_covers_the_whole_map(name):
    """The one water slip nothing else notices.

    A plane *below* every floor is how a map says it has no water — every
    official map ships one — so that is not an error. A plane above every floor
    is: the whole map becomes a swimming pool, nobody can jump, and the map
    source looks completely ordinary.
    """
    assert "water.floods" not in _findings(name)


@pytest.mark.parametrize("name", BUNDLED)
def test_spawns_are_spread_out(name):
    """Two spawns in the same spot means two players telefragged into each other
    on the first frame of a match."""
    complaints = _findings(name)
    assert "spawn.few" not in complaints and "spawn.crowded" not in complaints


@pytest.mark.parametrize("name", BUNDLED)
def test_the_border_stays_solid(name):
    """Out of bounds reads as solid, and that is the only thing keeping a player
    on the map — so a brush that opens the ring is a hole in the world."""
    assert "border.open" not in _findings(name)


@pytest.mark.parametrize("name", BUNDLED)
def test_every_cube_is_one_the_writer_can_store(name):
    """A map that builds but cannot be written is one you find out about at
    export. `maplint` asks the question at edit time instead."""
    complaints = _findings(name)
    assert not {"cube.type", "cube.semisolid", "cube.solid_lossy"} & set(complaints)


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
