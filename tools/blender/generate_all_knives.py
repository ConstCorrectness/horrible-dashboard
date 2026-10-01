#!/usr/bin/env python3
"""Procedural 3D Knife Suite & Rarity Texture Generator for Blender 4.2+

Designs and builds 6 competitive-grade tactical knives with CS-inspired geometry:
1. Default Tactical Tanto Combat Knife (M9/Tanto hybrid, serrated spine, fuller, glass breaker)
2. Karambit (Curved raptor claw, reverse-grip scales, safety index finger ring)
3. Butterfly Knife / Balisong (Clip-point swedge blade, dual channeled skeleton handles, latch)
4. M9 Bayonet (Military combat bayonet, sawback spine, muzzle-ring guard, barrel lug)
5. Skeleton Knife (One-piece full-tang, large finger hole, paracord wrapped skeletal handle)
6. Huntsman Knife (Heavy survival recurve tanto, double saw teeth, tactical grip scales)

Generates authentic PBR rarity finish textures:
- Fade / Marble Fade (Chromatic tri-color anodized gradient)
- Case Hardened "Blue Gem" (Heat-treated tempered steel with cobalt blue and fire scale)
- Crimson Web (Deep blood-red lacquer with procedural spiderweb lattice)
- Damascus Steel (Acid-etched folding billet topographical contours)
- Doppler Phase 2 (Sapphire & celestial nebula galaxy smoke)
- Lore (24k polished gold blade with Celtic knotwork filigree)
- Tiger Tooth (Golden amber base with laser-etched tiger claw stripes)
- Slaughter (High-gloss reflective ruby zebra chrome)
"""

import sys
import math
from pathlib import Path

try:
    import bmesh
    import bpy
    import mathutils
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print(
        "Error: Run this script inside Blender: blender -b -P tools/blender/generate_all_knives.py"
    )
    sys.exit(1)


def clear_objects():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def full_clear():
    clear_objects()
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)
    for img in list(bpy.data.images):
        bpy.data.images.remove(img, do_unlink=True)


def create_texture_image(name, width, height, generator_fn):
    """Generates an embedded Blender image with custom pixel logic."""
    img = bpy.data.images.new(name, width=width, height=height, alpha=True)
    pixels = [0.0] * (width * height * 4)
    for y in range(height):
        ny = y / float(height - 1)
        for x in range(width):
            nx = x / float(width - 1)
            r, g, b, a = generator_fn(nx, ny)
            idx = (y * width + x) * 4
            pixels[idx] = max(0.0, min(1.0, r))
            pixels[idx + 1] = max(0.0, min(1.0, g))
            pixels[idx + 2] = max(0.0, min(1.0, b))
            pixels[idx + 3] = max(0.0, min(1.0, a))
    img.pixels = pixels
    img.pack()
    return img


# --- Procedural Texture Generators ---


def tex_fade(nx, ny):
    """Amber Gold -> Vivid Magenta -> Deep Cyan Blue -> Violet gradient with brushed sheen."""
    t = nx * 0.75 + ny * 0.25
    brush = (((int(nx * 512) * 17 + int(ny * 512) * 31) % 19) / 19.0 - 0.5) * 0.04
    if t < 0.33:
        p = t / 0.33
        r = 1.0 * (1.0 - p) + 0.95 * p
        g = 0.75 * (1.0 - p) + 0.15 * p
        b = 0.08 * (1.0 - p) + 0.58 * p
    elif t < 0.68:
        p = (t - 0.33) / 0.35
        r = 0.95 * (1.0 - p) + 0.12 * p
        g = 0.15 * (1.0 - p) + 0.65 * p
        b = 0.58 * (1.0 - p) + 0.98 * p
    else:
        p = (t - 0.68) / 0.32
        r = 0.12 * (1.0 - p) + 0.45 * p
        g = 0.65 * (1.0 - p) + 0.10 * p
        b = 0.98 * (1.0 - p) + 0.88 * p
    return (r + brush, g + brush, b + brush, 1.0)


def tex_marble_fade(nx, ny):
    """Tricolor Red / Gold / Blue Marbled Fade."""
    angle = nx * 2.5 + math.sin(ny * 8.0) * 0.35
    t = (angle % 3.0) / 3.0
    if t < 0.35:
        p = t / 0.35
        r, g, b = (
            0.95 * (1.0 - p) + 1.0 * p,
            0.15 * (1.0 - p) + 0.80 * p,
            0.18 * (1.0 - p) + 0.10 * p,
        )
    elif t < 0.70:
        p = (t - 0.35) / 0.35
        r, g, b = (
            1.0 * (1.0 - p) + 0.12 * p,
            0.80 * (1.0 - p) + 0.55 * p,
            0.10 * (1.0 - p) + 0.98 * p,
        )
    else:
        p = (t - 0.70) / 0.30
        r, g, b = (
            0.12 * (1.0 - p) + 0.95 * p,
            0.55 * (1.0 - p) + 0.15 * p,
            0.98 * (1.0 - p) + 0.18 * p,
        )
    return (r, g, b, 1.0)


def tex_case_hardened(nx, ny):
    """Tempered heat-treated steel with vibrant blue gem islands and gold/purple oxides."""
    s1 = math.sin(nx * 14.0 + math.cos(ny * 11.0) * 2.0)
    s2 = math.cos(ny * 16.0 + math.sin(nx * 12.0) * 1.8)
    val = (s1 + s2) * 0.5
    if val > 0.35:
        p = (val - 0.35) / 0.65
        r = 0.12 * (1.0 - p) + 0.22 * p
        g = 0.55 * (1.0 - p) + 0.82 * p
        b = 0.95 * (1.0 - p) + 1.00 * p
    elif val > 0.05:
        p = (val - 0.05) / 0.30
        r = 0.68 * (1.0 - p) + 0.12 * p
        g = 0.22 * (1.0 - p) + 0.55 * p
        b = 0.72 * (1.0 - p) + 0.95 * p
    elif val > -0.35:
        p = (val + 0.35) / 0.40
        r = 0.88 * (1.0 - p) + 0.68 * p
        g = 0.68 * (1.0 - p) + 0.22 * p
        b = 0.15 * (1.0 - p) + 0.72 * p
    else:
        r, g, b = (0.35, 0.33, 0.30)
    return (r, g, b, 1.0)


def tex_crimson_web(nx, ny):
    """Glossy ruby red with black spiderweb lattice lines."""
    cx, cy = 0.45, 0.55
    dx, dy = nx - cx, ny - cy
    dist = math.sqrt(dx * dx + dy * dy)
    angle = math.atan2(dy, dx)
    is_spoke = abs((angle * 4.0 / math.pi) % 1.0 - 0.5) < 0.06
    ring = (dist * 18.0) % 1.0
    is_ring = abs(ring - 0.5) < 0.07 and dist > 0.04
    if is_spoke or is_ring:
        return (0.08, 0.08, 0.09, 1.0)
    grain = (((int(nx * 256) * 13 + int(ny * 256) * 37) % 23) / 23.0 - 0.5) * 0.03
    return (0.82 + grain, 0.06 + grain, 0.08 + grain, 1.0)


def tex_damascus(nx, ny):
    """Flowing topographical acid-etched damascus billet waves."""
    w1 = math.sin(nx * 32.0 + math.sin(ny * 16.0) * 3.5)
    w2 = math.cos(ny * 48.0 + math.cos(nx * 24.0) * 2.8)
    wave = (w1 + w2) * 0.5
    bright = 0.5 + 0.5 * math.sin(wave * 7.0)
    v = 0.28 + 0.58 * bright
    return (v * 0.98, v, v * 1.02, 1.0)


def tex_doppler_phase2(nx, ny):
    """Sapphire base with cosmic magenta/ruby galaxy smoke."""
    nebula = math.sin(nx * 9.0 + math.sin(ny * 12.0) * 1.5) * math.cos(
        ny * 10.0 + nx * 5.0
    )
    if nebula > 0.15:
        p = (nebula - 0.15) / 0.85
        r = 0.94 * p + 0.15 * (1.0 - p)
        g = 0.18 * p + 0.12 * (1.0 - p)
        b = 0.65 * p + 0.35 * (1.0 - p)
    else:
        p = max(0.0, (nebula + 1.0) / 1.15)
        r = 0.05 * p + 0.02 * (1.0 - p)
        g = 0.08 * p + 0.04 * (1.0 - p)
        b = 0.35 * p + 0.12 * (1.0 - p)
    return (r, g, b, 1.0)


def tex_lore_gold(nx, ny):
    """24k Mirror Gold with green dragon filigree accents."""
    knot = math.sin(nx * 28.0) * math.cos(ny * 24.0)
    if abs(knot) > 0.45:
        return (0.12, 0.52, 0.22, 1.0)
    return (0.98, 0.82, 0.20, 1.0)


def tex_tiger_tooth(nx, ny):
    """Amber gold base with laser-etched tiger stripes."""
    stripe = math.sin(nx * 32.0 + ny * 18.0 + math.sin(ny * 36.0) * 0.8)
    if stripe > 0.55:
        return (0.28, 0.12, 0.04, 1.0)
    return (0.98, 0.72, 0.10, 1.0)


def tex_slaughter(nx, ny):
    """Vibrant ruby lacquer with zebra chrome reflections."""
    zebra = math.sin(nx * 24.0 + math.sin(ny * 16.0) * 2.2)
    if zebra > 0.25:
        return (0.85, 0.14, 0.16, 1.0)
    return (0.58, 0.08, 0.10, 1.0)


def tex_steel(nx, ny):
    """Brushed high-carbon stainless steel with fine longitudinal grain."""
    grain = (((int(nx * 512) * 23 + int(ny * 512) * 41) % 17) / 17.0 - 0.5) * 0.03
    v = 0.86 + grain
    return (v * 0.98, v, v * 1.02, 1.0)


def create_pbr_material(name, base_color, metallic=0.9, roughness=0.25, tex_img=None):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        if tex_img:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = tex_img
            # The mesh's own UVs, not generated coordinates: glTF has no
            # generated coordinates, so the exporter would drop the mapping and
            # the file would disagree with what Blender shows.
            mat.node_tree.links.new(
                tex_node.outputs["Color"], bsdf.inputs["Base Color"]
            )
    return mat


# --- Geometry Helpers ---


def add_box(col, name, min_pt, max_pt, material=None, bevel=False, bevel_width=0.003):
    dx = max_pt[0] - min_pt[0]
    dy = max_pt[1] - min_pt[1]
    dz = max_pt[2] - min_pt[2]
    cx = (min_pt[0] + max_pt[0]) / 2.0
    cy = (min_pt[1] + max_pt[1]) / 2.0
    cz = (min_pt[2] + max_pt[2]) / 2.0

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(cx, cy, cz))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (dx, dy, dz)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if bevel:
        mod = obj.modifiers.new(name="Bevel", type="BEVEL")
        mod.width = bevel_width
        mod.segments = 2
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(35)
        bpy.ops.object.modifier_apply(modifier="Bevel")

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def add_cylinder(
    col,
    name,
    center,
    radius,
    height,
    material=None,
    segments=16,
    rot_axis="Z",
    rot_angle=0.0,
):
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=height, vertices=segments, location=center
    )
    obj = bpy.context.active_object
    obj.name = name

    if rot_angle != 0.0:
        if rot_axis == "X":
            obj.rotation_euler[0] = rot_angle
        elif rot_axis == "Y":
            obj.rotation_euler[1] = rot_angle
        elif rot_axis == "Z":
            obj.rotation_euler[2] = rot_angle
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def add_torus(
    col,
    name,
    center,
    major_r,
    minor_r,
    material=None,
    major_seg=24,
    minor_seg=12,
    rot_axis="Y",
    rot_angle=0.0,
):
    bpy.ops.mesh.primitive_torus_add(
        location=center,
        major_radius=major_r,
        minor_radius=minor_r,
        major_segments=major_seg,
        minor_segments=minor_seg,
    )
    obj = bpy.context.active_object
    obj.name = name

    if rot_angle != 0.0:
        if rot_axis == "X":
            obj.rotation_euler[0] = rot_angle
        elif rot_axis == "Y":
            obj.rotation_euler[1] = rot_angle
        elif rot_axis == "Z":
            obj.rotation_euler[2] = rot_angle
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


# --- Profile slabs -----------------------------------------------------------
#
# A knife is mostly two flat things: a blade and a pair of grip scales. Both are
# built here the same way, and neither is a box. An **outline** in the side
# plane (y up the spine, z along the blade, -z forward) is filled, sampled into
# a grid of interior points, and every point is given a **thickness** from how
# far it is from the outline:
#
# - a blade thins towards the segments flagged as cutting edge, which is a real
#   grind line and a real edge rather than a bevelled box with a strip glued
#   under it; a fuller is a band of reduced thickness, not a boolean;
# - a grip scale is thickest in the middle and rounds over at the rim, which is
#   what makes it read as a handle and not as a plank.
#
# UVs are a side projection, so a finish texture runs along the blade the way a
# pattern on a real knife does, instead of being scattered across smart-UV
# islands.


def _seg_dist(py, pz, ay, az, by, bz):
    dy, dz = by - ay, bz - az
    l2 = dy * dy + dz * dz
    t = (
        0.0
        if l2 < 1e-14
        else max(0.0, min(1.0, ((py - ay) * dy + (pz - az) * dz) / l2))
    )
    qy, qz = ay + dy * t - py, az + dz * t - pz
    return math.sqrt(qy * qy + qz * qz)


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _inside(py, pz, loop):
    """Even-odd point-in-polygon."""
    inside = False
    n = len(loop)
    for i in range(n):
        ay, az = loop[i]
        by, bz = loop[(i + 1) % n]
        if (az > pz) != (bz > pz):
            cross = ay + (pz - az) * (by - ay) / (bz - az)
            if py < cross:
                inside = not inside
    return inside


def _fill(loops, spacing):
    """A constrained Delaunay fill of `loops` (outline first, then holes).

    The outline is resampled to `spacing` and a regular grid of interior points
    is added, so every triangle is roughly the same size. A fill of the bare
    outline gives long slivers across a handle, and a thickness sampled at
    their corners shades as diagonal streaks.
    """
    coords, edges, faces = [], [], []
    for loop in loops:
        idx = []
        n = len(loop)
        for i in range(n):
            ay, az = loop[i]
            by, bz = loop[(i + 1) % n]
            steps = max(1, int(math.hypot(by - ay, bz - az) / spacing))
            for k in range(steps):
                f = k / steps
                idx.append(len(coords))
                coords.append((ay + (by - ay) * f, az + (bz - az) * f))
        for i in range(len(idx)):
            edges.append((idx[i], idx[(i + 1) % len(idx)]))
        faces.append(idx)
    ys = [p[0] for p in loops[0]]
    zs = [p[1] for p in loops[0]]
    rim = [(p, q) for loop in loops for p, q in zip(loop, loop[1:] + loop[:1])]
    y = min(ys) + spacing * 0.5
    while y < max(ys):
        z = min(zs) + spacing * 0.5
        while z < max(zs):
            if _inside(y, z, loops[0]) and not any(_inside(y, z, h) for h in loops[1:]):
                near = min(_seg_dist(y, z, a[0], a[1], b[0], b[1]) for a, b in rim)
                if near > spacing * 0.45:
                    coords.append((y, z))
            z += spacing
        y += spacing
    from mathutils.geometry import delaunay_2d_cdt

    verts, _e, tris, *_ = delaunay_2d_cdt([Vector(c) for c in coords], edges, faces, 2, 1e-7)
    bm = bmesh.new()
    vs = [bm.verts.new((0.0, v[0], v[1])) for v in verts]
    for t in tris:
        try:
            bm.faces.new([vs[i] for i in t])
        except ValueError:
            pass
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    return bm


def slab(
    col,
    name,
    outline,
    th_fn,
    material,
    edge_material=None,
    edge_th=0.0011,
    holes=(),
    cuts=3,
    x=0.0,
    uv_box=(-0.2, -0.1, 0.4),
):
    """Fill `outline` [(y, z, is_edge), ...] and give it thickness `th_fn`.

    `is_edge` marks the segment from that point to the next as cutting edge.
    `th_fn(y, z, d_edge, d_rim)` returns the full thickness at a point, where
    `d_edge` is its distance to the nearest edge segment and `d_rim` to the
    nearest segment of any kind. `holes` are extra closed loops of (y, z).
    """
    # Consecutive duplicates are a zero-length edge, and one of those is enough
    # for the fill to silently drop most of the shape.
    clean = []
    for p in outline:
        if clean and abs(clean[-1][0] - p[0]) < 1e-7 and abs(clean[-1][1] - p[1]) < 1e-7:
            clean[-1] = (p[0], p[1], clean[-1][2] or p[2])
            continue
        clean.append(p)
    if abs(clean[0][0] - clean[-1][0]) < 1e-7 and abs(clean[0][1] - clean[-1][1]) < 1e-7:
        clean.pop()
    outline = clean
    loops = [[(p[0], p[1]) for p in outline]] + [list(h) for h in holes]
    edge_segs, rim_segs = [], []
    for li, loop in enumerate(loops):
        n = len(loop)
        for i in range(n):
            a, b = loop[i], loop[(i + 1) % n]
            rim_segs.append((a[0], a[1], b[0], b[1]))
            if li == 0 and outline[i][2]:
                edge_segs.append((a[0], a[1], b[0], b[1]))

    bm = _fill(loops, 0.008 / (cuts + 1))
    bm.verts.index_update()

    thick = {}
    for v in bm.verts:
        py, pz = v.co.y, v.co.z
        de = min((_seg_dist(py, pz, *s) for s in edge_segs), default=1.0)
        dr = min(_seg_dist(py, pz, *s) for s in rim_segs)
        thick[v.index] = max(0.00035, th_fn(py, pz, de, dr))

    out = bmesh.new()
    uv = out.loops.layers.uv.new("UVMap")
    top = {
        v.index: out.verts.new((x + thick[v.index] * 0.5, v.co.y, v.co.z))
        for v in bm.verts
    }
    bot = {
        v.index: out.verts.new((x - thick[v.index] * 0.5, v.co.y, v.co.z))
        for v in bm.verts
    }
    u0, v0, span = uv_box

    def put_uv(face):
        for loop in face.loops:
            loop[uv].uv = ((loop.vert.co.z - u0) / span, (loop.vert.co.y - v0) / span)

    for f in bm.faces:
        ids = [v.index for v in f.verts]
        mean_th = sum(thick[i] for i in ids) / len(ids)
        mi = 1 if edge_material is not None and mean_th < edge_th else 0
        ft = out.faces.new([top[i] for i in ids])
        fb = out.faces.new([bot[i] for i in reversed(ids)])
        for face in (ft, fb):
            face.material_index = mi
            put_uv(face)
    for e in bm.edges:
        if len(e.link_faces) != 1:
            continue
        a, b = e.verts
        side = out.faces.new([top[a.index], top[b.index], bot[b.index], bot[a.index]])
        mean_th = (thick[a.index] + thick[b.index]) * 0.5
        side.material_index = (
            1 if edge_material is not None and mean_th < edge_th else 0
        )
        put_uv(side)
    bm.free()
    bmesh.ops.recalc_face_normals(out, faces=list(out.faces))

    me = bpy.data.meshes.new(name)
    out.to_mesh(me)
    out.free()
    me.materials.append(material)
    if edge_material is not None:
        me.materials.append(edge_material)
    obj = bpy.data.objects.new(name, me)
    col.objects.link(obj)
    return obj


def blade_th(spine=0.0055, edge=0.0005, grind=0.016, fuller=None, spine_round=0.0012):
    """A grind towards the edge, a softened spine, and an optional fuller.

    `fuller` is (z_from, z_to, y_centre, half_width, depth).
    """

    def f(y, z, de, dr):
        th = edge + (spine - edge) * _smooth(de / grind)
        # The spine's corners are broken, as on any finished blade.
        th *= 0.82 + 0.18 * _smooth(dr / spine_round)
        if fuller is not None:
            z0, z1, yc, hw, depth = fuller
            along = _smooth((z - z0) / 0.008) * _smooth((z1 - z) / 0.008)
            across = max(0.0, 1.0 - ((y - yc) / hw) ** 2)
            th -= 2.0 * depth * along * across
        return th

    return f


def pillow_th(full, rim=0.004, floor=0.4, ripple=None):
    """Thickest in the middle, rounding over at the rim. `ripple(y, z)` adds texture."""

    def f(y, z, de, dr):
        th = full * (floor + (1.0 - floor) * math.sqrt(_smooth(dr / rim)))
        if ripple is not None:
            th += ripple(y, z) * _smooth(dr / rim)
        return th

    return f


def flat_th(full):
    return lambda y, z, de, dr: full


def circle(cy, cz, r, n=20):
    return [
        (cy + r * math.cos(2 * math.pi * k / n), cz + r * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]


def rounded_rect(y0, y1, z0, z1, r, n=5):
    pts = []
    corners = [
        (y1 - r, z1 - r, 0.0),
        (y0 + r, z1 - r, 0.5),
        (y0 + r, z0 + r, 1.0),
        (y1 - r, z0 + r, 1.5),
    ]
    for cy, cz, start in corners:
        for k in range(n + 1):
            a = math.pi * (start + 0.5 * k / n)
            pts.append((cy + r * math.cos(a), cz + r * math.sin(a)))
    return pts


def teeth(y_base, depth, z_from, z_to, count, flag=False):
    """A row of points: `count` teeth between z_from and z_to, pointing +y by `depth`."""
    pts = []
    for i in range(count):
        za = z_from + (z_to - z_from) * i / count
        zb = z_from + (z_to - z_from) * (i + 0.5) / count
        pts.append((y_base, za, flag))
        pts.append((y_base + depth, zb, flag))
    return pts


def serrations(y_base, depth, z_from, z_to, count):
    """Scallops cut up into the edge: every segment is cutting edge."""
    pts = []
    for i in range(count):
        for k in range(4):
            f = (i + k / 4.0) / count
            z = z_from + (z_to - z_from) * f
            pts.append((y_base + depth * math.sin(math.pi * k / 4.0), z, True))
    return pts


def grip_outline(z0, z1, top, bottom, grooves=3, groove_depth=0.0035, samples=28):
    """A handle side profile: a gently swelled spine and finger grooves below."""
    pts = []
    for i in range(samples + 1):
        f = i / samples
        z = z0 + (z1 - z0) * f
        pts.append((top + 0.0015 * math.sin(math.pi * f), z, False))
    for i in range(samples + 1):
        f = 1.0 - i / samples
        z = z0 + (z1 - z0) * f
        groove = 0.0
        if 0.08 < f < 0.85 and grooves:
            g = (f - 0.08) / 0.77
            groove = groove_depth * (0.5 - 0.5 * math.cos(2 * math.pi * g * grooves))
        pts.append((bottom + groove, z, False))
    return pts


# Authored in metres with the blade down -Z and the spine up +Y. Exported in
# **cube units** (1 cube = 36 cm), like every other prop and the hands: a knife
# left in metres is a third of a cube long, a speck beside the fist holding it.
#
# The glTF exporter maps Blender (x, y, z) to (x, z, -y), so a quarter turn about
# +X is what lands the blade on glTF -Z and the spine on glTF +Y — the
# orientation both clients and `weapon-prop-orientation.test.ts` expect.
#
# The origin is the **hilt**: where the blade meets the guard. Both clients place
# a knife prop by that point rather than by its bounding box, so every knife's
# handle sits in the same fist whatever its blade does.
CUBE = 1.0 / 0.36
STAND_UP = Matrix.Scale(CUBE, 4) @ Matrix.Rotation(math.radians(90), 4, "X")


def finalize_knife(name, parts, empties=()):
    """Join each part's collection into one object, stand it up and hand it back.

    `parts` is [(object_name, collection, pivot_or_None)]; the first is the
    knife body and the rest are parented to it with their origin at `pivot` —
    the butterfly's handles, which the inspect swings about their pins.
    `empties` are (name, point) markers carried into the GLB as bare nodes:
    `spin_origin` is what a twirl rotates about.
    """
    rot = STAND_UP
    joined = []
    for obj_name, col, pivot in parts:
        objs = list(col.objects)
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        if len(objs) > 1:
            bpy.ops.object.join()
        obj = bpy.context.view_layer.objects.active
        obj.name = obj_name
        # Bake the transform into the mesh, then stand it up about the world
        # origin (not the object's own), so every part turns together.
        obj.data.transform(obj.matrix_world)
        obj.matrix_world = Matrix.Identity(4)
        obj.data.transform(rot)
        if pivot is not None:
            p = rot @ Vector(pivot)
            obj.data.transform(Matrix.Translation(-p))
            obj.location = p
        joined.append(obj)
    body = joined[0]
    for child in joined[1:]:
        child.parent = body
    for marker, point in empties:
        e = bpy.data.objects.new(marker, None)
        e.location = rot @ Vector(point)
        e.parent = body
        bpy.context.scene.collection.objects.link(e)
    for obj in joined:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.shade_smooth()
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(34))
        except (AttributeError, RuntimeError):
            pass
    faces = sum(len(o.data.polygons) for o in joined)
    print(f"Built '{name}': {len(joined)} node(s), {faces} faces")
    return body


def new_col(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


# --- 1. Default Tactical Combat Knife ---
def build_default_knife(mat_blade, mat_edge, mat_grip, mat_guard, mat_pommel):
    """A tanto: straight spine with jimping, a swedge, a true tanto point and a
    partly serrated edge, over contoured G10 scales on a steel liner."""
    c = new_col("Default_Knife")
    outline = [(0.0155, 0.004, False)]
    outline += teeth(0.0135, 0.002, -0.004, -0.028, 6)
    outline += [
        (0.0155, -0.030, False),
        (0.0155, -0.118, False),
        (0.0125, -0.141, False),
        (0.0030, -0.175, True),  # the point
        (-0.0165, -0.152, True),  # tanto transition
        (-0.0190, -0.100, True),
        (-0.0190, -0.047, True),
    ]
    outline += serrations(-0.0190, 0.0032, -0.047, -0.009, 5)
    outline += [(-0.0175, -0.006, False), (-0.0170, 0.004, False)]
    slab(
        c,
        "Blade",
        outline,
        blade_th(grind=0.017, fuller=(-0.108, -0.036, 0.0055, 0.0030, 0.0011)),
        mat_blade,
        mat_edge,
    )

    add_box(
        c,
        "Guard",
        (-0.011, -0.025, 0.002),
        (0.011, 0.021, 0.009),
        mat_guard,
        bevel=True,
        bevel_width=0.0025,
    )
    grip = grip_outline(0.009, 0.118, 0.0165, -0.0195, grooves=3)

    def ripple(y, z):
        return 0.0005 * math.sin(z * 900.0) * math.sin(y * 700.0)

    slab(
        c,
        "Scales",
        grip,
        pillow_th(0.0232, rim=0.0045, ripple=ripple),
        mat_grip,
        cuts=4,
    )
    liner = [(y * 1.05 + 0.0002, z, f) for (y, z, f) in grip]
    slab(c, "Liner", liner, flat_th(0.0092), mat_guard, cuts=1)
    for i, z in enumerate((0.030, 0.094)):
        add_cylinder(
            c,
            f"Screw_{i}",
            (0.0, -0.001, z),
            radius=0.0033,
            height=0.0252,
            material=mat_pommel,
            segments=6,
            rot_axis="Y",
            rot_angle=math.radians(90),
        )
    add_cylinder(
        c,
        "Breaker",
        (0.0, -0.001, 0.1180),
        radius=0.0065,
        height=0.0038,
        material=mat_pommel,
        segments=6,
    )
    for side in (-1, 1):
        add_torus(
            c,
            f"Lanyard_{side}",
            (side * 0.0118, -0.004, 0.109),
            major_r=0.0038,
            minor_r=0.0011,
            material=mat_pommel,
            major_seg=16,
            minor_seg=6,
            rot_axis="Y",
            rot_angle=math.radians(90),
        )
    return finalize_knife("Weapon_Knife_Default", [("Weapon_Knife_Default", c, None)])


# --- 2. Karambit ---
def build_karambit(mat_blade, mat_edge, mat_grip, mat_ring):
    """A talon: the blade sweeps down along a curve with the edge on the inside."""
    c = new_col("Karambit")
    n = 22

    def centre(s):
        return (-0.058 * s**1.7, -0.128 * math.sin(s * math.pi * 0.5))

    def width(s):
        return 0.0215 * (1.0 - s) ** 0.75 + 0.0008

    spine, edge = [], []
    for i in range(n + 1):
        s = i / n
        y, z = centre(s)
        y2, z2 = centre(min(1.0, s + 1e-3))
        y1, z1 = centre(max(0.0, s - 1e-3))
        ty, tz = y2 - y1, z2 - z1
        length = math.hypot(ty, tz)
        ny, nz = -tz / length, ty / length
        if ny < 0:
            ny, nz = -ny, -nz
        w = width(s) * 0.5
        spine.append((y + ny * w, z + nz * w, False))
        edge.append((y - ny * w, z - nz * w, True))
    outline = (
        [(0.011, 0.006, False)] + spine[:-1] + [(spine[-1][0], spine[-1][1], True)]
    )
    outline += list(reversed(edge))[1:-1] + [(-0.011, 0.006, False)]
    slab(c, "Blade", outline, blade_th(spine=0.0048, grind=0.012), mat_blade, mat_edge)

    handle = []
    for i in range(25):
        f = i / 24
        handle.append(
            (0.0145 + 0.010 * math.sin(f * math.pi * 0.9), 0.004 + 0.098 * f, False)
        )
    for i in range(25):
        f = 1.0 - i / 24
        groove = (
            0.0030 * (0.5 - 0.5 * math.cos(2 * math.pi * f * 2))
            if 0.1 < f < 0.9
            else 0.0
        )
        handle.append(
            (
                -0.0135 + 0.010 * math.sin(f * math.pi * 0.9) + groove,
                0.004 + 0.098 * f,
                False,
            )
        )
    slab(c, "Scales", handle, pillow_th(0.021, rim=0.004), mat_grip, cuts=4)
    ring = (0.0, 0.0095, 0.112)
    add_torus(
        c,
        "Ring",
        ring,
        major_r=0.0145,
        minor_r=0.0044,
        material=mat_ring,
        major_seg=32,
        minor_seg=12,
        rot_axis="Y",
        rot_angle=math.radians(90),
    )
    for i, z in enumerate((0.030, 0.074)):
        add_cylinder(
            c,
            f"Screw_{i}",
            (0.0, 0.004 + 0.006 * i, z),
            radius=0.0032,
            height=0.022,
            material=mat_ring,
            segments=6,
            rot_axis="Y",
            rot_angle=math.radians(90),
        )
    return finalize_knife(
        "Weapon_Knife_Karambit",
        [("Weapon_Knife_Karambit", c, None)],
        empties=[("spin_origin", ring)],
    )


# --- 3. Butterfly Knife / Balisong ---
def build_butterfly(mat_blade, mat_edge, mat_handle, mat_hardware):
    """A balisong whose two handles are **separate nodes**, pivoting on their pins,
    so the inspect can actually flip it open and shut."""
    body, safe, bite = (
        new_col("Butterfly"),
        new_col("Butterfly_Safe"),
        new_col("Butterfly_Bite"),
    )
    outline = [
        (0.0115, 0.004, False),
        (0.0115, -0.098, False),
        (0.0065, -0.138, False),
        (0.0010, -0.160, True),
        (-0.0115, -0.132, True),
        (-0.0135, -0.060, True),
        (-0.0130, -0.012, False),
        (-0.0110, 0.004, False),
    ]
    slab(
        body, "Blade", outline, blade_th(spine=0.0045, grind=0.013), mat_blade, mat_edge
    )
    add_box(
        body,
        "Tang",
        (-0.0034, -0.0155, -0.010),
        (0.0034, 0.0155, 0.009),
        mat_hardware,
        bevel=True,
        bevel_width=0.0012,
    )
    for side, sign in (("Safe", -1), ("Bite", 1)):
        add_cylinder(
            body,
            f"StopPin_{side}",
            (0.0, sign * 0.0135, -0.006),
            radius=0.0022,
            height=0.013,
            material=mat_hardware,
            segments=10,
            rot_axis="Y",
            rot_angle=math.radians(90),
        )

    pivots = {}
    for side, sign, col in (("Safe", -1, safe), ("Bite", 1, bite)):
        hy = sign * 0.0125
        pivot = (0.0, sign * 0.011, 0.006)
        pivots[side] = pivot
        rim = rounded_rect(hy - 0.0062, hy + 0.0062, 0.001, 0.118, 0.004)
        holes = [circle(hy, z, 0.0026, 14) for z in (0.030, 0.052, 0.074, 0.096)]
        slab(
            col,
            f"Handle_{side}",
            [(y, z, False) for (y, z) in rim],
            pillow_th(0.0148, rim=0.0022, floor=0.6),
            mat_handle,
            holes=holes,
            cuts=2,
        )
        add_cylinder(
            col,
            f"Pivot_{side}",
            pivot,
            radius=0.0036,
            height=0.0165,
            material=mat_hardware,
            segments=14,
            rot_axis="Y",
            rot_angle=math.radians(90),
        )
    add_box(
        bite,
        "Latch",
        (-0.0042, 0.004, 0.110),
        (0.0042, 0.0155, 0.118),
        mat_hardware,
        bevel=True,
        bevel_width=0.001,
    )
    return finalize_knife(
        "Weapon_Knife_Butterfly",
        [
            ("Weapon_Knife_Butterfly", body, None),
            ("handle_safe", safe, pivots["Safe"]),
            ("handle_bite", bite, pivots["Bite"]),
        ],
    )


# --- 4. M9 Tactical Bayonet ---
def build_bayonet(mat_blade, mat_edge, mat_handle, mat_guard, mat_pommel):
    """A long clip-point with a sawback spine, a muzzle-ring guard and a ribbed grip."""
    c = new_col("Bayonet")
    outline = [(0.0150, 0.004, False)]
    outline += teeth(0.0150, 0.0045, -0.024, -0.100, 7)
    outline += [
        (0.0150, -0.100, False),
        (0.0150, -0.150, False),
        (0.0070, -0.182, False),
        (-0.0010, -0.203, True),
        (-0.0180, -0.170, True),
        (-0.0225, -0.110, True),
        (-0.0225, -0.020, True),
        (-0.0190, -0.004, False),
        (-0.0180, 0.004, False),
    ]
    slab(
        c,
        "Blade",
        outline,
        blade_th(
            spine=0.0062, grind=0.019, fuller=(-0.140, -0.028, 0.0035, 0.0034, 0.0013)
        ),
        mat_blade,
        mat_edge,
    )
    add_box(
        c,
        "Guard",
        (-0.013, -0.024, -0.004),
        (0.013, 0.027, 0.006),
        mat_guard,
        bevel=True,
        bevel_width=0.0022,
    )
    add_torus(
        c,
        "Muzzle_Ring",
        (0.0, 0.034, 0.001),
        major_r=0.0115,
        minor_r=0.0032,
        material=mat_guard,
        major_seg=24,
        minor_seg=10,
    )
    add_cylinder(
        c,
        "Grip",
        (0.0, 0.0, 0.061),
        radius=0.0135,
        height=0.110,
        material=mat_handle,
        segments=28,
    )
    for i in range(7):
        add_torus(
            c,
            f"Rib_{i}",
            (0.0, 0.0, 0.014 + i * 0.0148),
            major_r=0.0138,
            minor_r=0.0019,
            material=mat_handle,
            major_seg=28,
            minor_seg=8,
        )
    add_box(
        c,
        "Pommel",
        (-0.0135, -0.0155, 0.106),
        (0.0135, 0.0155, 0.119),
        mat_pommel,
        bevel=True,
        bevel_width=0.0025,
    )
    add_box(
        c,
        "Lug",
        (-0.0055, 0.012, 0.107),
        (0.0055, 0.022, 0.118),
        mat_pommel,
        bevel=True,
        bevel_width=0.001,
    )
    add_cylinder(
        c,
        "Release",
        (0.0, -0.0165, 0.112),
        radius=0.0040,
        height=0.008,
        material=mat_blade,
        segments=12,
        rot_axis="Y",
        rot_angle=math.radians(90),
    )
    return finalize_knife("Weapon_Knife_Bayonet", [("Weapon_Knife_Bayonet", c, None)])


# --- 5. Skeleton Knife ---
def build_skeleton_knife(mat_blade, mat_edge, mat_wrap, mat_hardware):
    """One piece of steel: blade, finger hole and an open tang, wrapped in cord."""
    c = new_col("Skeleton")
    hole = (-0.002, 0.012)
    outline = [
        (0.0135, 0.118, False),
        (0.0135, 0.026, False),
        (0.0125, -0.010, False),
        (0.0125, -0.105, False),
        (0.0055, -0.140, False),
        (0.0005, -0.162, True),
        (-0.0120, -0.140, True),
        (-0.0170, -0.090, True),
        (-0.0150, -0.040, True),
        (-0.0165, -0.012, False),
        (-0.0140, 0.026, False),
        (-0.0140, 0.118, False),
    ]
    holes = [
        circle(hole[0], hole[1], 0.0085, 24),
        rounded_rect(-0.0068, 0.0062, 0.036, 0.104, 0.0045),
    ]
    slab(
        c,
        "Steel",
        outline,
        blade_th(spine=0.0048, grind=0.013),
        mat_blade,
        mat_edge,
        holes=holes,
    )
    for i in range(8):
        add_torus(
            c,
            f"Cord_{i}",
            (0.0, -0.0003, 0.040 + i * 0.0085),
            major_r=0.0122,
            minor_r=0.0024,
            material=mat_wrap,
            major_seg=18,
            minor_seg=8,
            rot_axis="Z",
            rot_angle=math.radians(14 * (1 if i % 2 else -1)),
        )
    return finalize_knife(
        "Weapon_Knife_Skeleton",
        [("Weapon_Knife_Skeleton", c, None)],
        empties=[("spin_origin", (0.0, hole[0], hole[1]))],
    )


# --- 6. Huntsman Knife ---
def build_huntsman(mat_blade, mat_edge, mat_grip, mat_hardware):
    """Heavy recurve with a double-row sawback, a gut-hook choil and chunky scales."""
    c = new_col("Huntsman")
    outline = [(0.0165, 0.004, False)]
    outline += teeth(0.0165, 0.0048, -0.016, -0.100, 8)
    outline += [
        (0.0165, -0.100, False),
        (0.0140, -0.135, False),
        (0.0050, -0.172, True),
        (-0.0180, -0.150, True),
        (-0.0270, -0.110, True),
        (-0.0240, -0.060, True),
        (-0.0255, -0.030, True),
        (-0.0200, -0.016, False),
        (-0.0160, -0.010, False),
        (-0.0205, -0.004, False),
        (-0.0200, 0.004, False),
    ]
    slab(c, "Blade", outline, blade_th(spine=0.0068, grind=0.021), mat_blade, mat_edge)
    add_box(
        c,
        "Guard",
        (-0.0125, -0.027, 0.002),
        (0.0125, 0.021, 0.009),
        mat_hardware,
        bevel=True,
        bevel_width=0.0025,
    )
    grip = grip_outline(0.009, 0.108, 0.0175, -0.0215, grooves=4, groove_depth=0.0042)
    slab(c, "Scales", grip, pillow_th(0.0255, rim=0.005), mat_grip, cuts=4)
    liner = [(y * 1.05, z, f) for (y, z, f) in grip]
    slab(c, "Liner", liner, flat_th(0.0098), mat_hardware, cuts=1)
    for i, z in enumerate((0.030, 0.062, 0.094)):
        add_cylinder(
            c,
            f"Screw_{i}",
            (0.0, -0.002, z),
            radius=0.0038,
            height=0.0275,
            material=mat_hardware,
            segments=6,
            rot_axis="Y",
            rot_angle=math.radians(90),
        )
    add_box(
        c,
        "Pommel",
        (-0.0118, -0.0200, 0.106),
        (0.0118, 0.0180, 0.119),
        mat_hardware,
        bevel=True,
        bevel_width=0.0028,
    )
    return finalize_knife("Weapon_Knife_Huntsman", [("Weapon_Knife_Huntsman", c, None)])


def render_showcase(out_dir, name):
    """One side-on still per knife, for checking the build without opening Blender."""
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = 900
    scene.render.resolution_y = 420
    cam = bpy.data.objects.new("ShowcaseCam", bpy.data.cameras.new("ShowcaseCam"))
    scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 0.36 * CUBE
    # After `STAND_UP` the blade runs along Blender +Y and the spine along +Z.
    cam.location = (1.0, 0.0, 0.0)
    cam.rotation_euler = (math.radians(90), 0.0, math.radians(90))
    scene.camera = cam
    scene.render.filepath = str(Path(out_dir) / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)


def main():
    repo_root = Path(__file__).resolve().parent.parent.parent
    web_public = repo_root / "apps/web/public"
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    render_dir = None
    if "--out" in argv:
        web_public = Path(argv[argv.index("--out") + 1])
    if "--render" in argv:
        render_dir = Path(argv[argv.index("--render") + 1])
    web_public.mkdir(parents=True, exist_ok=True)

    full_clear()
    print("=== [1/3] Generating Procedural Rarity Texture Bitmaps in Blender ===")
    t_fade = create_texture_image("Tex_Fade", 256, 256, tex_fade)
    t_marble = create_texture_image("Tex_Marble_Fade", 256, 256, tex_marble_fade)
    t_case = create_texture_image("Tex_Case_Hardened", 256, 256, tex_case_hardened)
    t_crimson = create_texture_image("Tex_Crimson_Web", 256, 256, tex_crimson_web)
    t_damascus = create_texture_image("Tex_Damascus", 256, 256, tex_damascus)
    t_doppler = create_texture_image("Tex_Doppler_P2", 256, 256, tex_doppler_phase2)
    t_lore = create_texture_image("Tex_Lore", 256, 256, tex_lore_gold)
    t_tiger = create_texture_image("Tex_Tiger_Tooth", 256, 256, tex_tiger_tooth)
    t_slaughter = create_texture_image("Tex_Slaughter", 256, 256, tex_slaughter)
    t_steel = create_texture_image("Tex_Steel", 256, 256, tex_steel)

    print("=== [2/3] Setting Up PBR Shader Materials ===")
    mat_steel = create_pbr_material(
        "Mat_Steel", (0.85, 0.86, 0.88), metallic=0.96, roughness=0.22, tex_img=t_steel
    )
    mat_edge = create_pbr_material(
        "Mat_Edge", (0.95, 0.96, 0.98), metallic=0.98, roughness=0.12
    )
    mat_grip = create_pbr_material(
        "Mat_Grip", (0.08, 0.09, 0.10), metallic=0.05, roughness=0.75
    )
    mat_guard = create_pbr_material(
        "Mat_Guard", (0.12, 0.13, 0.15), metallic=0.75, roughness=0.35
    )
    mat_pommel = create_pbr_material(
        "Mat_Pommel", (0.45, 0.46, 0.48), metallic=0.90, roughness=0.30
    )

    # Rarity Skin Materials
    mat_fade = create_pbr_material(
        "Mat_Skin_Fade", (0.9, 0.5, 0.5), metallic=0.98, roughness=0.15, tex_img=t_fade
    )
    mat_marble = create_pbr_material(
        "Mat_Skin_Marble",
        (0.8, 0.5, 0.5),
        metallic=0.98,
        roughness=0.15,
        tex_img=t_marble,
    )
    mat_case = create_pbr_material(
        "Mat_Skin_CaseHardened",
        (0.4, 0.6, 0.9),
        metallic=0.95,
        roughness=0.18,
        tex_img=t_case,
    )
    mat_crimson = create_pbr_material(
        "Mat_Skin_Crimson",
        (0.8, 0.1, 0.1),
        metallic=0.85,
        roughness=0.25,
        tex_img=t_crimson,
    )
    mat_damascus = create_pbr_material(
        "Mat_Skin_Damascus",
        (0.7, 0.7, 0.7),
        metallic=0.92,
        roughness=0.28,
        tex_img=t_damascus,
    )
    mat_doppler = create_pbr_material(
        "Mat_Skin_Doppler",
        (0.2, 0.1, 0.4),
        metallic=0.98,
        roughness=0.12,
        tex_img=t_doppler,
    )
    mat_lore = create_pbr_material(
        "Mat_Skin_Lore",
        (0.98, 0.82, 0.20),
        metallic=0.98,
        roughness=0.14,
        tex_img=t_lore,
    )
    mat_tiger = create_pbr_material(
        "Mat_Skin_Tiger",
        (0.95, 0.70, 0.10),
        metallic=0.96,
        roughness=0.16,
        tex_img=t_tiger,
    )
    mat_slaughter = create_pbr_material(
        "Mat_Skin_Slaughter",
        (0.75, 0.12, 0.15),
        metallic=0.95,
        roughness=0.15,
        tex_img=t_slaughter,
    )
    mat_paracord = create_pbr_material(
        "Mat_Paracord", (0.15, 0.16, 0.18), metallic=0.10, roughness=0.85
    )

    knives = [
        (
            "Default Tactical Knife",
            "hassault-weapon-knife.glb",
            lambda: build_default_knife(
                mat_steel, mat_edge, mat_grip, mat_guard, mat_pommel
            ),
        ),
        (
            "Karambit Fade",
            "hassault-weapon-knife-karambit.glb",
            lambda: build_karambit(mat_fade, mat_edge, mat_grip, mat_pommel),
        ),
        (
            "Butterfly Marble Fade",
            "hassault-weapon-knife-butterfly.glb",
            lambda: build_butterfly(mat_marble, mat_edge, mat_grip, mat_guard),
        ),
        (
            "M9 Bayonet Lore",
            "hassault-weapon-knife-bayonet.glb",
            lambda: build_bayonet(mat_lore, mat_edge, mat_grip, mat_guard, mat_pommel),
        ),
        (
            "Skeleton Crimson Web",
            "hassault-weapon-knife-skeleton.glb",
            lambda: build_skeleton_knife(
                mat_crimson, mat_edge, mat_paracord, mat_guard
            ),
        ),
        (
            "Huntsman Case Hardened",
            "hassault-weapon-knife-huntsman.glb",
            lambda: build_huntsman(mat_case, mat_edge, mat_grip, mat_pommel),
        ),
    ]

    print("=== [3/3] Constructing, Modeling, and Exporting All 6 Knife Props ===")
    for title, glb_name, build_fn in knives:
        clear_objects()
        print(f"\n--- Generating {title} -> {glb_name} ---")
        build_fn()
        if render_dir is not None:
            render_showcase(render_dir, Path(glb_name).stem)

        out_path = web_public / glb_name
        print(f"Exporting GLB to: {out_path}")
        bpy.ops.export_scene.gltf(
            filepath=str(out_path),
            export_format="GLB",
            use_selection=False,
            export_apply=True,
            export_yup=True,
            export_materials="EXPORT",
            export_lights=False,
            export_cameras=False,
        )

    print("\nAll 6 Knife Props and Rarity Skins generated and exported successfully!")


if __name__ == "__main__":
    main()
