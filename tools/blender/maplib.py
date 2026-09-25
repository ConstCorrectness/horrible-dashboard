"""Shared export step for the modelled hassault maps (`generate_*.py`).

Every generator here is authored in **metres** — a 70 m Dust II, a 4 m office
storey, a 2.6 m shipping container. The game is not in metres: one world unit is
one cube, and a standing player is 5.2 of them (eye 4.5 + 0.7 above it). Read
as cubes, a metre-scale map puts a 1.75 m person in a world built for someone a
third their size: a 2.2-unit body squeezing through a 4.4-unit "grand" archway,
a 4.2-unit tunnel it cannot stand up in, and a whole Dust II that is thirty body
widths across. That is what "the maps are too small" was.

So the conversion lives here, once, applied to the finished scene just before
export: `MAP_SCALE` cubes per metre, uniformly. 3.0 is the player's own
proportion (5.2 cubes / ~1.75 m) and the figure `generate_office.py` already
used for its heights. It is **uniform** on purpose — scaling only the footprint
would turn every crate into a slab and every barrel into an ellipse.

The generators keep authoring in metres. Everything that places something *on*
a map — the JSON's brushes, spawns, items and sites, the `world3d.ts` /
`world3d.rs` tables, bot cover nodes — is in cubes, i.e. already multiplied.

`HASSAULT_MAP_SCALE` and `HASSAULT_MAP_OUT` override the factor and the output
directory, so a generator can be checked against the committed GLB (run it at
1.0 into a scratch directory and compare) without touching the repo.
"""

from __future__ import annotations

import os
import shutil

import bmesh
import bpy
import math

from mathutils import Matrix, Vector

MAP_SCALE = 3.0

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)


def _bake_modifiers() -> None:
    """Apply every modifier, so a bevel's width scales with the mesh it rounds.

    Scaling the mesh under a live Bevel leaves its width in absolute units — a
    5 cm chamfer on a crate three times the size. Baking first makes the whole
    object, modifiers included, one thing that scales together.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in list(bpy.data.objects):
        if obj.type != "MESH" or not obj.modifiers:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
        )
        old = obj.data
        obj.modifiers.clear()
        obj.data = mesh
        if old.users == 0:
            bpy.data.meshes.remove(old)


def scale_scene(factor: float) -> None:
    """Scale the whole scene about the origin by `factor`, hierarchy intact.

    For a uniform `D = factor·I`, conjugating any object matrix `[A | t]` gives
    `[A | factor·t]` — D commutes with rotation and scale — so scaling every
    translation in the chain (basis and parent-inverse) and every mesh's
    vertices once yields exactly `D · world` for every object, without
    flattening the node tree the clients read names from.
    """
    if factor == 1.0:
        return
    _bake_modifiers()
    for mesh in bpy.data.meshes:
        if mesh.users:
            mesh.transform(Matrix.Scale(factor, 4))
    for obj in bpy.data.objects:
        basis = obj.matrix_basis.copy()
        basis.translation = basis.translation * factor
        obj.matrix_basis = basis
        inverse = obj.matrix_parent_inverse.copy()
        inverse.translation = inverse.translation * factor
        obj.matrix_parent_inverse = inverse
        if obj.type == "LIGHT":
            obj.data.energy *= factor * factor
    bpy.context.view_layer.update()


def add_wedge(collection, name, center, size, material, rise):
    """A ramp: a box whose top slopes from its bottom to its full height.

    `rise` is the side the high end faces (`+x`, `-x`, `+y`, `-y`). Several
    generators drew their ramps with `add_box`, which made every one a cliff —
    harmless while a body was a third of its drawn size and could hop onto it,
    impassable once the maps were scaled. Metres in, like everything here.
    """
    cx, cy, cz = center
    sx, sy, sz = size
    x0, x1, y0, y1 = cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2

    def top(x, y):
        t = {
            "+x": (x - x0) / sx,
            "-x": (x1 - x) / sx,
            "+y": (y - y0) / sy,
            "-y": (y1 - y) / sy,
        }[rise]
        return z0 + (z1 - z0) * t

    bm = bmesh.new()
    b = [bm.verts.new((x, y, z0)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    t = [bm.verts.new((x, y, top(x, y))) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    bm.verts.ensure_lookup_table()
    bm.faces.new((t[0], t[1], t[2], t[3]))
    bm.faces.new((b[3], b[2], b[1], b[0]))
    for i in range(4):
        j = (i + 1) % 4
        # Where the slope meets the floor a side collapses to a triangle.
        ring = [b[i], b[j], t[j], t[i]]
        unique = []
        for v in ring:
            if all((v.co - u.co).length > 1e-6 for u in unique):
                unique.append(v)
        if len(unique) >= 3:
            bm.faces.new(unique)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def _box(collection, name, lo, hi, material):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    size = [h - a for a, h in zip(lo, hi)]
    center = [(a + h) / 2 for a, h in zip(lo, hi)]
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def add_wall_with_door(collection, name, center, size, material, door_at, door_width, door_height):
    """A wall along its longer axis with a doorway cut through it.

    `door_at` is the doorway's centre along that axis. Three pieces: the two
    wall sections either side, and a lintel over the opening, so the doorway is
    a real hole in the collision mesh and not a painted one.
    """
    cx, cy, cz = center
    sx, sy, sz = size
    lo = [cx - sx / 2, cy - sy / 2, cz - sz / 2]
    hi = [cx + sx / 2, cy + sy / 2, cz + sz / 2]
    axis = 0 if sx >= sy else 1
    a, b = door_at - door_width / 2, door_at + door_width / 2
    left_hi = list(hi)
    left_hi[axis] = a
    right_lo = list(lo)
    right_lo[axis] = b
    lintel_lo = list(lo)
    lintel_hi = list(hi)
    lintel_lo[axis], lintel_hi[axis] = a, b
    lintel_lo[2] = lo[2] + door_height
    _box(collection, f"{name}_A", lo, left_hi, material)
    _box(collection, f"{name}_B", right_lo, hi, material)
    if lintel_lo[2] < hi[2]:
        _box(collection, f"{name}_Lintel", lintel_lo, lintel_hi, material)


def add_safety_floor(collection, name="Safety_Floor_ColOnly", thickness=0.5):
    """An invisible floor under the whole map, at its lowest surface.

    Some generators lay a floor slab per room and stop it at the walls, so the
    strip under a doorway has no floor at all: offline a body walking through
    the doorway fell out of the world, and the collision bake read the doorway
    as solid. `ColOnly` makes both clients collide with it and neither draw it.
    """
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = [min(a, b) for a, b in zip(lo, w)]
            hi = [max(a, b) for a, b in zip(hi, w)]
    if not math.isfinite(lo[0]):
        return None
    return _box(collection, name, (lo[0], lo[1], lo[2] - thickness), (hi[0], hi[1], lo[2]), None)


def export_map_glb(name: str) -> str:
    """Scale the built scene to cubes and write `<name>.glb` to both homes."""
    factor = float(os.environ.get("HASSAULT_MAP_SCALE", MAP_SCALE))
    add_safety_floor(bpy.context.scene.collection)
    scale_scene(factor)

    override = os.environ.get("HASSAULT_MAP_OUT")
    backend_dir = override or os.path.join(
        REPO_ROOT, "backend", "modules", "hassault", "maps"
    )
    web_dir = None if override else os.path.join(REPO_ROOT, "apps", "web", "public")
    os.makedirs(backend_dir, exist_ok=True)

    backend_path = os.path.join(backend_dir, f"{name}.glb")
    print(f"Exporting {name} at {factor}x to: {backend_path}")
    bpy.ops.export_scene.gltf(
        filepath=backend_path,
        export_format="GLB",
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials="EXPORT",
        export_lights=False,
        export_cameras=False,
    )
    if web_dir:
        os.makedirs(web_dir, exist_ok=True)
        shutil.copyfile(backend_path, os.path.join(web_dir, f"{name}.glb"))
    return backend_path
