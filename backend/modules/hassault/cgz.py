"""Reader for AssaultCube `.cgz` maps.

A `.cgz` is a gzip stream containing a fixed header, a run of map entities, and a
run-length-encoded grid of cubes. This is a faithful port of `load_world` and
`rldecodecubes` from `source/src/worldio.cpp`; the constants below are transcribed
from `world.h` rather than inferred, because a wrong offset here yields plausible
garbage rather than an error.

Cube 1 worlds — which AssaultCube still uses — are **not** BSP. The world is a flat
2D grid of columns, each with a floor and ceiling height, a type, and per-face
texture ids. That is why rendering this in WebGL is tractable: the whole map is
`(1 << sfactor)²` records of nine bytes.

Only the *code* this is ported from is freely licensed. AssaultCube's **content**
(maps, textures, models, sounds) is copyright and may not be redistributed, so no
AssaultCube map is committed here — that reader is pointed at a local install.
The writer at the bottom of this file is the other half of that: it is how the
app ships maps of its *own* (`mapsource.py`) and so needs no install to play.
See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field
from pathlib import Path

# ---- constants transcribed from source/src/world.h --------------------------------

# Cube types. Order matters — it is the on-disk encoding.
SOLID = 0
CORNER = 1
FHF = 2  # floor heightfield, uses neighbouring vdelta values
CHF = 3  # ceiling heightfield
SPACE = 4
SEMISOLID = 5  # only produced by mipmapping, never stored
MAXTYPE = 6

TYPE_NAMES = {
    SOLID: "solid",
    CORNER: "corner",
    FHF: "fhf",
    CHF: "chf",
    SPACE: "space",
    SEMISOLID: "semisolid",
}

# Hardcoded texture slots (world.h).
DEFAULT_SKY, DEFAULT_LIQUID, DEFAULT_WALL, DEFAULT_FLOOR, DEFAULT_CEIL = 0, 1, 2, 3, 4

# sqr.tag bits.
TAG_TRIGGER_MASK = 0x3F
TAG_CLIP = 0x40  # clips everything
TAG_PLCLIP = 0x80  # clips players only

SMALLEST_FACTOR = 6
LARGEST_FACTOR = 11
MAX_ENTITIES = 65535

# `sizeof(header)` and the leading part written before `waterlevel`. The base is
# every field up to the texture lists: 4+4+4+4+4 + 128 + 3*256 = 916; the tail is
# exactly 16 ints (waterlevel, watercolor, maprevision, ambient, flags, timestamp,
# reserved[10]) = 64. These two numbers drive every offset below.
SIZEOF_HEADER = 980
SIZEOF_BASEHEADER = 916

# `load_world` scales pre-v10 water levels; v10 stores tenths of a cube.
WATERLEVEL_SCALING = 10

MAGICS = (b"CUBE", b"ACMP")

# Static entity types (entity.h). Index is the on-disk `type` byte.
ENTITY_NAMES = [
    "notused",
    "light",
    "playerstart",
    "clips",
    "ammo",
    "grenade",
    "health",
    "helmet",
    "armour",
    "akimbo",
    "mapmodel",
    "carrot",
    "ladder",
    "ctf_flag",
    "sound",
    "clip",
    "plclip",
    "dummyent",
]
LIGHT = 1
PLAYERSTART = 2
MAPMODEL = 10
CTF_FLAG = 13

# Entity types whose `attr1` is a yaw angle.
ANGLED_TYPES = (PLAYERSTART, MAPMODEL, CTF_FLAG)

# v10 stores angles multiplied by ten (`ENTSCALE10`), older maps store whole
# degrees. Confirmed against the shipped 1.3 map set rather than assumed: all
# 1741 player spawns across the 44 official maps have `attr1` a multiple of ten,
# spanning 0..3590, with nothing at or above 3600.
ENTSCALE10 = 10


# The order the cube planes are concatenated in over the wire. Pinned here so the
# reader and the client agree on one definition rather than two that can drift.
PLANE_ORDER = (
    "type",
    "floor",
    "ceil",
    "wtex",
    "ftex",
    "ctex",
    "vdelta",
    "utex",
    "tag",
)


class CgzError(ValueError):
    """A map file that cannot be read. Carries a message meant for the user."""


def fix_header_size(version: int, header_size: int) -> int:
    """Port of `fixmapheadersize` — `headersize` is not trustworthy before v10.

    Versions 7 and 8 wrote an extra `char mediareq[128]` that later versions
    dropped, and everything below v10 recorded a size that may disagree with what
    was actually written.
    """
    if version < 4:
        return SIZEOF_BASEHEADER
    if version in (7, 8):
        return SIZEOF_HEADER + 128
    if version < 10 or header_size < SIZEOF_HEADER:
        return SIZEOF_HEADER
    return header_size


@dataclass(slots=True)
class MapEntity:
    """One static entity: a spawn point, item, light, map model, flag, or clip."""

    type: int
    name: str
    x: int
    y: int
    z: int
    attr1: int = 0
    attr2: int = 0
    attr3: int = 0
    attr4: int = 0
    attr5: int = 0
    attr6: int = 0
    attr7: int = 0
    # Yaw in real degrees for entities that carry one, else None. Resolved at
    # parse time because the raw units depend on the map version, which an
    # individual entity has no way to know.
    yaw: float | None = None


@dataclass(slots=True)
class CgzMap:
    """A parsed map: header fields, entities, and the cube grid as flat planes.

    The grid is held as nine parallel byte planes rather than a list of objects.
    A 256×256 map is 65 536 cubes, so an object per cube costs megabytes and,
    more importantly, cannot be handed to the browser without re-encoding — the
    planes go over the wire verbatim and become typed arrays.

    Index a cube at `(x, y)` as `y * ssize + x`, matching the engine's
    `SWS(w,x,y,s)` macro.
    """

    name: str
    magic: str
    version: int
    header_size: int
    sfactor: int
    title: str
    waterlevel: float
    watercolor: tuple[int, int, int, int]
    maprevision: int
    ambient: int
    flags: int
    timestamp: int
    entities: list[MapEntity] = field(default_factory=list)

    # Cube planes, each `ssize * ssize` bytes. `floor`/`ceil` are signed.
    type: bytes = b""
    floor: bytes = b""
    ceil: bytes = b""
    wtex: bytes = b""
    ftex: bytes = b""
    ctex: bytes = b""
    vdelta: bytes = b""
    utex: bytes = b""
    tag: bytes = b""

    # True when the file predates v10, whose entity attributes need a scaling
    # table this reader does not apply. Positions are unaffected.
    legacy_unscaled_attrs: bool = False
    truncated: bool = False

    @property
    def ssize(self) -> int:
        return 1 << self.sfactor

    @property
    def cubic_size(self) -> int:
        return self.ssize * self.ssize

    def spawns(self, team: int | None = None) -> list[MapEntity]:
        """Player spawn points, optionally for one team (attr2: 0 CLA, 1 RVSF)."""
        found = [e for e in self.entities if e.type == PLAYERSTART]
        return found if team is None else [e for e in found if e.attr2 == team]


def _decode_cubes(buf: bytes, cubic_size: int, version: int) -> tuple[list, bool]:
    """Port of `rldecodecubes`. Returns (nine planes, truncated).

    The encoding is a byte-tagged run: `255` repeats the previous cube N times,
    `SOLID` carries only a wall texture and a vdelta, and anything else is a full
    nine-field record. `253` is a SOLID written with all textures (the editor's
    undo path) and decodes as SOLID.
    """
    t_type = bytearray(cubic_size)
    t_floor = bytearray(cubic_size)
    t_ceil = bytearray(cubic_size)
    t_wtex = bytearray(cubic_size)
    t_ftex = bytearray(cubic_size)
    t_ctex = bytearray(cubic_size)
    t_vdelta = bytearray(cubic_size)
    t_utex = bytearray(cubic_size)
    t_tag = bytearray(cubic_size)

    n = len(buf)
    pos = 0
    i = 0
    have_prev = False
    truncated = False

    def read() -> int:
        nonlocal pos
        if pos >= n:
            raise IndexError
        value = buf[pos]
        pos += 1
        return value

    while i < cubic_size:
        try:
            ctype = read()
            if ctype == 255:
                # Repeat the previous cube. With no previous cube this is a
                # corrupt stream, exactly as the engine treats it.
                if not have_prev:
                    raise IndexError
                count = read()
                prev = i - 1
                for _ in range(count):
                    if i >= cubic_size:
                        break
                    t_type[i] = t_type[prev]
                    t_floor[i] = t_floor[prev]
                    t_ceil[i] = t_ceil[prev]
                    t_wtex[i] = t_wtex[prev]
                    t_ftex[i] = t_ftex[prev]
                    t_ctex[i] = t_ctex[prev]
                    t_vdelta[i] = t_vdelta[prev]
                    t_utex[i] = t_utex[prev]
                    t_tag[i] = t_tag[prev]
                    i += 1
                have_prev = True
                continue

            if ctype == 254:
                # v<=2 only: repeat the previous cube with a new light value.
                if not have_prev:
                    raise IndexError
                prev = i - 1
                t_type[i] = t_type[prev]
                t_floor[i] = t_floor[prev]
                t_ceil[i] = t_ceil[prev]
                t_wtex[i] = t_wtex[prev]
                t_ftex[i] = t_ftex[prev]
                t_ctex[i] = t_ctex[prev]
                t_vdelta[i] = t_vdelta[prev]
                t_utex[i] = t_utex[prev]
                t_tag[i] = t_tag[prev]
                read()
                read()
                i += 1
                have_prev = True
                continue

            # `sqrdefault`: SOLID, floor 0, ceil 16, default textures, tag 0.
            t_floor[i] = 0
            t_ceil[i] = 16
            t_ftex[i] = DEFAULT_FLOOR
            t_ctex[i] = DEFAULT_CEIL
            t_wtex[i] = t_utex[i] = DEFAULT_WALL
            t_tag[i] = 0
            t_vdelta[i] = 0

            if ctype == SOLID or ctype == 253:
                t_type[i] = SOLID
                t_utex[i] = t_wtex[i] = read()
                t_vdelta[i] = read()
                if version <= 2:
                    read()
                    read()
            else:
                if ctype < 0 or ctype >= MAXTYPE:
                    raise IndexError
                t_type[i] = ctype
                floor = read()
                ceiling = read()
                # Signed bytes on disk; compare as signed, store back as raw.
                sf = floor - 256 if floor > 127 else floor
                sc = ceiling - 256 if ceiling > 127 else ceiling
                if sf >= sc:
                    sf = sc - 1
                t_floor[i] = sf & 0xFF
                t_ceil[i] = sc & 0xFF
                t_wtex[i] = read()
                t_ftex[i] = read()
                t_ctex[i] = read()
                if version <= 2:
                    read()
                    read()
                t_vdelta[i] = read()
                t_utex[i] = read() if version >= 2 else t_wtex[i]
                t_tag[i] = read() if version >= 5 else 0
            i += 1
            have_prev = True
        except IndexError:
            # Ran off the end of the stream. The engine fills the rest with
            # defaults and carries on rather than refusing the map; a truncated
            # map is still playable, and refusing would lose the whole file.
            while i < cubic_size:
                t_type[i] = SOLID
                t_floor[i] = 0
                t_ceil[i] = 16
                t_ftex[i] = DEFAULT_FLOOR
                t_ctex[i] = DEFAULT_CEIL
                t_wtex[i] = t_utex[i] = DEFAULT_WALL
                i += 1
            truncated = True
            break

    planes = [
        bytes(t_type),
        bytes(t_floor),
        bytes(t_ceil),
        bytes(t_wtex),
        bytes(t_ftex),
        bytes(t_ctex),
        bytes(t_vdelta),
        bytes(t_utex),
        bytes(t_tag),
    ]
    return planes, truncated


def parse_cgz(data: bytes, name: str = "") -> CgzMap:
    """Parse the bytes of a `.cgz` file. Raises `CgzError` on anything malformed."""
    try:
        raw = gzip.decompress(data)
    except OSError as exc:
        raise CgzError(f"not a gzip-compressed map: {exc}") from exc

    if len(raw) < SIZEOF_BASEHEADER:
        raise CgzError("file is too short to contain a map header")

    magic = raw[0:4]
    if magic not in MAGICS:
        raise CgzError(
            f"bad magic {magic!r} — expected one of {[m.decode() for m in MAGICS]}"
        )
    version, header_size, sfactor, numents = struct.unpack_from("<iiii", raw, 4)
    if not SMALLEST_FACTOR <= sfactor <= LARGEST_FACTOR:
        raise CgzError(f"illegal map size: sfactor {sfactor}")
    if numents > MAX_ENTITIES or numents < 0:
        raise CgzError(f"illegal entity count: {numents}")

    title = raw[20:148].split(b"\0")[0].decode("latin-1", "replace")

    header_size = fix_header_size(version, header_size)

    # The tail of the header (water, ambient, flags) only exists from v4.
    waterlevel, ambient, maprevision, flags, timestamp = 0, 0, 0, 0, 0
    watercolor = (0, 0, 0, 0)
    if version >= 4 and header_size >= SIZEOF_HEADER and len(raw) >= SIZEOF_HEADER:
        (waterlevel,) = struct.unpack_from("<i", raw, 916)
        watercolor = tuple(raw[920:924])  # type: ignore[assignment]
        maprevision, ambient, flags, timestamp = struct.unpack_from("<iiii", raw, 924)
    else:
        waterlevel = -100000
    if version < 10:
        waterlevel *= WATERLEVEL_SCALING

    # Entities begin at `headersize`, *not* at the end of the struct: v10 maps
    # carry a variable-length extra-header block between the two, and every
    # official 1.3 map has one. Reading from 980 would land mid-blob.
    offset = header_size
    entity_size = 12 if version < 10 else 16
    entities: list[MapEntity] = []
    for _ in range(numents):
        if offset + entity_size > len(raw):
            break
        x, y, z, attr1 = struct.unpack_from("<hhhh", raw, offset)
        etype, attr2, attr3, attr4 = struct.unpack_from("<BBBB", raw, offset + 8)
        attr5 = attr6 = attr7 = 0
        if entity_size == 16:
            (attr5,) = struct.unpack_from("<h", raw, offset + 12)
            (attr6,) = struct.unpack_from("<b", raw, offset + 14)
            (attr7,) = struct.unpack_from("<B", raw, offset + 15)
        offset += entity_size
        yaw = None
        if etype in ANGLED_TYPES:
            yaw = (attr1 / ENTSCALE10) if version >= 10 else float(attr1)
        entities.append(
            MapEntity(
                type=etype,
                name=ENTITY_NAMES[etype] if etype < len(ENTITY_NAMES) else "unknown",
                yaw=yaw,
                x=x,
                y=y,
                z=z,
                attr1=attr1,
                attr2=attr2,
                attr3=attr3,
                attr4=attr4,
                attr5=attr5,
                attr6=attr6,
                attr7=attr7,
            )
        )

    cubic_size = (1 << sfactor) ** 2
    planes, truncated = _decode_cubes(raw[offset:], cubic_size, version)

    return CgzMap(
        name=name or title,
        magic=magic.decode(),
        version=version,
        header_size=header_size,
        sfactor=sfactor,
        title=title,
        waterlevel=waterlevel / WATERLEVEL_SCALING,
        watercolor=watercolor,
        maprevision=maprevision,
        ambient=ambient,
        flags=flags,
        timestamp=timestamp,
        entities=entities,
        type=planes[0],
        floor=planes[1],
        ceil=planes[2],
        wtex=planes[3],
        ftex=planes[4],
        ctex=planes[5],
        vdelta=planes[6],
        utex=planes[7],
        tag=planes[8],
        legacy_unscaled_attrs=version < 10,
        truncated=truncated,
    )


def read_cgz(path: str | Path) -> CgzMap:
    """Parse a `.cgz` from disk, naming the map after its filename."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CgzError(f"could not read {path.name}: {exc}") from exc
    return parse_cgz(data, name=path.stem)


# ---- writing ----------------------------------------------------------------------
#
# The inverse of everything above, used for the maps this project authors itself
# (`mapsource.py`) rather than for anything read out of an install. Two consumers:
# the export route, so a bundled map can be opened in AssaultCube's own editor,
# and the round-trip test, which is what actually pins the writer — a map built
# from source, written, and parsed back must come out identical cube for cube.
#
# Only version 10 is written. Writing an older version would mean reproducing the
# quirks this reader exists to absorb (the `mediareq` block, unscaled angles,
# 12-byte entities), and nothing needs it.

WRITE_VERSION = 10

# Value of every field a SOLID record does *not* store, from `sqrdefault`.
_SOLID_DEFAULTS = (0, 16, DEFAULT_FLOOR, DEFAULT_CEIL, 0)


def _encode_record(rec: tuple[int, ...]) -> bytes:
    """One cube, in `rldecodecubes`' record form."""
    ctype, floor, ceil, wtex, ftex, ctex, vdelta, utex, tag = rec
    if ctype == SOLID:
        # A SOLID record carries only wtex and vdelta — the reader fills the rest
        # in from `sqrdefault`, so a solid cube holding anything else would be
        # silently rewritten by its own round trip. Refuse instead of losing it.
        if (floor, ceil, ftex, ctex, tag) != _SOLID_DEFAULTS or utex != wtex:
            raise CgzError(
                "a solid cube can only store wtex and vdelta; this one carries "
                f"floor={floor} ceil={ceil} ftex={ftex} ctex={ctex} utex={utex} tag={tag}"
            )
        return bytes((SOLID, wtex, vdelta))
    if not 0 <= ctype < MAXTYPE:
        raise CgzError(f"illegal cube type {ctype}")
    if ctype == SEMISOLID:
        # Only ever produced by mipmapping, never stored — and the reader would
        # hand it straight back as a solid the physics can't see through.
        raise CgzError("semisolid cubes are a mipmap artifact and are never stored")
    return bytes((ctype, floor, ceil, wtex, ftex, ctex, vdelta, utex, tag))


def _encode_cubes(world: CgzMap) -> bytes:
    """Run-length encode the grid, the inverse of `_decode_cubes`.

    A run is `255` plus a count and repeats the *previous* cube, so consecutive
    runs chain correctly: after one, the reader's "previous" is still the same
    record, which is what lets a count above 255 be split across several.
    """
    records = list(zip(*(getattr(world, plane) for plane in PLANE_ORDER), strict=True))
    out = bytearray()
    i, n = 0, len(records)
    while i < n:
        rec = records[i]
        out += _encode_record(rec)
        j = i + 1
        while j < n and records[j] == rec:
            j += 1
        repeats = j - i - 1
        while repeats > 0:
            chunk = min(repeats, 255)
            out += bytes((255, chunk))
            repeats -= chunk
        i = j
    return bytes(out)


def write_cgz(world: CgzMap) -> bytes:
    """Serialize a map to `.cgz` bytes — a real v10 file AssaultCube can open."""
    if not SMALLEST_FACTOR <= world.sfactor <= LARGEST_FACTOR:
        raise CgzError(f"illegal map size: sfactor {world.sfactor}")
    if len(world.entities) > MAX_ENTITIES:
        raise CgzError(f"too many entities: {len(world.entities)}")

    head = bytearray(SIZEOF_HEADER)
    magic = world.magic.encode("ascii", "ignore")[:4]
    head[0:4] = magic if magic in MAGICS else b"ACMP"
    struct.pack_into(
        "<iiii",
        head,
        4,
        WRITE_VERSION,
        # No extra-header block, so entities begin at exactly `SIZEOF_HEADER` —
        # which `fix_header_size` will hand straight back for a v10 map.
        SIZEOF_HEADER,
        world.sfactor,
        len(world.entities),
    )
    title = world.title.encode("latin-1", "replace")[:127]
    head[20 : 20 + len(title)] = title
    # `texlists[3][256]`, the header's texture-slot tables. This reader ignores
    # them entirely; identity is the neutral value, where all-zero would not be.
    for base in (148, 404, 660):
        head[base : base + 256] = bytes(range(256))
    # v10 stores tenths of a cube, which is what the parsed value was divided by.
    struct.pack_into("<i", head, 916, round(world.waterlevel * WATERLEVEL_SCALING))
    head[920:924] = bytes(world.watercolor)
    struct.pack_into(
        "<iiii",
        head,
        924,
        world.maprevision,
        world.ambient,
        world.flags,
        world.timestamp,
    )

    body = bytearray()
    for e in world.entities:
        attr1 = e.attr1
        # A map parsed from a pre-v10 file holds whole degrees in `attr1`, and
        # this writes v10 — where the same number means a tenth of what it did.
        # Writing it through unscaled would quietly turn 90° into 9°.
        if world.legacy_unscaled_attrs and e.type in ANGLED_TYPES:
            attr1 = round(attr1 * ENTSCALE10)
        body += struct.pack("<hhhh", e.x, e.y, e.z, attr1)
        body += struct.pack("<BBBB", e.type, e.attr2, e.attr3, e.attr4)
        body += struct.pack("<hbB", e.attr5, e.attr6, e.attr7)

    return gzip.compress(bytes(head) + bytes(body) + _encode_cubes(world))
