#!/usr/bin/env python3
"""Bake a modelled map's collision into its JSON brush world.

A `gltf` map exists twice. The clients draw the GLB and, offline, collide with it
through Rapier. A hosted match does not: the server — and the client predicting
against it — simulates the Cube-style heightfield built from the map JSON's
`brushes`. Those brushes used to be a hand-drawn sketch, an open box with a few
dividing walls, so online every crate, van, pillar and building in the GLB was
something you walked straight through.

This script replaces the sketch with the model. It loads the exported GLB,
keeps exactly the nodes the clients collide with (`glb-colliders.json`, the same
rule `glb-colliders.ts` and `world3d.rs` apply), and rasterises them into the
256-cube grid: for every cell, vertical rays at 4x4 points record where solid
begins and ends, the free space common to all of them is the column's open
space, and the lowest opening a body can stand up in becomes that cell's floor
and ceiling. The result is written back as merged `room` rects.

What a heightfield cannot say, and the choices made about it:
- **One opening per column.** Where a catwalk crosses a street, the street wins
  (the lowest space tall enough to stand in); the catwalk becomes that cell's
  ceiling. Stairs still climb to an upper floor because the space under a stair
  is solid, so the stair top is the lowest opening there.
- **Whole cubes.** Floors round to the nearest cube, ceilings round down — a
  shelf 0.3 lower than drawn rather than a head poking through one.
- **Conservative footprint.** A cell is blocked if any of its 16 samples is, so
  thin walls cannot fall between samples; props read up to a cube larger.
- **Breakable glass is open.** The server does not simulate shattering, and a
  pane that is solid forever would close sightlines the map is built around.

Run headless, after the generator (it reads the committed GLB):

    blender --background --factory-startup --python tools/blender/bake_collision.py -- hd_dust2 [hd_nuke ...]
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
MAPS_DIR = os.path.join(REPO_ROOT, "backend", "modules", "hassault", "maps")
COLLIDERS = os.path.join(
    REPO_ROOT, "packages", "core", "src", "modules", "hassault", "glb-colliders.json"
)

SFACTOR = 8
SSIZE = 1 << SFACTOR
SAMPLES = 4  # per cell edge
# A standing body is 5.2 tall (`physics.STANDING_HEIGHT`); an opening must hold
# it after the floor rounds up and the ceiling rounds down.
MIN_OPENING = 6
# Thickness given to a one-sided plane (a floor or roof with no other face).
OPEN_SURFACE = 0.5
# A running jump lands on a ledge this high (`maplint.JUMP_CLIMB`, measured).
JUMP_CLIMB = 4
BREAKABLE = re.compile(
    r"Window.*Glass|Glass.*Window|Breakable.*Glass|Glass.*Breakable|Curtain.*Glass|Window_Glass",
    re.I,
)

with open(COLLIDERS, encoding="utf-8") as f:
    TABLE = json.load(f)


def collides(obj) -> bool:
    """`glbNodeCollides`, in Blender's terms. Breakable glass counts as open."""
    name = obj.name
    if BREAKABLE.search(name):
        return False
    if any(k in name for k in TABLE["explicitNonCollider"]):
        return False
    mats = [m.name for m in obj.data.materials if m]
    decor = any(k in name for k in TABLE["decorNameKeywords"]) or any(
        k in m.lower() for m in mats for k in TABLE["decorMaterialKeywords"]
    )
    if not decor:
        return True
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    sx, sy, sz = (
        max(v[i] for v in corners) - min(v[i] for v in corners) for i in range(3)
    )
    return min(sx, sy) >= TABLE["standWidth"] or (
        sz >= TABLE["blockHeight"] and max(sx, sy) >= TABLE["blockWidth"]
    )


def load_colliders(name: str):
    """One BVH per colliding object, plus its footprint for a cheap cull.

    Per object, not one tree for the scene: a ray query returns only the nearest
    hit, so walking a line through a single tree has to step past each hit, and a
    face coincident with another object's (a crate standing on a plinth, a
    plateau's top flush with a ramp's) is stepped past unseen. Missing one face
    unbalances the solid/open count for the rest of the column, which showed up
    as salt-and-pepper holes. Within one closed object that cannot happen.
    """
    quiet, sys.stdout = sys.stdout, io.StringIO()
    try:
        bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
        bpy.ops.import_scene.gltf(filepath=os.path.join(MAPS_DIR, f"{name}.glb"))
    finally:
        sys.stdout = quiet
    solids = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    zlo, zhi = math.inf, -math.inf
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not collides(obj):
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        verts = [obj.matrix_world @ v.co for v in mesh.vertices]
        polys = [list(p.vertices) for p in mesh.polygons]
        evaluated.to_mesh_clear()
        if not polys:
            continue
        lo = [min(v[i] for v in verts) for i in range(3)]
        hi = [max(v[i] for v in verts) for i in range(3)]
        zlo, zhi = min(zlo, lo[2]), max(zhi, hi[2])
        solids.append((BVHTree.FromPolygons(verts, polys), lo, hi))
    return solids, zlo, zhi


def object_solid(tree, x: float, y: float, zlo: float, zhi: float) -> list[tuple[float, float]]:
    """Where one object is solid along the vertical line at (x, y), bottom up.

    Counts depth by face orientation: a face whose normal points down is where
    solid begins going up, one pointing up is where it ends. An *open* surface —
    a roof or floor modelled as a single plane, with no face on the other side —
    is read as a thin slab, never as "solid all the way down": that reading
    turned every room under a one-sided roof into rock.
    """
    hits = []
    origin = Vector((x, y, zhi + 1.0))
    down = Vector((0.0, 0.0, -1.0))
    remaining = zhi - zlo + 2.0
    while remaining > 0:
        loc, normal, _, dist = tree.ray_cast(origin, down, remaining)
        if loc is None:
            break
        if abs(normal.z) > 0.05:
            hits.append((loc.z, normal.z))
        origin = loc + down * 1e-4
        remaining -= dist + 1e-4
    hits.sort()
    out, depth, start = [], 0, 0.0
    for z, nz in hits:
        if nz < 0:
            if depth == 0:
                start = z
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                out.append((start, z))
        else:
            out.append((z - OPEN_SURFACE, z))  # an upward face with no underside
    if depth > 0:
        out.append((start, start + OPEN_SURFACE))  # a downward face with no top
    return out


def free_intervals(solids, x: float, y: float, zlo: float, zhi: float) -> list[tuple[float, float]]:
    """Open space along the line: everything no object is solid in."""
    blocked = []
    for tree, lo, hi in solids:
        if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]:
            blocked += object_solid(tree, x, y, zlo, zhi)
    blocked.sort()
    out, cursor = [], -math.inf
    for a, b in blocked:
        if a > cursor:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < math.inf:
        out.append((cursor, math.inf))
    return out


def intersect(a, b):
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo < hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def bake(name: str) -> list[dict]:
    solids, zlo, zhi = load_colliders(name)
    sky = min(127, math.ceil(zhi) + 8)
    # A small irrational offset keeps samples off the modelled edges, which sit
    # on tidy multiples of 0.3 after the metres-to-cubes scale.
    offsets = [(i + 0.5) / SAMPLES + 0.0137 for i in range(SAMPLES)]
    openings: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for cy in range(1, SSIZE - 1):
        for cx in range(1, SSIZE - 1):
            cell_solids = [
                s for s in solids if s[1][0] <= cx + 1 and s[2][0] >= cx and s[1][1] <= cy + 1 and s[2][1] >= cy
            ]
            common = None
            for oy in offsets:
                for ox in offsets:
                    free = free_intervals(cell_solids, cx + ox, cy + oy, zlo, zhi)
                    common = free if common is None else intersect(common, free)
                    if not common:
                        break
                if not common:
                    break
            spans = []
            for bottom, top in common or []:
                if bottom == -math.inf:
                    continue  # nothing underfoot
                floor = round(bottom)
                ceil = sky if top == math.inf else min(sky, math.floor(top))
                if ceil - floor >= MIN_OPENING and -128 <= floor <= 127:
                    spans.append((floor, ceil))
            if spans:
                openings[(cx, cy)] = spans
    source = read_source(name)
    return merge(choose(openings, spawns_of(source), ladders_of(source), sky))


def read_source(name: str) -> dict:
    with open(os.path.join(MAPS_DIR, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def spawns_of(source: dict) -> list[tuple[int, int, float]]:
    return [
        (int(e["x"]), int(e["y"]), float(e.get("z", 0)))
        for e in source.get("entities", [])
        if e.get("type") == "playerstart"
    ]


def ladders_of(source: dict) -> list[tuple[int, int, float]]:
    return [
        (int(e["x"]), int(e["y"]), float((e.get("attrs") or [0])[0]))
        for e in source.get("entities", [])
        if e.get("type") == "ladder"
    ]


def choose(openings, spawns, ladders, sky):
    """Pick one opening per column: the one players can actually get to.

    Every opening of every column is a node. From the spawns, a body moves to a
    neighbouring column's opening when it can step or jump up to it (or drop to
    it) and the two openings overlap enough to pass between; a ladder joins the
    openings at its foot to those at its top. Each column then keeps the lowest
    opening that was reached — the street under a catwalk, not the catwalk — and
    a column nobody reaches keeps its top surface only when that surface is open
    to the sky, i.e. a crate or wall top you can shoot and throw over. Anything
    else (the space under a floor slab, the dunes outside the perimeter) closes.
    """
    seen = set()
    queue = []
    for x, y, z in spawns:
        for i, (f, c) in enumerate(openings.get((x, y), [])):
            if f <= z + 1 and c - z >= MIN_OPENING:
                seen.add(((x, y), i))
                queue.append(((x, y), i))
                break
    ladder_links = {}
    for lx, ly, height in ladders:
        near = [(lx + dx, ly + dy) for dx in range(-3, 4) for dy in range(-3, 4)]
        base_cells = [(c, i) for c in near for i, o in enumerate(openings.get(c, []))]
        if not base_cells:
            continue
        base = min(openings[c][i][0] for c, i in base_cells)
        top = base + height
        feet = [(c, i) for c, i in base_cells if abs(openings[c][i][0] - base) <= 1]
        heads = [(c, i) for c, i in base_cells if abs(openings[c][i][0] - top) <= JUMP_CLIMB]
        for a in feet:
            ladder_links.setdefault(a, []).extend(heads)
        for b in heads:
            ladder_links.setdefault(b, []).extend(feet)
    while queue:
        node = queue.pop()
        (cx, cy), i = node
        f, c = openings[(cx, cy)][i]
        nexts = list(ladder_links.get(node, []))
        for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            for j, (nf, nc) in enumerate(openings.get(n, [])):
                if nf - f <= JUMP_CLIMB and min(c, nc) - max(f, nf) >= MIN_OPENING:
                    nexts.append((n, j))
        for nxt in nexts:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    reached_cells = {}
    for cell, i in seen:
        if cell not in reached_cells or openings[cell][i][0] < openings[cell][reached_cells[cell]][0]:
            reached_cells[cell] = i
    chosen = {cell: openings[cell][i] for cell, i in reached_cells.items()}
    walked = dict(chosen)  # judged against reached floor only, never a chain of tops
    for cell, spans in openings.items():
        if cell in chosen:
            continue
        top_floor, top_ceil = spans[-1]
        if top_ceil >= sky and any(
            (cell[0] + dx, cell[1] + dy) in walked
            and walked[(cell[0] + dx, cell[1] + dy)][0] < top_floor
            for dx in range(-3, 4)
            for dy in range(-3, 4)
        ):
            chosen[cell] = (top_floor, top_ceil)
    return chosen


def merge(cells: dict[tuple[int, int], tuple[int, int]]) -> list[dict]:
    """Greedy rectangles of cells that share a floor and ceiling."""
    brushes, done = [], set()
    for cy in range(SSIZE):
        for cx in range(SSIZE):
            key = cells.get((cx, cy))
            if key is None or (cx, cy) in done:
                continue
            w = 1
            while cells.get((cx + w, cy)) == key and (cx + w, cy) not in done:
                w += 1
            h = 1
            while all(
                cells.get((cx + i, cy + h)) == key and (cx + i, cy + h) not in done
                for i in range(w)
            ):
                h += 1
            for j in range(h):
                for i in range(w):
                    done.add((cx + i, cy + j))
            brushes.append(
                {"op": "room", "rect": [cx, cy, w, h], "floor": key[0], "ceil": key[1]}
            )
    return brushes


def write(name: str, brushes: list[dict]) -> None:
    path = os.path.join(MAPS_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as f:
        source = json.load(f)
    source["sfactor"] = SFACTOR
    source["collision"] = "baked"
    source["brushes"] = brushes
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump(source) + "\n")
    print(f"{name}: {len(brushes)} brushes")


def dump(v, ind: int = 0) -> str:
    """indent=2 JSON, but a row of scalars stays on one line (the files' style)."""
    flat = json.dumps(v, separators=(", ", ": "))
    if not isinstance(v, (dict, list)):
        return flat
    kids = v.values() if isinstance(v, dict) else v
    if len(flat) <= 90 and all(not isinstance(x, (dict, list)) for x in kids):
        return flat
    pad, nl = "  " * (ind + 1), "\n"
    if isinstance(v, dict):
        body = ("," + nl).join(
            f"{pad}{json.dumps(k)}: {dump(x, ind + 1)}" for k, x in v.items()
        )
        return "{" + nl + body + nl + "  " * ind + "}"
    body = ("," + nl).join(f"{pad}{dump(x, ind + 1)}" for x in v)
    return "[" + nl + body + nl + "  " * ind + "]"


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    for map_name in argv:
        write(map_name, bake(map_name))
