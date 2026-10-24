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
- **Conservative footprint, minus slivers.** A cell is blocked if any of its 16
  samples is, so thin walls cannot fall between samples. The one exception is a
  sliver: when only a minority of samples, all on one side, stand on something
  higher and the neighbour on that side is mostly that same thing, the cell
  keeps the floor most of it stands on. The neighbour still blocks, so nothing
  thin opens up; a crate's edge just stops reading a cube wider than it is,
  which (with a body resting on the highest floor it overlaps) left players
  standing on air beside every ledge.
- **Breakable glass is open.** The server does not simulate shattering, and a
  pane that is solid forever would close sightlines the map is built around.

Run headless, after the generator. It reads the mesh the generator left in
`tools/blender/.collision/` (the map before its AO edge splits, which change
how rays count faces: see `maplib.COLLISION_DIR`). Without it the bake stops;
`--from-shipped` bakes the committed GLB instead, knowing its floors move. The
source's hash is written as `collisionBaked`, beside the `collisionSource` the
generator wrote, so a map regenerated and not re-baked fails its test:

    blender --background --factory-startup --python tools/blender/bake_collision.py -- hd_dust2 [hd_nuke ...]
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
MAPS_DIR = os.path.join(REPO_ROOT, "backend", "modules", "hassault", "maps")
COLLISION_DIR = os.path.join(REPO_ROOT, "tools", "blender", ".collision")
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
# glTF splits a vertex wherever its normal changes, so a flat-shaded box arrives
# as six loose quads; positions closer than this are one vertex again.
WELD = 1e-4
# Parts are filed by tile so a cell only looks at the parts near it.
TILE = 8
# Float32 noise on a hit height, well under anything modelled.
EPS = 1e-3
# A part narrower than this (cubes) can slip between the 0.25-spaced samples.
THIN = 0.3
# The most samples a trimmed sliver may hold: one row of the 4x4. Trimming any
# minority shrank an office chair enough to leave a pocket a body fits in and
# cannot walk to.
SLIVER = SAMPLES
BREAKABLE = re.compile(
    r"Window.*Glass|Glass.*Window|Breakable.*Glass|Glass.*Breakable|Curtain.*Glass|Window_Glass",
    re.I,
)

with open(COLLIDERS, encoding="utf-8") as f:
    TABLE = json.load(f)


def collides(obj, scale: float = 1.0) -> bool:
    """`glbNodeCollides`, in Blender's terms. Breakable glass counts as open.

    `scale` converts the object's size to cubes, for a generator asking before
    `maplib.export_map_glb` has scaled the scene (it authors in metres).
    """
    name = obj.name
    if BREAKABLE.search(name):
        return False
    # `maplib.add_boundary_walls` exists for the clients' physics, which walk the
    # GLB. The grid already ends in rock past the map; baked, the ring became
    # columns open to the sky, and on a roofed map (the office) their corners
    # were standable and unreachable, which `maplint` rightly calls cut off.
    if name.startswith("Map_Bounds_"):  # maplib.BOUNDS_PREFIX
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
        (max(v[i] for v in corners) - min(v[i] for v in corners)) * scale
        for i in range(3)
    )
    return min(sx, sy) >= TABLE["standWidth"] or (
        sz >= TABLE["blockHeight"] and max(sx, sy) >= TABLE["blockWidth"]
    )


def load_colliders(name: str, from_shipped: bool = False):
    """The colliding geometry as parts, each with an up-facing and a down-facing
    BVH, filed by tile; plus the scene's height range.

    A ray query returns only the nearest hit, so walking a line through a tree
    means stepping past each hit, and a face coincident with another is stepped
    past unseen. Missing one face unbalances the solid/open count for the rest of
    the column. So the trees are cut finer than objects, twice over:

    - **Per welded part**, not per object. Generators join hundreds of boxes into
      one mesh (Assault's batches, `add_boxes`), and a crate on a slab inside one
      mesh is exactly a coincident pair. Parts that only touch are separate.
    - **Up-facing and down-facing faces in separate trees.** A part whose pieces
      share vertices (two boxes stacked with the same footprint) still has a
      top and a bottom in one plane; split by facing, each tree holds one of
      them, so both are found. Walls (`|nz| <= 0.05`) never count and are
      left out.
    """
    quiet, sys.stdout = sys.stdout, io.StringIO()
    try:
        bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
        bpy.ops.import_scene.gltf(filepath=source_glb(name, from_shipped))
    finally:
        sys.stdout = quiet
    parts = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    zlo, zhi = math.inf, -math.inf
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not collides(obj):
            continue
        evaluated = obj.evaluated_get(depsgraph)
        bm = bmesh.new()
        bm.from_mesh(evaluated.to_mesh())
        evaluated.to_mesh_clear()
        # Welded in the object's own space; positions go to world space only on
        # the way into the trees, in double precision. `bm.transform` rounds
        # them to float32 first, and a roof at 12.0 became 11.99999, a whole
        # cube lower once the ceiling rounds down.
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=WELD)
        bm.normal_update()
        for island in _islands(bm):
            part = _part(island, obj.matrix_world, obj.name)
            if part is None:
                continue
            parts.append(part)
            zlo, zhi = min(zlo, part[2][2]), max(zhi, part[3][2])
        bm.free()
    index: dict[tuple[int, int], list] = {}
    for part in parts:
        lo, hi = part[2], part[3]
        for tx in range(int(lo[0]) // TILE, int(hi[0]) // TILE + 1):
            for ty in range(int(lo[1]) // TILE, int(hi[1]) // TILE + 1):
                index.setdefault((tx, ty), []).append(part)
    return index, zlo, zhi


def _islands(bm) -> list:
    """Faces grouped into islands joined by shared (welded) vertices."""
    bm.faces.ensure_lookup_table()
    seen, out = set(), []
    for face in bm.faces:
        if face.index in seen:
            continue
        seen.add(face.index)
        stack, island = [face], []
        while stack:
            f = stack.pop()
            island.append(f)
            for v in f.verts:
                for g in v.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        stack.append(g)
        out.append(island)
    return out


def _part(island, matrix, name: str = ""):
    """(up tree, down tree, lo, hi, name) for one island in world space, or None
    if it has no floor or ceiling face at all (a lone wall panel).

    Facing is judged by the world normal (the inverse transpose), which is the
    side the clients draw as outside even on a mirrored object.
    """
    normals = matrix.to_3x3().inverted_safe().transposed()
    world = {}
    trees = []
    for sign in (1, -1):
        verts, polys, slot = [], [], {}
        for f in island:
            if (normals @ f.normal).normalized().z * sign <= 0.05:
                continue
            poly = []
            for v in f.verts:
                if v.index not in slot:
                    slot[v.index] = len(verts)
                    if v.index not in world:
                        world[v.index] = matrix @ v.co
                    verts.append(world[v.index])
                poly.append(slot[v.index])
            # Wound so the tree's own normal agrees, mirrored or not.
            polys.append(poly if matrix.determinant() > 0 else poly[::-1])
        trees.append(BVHTree.FromPolygons(verts, polys) if polys else None)
    if trees == [None, None]:
        return None
    cos = [world.get(v.index) or matrix @ v.co for f in island for v in f.verts]
    lo = [min(c[i] for c in cos) for i in range(3)]
    hi = [max(c[i] for c in cos) for i in range(3)]
    return trees[0], trees[1], lo, hi, name


def near(index, x: float, y: float) -> list:
    """The parts whose footprint holds the line at (x, y)."""
    return [
        p
        for p in index.get((int(x) // TILE, int(y) // TILE), ())
        if p[2][0] <= x <= p[3][0] and p[2][1] <= y <= p[3][1]
    ]


def source_glb(name: str, from_shipped: bool = False) -> str:
    """The unsplit mesh the generator left for this bake.

    The shipped GLB only on request: its AO edge splits moved 45 Inferno floors
    by up to four cubes, and a quiet fallback to it is how a bake goes wrong
    without anyone noticing.
    """
    unsplit = os.path.join(COLLISION_DIR, f"{name}.glb")
    if os.path.isfile(unsplit):
        return unsplit
    if not from_shipped:
        raise SystemExit(
            f"{name}: no {unsplit}. Regenerate the map first, or pass "
            "--from-shipped to bake the shipped GLB (whose floors move)."
        )
    print(f"{name}: baking the shipped GLB on request", file=sys.__stdout__)
    return os.path.join(MAPS_DIR, f"{name}.glb")


def _hits(tree, x: float, y: float, zlo: float, zhi: float, nz: float, out) -> None:
    origin = Vector((x, y, zhi + 1.0))
    down = Vector((0.0, 0.0, -1.0))
    remaining = zhi - zlo + 2.0
    while remaining > 0:
        loc, _normal, _, dist = tree.ray_cast(origin, down, remaining)
        if loc is None:
            break
        out.append((loc.z, nz))
        origin = loc + down * 1e-4
        remaining -= dist + 1e-4


def object_solid(
    part, x: float, y: float, zlo: float, zhi: float
) -> list[tuple[float, float]]:
    """Where one part is solid along the vertical line at (x, y), bottom up.

    Counts depth by face orientation: a face whose normal points down is where
    solid begins going up, one pointing up is where it ends. An *open* surface —
    a roof or floor modelled as a single plane, with no face on the other side —
    is read as a thin slab, never as "solid all the way down": that reading
    turned every room under a one-sided roof into rock.
    """
    hits = []
    up_tree, down_tree = part[0], part[1]
    if up_tree is not None:
        _hits(up_tree, x, y, zlo, zhi, 1.0, hits)
    if down_tree is not None:
        _hits(down_tree, x, y, zlo, zhi, -1.0, hits)
    # At one height a bottom sorts before a top, so a box standing on a box
    # reads as one solid rather than two with a seam of nothing between.
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


def free_intervals(
    index, x: float, y: float, zlo: float, zhi: float
) -> list[tuple[float, float]]:
    """Open space along the line: everything no part is solid in."""
    blocked = []
    for part in near(index, x, y):
        blocked += object_solid(part, x, y, zlo, zhi)
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


# A small irrational offset keeps samples off the modelled edges, which sit on
# tidy multiples of 0.3 after the metres-to-cubes scale.
OFFSETS = [(i + 0.5) / SAMPLES + 0.0137 for i in range(SAMPLES)]


def sample_cell(index, cx: int, cy: int, zlo: float, zhi: float):
    """The cell's common open spans, each with every sample's own floor under it.

    Returns `[(bottom, top, floors)]`: `bottom`/`top` bound the space open at all
    16 samples, `floors[iy][ix]` is where that sample's own open interval
    containing the span begins (`bottom` is their max).
    """
    frees = [
        [free_intervals(index, cx + ox, cy + oy, zlo, zhi) for ox in OFFSETS]
        for oy in OFFSETS
    ]
    common = None
    for row in frees:
        for free in row:
            common = free if common is None else intersect(common, free)
            if not common:
                return []
    # A part thinner than the sample spacing (a stair rail, a pole, a steel
    # plate) can fall between all sixteen samples, and then it blocks nothing on
    # the server while both clients draw and collide with it. One more sample on
    # its centreline catches it; it only ever blocks, so it joins `common` and
    # stays out of the floors the sliver rule weighs.
    for x, y in _thin_points(index, cx, cy):
        common = intersect(common, free_intervals(index, x, y, zlo, zhi))
        if not common:
            return []
    out = []
    for bottom, top in common:
        floors = [
            [next(a for a, b in free if a <= bottom and b >= top) for free in row]
            for row in frees
        ]
        out.append((bottom, top, floors))
    return out


def _thin_points(index, cx: int, cy: int) -> list[tuple[float, float]]:
    """A point on the centreline of each thin part crossing the cell."""
    points = []
    for part in index.get((cx // TILE, cy // TILE), ()):
        lo, hi = part[2], part[3]
        if hi[0] < cx or lo[0] > cx + 1 or hi[1] < cy or lo[1] > cy + 1:
            continue
        span = [(max(lo[i], c), min(hi[i], c + 1)) for i, c in ((0, cx), (1, cy))]
        if min(hi[0] - lo[0], hi[1] - lo[1]) >= THIN:
            continue
        x, y = ((a + b) / 2 for a, b in span)
        points.append((x, y))
    return points


def _sliver_floor(cell, bottom: float, floors, majority, below: float) -> float:
    """`bottom` unless the cell's high part is a sliver its neighbour carries.

    `majority(cell, height)` says whether a neighbour mostly stands at, or is
    solid at, `height`. The high samples must be a minority and lie on one side
    (or corner) of the cell, and a neighbour on that side must be mostly that
    high: then that neighbour blocks or holds the feature, and this cell can
    take the floor most of it stands on.

    Never below `below`, the top of the opening under this one in the same
    column: there the low samples are looking down a hole (Nuke's vent hatch in
    A's floor), and dropping the floor through the slab gave the room and the
    crawl space under it one floor, which closed the hatch.
    """
    flat = sorted(f for row in floors for f in row)
    low = flat[len(flat) // 2]  # upper median: ties go to the higher floor
    if low == -math.inf or bottom - low < 0.5 or low < below:
        return bottom
    high = [
        (ix, iy)
        for iy, row in enumerate(floors)
        for ix, f in enumerate(row)
        if f > low + 0.25
    ]
    # No high sample: a thin part between the samples raised the floor (see
    # `_thin_points`), and that is this cell's own feature to keep.
    if not high or len(high) > SLIVER:
        return bottom
    xs, ys = {ix for ix, _ in high}, {iy for _, iy in high}
    half = SAMPLES // 2
    dx = 1 if min(xs) >= half else -1 if max(xs) < half else 0
    dy = 1 if min(ys) >= half else -1 if max(ys) < half else 0
    if dx == 0 and dy == 0:
        return bottom  # high samples on both sides: a feature of this cell's own
    sides = {(dx, dy), (dx, 0), (0, dy)} - {(0, 0)}
    if any(majority((cell[0] + sx, cell[1] + sy), bottom) for sx, sy in sides):
        return low
    return bottom


def bake(name: str, from_shipped: bool = False) -> list[dict]:
    index, zlo, zhi = load_colliders(name, from_shipped)
    sky = min(127, math.ceil(zhi) + 8)
    sampled: dict[tuple[int, int], list] = {}
    for cy in range(1, SSIZE - 1):
        for cx in range(1, SSIZE - 1):
            spans = sample_cell(index, cx, cy, zlo, zhi)
            if spans:
                sampled[(cx, cy)] = spans

    def majority(cell, height: float) -> bool:
        """Whether `cell` is mostly at `height`: most samples of some span stand
        there, or nothing in it is open at that height (it is the solid)."""
        spans = sampled.get(cell)
        if not spans:
            return True
        for _bottom, _top, floors in spans:
            flat = [f for row in floors for f in row]
            if sum(abs(f - height) < 0.25 for f in flat) * 2 > len(flat):
                return True
        return not any(b - 0.25 <= height <= t for b, t, _ in spans)

    openings: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for cell, spans in sampled.items():
        out = []
        # The top of the last opening below this one a body could be in; the
        # gap under a crate lid's overhang is not one.
        below = -math.inf
        for bottom, top, floors in spans:
            if bottom == -math.inf:
                below = top
                continue  # nothing underfoot
            # Only a real opening is trimmed: a float-noise sliver of "open"
            # between two touching slabs, dropped, became a ten-cube opening
            # that closed Nuke's vent.
            stand = (
                bottom
                if top - bottom < MIN_OPENING
                else _sliver_floor(cell, bottom, floors, majority, below)
            )
            if top - bottom >= MIN_OPENING:
                below = top
            floor = math.floor(stand + 0.5)
            # The trees are float32: a roof modelled at exactly 12 comes back as
            # 11.99999 or 12.00001 by triangle, and rounding down must not turn
            # that noise into a whole cube.
            ceil = sky if top == math.inf else min(sky, math.floor(top + EPS))
            if ceil - floor >= MIN_OPENING and -128 <= floor <= 127:
                out.append((floor, ceil))
        if out:
            openings[cell] = out
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
        heads = [
            (c, i) for c, i in base_cells if abs(openings[c][i][0] - top) <= JUMP_CLIMB
        ]
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
        if (
            cell not in reached_cells
            or openings[cell][i][0] < openings[cell][reached_cells[cell]][0]
        ):
            reached_cells[cell] = i
    chosen = {cell: openings[cell][i] for cell, i in reached_cells.items()}
    walked = dict(chosen)  # judged against reached floor only, never a chain of tops
    for cell, spans in openings.items():
        if cell in chosen:
            continue
        near = [
            walked[n][0]
            for dx in range(-3, 4)
            for dy in range(-3, 4)
            if (n := (cell[0] + dx, cell[1] + dy)) in walked
        ]
        if not near:
            continue
        # Its top, when that is open to the sky and above the floor beside it: a
        # crate's top. Nothing roofed (a catwalk nobody climbs to is a cut-off
        # island). Under a roof that makes a prop nobody reaches (a fridge in
        # the bank's office) solid up to the roof; the server places bodies
        # only where they fit (`physics.place_body`), so none is lifted there.
        top_floor, top_ceil = spans[-1]
        if top_ceil >= sky and top_floor > min(near):
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


def sha256(path: str) -> str:
    """Short content hash of a file: what `collisionSource`/`collisionBaked` hold."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def write(name: str, brushes: list[dict], baked_from: str) -> None:
    path = os.path.join(MAPS_DIR, f"{name}.json")
    with open(path, encoding="utf-8") as f:
        source = json.load(f)
    source["sfactor"] = SFACTOR
    source["collision"] = "baked"
    source["collisionBaked"] = sha256(baked_from)
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
    shipped = "--from-shipped" in argv
    for map_name in (a for a in argv if not a.startswith("--")):
        write(map_name, bake(map_name, shipped), source_glb(map_name, shipped))
