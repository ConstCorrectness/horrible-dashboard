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

**Lights and atmosphere.** A GLB carries geometry and materials; how the map is
*lit* lives in its JSON, where both clients and the server read it
(`backend/modules/hassault/atmosphere.py`). A generator records point lights with
`add_light` and picks a sky with `set_atmosphere`, and `export_map_glb` writes
both into `<name>.json` beside the GLB — replacing that file's `light` entities
and `atmosphere` block, so a re-run is idempotent and nothing else in the JSON
(brushes, spawns, sites) is touched.

**Colour.** glTF's base colour is *linear*, and every generator here picked its
colours by eye as sRGB — `(0.84, 0.74, 0.58)` is a sandstone swatch, not a
linear reflectance. Exported as-is, each surface was about twice as bright as
meant and most of its saturation was gone: Dust II's sandstone drew as pale
grey. `export_map_glb` converts each Principled base colour once, on the way
out (lifting only the deepest darks, see `DARK_FLOOR`), so the generators
keep the numbers a person can read.
"""

from __future__ import annotations

import json
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
    t = [
        bm.verts.new((x, y, top(x, y)))
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    ]
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


def add_boxes(collection, name, boxes, material):
    """Many axis-aligned boxes as **one** object: `boxes` is `(center, size)` pairs.

    For dressing — trim, beams, slats, sandbags — where a node per piece would
    grow the GLB by its node and accessor overhead rather than its geometry, and
    every client would walk a few hundred more nodes at load. Give it a `NonCol`
    name unless the pieces are meant to be stood on.
    """
    bm = bmesh.new()
    for center, size in boxes:
        piece = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        bmesh.ops.scale(bm, vec=Vector(size), verts=piece)
        bmesh.ops.translate(bm, vec=Vector(center), verts=piece)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def add_plinths(collection, name, walls, material, height=0.5, proud=0.08, base=0.0):
    """A proud course along both long faces of each wall: `walls` is `(center, size)`.

    The cheapest thing that stops a long wall reading as a flat plane. `base` is
    the floor height the course sits on.
    """
    boxes = []
    for (cx, cy, _), (sx, sy, _) in walls:
        if sx >= sy:
            for side in (-1, 1):
                boxes.append(
                    (
                        (cx, cy + side * (sy / 2 + proud / 2), base + height / 2),
                        (sx, proud, height),
                    )
                )
        else:
            for side in (-1, 1):
                boxes.append(
                    (
                        (cx + side * (sx / 2 + proud / 2), cy, base + height / 2),
                        (proud, sy, height),
                    )
                )
    return add_boxes(collection, name, boxes, material)


def add_stairs(collection, name, start, width, rise, run, steps, axis, material):
    """A flight of solid steps: `start` is the bottom front centre, in metres.

    `axis` is the direction of climb (`+x`, `-x`, `+y`, `-y`). Each step is a box
    from the floor up, so the underside is solid and the collision bake sees a
    staircase rather than a floating ramp. Riser `rise / steps` stays under the
    body's step height as long as a step is below ~0.45 m.
    """
    sx, sy, sz = start
    dx, dy = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}[axis]
    tread = run / steps
    boxes = []
    for i in range(steps):
        h = rise * (i + 1) / steps
        along = tread * (i + 0.5)
        cx, cy = sx + dx * along, sy + dy * along
        size = (tread, width, h) if dx else (width, tread, h)
        boxes.append(((cx, cy, sz + h / 2), size))
    return add_boxes(collection, name, boxes, material)


def add_wall_with_door(
    collection, name, center, size, material, door_at, door_width, door_height
):
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
    return _box(
        collection, name, (lo[0], lo[1], lo[2] - thickness), (hi[0], hi[1], lo[2]), None
    )


# ---- lights and atmosphere ----------------------------------------------------------

#: Point lights recorded by `add_light`, in metres, until `export_map_glb`.
_LIGHTS: list[dict] = []
#: The map's atmosphere block, as `set_atmosphere` left it.
_ATMOSPHERE: dict = {}

#: Named skies. Keys are `atmosphere.py`'s; a map may override any of them. Sun
#: and fill directions are three-space — x east, y **up**, z the cube map's y —
#: and point toward the light.
ATMOSPHERES: dict[str, dict] = {
    # Hard noon over pale stone: a high warm sun, a sandy bounce from below and
    # a haze at the horizon rather than a blue wall.
    "desert_noon": {
        "skyZenith": 0x2E6CC7,
        "skyHorizon": 0xD6DCE0,
        "hemiSky": 0xB4CCEB,
        "hemiGround": 0xB58C5C,
        "hemiIntensity": 1.25,
        "sunColor": 0xFFE7C6,
        "sunIntensity": 3.1,
        "sunDir": [0.42, 0.8, 0.43],
        "fillColor": 0xFFD6A3,
        "fillIntensity": 0.35,
        "fillDir": [-0.45, 0.25, -0.6],
        "fogColor": 0xDAD4C6,
        "fogDensity": 0.0011,
        "exposure": 1.0,
        "sunDisc": True,
    },
    # Late morning on the coast: a lower, whiter sun and a bluer, clearer sky.
    "coastal_morning": {
        "skyZenith": 0x3572C4,
        "skyHorizon": 0xC7DAEA,
        "hemiSky": 0xB9D2F2,
        "hemiGround": 0xA88A66,
        "hemiIntensity": 1.3,
        "sunColor": 0xFFF0DA,
        "sunIntensity": 2.8,
        "sunDir": [-0.55, 0.62, 0.55],
        "fillColor": 0xCFE0FF,
        "fillIntensity": 0.35,
        "fillDir": [0.5, 0.3, -0.6],
        "fogColor": 0xCBD8E2,
        "fogDensity": 0.0012,
        "exposure": 1.0,
        "sunDisc": True,
    },
    # A low amber sun raking across terracotta, long shadows, warm dust.
    "golden_hour": {
        "skyZenith": 0x3B5E9C,
        "skyHorizon": 0xF0C89A,
        "hemiSky": 0xA9B8D8,
        "hemiGround": 0x8C5E3C,
        "hemiIntensity": 1.15,
        "sunColor": 0xFFC98F,
        "sunIntensity": 3.0,
        "sunDir": [0.72, 0.42, -0.55],
        "fillColor": 0x8FA6D8,
        "fillIntensity": 0.4,
        "fillDir": [-0.6, 0.35, 0.5],
        "fogColor": 0xE3C4A0,
        "fogDensity": 0.0016,
        "exposure": 1.05,
        "sunDisc": True,
    },
    # Flat grey industrial daylight: a soft sun through cloud, a big sky term,
    # and a cold haze that stops the far fence reading sharp.
    "overcast": {
        "skyZenith": 0x75808E,
        "skyHorizon": 0xB8BEC4,
        "hemiSky": 0xC6CFD9,
        "hemiGround": 0x5E5A54,
        "hemiIntensity": 2.0,
        "sunColor": 0xE8ECF0,
        "sunIntensity": 1.3,
        "sunDir": [0.3, 0.85, 0.42],
        "fillColor": 0xB8C4D2,
        "fillIntensity": 0.4,
        "fillDir": [-0.5, 0.4, -0.6],
        "fogColor": 0xAEB4BA,
        "fogDensity": 0.0022,
        "exposure": 1.05,
        "sunDisc": False,
    },
    # Indoors under strip lights: no sun to speak of, a cool even ambient, and
    # the map's own lamps doing the modelling.
    "interior_cool": {
        "skyZenith": 0x1A2230,
        "skyHorizon": 0x3A4656,
        "hemiSky": 0xD8E4F4,
        "hemiGround": 0x4A4640,
        "hemiIntensity": 1.7,
        "sunColor": 0xF2F6FF,
        "sunIntensity": 0.9,
        "sunDir": [0.25, 0.9, 0.35],
        "fillColor": 0xB8C8E0,
        "fillIntensity": 0.35,
        "fillDir": [-0.5, 0.4, -0.6],
        "fogColor": 0x2A323E,
        "fogDensity": 0.0018,
        "exposure": 1.1,
        "sunDisc": False,
    },
    # Marble and brass under warm chandeliers.
    "interior_warm": {
        "skyZenith": 0x20242C,
        "skyHorizon": 0x4A4A4E,
        "hemiSky": 0xFFE8CC,
        "hemiGround": 0x4E4034,
        "hemiIntensity": 1.6,
        "sunColor": 0xFFF0DC,
        "sunIntensity": 1.1,
        "sunDir": [0.3, 0.86, 0.4],
        "fillColor": 0xC8D4F0,
        "fillIntensity": 0.3,
        "fillDir": [-0.5, 0.4, -0.6],
        "fogColor": 0x2E2A28,
        "fogDensity": 0.0016,
        "exposure": 1.1,
        "sunDisc": False,
    },
    # Dusk over a scrapyard: sodium lamps against a violet sky.
    "industrial_dusk": {
        "skyZenith": 0x28304E,
        "skyHorizon": 0xB88A78,
        "hemiSky": 0x8E9CC4,
        "hemiGround": 0x5A4A3C,
        "hemiIntensity": 1.4,
        "sunColor": 0xFFB482,
        "sunIntensity": 1.9,
        "sunDir": [-0.7, 0.35, 0.6],
        "fillColor": 0x9AA8D8,
        "fillIntensity": 0.45,
        "fillDir": [0.5, 0.4, -0.6],
        "fogColor": 0x8A7E86,
        "fogDensity": 0.002,
        "exposure": 1.1,
        "sunDisc": True,
    },
}


def set_atmosphere(preset: str, **overrides) -> dict:
    """Choose the map's sky and light rig: a named preset, then any overrides."""
    global _ATMOSPHERE
    _ATMOSPHERE = {**ATMOSPHERES[preset], **overrides}
    return _ATMOSPHERE


def add_light(pos, color=(255, 214, 160), radius=8.0, intensity=1.0) -> dict:
    """Record a point light at `pos`, in metres, reaching `radius` metres.

    Only the light: the fixture it hangs from is the generator's own mesh (a
    lantern, a strip light), which has an emissive face and a `NonCol` name.
    `color` is 0-255 sRGB; `intensity` a multiplier on the radius's default
    brightness (see `atmosphere.lights`).
    """
    light = {
        "pos": tuple(float(v) for v in pos),
        "color": tuple(int(c) for c in color),
        "radius": float(radius),
        "intensity": float(intensity),
    }
    _LIGHTS.append(light)
    return light


def make_material(name, color, emission=None, strength=0.0, metallic=0.0, roughness=0.6):
    """A plain Principled material, reused by name."""
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
        bsdf.inputs[key].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    return mat


def add_lantern(
    collection, name, pos, hang=0.6, color=(255, 186, 112), radius=7.0, intensity=2.0
):
    """A hanging brass lantern whose flame is a light: `pos` is where it hangs from.

    One merged fixture mesh plus one flame mesh, both `NonCol`, and a recorded
    light at the flame. `hang` is the chain length in metres.
    """
    brass = make_material(
        "mat_lantern_brass", (0.75, 0.6, 0.22), metallic=0.88, roughness=0.25
    )
    flame = make_material(
        "mat_lantern_flame", (1.0, 0.7, 0.3), emission=(1.0, 0.7, 0.3), strength=5.0
    )
    x, y, z = pos
    body = z - hang - 0.25
    add_boxes(
        collection,
        f"{name}_Fixture_NonCol",
        [
            ((x, y, z - hang / 2), (0.04, 0.04, hang)),
            ((x, y, body + 0.25), (0.36, 0.36, 0.06)),
            ((x, y, body - 0.25), (0.3, 0.3, 0.06)),
            ((x - 0.16, y, body), (0.03, 0.03, 0.5)),
            ((x + 0.16, y, body), (0.03, 0.03, 0.5)),
            ((x, y - 0.16, body), (0.03, 0.03, 0.5)),
            ((x, y + 0.16, body), (0.03, 0.03, 0.5)),
        ],
        brass,
    )
    add_boxes(
        collection, f"{name}_Flame_NonCol", [((x, y, body), (0.16, 0.16, 0.3))], flame
    )
    return add_light((x, y, body), color=color, radius=radius, intensity=intensity)


def lights_from_emitters(
    match,
    color=(255, 240, 220),
    radius=8.0,
    intensity=1.5,
    spacing=4.0,
    drop=0.35,
    limit=None,
) -> int:
    """A light at every fixture already modelled: objects whose material name
    contains any of `match`.

    The maps model their lamps (troffers, chandeliers, floodlights) as emissive
    meshes, which glow and light nothing. This puts a point light just under
    each one — `drop` metres below its centre, so it is in the room rather than
    in the ceiling — and merges fixtures closer than `spacing`, since a row of
    troffer tubes is one light source to the eye and 64 is the native budget
    for the whole view. Returns how many lights were added.
    """
    needles = [m.lower() for m in ([match] if isinstance(match, str) else match)]
    centers = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        names = [
            (s.material.name if s.material else "").lower() for s in obj.material_slots
        ]
        if not any(n in name for n in needles for name in names):
            continue
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        center = sum(corners, Vector()) / 8.0
        center.z = min(c.z for c in corners) - drop
        centers.append(center)
    placed = []
    for c in centers:
        if all((c - p).length >= spacing for p in placed):
            placed.append(c)
    if limit is not None:
        placed = placed[:limit]
    for c in placed:
        add_light(tuple(c), color=color, radius=radius, intensity=intensity)
    return len(placed)


def srgb_to_linear(c: float) -> float:
    """One sRGB channel (0..1) as linear reflectance."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


#: The darkest a channel may convert to, as a fraction of its authored value.
#: The exact curve takes a 0.2 asphalt to 0.033, which under these rigs — a sky,
#: a sun and no bounce light — draws as black. Only darks are lifted: anything
#: authored above ~0.45 converts to more than this already.
DARK_FLOOR = 0.3


def albedo(c: float) -> float:
    """An authored (sRGB) channel as the linear reflectance the map exports."""
    return max(srgb_to_linear(c), DARK_FLOOR * c)


def _linearize_base_colors() -> None:
    """Convert every Principled base colour from the sRGB it was picked in."""
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs["Base Color"]
            r, g, b, a = socket.default_value
            socket.default_value = (
                albedo(r),
                albedo(g),
                albedo(b),
                a,
            )


def dump_json(v, ind: int = 0) -> str:
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
            f"{pad}{json.dumps(k)}: {dump_json(x, ind + 1)}" for k, x in v.items()
        )
        return "{" + nl + body + nl + "  " * ind + "}"
    body = ("," + nl).join(f"{pad}{dump_json(x, ind + 1)}" for x in v)
    return "[" + nl + body + nl + "  " * ind + "]"


def _write_map_json(path: str, factor: float) -> None:
    """Put the recorded lights and atmosphere into the map's JSON, in cubes.

    Light entities are replaced wholesale and nothing else moves; the
    atmosphere block sits after `ambient`, where a reader looks for how the map
    is lit.
    """
    if not os.path.isfile(path) or (not _LIGHTS and not _ATMOSPHERE):
        return
    with open(path, encoding="utf-8") as f:
        source = json.load(f)
    entities = [e for e in source.get("entities", []) if e.get("type") != "light"]
    for light in _LIGHTS:
        x, y, z = (v * factor for v in light["pos"])
        entities.append(
            {
                "type": "light",
                "x": int(round(x)),
                "y": int(round(y)),
                "z": int(round(z)),
                "radius": max(1, min(255, int(round(light["radius"] * factor)))),
                "color": list(light["color"]),
                "intensity": round(light["intensity"], 2),
            }
        )
    source["entities"] = entities
    if _ATMOSPHERE:
        rebuilt = {}
        for key, value in source.items():
            if key == "atmosphere":
                continue
            rebuilt[key] = value
            if key == "ambient":
                rebuilt["atmosphere"] = _ATMOSPHERE
        rebuilt.setdefault("atmosphere", _ATMOSPHERE)
        source = rebuilt
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_json(source) + "\n")
    print(f"Wrote {len(_LIGHTS)} lights and atmosphere to: {path}")


def export_map_glb(name: str) -> str:
    """Scale the built scene to cubes and write `<name>.glb` to both homes.

    Also writes the recorded lights and atmosphere into `<name>.json` when the
    output is the repo's own maps directory.
    """
    factor = float(os.environ.get("HASSAULT_MAP_SCALE", MAP_SCALE))
    add_safety_floor(bpy.context.scene.collection)
    _linearize_base_colors()
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
        _write_map_json(os.path.join(backend_dir, f"{name}.json"), factor)
    return backend_path
