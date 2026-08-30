"""Whether a map is *playable*, asked of a map that is not on disk yet.

Every check here already existed — as assertions in
`backend/tests/test_hassault_bundled.py`, run over the three bundled maps. That
is the right place for them and they stay there, but it is the wrong *shape* for
an editor: a mapper dragging a room wider needs to be told, while they are
looking at it, that they have just severed the gallery from the rest of the map.
So the checks move here and the tests call this instead. One definition, two
audiences.

**A finding names cells, not just a problem.** `cells` is what lets a client paint
the failure onto the floor it happens on, which is the whole reason a live
validator beats a test run: "37 cells are cut off" is a number, and the same
thing drawn in red is an answer.

Severity splits on one question — would the map still work?

- `error`: it would not. The writer would refuse it, or a player would be stuck
  in rock, or half the map is unreachable.
- `warn`: it works and it is probably a mistake. Too few spawns to fill a match,
  an item resting on a spawn point.

Nothing here is advisory-only styling. A map with no findings is one the bundled
suite would accept, which is deliberately a high bar: these three maps are the
worked examples, and an editor that let you save something worse than them would
make the bar decorative.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from backend.modules.hassault import physics, pickups
from backend.modules.hassault.cgz import (
    MAXTYPE,
    SEMISOLID,
    SOLID,
    CgzMap,
    DEFAULT_CEIL,
    DEFAULT_FLOOR,
)

#: Enough spawns to fill a match without two players sharing one.
MIN_SPAWNS = 8
#: Chebyshev cells between the two nearest spawns. Closer is a telefrag.
MIN_SPAWN_SEPARATION = 4
#: A map with fewer items makes pickups a thing only AssaultCube owners see.
MIN_ITEMS = 8
#: The kinds a map has to carry for the pickup mechanics to mean anything.
REQUIRED_ITEM_KINDS = frozenset({"health", "ammo", "armour"})

#: What a SOLID record can store. Everything else must be its `sqrdefault`, or
#: `cgz._encode_record` refuses the cube — caught here rather than at export,
#: because "your map cannot be saved" is the worst possible time to learn it.
_SOLID_DEFAULTS = (0, 16, DEFAULT_FLOOR, DEFAULT_CEIL, 0)


@dataclass(slots=True)
class Finding:
    """One thing wrong with a map, and where."""

    code: str
    severity: str  # "error" | "warn"
    message: str
    #: Cells this is about, as `[x, y]` pairs. Capped — see `_cap`.
    cells: list[list[int]] = field(default_factory=list)
    #: How many cells there really were, when `cells` was capped.
    cell_count: int = 0
    #: Index into the document's `entities`, when the finding is about one.
    entity: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "cells": self.cells,
            "cellCount": self.cell_count or len(self.cells),
            "entity": self.entity,
        }


#: A finding is drawn, not printed, so a client needs the cells — but a map with
#: a broken border has ~500 of them and a truncated payload is no less useful for
#: pointing at the problem.
_CELL_CAP = 512


def _cap(cells: list[tuple[int, int]]) -> tuple[list[list[int]], int]:
    return [[x, y] for x, y in cells[:_CELL_CAP]], len(cells)


def _plural(count: int, word: str) -> str:
    """`3 cells` / `1 cell`. A validator that says "1 cells are" reads as a bug in
    the validator, which is exactly the wrong thing to be wondering about."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _are(count: int) -> str:
    return "is" if count == 1 else "are"


def _standable(world: physics.World) -> set[tuple[int, int]]:
    """Cells a player's body actually fits in, standing on that cell's floor."""
    return {
        (cx, cy)
        for cy in range(world.ssize)
        for cx in range(world.ssize)
        if not world.is_solid(cx, cy)
        and physics.can_stand(world, cx + 0.5, cy + 0.5, world.floor_at(cx, cy))
    }


def _reachable(
    world: physics.World, start: tuple[int, int], cells: set[tuple[int, int]]
) -> set[tuple[int, int]]:
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


# ---- the checks -------------------------------------------------------------------


def _check_border(cmap: CgzMap, out: list[Finding]) -> None:
    """The engine guarantees a solid border and the physics leans on it — out of
    bounds reads as solid, which is the only thing keeping a player on the map."""
    ssize = 1 << cmap.sfactor
    broken = [
        (x, y)
        for y in range(ssize)
        for x in range(ssize)
        if (x == 0 or y == 0 or x == ssize - 1 or y == ssize - 1)
        and cmap.type[y * ssize + x] != SOLID
    ]
    if broken:
        cells, count = _cap(broken)
        out.append(
            Finding(
                code="border.open",
                severity="error",
                message=(
                    f"{_plural(count, 'cell')} on the map border {_are(count)} not solid; a "
                    "brush has reached the outer ring and players can leave the world"
                ),
                cells=cells,
                cell_count=count,
            )
        )


def _check_writable(cmap: CgzMap, out: list[Finding]) -> None:
    """Would `write_cgz` accept this? Asked here so a map that cannot be exported
    says so while you are building it, not when you press save."""
    ssize = 1 << cmap.sfactor
    illegal: list[tuple[int, int]] = []
    semisolid: list[tuple[int, int]] = []
    lossy: list[tuple[int, int]] = []
    for index in range(ssize * ssize):
        ctype = cmap.type[index]
        cell = (index % ssize, index // ssize)
        if ctype == SEMISOLID:
            semisolid.append(cell)
        elif not 0 <= ctype < MAXTYPE:
            illegal.append(cell)
        elif ctype == SOLID:
            floor = cmap.floor[index]
            ceil = cmap.ceil[index]
            stored = (
                floor - 256 if floor > 127 else floor,
                ceil - 256 if ceil > 127 else ceil,
                cmap.ftex[index],
                cmap.ctex[index],
                cmap.tag[index],
            )
            if stored != _SOLID_DEFAULTS or cmap.utex[index] != cmap.wtex[index]:
                lossy.append(cell)

    for cells, code, message in (
        (
            illegal,
            "cube.type",
            "cells carry a cube type the format has no encoding for",
        ),
        (
            semisolid,
            "cube.semisolid",
            "cells are SEMISOLID, which is a mipmap artifact and is never stored",
        ),
        (
            lossy,
            "cube.solid_lossy",
            "solid cells carry a floor, ceiling, texture or tag that a SOLID record "
            "cannot store; the writer would refuse them",
        ),
    ):
        if cells:
            capped, count = _cap(cells)
            out.append(
                Finding(
                    code=code,
                    severity="error",
                    message=f"{count} {message}",
                    cells=capped,
                    cell_count=count,
                )
            )


def _check_spawns(cmap: CgzMap, sim: physics.World, out: list[Finding]) -> None:
    spawns = cmap.spawns()
    if not spawns:
        out.append(
            Finding(
                code="spawn.none",
                severity="error",
                message="the map has no playerstart; nobody can enter it",
            )
        )
        return
    if len(spawns) < MIN_SPAWNS:
        out.append(
            Finding(
                code="spawn.few",
                severity="warn",
                message=(
                    f"only {len(spawns)} spawns; a full match wants at least "
                    f"{MIN_SPAWNS}"
                ),
                cells=[[s.x, s.y] for s in spawns],
            )
        )

    # `spawn_at` resolves the height the body would actually rest at, so this is
    # asking the same question the first frame of a match asks.
    stuck: list[tuple[int, int]] = []
    for spawn in spawns:
        state = physics.spawn_at(sim, spawn)
        if not physics.can_stand(sim, state.x, state.y, state.z):
            stuck.append((spawn.x, spawn.y))
    if stuck:
        cells, count = _cap(stuck)
        out.append(
            Finding(
                code="spawn.blocked",
                severity="error",
                message=(
                    f"{_plural(count, 'spawn')} {_are(count)} inside something; a player "
                    "landing there cannot move"
                ),
                cells=cells,
                cell_count=count,
            )
        )

    # Two spawns in one cell is two players telefragged on the first frame; near
    # neighbours are the softer version of the same thing.
    crowded: list[tuple[int, int]] = []
    for a in spawns:
        others = [b for b in spawns if b is not a]
        if not others:
            continue
        nearest = min(max(abs(a.x - b.x), abs(a.y - b.y)) for b in others)
        if nearest < MIN_SPAWN_SEPARATION:
            crowded.append((a.x, a.y))
    if crowded:
        cells, count = _cap(crowded)
        out.append(
            Finding(
                code="spawn.crowded",
                severity="warn",
                message=(
                    f"{_plural(count, 'spawn')} {_are(count)} within {MIN_SPAWN_SEPARATION} "
                    "cells of another; players will land on top of each other"
                ),
                cells=cells,
                cell_count=count,
            )
        )


def _check_reachable(
    cmap: CgzMap, sim: physics.World, out: list[Finding]
) -> set[tuple[int, int]]:
    """Every standable cell connects to every other one.

    This is the check that catches a raised gallery whose stairs do not reach it,
    or a room walled off by an off-by-one rect — neither of which fails anything
    else. It is also the reason this module exists: drawn on the floor, it is the
    difference between a map you can debug and a map you can only test.

    Returns the reachable set, which the item checks reuse.
    """
    cells = _standable(sim)
    if not cells:
        out.append(
            Finding(
                code="world.sealed",
                severity="error",
                message="there is nowhere on this map a player can stand",
            )
        )
        return set()

    spawns = cmap.spawns()
    if not spawns:
        return cells

    first = physics.spawn_at(sim, spawns[0])
    reached = _reachable(sim, (int(first.x), int(first.y)), cells)
    cut = sorted(cells - reached)
    if cut:
        capped, count = _cap(cut)
        out.append(
            Finding(
                code="world.cutoff",
                severity="error",
                message=(
                    f"{_plural(count, 'standable cell')} cannot be walked to from the first "
                    "spawn; part of the map is sealed off"
                ),
                cells=capped,
                cell_count=count,
            )
        )
    return reached


def _check_items(
    cmap: CgzMap,
    sim: physics.World,
    reached: set[tuple[int, int]],
    out: list[Finding],
) -> None:
    placed = pickups.place(sim, cmap.entities)
    kinds = {item.kind for item in placed}

    if len(placed) < MIN_ITEMS:
        out.append(
            Finding(
                code="item.few",
                severity="warn",
                message=f"only {len(placed)} items; a map wants at least {MIN_ITEMS}",
            )
        )
    missing = sorted(REQUIRED_ITEM_KINDS - kinds)
    if missing:
        out.append(
            Finding(
                code="item.kinds",
                severity="warn",
                message=f"the map carries no {', '.join(missing)}",
            )
        )

    # `place` resolves an item onto the floor beneath it, so an item is never
    # *floating*; what it can be is resting inside a pillar, or in a sealed room.
    # Either is invisible until somebody spends a match hunting for an armour.
    buried = [
        (int(i.x), int(i.y))
        for i in placed
        if not physics.can_stand(sim, i.x, i.y, i.z)
    ]
    if buried:
        cells, count = _cap(buried)
        out.append(
            Finding(
                code="item.buried",
                severity="error",
                message=f"{_plural(count, 'item')} {_are(count)} inside something solid",
                cells=cells,
                cell_count=count,
            )
        )
    stranded = [
        (int(i.x), int(i.y)) for i in placed if (int(i.x), int(i.y)) not in reached
    ]
    if stranded:
        cells, count = _cap(stranded)
        out.append(
            Finding(
                code="item.stranded",
                severity="error",
                message=f"{_plural(count, 'item')} cannot be walked to",
                cells=cells,
                cell_count=count,
            )
        )

    # An item within reach of a spawn is a free pickup for whoever died last,
    # which turns dying into a resupply.
    spawns = [physics.spawn_at(sim, s) for s in cmap.spawns()]
    on_spawn = [
        (int(i.x), int(i.y))
        for i in placed
        if any(pickups.in_reach(i, s.x, s.y, s.z) for s in spawns)
    ]
    if on_spawn:
        cells, count = _cap(on_spawn)
        out.append(
            Finding(
                code="item.on_spawn",
                severity="warn",
                message=(
                    f"{_plural(count, 'item')} {_are(count)} within pickup reach of a spawn; "
                    "dying would be a resupply"
                ),
                cells=cells,
                cell_count=count,
            )
        )


def _check_ladders(sim: physics.World, out: list[Finding]) -> None:
    """A ladder is only a route if climbing it ends with you standing somewhere.

    The failure is quiet and specific: the body is 2.2 cubes wide, so a ladder
    flush against the lip it serves is already stood on by anyone at its foot,
    and one set too far back drops you before the ledge. Both look fine in the
    source, so this climbs each one the way a player would and insists on
    arriving.
    """
    for ladder in sim.ladders:
        arrived = False
        # Either side: a mapper decides which way a ladder faces by where they
        # put it, and this asks only that *one* approach works.
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
        if not arrived:
            out.append(
                Finding(
                    code="ladder.dead_end",
                    severity="error",
                    message=(
                        f"the ladder at ({ladder.x:.0f}, {ladder.y:.0f}) spans "
                        f"{ladder.base:.0f} to {ladder.top:.0f} and cannot be "
                        "climbed to anywhere you can stand"
                    ),
                    cells=[[int(ladder.x), int(ladder.y)]],
                )
            )


def _check_water(sim: physics.World, out: list[Finding]) -> None:
    """The one water slip nothing else notices.

    A plane *below* every floor is how a map says it has no water — every
    official map ships one — so that is not an error. A plane above every floor
    is: the map becomes a swimming pool, nobody can jump, and the source looks
    completely ordinary.
    """
    floors = [
        sim.floor_at(x, y)
        for y in range(sim.ssize)
        for x in range(sim.ssize)
        if not sim.is_solid(x, y)
    ]
    if floors and max(floors) <= sim.waterlevel:
        out.append(
            Finding(
                code="water.floods",
                severity="error",
                message=(
                    f"the water level ({sim.waterlevel:.0f}) is above every floor; "
                    "the whole map is underwater"
                ),
            )
        )


def lint(cmap: CgzMap) -> list[Finding]:
    """Every playability check, over a built map. Errors first, then warnings.

    Order within a severity is the order the checks run, which is roughly cheap
    to expensive — but the sort is what a reader sees, and a reader wants the
    thing that breaks the map at the top.
    """
    out: list[Finding] = []
    _check_border(cmap, out)
    _check_writable(cmap, out)

    sim = physics.World.from_map(cmap)
    _check_spawns(cmap, sim, out)
    reached = _check_reachable(cmap, sim, out)
    _check_items(cmap, sim, reached, out)
    _check_ladders(sim, out)
    _check_water(sim, out)

    return sorted(out, key=lambda f: 0 if f.severity == "error" else 1)
