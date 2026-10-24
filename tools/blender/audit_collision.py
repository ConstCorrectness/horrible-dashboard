"""Check a map's baked grid against the colliders it was baked from.

The server, and every client predicting against it, walks the grid; the
players see the GLB. Where the two disagree a player stands on air, sinks into a
floor, walks into a crate, or finds a building turned into a solid block — the
"in the sky", "in a box" and "glitching around" reports. `bake_collision.py`
makes the grid; nothing used to check it, so this asks two questions of every
cell a body can reach from a spawn (the grid's own walk: climb up to
`JUMP_CLIMB`, drop any height, `MIN_OPENING` of headroom), and reports a third:

- **floor**: the grid's floor against the mesh, sampled exactly as the bake
  samples a cell. A floor more than `--tolerance` cubes from what *every* sample
  stands on is a hard failure: a floor the bake invented (a collapsed stack, a
  slab read on the wrong side), not the grid's own coarseness. A cell that is
  half crate and half street legitimately has the crate's floor.
- **overhang**: cells whose floor is above the mesh under their centre by more
  than `--tolerance` — the conservative footprint, where a body can stand out
  over a drop. Reported as a count, never failed; the sliver rule keeps it small.
- **sealed**: columns the bake did not reach while the mesh has a room-sized
  opening below their floor. Reported, never failed — a closed building is often
  meant — but a big one next to a street is how a room with a too-narrow door
  turns into a box.

Whether a body can end up *inside* a collider is `probe_eye_inside.py`'s
question (it now tests colliders too, at every body position the server allows).

    blender --background --factory-startup --python tools/blender/audit_collision.py -- hd_dust2 [hd_nuke ...] [--tolerance 1.5]

Reads `tools/blender/.collision/<map>.glb` (regenerate first) and the
committed `<map>.json`. Exits 1 on any floor failure.
"""

from __future__ import annotations

import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake_collision as bc  # noqa: E402

#: How tall a standing body is (`physics.STANDING_HEIGHT`).
BODY = 5.2


def grid_cells(source: dict) -> dict[tuple[int, int], tuple[int, int]]:
    cells = {}
    for b in source.get("brushes", []):
        if b.get("op") != "room":
            continue
        x, y, w, h = b["rect"]
        for j in range(h):
            for i in range(w):
                cells[(x + i, y + j)] = (b["floor"], b["ceil"])
    return cells


def reached(cells, spawns) -> set[tuple[int, int]]:
    queue, seen = deque(), set()
    for x, y, _z in spawns:
        if (x, y) in cells:
            seen.add((x, y))
            queue.append((x, y))
    while queue:
        cx, cy = queue.popleft()
        f, c = cells[(cx, cy)]
        for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if n in seen or n not in cells:
                continue
            nf, nc = cells[n]
            if nf - f <= bc.JUMP_CLIMB and min(c, nc) - max(f, nf) >= bc.MIN_OPENING:
                seen.add(n)
                queue.append(n)
    return seen


def clusters(cells: set) -> list[list[tuple[int, int]]]:
    out, seen = [], set()
    for start in sorted(cells):
        if start in seen:
            continue
        seen.add(start)
        group, queue = [], deque([start])
        while queue:
            cx, cy = queue.popleft()
            group.append((cx, cy))
            for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if n in cells and n not in seen:
                    seen.add(n)
                    queue.append(n)
        out.append(group)
    return sorted(out, key=len, reverse=True)


def audit(name: str, tolerance: float) -> dict:
    index, zlo, zhi = bc.load_colliders(name)
    source = bc.read_source(name)
    cells = grid_cells(source)
    walk = reached(cells, bc.spawns_of(source))
    floors, overhang = [], 0
    for cx, cy in sorted(walk):
        f, c = cells[(cx, cy)]
        stands = [
            fl
            for _bottom, top, grid in bc.sample_cell(index, cx, cy, zlo, zhi)
            if top >= f + min(BODY, c - f) - 0.25
            for row in grid
            for fl in row
            if fl != float("-inf")
        ]
        # The bake's extra samples on thin parts (a rail) can raise a floor too.
        for x, y in bc._thin_points(index, cx, cy):
            stands += [
                a
                for a, _b in bc.free_intervals(index, x, y, zlo, zhi)
                if a != float("-inf")
            ]
        nearest = min(stands, key=lambda s: abs(s - f), default=None)
        if nearest is None or abs(nearest - f) > tolerance:
            floors.append((cx, cy, f, None if nearest is None else round(nearest, 2)))
            continue
        centre = [
            a
            for a, b in bc.free_intervals(index, cx + 0.5, cy + 0.5, zlo, zhi)
            if a <= f + tolerance and b >= f + 1
        ]
        if centre and f - max(centre) > tolerance:
            overhang += 1
    sealed = set()
    for cx, cy in (cell for cell in cells if cell not in walk):
        f, _c = cells[(cx, cy)]
        for bottom, top, _floors in bc.sample_cell(index, cx, cy, zlo, zhi):
            if (
                bottom != float("-inf")
                and bottom < f - 1
                and top - bottom >= bc.MIN_OPENING
            ):
                sealed.add((cx, cy))
                break
    rooms = [g for g in clusters(sealed) if len(g) >= 12]
    return {
        "map": name,
        "reached": len(walk),
        "floor": floors,
        "overhang": overhang,
        "sealed": [
            {
                "cells": len(g),
                "box": [
                    min(x for x, _ in g),
                    min(y for _, y in g),
                    max(x for x, _ in g),
                    max(y for _, y in g),
                ],
            }
            for g in rooms
        ],
    }


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    tolerance = 1.5
    if "--tolerance" in argv:
        i = argv.index("--tolerance")
        tolerance = float(argv[i + 1])
        del argv[i : i + 2]
    failed = False
    out = sys.__stdout__
    for name in argv:
        r = audit(name, tolerance)
        failed |= bool(r["floor"])
        print(
            f"{name}: {r['reached']} cells reached; {len(r['floor'])} floor, "
            f"{r['overhang']} overhang, {len(r['sealed'])} sealed rooms",
            file=out,
        )
        for cx, cy, f, mesh in r["floor"][:15]:
            print(f"  floor  ({cx},{cy}) grid {f}, nearest sample {mesh}", file=out)
        for room in r["sealed"][:10]:
            print(f"  sealed {room['cells']} cells in {room['box']}", file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
