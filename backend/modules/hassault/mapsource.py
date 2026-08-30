"""Maps this project authors itself, built from a declarative source.

HorribleAssault cannot ship AssaultCube's maps — its content is redistributable
only inside an unmodified AssaultCube package, and this repo is public. That
restriction is about *their* maps. It says nothing about ours, and the format is
understood well enough to write (`cgz.write_cgz`), so the game ships with its own
maps and needs no install to be playable. Pointing `hassault.installPath` at a
real AssaultCube is now an *enhancement* — 44 more maps — rather than the price
of entry.

**The source of truth is the JSON in `maps/`, not a `.cgz`.** A committed binary
would be an opaque blob in a public repo: it cannot be reviewed, it cannot be
diffed, and nudging one spawn means regenerating something nobody can read. A
map here is a few dozen rectangles, so it diffs like code and a reviewer can see
what changed. `write_cgz` then exists for the two directions that genuinely want
a file: exporting a map so it opens in AssaultCube's own editor, and the
round-trip test that pins the writer against the reader.

## The format

A Cube 1 world is a flat grid of columns — one floor height, one ceiling height,
and per-face texture ids per cell. There is no overlapping geometry, so there is
nothing to model beyond painting rectangles onto that grid. The whole vocabulary
is three brush ops applied in order over solid rock:

```json
{
  "title": "Atrium",
  "sfactor": 7,
  "brushes": [
    { "op": "room",   "rect": [10, 10, 40, 40], "floor": 0, "ceil": 14 },
    { "op": "solid",  "rect": [24, 24, 4, 4], "wtex": 9 },
    { "op": "stairs", "rect": [30, 10, 8, 6], "axis": "y", "from": 0, "to": 6 }
  ],
  "entities": [{ "type": "playerstart", "x": 12, "y": 12, "yaw": 45, "team": 0 }]
}
```

`rect` is `[x, y, width, height]` in cells. Later brushes overwrite earlier ones,
so a `solid` after a `room` cuts a pillar back out of it.

Heights are in cubes and **signed** — a floor may go below zero — and the units
are the ones the physics reads directly: a body needs `PLAYER_EYE_HEIGHT +
PLAYER_ABOVE_EYE` (5.2) of headroom to stand, and can step up `STEP_HEIGHT`
(1.6) without jumping, which is what makes a run of one-cube stairs walkable.

Texture ids are raw slot numbers. Nothing resolves them to images yet — the
renderer tints by id — but distinct ids still read as distinct materials, so
they are chosen to be far apart rather than left at the defaults.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.modules.hassault.cgz import (
    ANGLED_TYPES,
    DEFAULT_CEIL,
    DEFAULT_FLOOR,
    DEFAULT_WALL,
    ENTITY_NAMES,
    ENTSCALE10,
    LARGEST_FACTOR,
    LIGHT,
    PLAYERSTART,
    SMALLEST_FACTOR,
    SOLID,
    SPACE,
    CgzError,
    CgzMap,
    MapEntity,
)

MAPS_DIR = Path(__file__).parent / "maps"

# Every bundled map is named with this prefix. It keeps our maps and an install's
# maps in one flat namespace without either being able to shadow the other — a
# bundled `ac_desert` would be a nasty surprise for someone who owns the real one.
BUNDLED_PREFIX = "hd_"

# Solid rock, the state every cell starts in. Matches `sqrdefault` in the fields a
# SOLID record cannot store, because `write_cgz` refuses anything else.
ROCK_WTEX = 6

# A `playerstart`'s z is the mapper's *eye* at placement time, and across the
# official corpus the modal value is exactly four above the floor. Bundled maps
# reproduce that so an exported map looks placed-by-hand in AC's editor; the
# simulation ignores it either way and takes the height from the world.
SPAWN_EYE_OFFSET = 4

_MIN_HEIGHT, _MAX_HEIGHT = -128, 127


def _fail(message: str) -> CgzError:
    return CgzError(f"bad map source: {message}")


def _byte(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
        raise _fail(f"{field} must be a byte 0..255, got {value!r}")
    return value


def _height(value: Any, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not _MIN_HEIGHT <= value <= _MAX_HEIGHT
    ):
        raise _fail(f"{field} must be a signed byte -128..127, got {value!r}")
    return value


class _Grid:
    """The nine planes under construction, addressed by cell."""

    def __init__(self, ssize: int) -> None:
        self.ssize = ssize
        n = ssize * ssize
        # Which brush last painted each cell, or -1 for untouched rock. Brushes
        # compose by overwrite, so "who owns this cell" is only answerable while
        # they are being applied — recovering it afterwards would mean replaying
        # the list, which is the same work done twice. The editor needs it to
        # turn a crosshair on a wall into a brush you can drag.
        self.owners = [-1] * n
        self.brush = -1
        self.type = bytearray([SOLID]) * n
        self.floor = bytearray(n)
        self.ceil = bytearray([16]) * n
        self.wtex = bytearray([ROCK_WTEX]) * n
        self.ftex = bytearray([DEFAULT_FLOOR]) * n
        self.ctex = bytearray([DEFAULT_CEIL]) * n
        self.vdelta = bytearray(n)
        self.utex = bytearray([ROCK_WTEX]) * n
        self.tag = bytearray(n)

    def cells(self, rect: Any) -> list[int]:
        """Validate a `[x, y, w, h]` rect and return the indices it covers.

        The outermost ring is refused rather than clipped. The engine guarantees
        a solid border and the physics leans on it — `is_solid` treats out of
        bounds as solid, which is the only thing keeping a player on the map — so
        a brush that opens the edge is a bug in the map, not something to fix up.
        """
        if not isinstance(rect, list) or len(rect) != 4:
            raise _fail(f"rect must be [x, y, w, h], got {rect!r}")
        x, y, w, h = rect
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in rect):
            raise _fail(f"rect values must be integers, got {rect!r}")
        if w <= 0 or h <= 0:
            raise _fail(f"rect has no area: {rect!r}")
        if x < 1 or y < 1 or x + w > self.ssize - 1 or y + h > self.ssize - 1:
            raise _fail(
                f"rect {rect!r} touches the map border; the outer ring must stay "
                f"solid (map is {self.ssize}x{self.ssize})"
            )
        return [(y + dy) * self.ssize + (x + dx) for dy in range(h) for dx in range(w)]

    def paint(
        self,
        index: int,
        *,
        ctype: int,
        floor: int,
        ceil: int,
        wtex: int,
        ftex: int,
        ctex: int,
        utex: int,
        tag: int,
    ) -> None:
        self.type[index] = ctype
        self.floor[index] = floor & 0xFF
        self.ceil[index] = ceil & 0xFF
        self.wtex[index] = wtex
        self.ftex[index] = ftex
        self.ctex[index] = ctex
        self.utex[index] = utex
        self.tag[index] = tag
        self.vdelta[index] = 0
        self.owners[index] = self.brush

    def floor_of(self, index: int) -> int:
        v = self.floor[index]
        return v - 256 if v > 127 else v


def _op_room(grid: _Grid, brush: dict[str, Any]) -> None:
    """Carve open space: a floor, a ceiling, and the walls that fall out of it."""
    floor = _height(brush.get("floor", 0), "room.floor")
    ceil = _height(brush.get("ceil", 16), "room.ceil")
    if floor >= ceil:
        raise _fail(f"room floor {floor} is not below its ceiling {ceil}")
    wtex = _byte(brush.get("wtex", DEFAULT_WALL), "room.wtex")
    kwargs = {
        "ctype": SPACE,
        "floor": floor,
        "ceil": ceil,
        "wtex": wtex,
        "ftex": _byte(brush.get("ftex", DEFAULT_FLOOR), "room.ftex"),
        "ctex": _byte(brush.get("ctex", DEFAULT_CEIL), "room.ctex"),
        # The *upper* wall texture, used by an overhang. Defaults to the wall
        # rather than to slot 2, so a room with one wall texture gets one look.
        "utex": _byte(brush.get("utex", wtex), "room.utex"),
        "tag": _byte(brush.get("tag", 0), "room.tag"),
    }
    for index in grid.cells(brush.get("rect")):
        grid.paint(index, **kwargs)


def _op_solid(grid: _Grid, brush: dict[str, Any]) -> None:
    """Put rock back: pillars, cover, and the walls between carved rooms."""
    wtex = _byte(brush.get("wtex", ROCK_WTEX), "solid.wtex")
    for index in grid.cells(brush.get("rect")):
        # Every non-stored field must be its `sqrdefault` value or `write_cgz`
        # refuses the cube — a SOLID record has nowhere to put them.
        grid.paint(
            index,
            ctype=SOLID,
            floor=0,
            ceil=16,
            wtex=wtex,
            ftex=DEFAULT_FLOOR,
            ctex=DEFAULT_CEIL,
            utex=wtex,
            tag=0,
        )


def _op_stairs(grid: _Grid, brush: dict[str, Any]) -> None:
    """A floor that climbs across the rect, one column of cells per step.

    Steps rather than a heightfield on purpose: `FHF` reads `vdelta` from the
    *corner-vertex* cell, so a slope only holds together when its neighbours
    agree, and getting that wrong tears the surface at every seam. A run of whole
    cubes is walkable as long as no single rise exceeds `STEP_HEIGHT` (1.6), and
    the playability test is what checks that rather than an assumption here.
    """
    axis = brush.get("axis", "x")
    if axis not in ("x", "y"):
        raise _fail(f"stairs.axis must be 'x' or 'y', got {axis!r}")
    start = _height(brush.get("from", 0), "stairs.from")
    end = _height(brush.get("to", 0), "stairs.to")
    ceil = _height(brush.get("ceil", 16), "stairs.ceil")
    wtex = _byte(brush.get("wtex", DEFAULT_WALL), "stairs.wtex")
    ftex = _byte(brush.get("ftex", DEFAULT_FLOOR), "stairs.ftex")
    ctex = _byte(brush.get("ctex", DEFAULT_CEIL), "stairs.ctex")

    x, y, w, h = brush.get("rect", [0, 0, 0, 0])[:4]
    span = w if axis == "x" else h
    if span < 2:
        raise _fail("stairs need at least two cells along their axis")
    for index in grid.cells(brush.get("rect")):
        cx, cy = index % grid.ssize - x, index // grid.ssize - y
        t = (cx if axis == "x" else cy) / (span - 1)
        floor = round(start + (end - start) * t)
        if floor >= ceil:
            raise _fail(f"stairs reach {floor}, at or above their ceiling {ceil}")
        grid.paint(
            index,
            ctype=SPACE,
            floor=floor,
            ceil=ceil,
            wtex=wtex,
            ftex=ftex,
            ctex=ctex,
            utex=wtex,
            tag=0,
        )


_OPS = {"room": _op_room, "solid": _op_solid, "stairs": _op_stairs}


def _build_entity(grid: _Grid, spec: dict[str, Any]) -> MapEntity:
    """One entity. `playerstart` and `light` are typed; anything else is raw."""
    kind = spec.get("type")
    if kind not in ENTITY_NAMES:
        raise _fail(f"unknown entity type {kind!r}")
    etype = ENTITY_NAMES.index(kind)
    x, y = spec.get("x"), spec.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        raise _fail(f"entity needs integer x and y, got {spec!r}")
    if not (0 <= x < grid.ssize and 0 <= y < grid.ssize):
        raise _fail(f"entity at ({x}, {y}) is outside the map")

    attrs = [0] * 7
    yaw: float | None = None
    if etype == PLAYERSTART:
        yaw = float(spec.get("yaw", 0)) % 360.0
        attrs[0] = round(yaw * ENTSCALE10)
        attrs[1] = 1 if spec.get("team") in (1, "rvsf") else 0
    elif etype == LIGHT:
        attrs[0] = _byte(spec.get("radius", 32), "light.radius")
        color = spec.get("color", [255, 255, 255])
        if not isinstance(color, list) or len(color) != 3:
            raise _fail(f"light.color must be [r, g, b], got {color!r}")
        attrs[1:4] = [_byte(c, "light.color") for c in color]
    else:
        raw = spec.get("attrs", [])
        if not isinstance(raw, list) or len(raw) > 7:
            raise _fail(f"entity attrs must be a list of at most 7, got {raw!r}")
        attrs[: len(raw)] = [int(v) for v in raw]
        if etype in ANGLED_TYPES:
            yaw = (attrs[0] / ENTSCALE10) % 360.0

    z = spec.get("z")
    if z is None:
        # Placed relative to the floor actually built underneath it, so a spawn
        # moved in the source does not silently keep an old height.
        z = grid.floor_of(y * grid.ssize + x) + SPAWN_EYE_OFFSET
    return MapEntity(
        type=etype,
        name=kind,
        x=x,
        y=y,
        z=int(z),
        yaw=yaw,
        attr1=attrs[0],
        attr2=attrs[1],
        attr3=attrs[2],
        attr4=attrs[3],
        attr5=attrs[4],
        attr6=attrs[5],
        attr7=attrs[6],
    )


def build(source: dict[str, Any], name: str = "") -> CgzMap:
    """Turn a map source document into a parsed map, ready to serve or write."""
    return _build(source, name)[0]


def build_with_owners(
    source: dict[str, Any], name: str = ""
) -> tuple[CgzMap, list[int]]:
    """`build`, plus the index of the brush that painted each cell (-1 for rock).

    A separate entry point rather than a second parameter on `build`, because
    every existing caller wants the map and nothing else, and an out-parameter
    they all have to pass `None` to is how a hot path grows a wart.
    """
    return _build(source, name)


def _build(source: dict[str, Any], name: str) -> tuple[CgzMap, list[int]]:
    sfactor = source.get("sfactor", 7)
    if not isinstance(sfactor, int) or not SMALLEST_FACTOR <= sfactor <= LARGEST_FACTOR:
        raise _fail(
            f"sfactor must be {SMALLEST_FACTOR}..{LARGEST_FACTOR}, got {sfactor!r}"
        )

    grid = _Grid(1 << sfactor)
    brushes = source.get("brushes", [])
    if not isinstance(brushes, list):
        raise _fail("brushes must be a list")
    for position, brush in enumerate(brushes):
        if not isinstance(brush, dict):
            raise _fail(f"brush {position} is not an object")
        op = _OPS.get(brush.get("op"))
        if op is None:
            raise _fail(f"brush {position} has unknown op {brush.get('op')!r}")
        grid.brush = position
        op(grid, brush)
    grid.brush = -1

    entity_specs = source.get("entities", [])
    if not isinstance(entity_specs, list):
        raise _fail("entities must be a list")
    entities = [_build_entity(grid, spec) for spec in entity_specs]

    watercolor = source.get("watercolor", [20, 40, 60, 140])
    if not isinstance(watercolor, list) or len(watercolor) != 4:
        raise _fail(f"watercolor must be [r, g, b, a], got {watercolor!r}")

    built = CgzMap(
        name=name or str(source.get("name", "")),
        magic="ACMP",
        version=10,
        header_size=980,
        sfactor=sfactor,
        title=str(source.get("title", name)),
        waterlevel=float(source.get("waterlevel", -100.0)),
        watercolor=tuple(_byte(c, "watercolor") for c in watercolor),  # type: ignore[arg-type]
        maprevision=int(source.get("revision", 1)),
        ambient=_byte(source.get("ambient", 40), "ambient"),
        flags=0,
        timestamp=0,
        entities=entities,
        type=bytes(grid.type),
        floor=bytes(grid.floor),
        ceil=bytes(grid.ceil),
        wtex=bytes(grid.wtex),
        ftex=bytes(grid.ftex),
        ctex=bytes(grid.ctex),
        vdelta=bytes(grid.vdelta),
        utex=bytes(grid.utex),
        tag=bytes(grid.tag),
    )
    return built, grid.owners


# ---- the bundled catalog ----------------------------------------------------------


def is_bundled_name(name: str) -> bool:
    """Bundled names index into a directory, so they are validated, not trusted —
    the same rule `assets.find_map` applies to an install's map names."""
    return (
        bool(name)
        and name.startswith(BUNDLED_PREFIX)
        and all(ch.isalnum() or ch in "-_" for ch in name)
    )


@lru_cache(maxsize=1)
def bundled_names() -> tuple[str, ...]:
    """Every map shipped with the app, sorted. Cached: the directory is read-only
    at runtime and ships inside the package."""
    if not MAPS_DIR.is_dir():
        return ()
    return tuple(
        sorted(p.stem for p in MAPS_DIR.glob("*.json") if is_bundled_name(p.stem))
    )


@lru_cache(maxsize=8)
def load_bundled(name: str) -> CgzMap | None:
    """Build a bundled map by name, or `None` if there is no such map.

    Cached by name rather than by mtime — unlike an install's maps these ship
    with the code and cannot change under a running process.
    """
    if not is_bundled_name(name):
        return None
    path = MAPS_DIR / f"{name}.json"
    if not path.is_file():
        return None
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CgzError(f"could not read bundled map {name}: {exc}") from exc
    if not isinstance(source, dict):
        raise _fail(f"{name} is not an object")
    return build(source, name=name)


# ---- what a document may contain --------------------------------------------------
#
# Served to the editors rather than written out again in TypeScript and Rust, the
# `plane_order` / `zoom_levels` precedent — and for the reason the Model
# Designer's inspector gives: a form hand-maintained beside a schema is a form
# that eventually describes a field the backend no longer has.
#
# It lives here, next to the ops it describes, so a new field and its spec are one
# diff apart. Two files apart is how they drift.


def _f(
    name: str,
    ftype: str,
    default: Any = None,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: list[str] | None = None,
    required: bool = False,
    description: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "type": ftype,
        "default": default,
        "minimum": minimum,
        "maximum": maximum,
        "choices": choices,
        "required": required,
        "description": description,
    }


_RECT = _f(
    "rect",
    "rect",
    None,
    required=True,
    description=(
        "[x, y, width, height] in cells. The outer ring is refused rather than "
        "clipped: the physics reads out-of-bounds as solid, and that border is "
        "the only thing keeping a player on the map."
    ),
)
_HEIGHT = {"minimum": _MIN_HEIGHT, "maximum": _MAX_HEIGHT}


def schema() -> dict[str, Any]:
    """Every brush op, entity type and document field an editor can offer."""
    return {
        "brushes": [
            {
                "name": "room",
                "description": "Carve open space: a floor, a ceiling, and the walls that fall out of it.",
                "fields": [
                    _RECT,
                    _f(
                        "floor",
                        "int",
                        0,
                        description="Signed; a floor may go below zero.",
                        **_HEIGHT,
                    ),
                    _f(
                        "ceil",
                        "int",
                        16,
                        description="Must be above the floor.",
                        **_HEIGHT,
                    ),
                    _f("wtex", "texture", DEFAULT_WALL),
                    _f("ftex", "texture", DEFAULT_FLOOR),
                    _f("ctex", "texture", DEFAULT_CEIL),
                    _f(
                        "utex",
                        "texture",
                        None,
                        description="The upper wall, used by an overhang. Defaults to wtex, so a room with one wall texture gets one look.",
                    ),
                    _f(
                        "tag",
                        "int",
                        0,
                        minimum=0,
                        maximum=255,
                        description="Clip bits: 0x40 clip, 0x80 player-clip.",
                    ),
                ],
            },
            {
                "name": "solid",
                "description": "Put rock back: pillars, cover, and the walls between carved rooms.",
                "fields": [
                    _RECT,
                    _f(
                        "wtex",
                        "texture",
                        ROCK_WTEX,
                        description="The only thing a solid cube can carry. Everything else must stay at its default or the writer refuses the cube.",
                    ),
                ],
            },
            {
                "name": "stairs",
                "description": "A floor that climbs across the rect, one column of whole cubes per step.",
                "fields": [
                    _RECT,
                    _f(
                        "axis",
                        "enum",
                        "x",
                        choices=["x", "y"],
                        description="Needs at least two cells along it.",
                    ),
                    _f(
                        "from",
                        "int",
                        0,
                        description="Floor height at the start of the run.",
                        **_HEIGHT,
                    ),
                    _f(
                        "to",
                        "int",
                        0,
                        description="Floor height at the end.",
                        **_HEIGHT,
                    ),
                    _f("ceil", "int", 16, **_HEIGHT),
                    _f("wtex", "texture", DEFAULT_WALL),
                    _f("ftex", "texture", DEFAULT_FLOOR),
                    _f("ctex", "texture", DEFAULT_CEIL),
                ],
            },
        ],
        "entities": [
            {
                "name": "playerstart",
                "description": "Where a player enters. The height is resolved from the world, so z is optional.",
                "fields": [
                    _f("x", "int", None, required=True),
                    _f("y", "int", None, required=True),
                    _f(
                        "z",
                        "int",
                        None,
                        description="The mapper's eye, not the ground. Omit and it is placed above the floor actually built underneath.",
                    ),
                    _f(
                        "yaw",
                        "number",
                        0,
                        minimum=0,
                        maximum=360,
                        description="Degrees.",
                    ),
                    _f(
                        "team",
                        "enum",
                        0,
                        choices=["0", "1"],
                        description="0 CLA, 1 RVSF.",
                    ),
                ],
            },
            {
                "name": "light",
                "description": "A point light. Radius is in cubes.",
                "fields": [
                    _f("x", "int", None, required=True),
                    _f("y", "int", None, required=True),
                    _f("z", "int", None),
                    _f("radius", "int", 32, minimum=0, maximum=255),
                    _f("color", "color", [255, 255, 255]),
                ],
            },
            {
                "name": "ladder",
                "description": "A volume you cannot fall out of, spanning upward from the floor.",
                "fields": [
                    _f("x", "int", None, required=True),
                    _f("y", "int", None, required=True),
                    _f("z", "int", None),
                    _f(
                        "attrs",
                        "int",
                        [7],
                        description="[height in cubes]. A height of 0 is dropped, never unbounded.",
                    ),
                ],
            },
        ]
        + [
            {
                "name": kind,
                "description": "A pickup. Its height is resolved onto the floor beneath it.",
                "fields": [
                    _f("x", "int", None, required=True),
                    _f("y", "int", None, required=True),
                    _f("z", "int", 0),
                ],
            }
            for kind in ("health", "ammo", "clips", "grenade", "helmet", "armour")
        ],
        "map_fields": [
            _f("title", "string", "Untitled"),
            _f("author", "string", ""),
            _f("license", "string", "CC0-1.0"),
            _f(
                "sfactor",
                "int",
                7,
                minimum=SMALLEST_FACTOR,
                maximum=LARGEST_FACTOR,
                description="The map is (1 << sfactor) cells square. Changing it does not move any brush.",
            ),
            _f(
                "waterlevel",
                "number",
                -100,
                description="In cubes. Below every floor is how a map says it has no water.",
            ),
            _f("watercolor", "color", [20, 40, 60, 140]),
            _f("ambient", "int", 40, minimum=0, maximum=255),
        ],
        "entity_names": list(ENTITY_NAMES),
    }
