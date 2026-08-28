#!/usr/bin/env python
"""Decimate a weapon mesh down to a view-model triangle budget, in Blender.

The missing first step of `build_hassault_weapon.mjs` for the models that are
too dense to use. That script converts and orients a weapon; it deliberately
does **not** simplify one, because three.js has no simplifier worth using at a
98% reduction — so it warns above 40k triangles and leaves the judgement to a
person. This is that judgement, made once and written down.

    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \\
      --python scripts/decimate_weapon.py -- \\
      --in "assets/horribleAssault/carbine-m4a1/source/Carbine M4A1.fbx" \\
      --out .cache/hassault/carbine-m4a1-lod.fbx \\
      --budget 30000

Run headless (`--background`), so it needs no display and no GPU.

## Why a per-mesh budget rather than one ratio

The obvious implementation is one `ratio` on every mesh, and it produces a
weapon that is wrong in a specific and recognisable way. This M4A1 is 25 meshes
whose sizes span three orders of magnitude: the receiver is a few hundred
thousand triangles, the rear sight aperture is a few dozen. A flat 4% ratio
takes the receiver to something reasonable and takes the sight to **one
triangle** — the small parts, which are exactly the ones a player is looking
down, dissolve first.

So the budget is allocated *proportionally* and then floored: no mesh is
reduced below `MIN_FACES`, or below its own original count if it started
smaller. Meshes that hit the floor keep their triangles, and the surplus comes
out of the large ones, which is where it is invisible.

## What decimation costs, honestly

Collapse decimation is a *geometric* simplification. It does not know about UV
seams, and at these ratios it will move vertices across them — so the texture
crawls slightly on the most-reduced meshes. That is acceptable here and worth
naming: this is a view model held at arm's length, most of its apparent detail
lives in the normal map rather than in the silhouette, and the alternative is
no model at all. It would **not** be acceptable for a world asset the camera
can approach.

`use_collapse_triangulate` is on because the exporter triangulates anyway, and
letting the decimator see the triangles it is actually collapsing gives a
better result than triangulating a decimated quad mesh afterwards.

## What this deliberately does not do

It does not scale, orient, or touch materials. Those are
`build_hassault_weapon.mjs`'s, which derives the scale from a stated real
length and refuses to guess the forward axis — and a second place that could
decide either of them is a second place they can disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

# The fewest faces any single mesh is reduced to. Below roughly this a small
# part stops reading as a shape at all: a trigger becomes a sliver, a sight
# aperture becomes a line. Chosen as a floor rather than a target — a mesh that
# started with fewer keeps every face it had.
MIN_FACES = 64


def parse_args(argv: list[str]) -> dict[str, str]:
    """Blender passes the script everything after a bare `--`."""
    if "--" not in argv:
        return {}
    out: dict[str, str] = {}
    rest = argv[argv.index("--") + 1 :]
    for i in range(0, len(rest) - 1, 2):
        if rest[i].startswith("--"):
            out[rest[i][2:]] = rest[i + 1]
    return out


def import_any(path: Path) -> None:
    """Import an FBX through whichever operator this Blender ships.

    5.0 replaced the Python FBX *importer* with a C++ one under a new name
    (`wm.fbx_import`) and left the exporter where it was, so the two halves need
    different handling and a build that has only the old importer still works.

    Tried rather than tested, because **`hasattr` cannot answer this**:
    `bpy.ops.wm` resolves attributes dynamically, so `hasattr(bpy.ops.wm,
    anything)` is `True` and the check silently passes for an operator that does
    not exist. That read as a working version probe right up until the export
    raised at the end of a two-minute decimation.
    """
    try:
        bpy.ops.wm.fbx_import(filepath=str(path))
    except AttributeError:
        bpy.ops.import_scene.fbx(filepath=str(path))


def export_any(path: Path) -> None:
    # `path_mode="STRIP"`: the maps are matched out of the sibling `textures/`
    # directory by `build_hassault_weapon.mjs`, never read from the FBX — see its
    # header. Copying them beside this file would write 32 MB nothing reads.
    try:
        bpy.ops.export_scene.fbx(
            filepath=str(path), use_selection=False, path_mode="STRIP"
        )
    except AttributeError:
        bpy.ops.wm.fbx_export(filepath=str(path))


def faces_of(objects) -> int:
    return sum(len(o.data.polygons) for o in objects)


def main() -> int:
    args = parse_args(sys.argv)
    src = Path(args.get("in", "")).resolve()
    dst = Path(args.get("out", "")).resolve()
    budget = int(args.get("budget", "30000"))
    if not src.exists():
        print(f"decimate: no such file: {src}", file=sys.stderr)
        return 1

    # A clean file, not the startup scene: the default cube would be welded into
    # the weapon and nothing downstream would say so.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_any(src)

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        print(f"decimate: {src.name} has no meshes", file=sys.stderr)
        return 1

    before = faces_of(meshes)
    print(f"decimate: {len(meshes)} meshes, {before} faces, budget {budget}")
    if before <= budget:
        print("decimate: already inside the budget; nothing to do")
        return 1

    # Two passes. The first works out which meshes are already at or under the
    # floor — those are spent, whatever the ratio says — and the second shares
    # what is left over the meshes that can actually give it up. Without the
    # split, the floor silently pushes the total back over the budget.
    share = budget / before
    spent = 0
    reducible = []
    for obj in meshes:
        n = len(obj.data.polygons)
        if n * share < MIN_FACES:
            spent += min(n, MIN_FACES)
        else:
            reducible.append(obj)
    room = max(budget - spent, 0)
    pool = faces_of(reducible)
    ratio = (room / pool) if pool else 1.0

    for obj in meshes:
        n = len(obj.data.polygons)
        want = (
            max(MIN_FACES, round(n * ratio)) if obj in reducible else min(n, MIN_FACES)
        )
        if want >= n:
            continue
        mod = obj.modifiers.new(name="decimate", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = want / n
        mod.use_collapse_triangulate = True
        # Applied rather than left as a modifier: the FBX exporter would bake it
        # anyway, and a file whose triangle count depends on an evaluation
        # setting is one that reports a number it does not have.
        ctx = bpy.context.copy()
        ctx["object"] = obj
        with bpy.context.temp_override(**ctx):
            bpy.ops.object.modifier_apply(modifier=mod.name)

    after = faces_of([o for o in bpy.data.objects if o.type == "MESH"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    export_any(dst)
    pct = 100.0 * (1.0 - after / before)
    print(f"decimate: {before} -> {after} faces ({pct:.1f}% off) -> {dst}")
    # Reported, not enforced: the floor can legitimately hold the total above the
    # budget on a model made of many small parts, and failing there would be this
    # script refusing to do the only thing it can do.
    if after > budget:
        print(
            f"decimate: over budget by {after - budget}, held up by the {MIN_FACES}-face floor"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
