"""Visible map objects a player's eye can end up inside.

Both clients cull back faces, and from inside a mesh every face is a back face, so
an object with an eye in it vanishes for that player while still hiding them from
everyone else: "see-through from one side". The server decides where a body can
be, so this asks it (`tools/hassault_body_points.py`) and then tests every body
point, at the crouched and standing eye heights, against every visible mesh:

    uv run python tools/hassault_body_points.py hd_dust2 > dust2-bodies.json
    blender --background --factory-startup --python tools/blender/probe_eye_inside.py -- hd_dust2 dust2-bodies.json

Each hit is classed by the bake's own `collides`. A `NonCol` or decoration mesh
with an eye in it is a bug: make it solid, or move it where no eye reaches. A
breakable pane is reported apart, because hosted play treats glass as open on
purpose (see `bake_collision.py`), so a body can always stand in one. Exits 1 when
anything other than glass is hit.
"""

import json
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake_collision  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1 :]
name, bodies_path = argv[0], argv[1]
with open(bodies_path, encoding="utf-8") as f:
    probe = json.load(f)
eyes = probe["eyes"]

bpy.ops.wm.read_homefile(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.path.join(bake_collision.MAPS_DIR, f"{name}.glb"))

by_cell = {}
for x, y, floor in probe["bodies"]:
    by_cell.setdefault((int(x), int(y)), []).append((x, y, floor))

up = Vector((0.0, 0.0, 1.0))
depsgraph = bpy.context.evaluated_depsgraph_get()
rows = []
for obj in bpy.context.scene.objects:
    if obj.type != "MESH" or "ColOnly" in obj.name or "Invisible" in obj.name:
        continue
    breakable = bool(bake_collision.BREAKABLE.search(obj.name))
    if bake_collision.collides(obj) and not breakable:
        continue  # the server keeps bodies out of it
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    tree = BVHTree.FromPolygons(
        [obj.matrix_world @ v.co for v in mesh.vertices],
        [list(p.vertices) for p in mesh.polygons],
    )
    evaluated.to_mesh_clear()
    inside, example = 0, None
    for cx in range(int(lo[0]), int(hi[0]) + 1):
        for cy in range(int(lo[1]), int(hi[1]) + 1):
            for x, y, floor in by_cell.get((cx, cy), ()):
                if not (lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]):
                    continue
                for eye in eyes:
                    z = floor + eye
                    if not lo[2] <= z <= hi[2]:
                        continue
                    # Inside a closed mesh: an odd number of crossings straight up.
                    point, crossings = Vector((x, y, z)), 0
                    while True:
                        hit, _normal, _index, _dist = tree.ray_cast(point, up, 1000.0)
                        if hit is None:
                            break
                        crossings += 1
                        point = hit + Vector((0.0, 0.0, 1e-4))
                    if crossings % 2:
                        inside += 1
                        example = example or (round(x, 2), round(y, 2), round(z, 2))
    if inside:
        rows.append((breakable, inside, obj.name, example))

bugs = 0
for breakable, inside, obj_name, example in sorted(rows, key=lambda r: (r[0], -r[1])):
    kind = "glass (open in hosted play)" if breakable else "EYE INSIDE"
    bugs += not breakable
    print(f"{kind:28s} {inside:5d} eyes  {obj_name}  e.g. eye at {example}")
print(
    f"{name}: {bugs} object(s) an eye fits inside, {len(rows) - bugs} breakable pane(s)"
)
sys.exit(1 if bugs else 0)
