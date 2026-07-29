"""The HorribleAssault `.cgz` reader.

Two kinds of test here. The first are hermetic: maps synthesized in-memory so the
header, entity, and run-length paths are exercised on every machine, CI included.
The second run over a **real AssaultCube install** when one is present and skip
otherwise — the format has enough historical quirks (variable header size, the
`ACMP` magic, v10 attribute scaling) that only real files prove the reader works.

No map is committed here: AssaultCube's content is copyright and redistributable
only inside an unmodified AssaultCube package. See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import gzip
import struct

import pytest

from backend.modules.hassault import assets
from backend.modules.hassault.cgz import (
    DEFAULT_CEIL,
    DEFAULT_FLOOR,
    DEFAULT_WALL,
    PLANE_ORDER,
    PLAYERSTART,
    SIZEOF_HEADER,
    SOLID,
    SPACE,
    CgzError,
    fix_header_size,
    parse_cgz,
    read_cgz,
)

# ---- synthetic maps ---------------------------------------------------------------


def _build_header(
    *,
    magic: bytes = b"ACMP",
    version: int = 10,
    header_size: int = SIZEOF_HEADER,
    sfactor: int = 6,
    numents: int = 0,
    title: bytes = b"test map",
    waterlevel: int = -50,
) -> bytes:
    head = bytearray(SIZEOF_HEADER)
    head[0:4] = magic
    struct.pack_into("<iiii", head, 4, version, header_size, sfactor, numents)
    head[20 : 20 + len(title)] = title
    struct.pack_into("<i", head, 916, waterlevel)
    head[920:924] = bytes((10, 20, 30, 40))
    struct.pack_into("<iiii", head, 924, 7, 3, 0, 12345)  # revision, ambient, …
    return bytes(head)


def _entity(
    etype: int, x: int, y: int, z: int, attr1: int = 0, attr2: int = 0
) -> bytes:
    return (
        struct.pack("<hhhh", x, y, z, attr1)
        + struct.pack("<BBBB", etype, attr2, 0, 0)
        + struct.pack("<hbB", 0, 0, 0)
    )


def _make_map(cubes: bytes, *, entities: bytes = b"", **header) -> bytes:
    numents = header.pop("numents", len(entities) // 16)
    head = _build_header(numents=numents, **header)
    return gzip.compress(head + entities + cubes)


def test_parses_a_minimal_map():
    sfactor = 6
    cubic = (1 << sfactor) ** 2
    # One SOLID cube, then repeat it for the rest of the grid.
    cubes = bytes([SOLID, 42, 0]) + bytes([255, 255]) * (cubic // 255 + 1)
    world = parse_cgz(_make_map(cubes, sfactor=sfactor), name="mini")

    assert world.magic == "ACMP" and world.version == 10
    assert world.ssize == 64 and world.cubic_size == cubic
    assert len(world.type) == cubic
    assert world.type[0] == SOLID and world.wtex[0] == 42
    assert world.title == "test map"
    assert world.waterlevel == -5.0  # v10 stores tenths
    assert world.watercolor == (10, 20, 30, 40)
    assert not world.truncated


def test_run_length_repeat_copies_the_previous_cube():
    sfactor = 6
    cubic = (1 << sfactor) ** 2
    # A full SPACE record, then a run of 9 copies, then fill.
    full = bytes([SPACE, 0, 8, 11, 12, 13, 3, 14, 0])
    cubes = full + bytes([255, 9]) + bytes([255, 255]) * (cubic // 255 + 1)
    world = parse_cgz(_make_map(cubes, sfactor=sfactor))

    for i in range(10):
        assert world.type[i] == SPACE, i
        assert world.wtex[i] == 11 and world.ftex[i] == 12 and world.ctex[i] == 13
        assert world.vdelta[i] == 3 and world.utex[i] == 14


def test_solid_cubes_get_engine_defaults():
    """A SOLID record carries only wtex and vdelta; everything else comes from
    `sqrdefault`, so a reader that leaves them zero renders a different map."""
    cubic = 64 * 64
    cubes = bytes([SOLID, 77, 5]) + bytes([255, 255]) * (cubic // 255 + 1)
    world = parse_cgz(_make_map(cubes, sfactor=6))
    assert world.wtex[0] == 77 and world.utex[0] == 77
    assert world.ftex[0] == DEFAULT_FLOOR and world.ctex[0] == DEFAULT_CEIL
    assert world.floor[0] == 0 and world.ceil[0] == 16


def test_floor_is_forced_below_ceiling():
    """`load_world` clamps floor < ceil for pre-12_13 maps; a map that violates it
    must not produce an inverted column."""
    cubic = 64 * 64
    cubes = bytes([SPACE, 20, 10, 1, 2, 3, 0, 4, 0]) + bytes([255, 255]) * (
        cubic // 255 + 1
    )
    world = parse_cgz(_make_map(cubes, sfactor=6))
    floor = world.floor[0] - 256 if world.floor[0] > 127 else world.floor[0]
    ceiling = world.ceil[0] - 256 if world.ceil[0] > 127 else world.ceil[0]
    assert floor < ceiling and floor == 9


def test_truncated_cube_stream_is_filled_not_rejected():
    """A short stream fills the remainder with defaults and flags it — refusing
    would throw away an otherwise readable map."""
    world = parse_cgz(_make_map(bytes([SOLID, 1, 0]), sfactor=6))
    assert world.truncated
    assert len(world.type) == 64 * 64
    assert world.type[-1] == SOLID and world.wtex[-1] == DEFAULT_WALL


def test_entities_are_read_and_angles_scaled():
    cubic = 64 * 64
    cubes = bytes([SOLID, 1, 0]) + bytes([255, 255]) * (cubic // 255 + 1)
    ents = _entity(PLAYERSTART, 100, 120, 14, attr1=1810, attr2=1)
    world = parse_cgz(_make_map(cubes, entities=ents, sfactor=6, numents=1))

    assert len(world.entities) == 1
    spawn = world.entities[0]
    assert spawn.name == "playerstart"
    assert (spawn.x, spawn.y, spawn.z) == (100, 120, 14)
    assert spawn.yaw == 181.0, "v10 stores angles multiplied by ten"
    assert world.spawns(1) == [spawn] and world.spawns(0) == []


def test_entities_start_at_headersize_not_at_the_struct_end():
    """v10 maps carry a variable extra-header block between the struct and the
    entities — every official 1.3 map does. Reading from 980 lands mid-blob."""
    cubic = 64 * 64
    cubes = bytes([SOLID, 1, 0]) + bytes([255, 255]) * (cubic // 255 + 1)
    extra = b"\xde\xad\xbe\xef" * 16  # 64 bytes of header extra
    head = _build_header(sfactor=6, numents=1, header_size=SIZEOF_HEADER + len(extra))
    ents = _entity(PLAYERSTART, 7, 8, 9, attr1=900)
    world = parse_cgz(gzip.compress(head + extra + ents + cubes))

    assert len(world.entities) == 1
    assert (world.entities[0].x, world.entities[0].y) == (7, 8)


def test_old_format_entities_are_twelve_bytes():
    cubic = 64 * 64
    cubes = bytes([SOLID, 1, 0]) + bytes([255, 255]) * (cubic // 255 + 1)
    ent = struct.pack("<hhhh", 5, 6, 7, 90) + struct.pack("<BBBB", PLAYERSTART, 0, 0, 0)
    head = _build_header(version=9, sfactor=6, numents=1)
    world = parse_cgz(gzip.compress(head + ent + cubes))

    assert len(world.entities) == 1
    assert world.entities[0].yaw == 90.0, "pre-v10 angles are whole degrees"
    assert world.legacy_unscaled_attrs


def test_cube_magic_is_accepted_too():
    cubic = 64 * 64
    cubes = bytes([SOLID, 1, 0]) + bytes([255, 255]) * (cubic // 255 + 1)
    world = parse_cgz(_make_map(cubes, magic=b"CUBE", sfactor=6))
    assert world.magic == "CUBE"


@pytest.mark.parametrize(
    "version,size,expected",
    [
        (3, 4000, 916),  # pre-v4: base header only
        (7, 999, SIZEOF_HEADER + 128),  # mediareq
        (8, 999, SIZEOF_HEADER + 128),
        (9, 4000, SIZEOF_HEADER),  # untrustworthy before v10
        (10, 400, SIZEOF_HEADER),  # too small to be real
        (10, 3024, 3024),  # trusted
    ],
)
def test_fix_header_size(version, size, expected):
    assert fix_header_size(version, size) == expected


# ---- rejection --------------------------------------------------------------------


def test_rejects_non_gzip():
    with pytest.raises(CgzError, match="gzip"):
        parse_cgz(b"definitely not gzip")


def test_rejects_bad_magic():
    cubes = bytes([SOLID, 1, 0]) * 100
    with pytest.raises(CgzError, match="magic"):
        parse_cgz(_make_map(cubes, magic=b"NOPE", sfactor=6))


@pytest.mark.parametrize("sfactor", [0, 5, 12, 99])
def test_rejects_illegal_map_size(sfactor):
    head = _build_header(sfactor=sfactor)
    with pytest.raises(CgzError, match="illegal map size"):
        parse_cgz(gzip.compress(head + bytes(100)))


def test_rejects_short_file():
    with pytest.raises(CgzError, match="too short"):
        parse_cgz(gzip.compress(b"ACMP" + bytes(20)))


# ---- path safety ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "..", "a/b", "a\\b", "map.cgz", "", "map;rm", "map name"],
)
def test_find_map_refuses_anything_that_is_not_a_bare_name(name):
    assert assets.find_map(name) is None


# ---- the real corpus, when an install is present ----------------------------------

_INSTALL = assets.install_root()
needs_install = pytest.mark.skipif(
    _INSTALL is None, reason="no local AssaultCube install to read"
)


def installed_maps() -> list[dict[str, str]]:
    """Only the install's maps. `list_maps` also carries the ones this app ships,
    which are built from source and have no `.cgz` on disk to read — they are
    covered by `test_hassault_bundled.py`, and this file is about real files."""
    return [m for m in assets.list_maps() if m["source"] != "bundled"]


@needs_install
def test_every_installed_map_parses():
    """The reader must handle the whole shipped map set, not just a happy path."""
    failures = []
    for summary in installed_maps():
        path = assets.find_map(summary["name"])
        assert path is not None
        try:
            world = read_cgz(path)
        except CgzError as exc:
            failures.append(f"{summary['name']}: {exc}")
            continue
        if world.truncated:
            failures.append(f"{summary['name']}: cube stream ran short")
        if len(world.type) != world.cubic_size:
            failures.append(f"{summary['name']}: wrong grid size")
    assert not failures, "maps failed to parse: " + "; ".join(failures)


@needs_install
def test_installed_maps_have_plausible_content():
    maps = installed_maps()
    assert maps, "an install was detected but holds no maps"
    world = read_cgz(assets.find_map(maps[0]["name"]))

    assert world.entities, "a real map always has entities"
    assert world.spawns(), "a real map always has player spawns"
    # Every plane is exactly one byte per cube — this is what lets the grid be
    # shipped as typed arrays.
    for plane in PLANE_ORDER:
        assert len(getattr(world, plane)) == world.cubic_size, plane
    # A playable map is neither all solid nor all empty.
    solid = world.type.count(SOLID)
    assert 0 < solid < world.cubic_size


@needs_install
def test_all_spawn_angles_are_in_range():
    """Cross-checks the v10 angle scaling against the whole corpus: a wrong
    divisor would push yaw outside 0..360."""
    for summary in installed_maps():
        world = read_cgz(assets.find_map(summary["name"]))
        for spawn in world.spawns():
            assert 0.0 <= (spawn.yaw or 0.0) < 360.0, (summary["name"], spawn.yaw)
