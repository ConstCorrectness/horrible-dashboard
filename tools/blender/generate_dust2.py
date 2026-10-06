#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Desert Citadel II (hd_dust2).
Authentic competitive tournament arena on a 70m x 70m footprint:
- Long A: Sunken Pit with sniper ramp, Long corridor, corner, double sandstone archway, ramp up to A.
- Bomb Site A: Elevated sandstone plateau (+1.2m), double wooden boxes, goose wall, ramp to CT.
- Catwalk / Short A: Elevated stone walkway (+2.4m) overlooking Mid, Xbox jump crate, stairs to Lower Dark.
- Middle & Mid Doors: Central street with heavy wooden double doors slightly ajar with sniper slit.
- Dark Tunnels: Subterranean vaulted stone tunnel network connecting T side, Lower Dark, and Upper B.
- Bomb Site B: Enclosed Moroccan fortress courtyard, Upper B tunnel lip, B Window, B Double Doors, Back Platform.
- T & CT Spawns: Terracotta souk courtyard with fabric sun canopies, Persian rugs, and desert palm trees.

Outputs:
  - backend/modules/hassault/maps/hd_dust2.glb
  - apps/web/public/hd_dust2.glb
"""

import os
import sys
import math

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print(
        "Error: generate_dust2.py must be run from within Blender (e.g. `blender --background --python ...`)"
    )
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cutlayout  # noqa: E402
import dust2_layout  # noqa: E402
import maplib  # noqa: E402  (a sibling, found via the path above)
import props  # noqa: E402


def clear_scene():
    """Remove all default objects, meshes, materials, and collections."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def get_or_create_collection(name):
    col = bpy.data.collections.get(name)
    if not col:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def create_pbr_material(
    name,
    base_color,
    metallic=0.0,
    roughness=0.7,
    emission_color=None,
    emission_strength=0.0,
    alpha=1.0,
    bump_strength=0.0,
    bump_scale=24.0,
):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    bsdf.inputs["Base Color"].default_value = (*base_color[:3], alpha)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness

    if emission_color and emission_strength > 0.0:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*emission_color[:3], 1.0)
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = (*emission_color[:3], 1.0)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission_strength

    if alpha < 1.0:
        if "Transmission Weight" in bsdf.inputs:
            bsdf.inputs["Transmission Weight"].default_value = 1.0 - alpha
        elif "Transmission" in bsdf.inputs:
            bsdf.inputs["Transmission"].default_value = 1.0 - alpha
        mat.blend_method = "BLEND"

    if bump_strength > 0.0:
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-600, -200)
        noise = nodes.new(type="ShaderNodeTexNoise")
        noise.location = (-400, -200)
        noise.inputs["Scale"].default_value = bump_scale
        noise.inputs["Detail"].default_value = 4.0
        bump = nodes.new(type="ShaderNodeBump")
        bump.location = (-200, -200)
        bump.inputs["Strength"].default_value = bump_strength
        mat.node_tree.links.new(tex_coord.outputs["Generated"], noise.inputs["Vector"])
        mat.node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        mat.node_tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    mats = {}
    # Sun-bleached limestone & ochre sandstone with realistic masonry bump
    mats["sandstone_light"] = create_pbr_material(
        "mat_dust2_sandstone_light",
        (0.84, 0.74, 0.58),
        metallic=0.02,
        roughness=0.85,
        bump_strength=0.15,
        bump_scale=18.0,
    )
    mats["sandstone_ochre"] = create_pbr_material(
        "mat_dust2_sandstone_ochre",
        (0.76, 0.58, 0.40),
        metallic=0.02,
        roughness=0.90,
        bump_strength=0.20,
        bump_scale=15.0,
    )
    mats["sandstone_dark"] = create_pbr_material(
        "mat_dust2_sandstone_dark",
        (0.58, 0.46, 0.35),
        metallic=0.02,
        roughness=0.92,
        bump_strength=0.22,
        bump_scale=20.0,
    )
    mats["limestone_paving"] = create_pbr_material(
        "mat_dust2_limestone_paving",
        (0.78, 0.72, 0.62),
        metallic=0.01,
        roughness=0.80,
        bump_strength=0.18,
        bump_scale=25.0,
    )
    mats["desert_sand"] = create_pbr_material(
        "mat_dust2_desert_sand",
        (0.88, 0.76, 0.54),
        metallic=0.0,
        roughness=0.95,
        bump_strength=0.08,
        bump_scale=32.0,
    )

    # Moroccan architectural accents
    mats["moorish_tile_blue"] = create_pbr_material(
        "mat_dust2_moorish_tile_blue",
        (0.12, 0.35, 0.58),
        metallic=0.10,
        roughness=0.25,
        bump_strength=0.05,
        bump_scale=40.0,
    )
    mats["moorish_tile_gold"] = create_pbr_material(
        "mat_dust2_moorish_tile_gold",
        (0.82, 0.62, 0.18),
        metallic=0.15,
        roughness=0.30,
        bump_strength=0.05,
        bump_scale=40.0,
    )
    mats["wood_cedar_weathered"] = create_pbr_material(
        "mat_dust2_wood_cedar_weathered",
        (0.38, 0.28, 0.20),
        metallic=0.0,
        roughness=0.75,
        bump_strength=0.30,
        bump_scale=12.0,
    )
    mats["wood_crate"] = create_pbr_material(
        "mat_dust2_wood_crate",
        (0.52, 0.38, 0.24),
        metallic=0.0,
        roughness=0.68,
        bump_strength=0.25,
        bump_scale=14.0,
    )
    mats["metal_iron_rusted"] = create_pbr_material(
        "mat_dust2_metal_iron_rusted",
        (0.25, 0.20, 0.18),
        metallic=0.80,
        roughness=0.55,
        bump_strength=0.20,
        bump_scale=28.0,
    )
    mats["metal_scaffolding"] = create_pbr_material(
        "mat_metal_scaffolding",
        (0.45, 0.45, 0.48),
        metallic=0.85,
        roughness=0.35,
        bump_strength=0.08,
        bump_scale=30.0,
    )

    # Fabrics & foliage
    mats["canopy_crimson"] = create_pbr_material(
        "mat_dust2_canopy_crimson",
        (0.58, 0.12, 0.14),
        metallic=0.0,
        roughness=0.88,
        bump_strength=0.12,
        bump_scale=35.0,
    )
    mats["canopy_saffron"] = create_pbr_material(
        "mat_dust2_canopy_saffron",
        (0.85, 0.55, 0.10),
        metallic=0.0,
        roughness=0.88,
        bump_strength=0.12,
        bump_scale=35.0,
    )
    mats["palm_bark"] = create_pbr_material(
        "mat_dust2_palm_bark",
        (0.30, 0.22, 0.16),
        metallic=0.0,
        roughness=0.95,
        bump_strength=0.35,
        bump_scale=8.0,
    )
    mats["sack"] = create_pbr_material(
        "mat_dust2_sack_burlap",
        (0.60, 0.48, 0.30),
        metallic=0.0,
        roughness=0.95,
        bump_strength=0.2,
        bump_scale=40.0,
    )
    mats["palm_fronds"] = create_pbr_material(
        "mat_palm_fronds", (0.18, 0.38, 0.12), metallic=0.0, roughness=0.70
    )

    # Lanterns & lighting
    mats["lantern_brass"] = create_pbr_material(
        "mat_lantern_brass", (0.75, 0.60, 0.22), metallic=0.88, roughness=0.25
    )
    mats["lantern_flame"] = create_pbr_material(
        "mat_lantern_flame",
        (1.0, 0.70, 0.30),
        metallic=0.0,
        roughness=0.10,
        emission_color=(1.0, 0.70, 0.30),
        emission_strength=5.0,
    )

    # Breakable glass windows
    mats["glass_window"] = create_pbr_material(
        "mat_glass_window",
        (0.82, 0.92, 0.98),
        metallic=0.05,
        roughness=0.06,
        alpha=0.35,
    )

    return mats


def add_box(collection, name, center, size, material):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_cylinder(collection, name, center, radius, height, material, segments=16):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=height,
    )
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_arch(
    collection, name, center, span, height, depth, material, segments=8, yaw=0.0
):
    """Adds a detailed Moorish horseshoe arched portal cutout.

    The span runs along x and the depth along y; `yaw` (radians) turns it about
    its own centre, for a gate in a wall that runs north to south.
    """
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    pillar_w = 0.8
    pillar_h = height - span * 0.45

    # Left pillar with base plinth and carved capital
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((pillar_w, depth, pillar_h)), verts=bm.verts)
    bmesh.ops.translate(
        bm,
        vec=Vector((-span * 0.5 - pillar_w * 0.5, 0.0, pillar_h * 0.5)),
        verts=bm.verts,
    )

    # Left base plinth
    plinth_l = bmesh.new()
    bmesh.ops.create_cube(plinth_l, size=1.0)
    bmesh.ops.scale(
        plinth_l, vec=Vector((pillar_w * 1.25, depth * 1.2, 0.4)), verts=plinth_l.verts
    )
    bmesh.ops.translate(
        plinth_l,
        vec=Vector((-span * 0.5 - pillar_w * 0.5, 0.0, 0.2)),
        verts=plinth_l.verts,
    )
    for v in plinth_l.verts:
        bm.verts.new(v.co)
    plinth_l.free()

    # Right pillar
    r_bm = bmesh.new()
    bmesh.ops.create_cube(r_bm, size=1.0)
    bmesh.ops.scale(r_bm, vec=Vector((pillar_w, depth, pillar_h)), verts=r_bm.verts)
    bmesh.ops.translate(
        r_bm,
        vec=Vector((span * 0.5 + pillar_w * 0.5, 0.0, pillar_h * 0.5)),
        verts=r_bm.verts,
    )
    for v in r_bm.verts:
        bm.verts.new(v.co)
    r_bm.free()

    # Right base plinth
    plinth_r = bmesh.new()
    bmesh.ops.create_cube(plinth_r, size=1.0)
    bmesh.ops.scale(
        plinth_r, vec=Vector((pillar_w * 1.25, depth * 1.2, 0.4)), verts=plinth_r.verts
    )
    bmesh.ops.translate(
        plinth_r,
        vec=Vector((span * 0.5 + pillar_w * 0.5, 0.0, 0.2)),
        verts=plinth_r.verts,
    )
    for v in plinth_r.verts:
        bm.verts.new(v.co)
    plinth_r.free()

    # Arch curvature segments
    radius = span * 0.5
    for i in range(segments):
        a0 = math.pi * i / segments
        a1 = math.pi * (i + 1) / segments
        x0 = math.cos(a0) * radius
        z0 = math.sin(a0) * radius
        x1 = math.cos(a1) * radius
        z1 = math.sin(a1) * radius

        seg_bm = bmesh.new()
        bmesh.ops.create_cube(seg_bm, size=1.0)
        dx = x1 - x0
        dz = z1 - z0
        seg_len = math.hypot(dx, dz)
        seg_thick = 0.7
        bmesh.ops.scale(
            seg_bm, vec=Vector((seg_len, depth, seg_thick)), verts=seg_bm.verts
        )
        ang = math.atan2(dz, dx)
        rot_mat = Matrix.Rotation(ang, 4, "Y")
        bmesh.ops.transform(seg_bm, matrix=rot_mat, verts=seg_bm.verts)
        bmesh.ops.translate(
            seg_bm,
            vec=Vector(((x0 + x1) * 0.5, 0.0, pillar_h + (z0 + z1) * 0.5)),
            verts=seg_bm.verts,
        )
        for v in seg_bm.verts:
            bm.verts.new(v.co)
        seg_bm.free()

    # Top lintel and cornice
    top_bm = bmesh.new()
    bmesh.ops.create_cube(top_bm, size=1.0)
    bmesh.ops.scale(
        top_bm,
        vec=Vector((span + pillar_w * 2.6, depth * 1.1, 0.7)),
        verts=top_bm.verts,
    )
    bmesh.ops.translate(
        top_bm, vec=Vector((0.0, 0.0, height + 0.6)), verts=top_bm.verts
    )
    for v in top_bm.verts:
        bm.verts.new(v.co)
    top_bm.free()

    if yaw:
        bmesh.ops.rotate(
            bm,
            cent=(0.0, 0.0, 0.0),
            matrix=Matrix.Rotation(yaw, 3, "Z"),
            verts=bm.verts,
        )
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_crate_stack(collection, prefix, base_pos, mats):
    """Detailed CS shipping crate stack with corner iron brackets and side slats."""
    bx, by, bz = base_pos
    # Base crate
    add_box(
        collection,
        f"{prefix}_crate_base",
        (bx, by, bz + 0.6),
        (1.2, 1.2, 1.2),
        mats["wood_crate"],
    )
    # Iron corner brackets
    add_box(
        collection,
        f"{prefix}_bracket_h_NonCol",
        (bx, by, bz + 0.6),
        (1.24, 0.08, 1.24),
        mats["metal_iron_rusted"],
    )
    add_box(
        collection,
        f"{prefix}_bracket_v_NonCol",
        (bx, by, bz + 0.6),
        (0.08, 1.24, 1.24),
        mats["metal_iron_rusted"],
    )
    # Cross brace detail
    add_box(
        collection,
        f"{prefix}_xbrace_NonCol",
        (bx + 0.61, by, bz + 0.6),
        (0.02, 1.0, 0.12),
        mats["wood_cedar_weathered"],
    )
    add_box(
        collection,
        f"{prefix}_xbrace2_NonCol",
        (bx - 0.61, by, bz + 0.6),
        (0.02, 1.0, 0.12),
        mats["wood_cedar_weathered"],
    )


def add_palm_tree(collection, name, pos, mats):
    """Realistic desert date palm with curved ringed trunk and layered drooping fronds."""
    px, py, pz = pos
    # Multi-segment curved trunk with bark rings
    segments = 6
    curr_z = pz
    curr_x = px
    curr_y = py
    for s in range(segments):
        h = 1.1
        tilt_x = math.sin(s * 0.35) * 0.08
        tilt_y = math.cos(s * 0.35) * 0.08
        rad = 0.32 - s * 0.02
        add_cylinder(
            collection,
            f"{name}_trunk_{s}",
            (curr_x, curr_y, curr_z + h * 0.5),
            radius=rad,
            height=h,
            material=mats["palm_bark"],
            segments=10,
        )
        # Bark ring collar
        add_cylinder(
            collection,
            f"{name}_ring_{s}_NonCol",
            (curr_x, curr_y, curr_z + h),
            radius=rad * 1.12,
            height=0.12,
            material=mats["palm_bark"],
            segments=10,
        )
        curr_z += h
        curr_x += tilt_x
        curr_y += tilt_y

    # Layered 3D Fronds Canopy
    for tier in [0, 1]:
        tz = curr_z - tier * 0.35
        frond_count = 10
        for i in range(frond_count):
            ang = 360.0 * i / frond_count + (tier * 18.0)
            rad = math.radians(ang)
            dist = 1.8 - tier * 0.3
            fx = curr_x + math.cos(rad) * dist * 0.65
            fy = curr_y + math.sin(rad) * dist * 0.65
            fz = tz + 0.2 - tier * 0.25
            # Frond leaf blade with droop
            add_box(
                collection,
                f"{name}_frond_{tier}_{i}_NonCol",
                (fx, fy, fz),
                (dist, 0.45, 0.05),
                mats["palm_fronds"],
            )


def add_crenellations(collection, name, start, end, height, mats):
    """Adds defensive notched stone battlements / crenellations along a wall top."""
    sx, sy, sz = start
    ex, ey, ez = end
    dx = ex - sx
    dy = ey - sy
    length = math.hypot(dx, dy)
    count = int(length // 1.6)
    if count < 1:
        return
    step_x = dx / count
    step_y = dy / count
    for i in range(count):
        if i % 2 == 0:
            cx = sx + (i + 0.5) * step_x
            cy = sy + (i + 0.5) * step_y
            size_x = abs(step_x) if abs(step_x) > 0.4 else 0.8
            size_y = abs(step_y) if abs(step_y) > 0.4 else 0.8
            add_box(
                collection,
                f"{name}_cren_{i}",
                (cx, cy, sz + height * 0.5),
                (size_x, size_y, height),
                mats["sandstone_ochre"],
            )


def add_hanging_lantern(collection, name, ceiling_pos, mats, radius=7.0, intensity=2.0):
    """Ornate brass Moroccan lantern with chain link and glowing flame core."""
    cx, cy, cz = ceiling_pos
    # Iron chain
    add_cylinder(
        collection,
        f"{name}_chain_NonCol",
        (cx, cy, cz - 0.4),
        radius=0.03,
        height=0.8,
        material=mats["metal_iron_rusted"],
        segments=6,
    )
    # Brass cap
    add_cylinder(
        collection,
        f"{name}_cap_NonCol",
        (cx, cy, cz - 0.82),
        radius=0.18,
        height=0.08,
        material=mats["lantern_brass"],
        segments=8,
    )
    # 8-sided glazed lantern body
    add_cylinder(
        collection,
        f"{name}_body_NonCol",
        (cx, cy, cz - 1.05),
        radius=0.22,
        height=0.42,
        material=mats["lantern_brass"],
        segments=8,
    )
    # Emissive flame core
    add_cylinder(
        collection,
        f"{name}_flame_NonCol",
        (cx, cy, cz - 1.05),
        radius=0.10,
        height=0.25,
        material=mats["lantern_flame"],
        segments=6,
    )
    # Bottom finial point
    add_cylinder(
        collection,
        f"{name}_finial_NonCol",
        (cx, cy, cz - 1.30),
        radius=0.06,
        height=0.12,
        material=mats["lantern_brass"],
        segments=6,
    )
    add_light(
        (cx, cy, cz - 1.05), color=(255, 186, 112), radius=radius, intensity=intensity
    )


def add_market_stall(collection, name, center, size, mats):
    """Detailed market stall with cedar timber framework, woven striped canopy, and goods."""
    cx, cy, cz = center
    sx, sy, sz = size
    # 4 Corner Timber Posts
    for dx in [-1, 1]:
        for dy in [-1, 1]:
            px = cx + dx * (sx * 0.5 - 0.1)
            py = cy + dy * (sy * 0.5 - 0.1)
            add_cylinder(
                collection,
                f"{name}_post_{dx}_{dy}",
                (px, py, cz + sz * 0.5),
                radius=0.07,
                height=sz,
                material=mats["wood_cedar_weathered"],
                segments=8,
            )
    # Perimeter roof beams
    add_box(
        collection,
        f"{name}_beam_n_NonCol",
        (cx, cy + sy * 0.5 - 0.1, cz + sz - 0.06),
        (sx, 0.12, 0.12),
        mats["wood_cedar_weathered"],
    )
    add_box(
        collection,
        f"{name}_beam_s_NonCol",
        (cx, cy - sy * 0.5 + 0.1, cz + sz - 0.06),
        (sx, 0.12, 0.12),
        mats["wood_cedar_weathered"],
    )
    # Striped Fabric Canopy: two half-depth panels side by side. They once shared
    # one centre, so the same box was drawn twice in two colours and z-fought.
    add_box(
        collection,
        f"{name}_canopy_red_NonCol",
        (cx, cy - sy * 0.25, cz + sz + 0.05),
        (sx * 1.08, sy * 0.5, 0.04),
        mats["canopy_crimson"],
    )
    add_box(
        collection,
        f"{name}_canopy_gold_NonCol",
        (cx, cy + sy * 0.25, cz + sz + 0.05),
        (sx * 1.08, sy * 0.5, 0.04),
        mats["canopy_saffron"],
    )
    # Display Table
    add_box(
        collection,
        f"{name}_table",
        (cx, cy, cz + 0.45),
        (sx * 0.85, sy * 0.75, 0.9),
        mats["wood_crate"],
    )
    # Amphoras / Produce on table
    add_cylinder(
        collection,
        f"{name}_amphora1_NonCol",
        (cx - sx * 0.25, cy, cz + 1.15),
        radius=0.18,
        height=0.5,
        material=mats["sandstone_ochre"],
        segments=10,
    )
    add_cylinder(
        collection,
        f"{name}_amphora2_NonCol",
        (cx + sx * 0.25, cy, cz + 1.15),
        radius=0.18,
        height=0.5,
        material=mats["sandstone_dark"],
        segments=10,
    )


def build_dust2_terrain_and_perimeter(col, mats):
    """Main ground terrain, desert sand bed, perimeter walls, and crenellated battlements."""
    # Ground paving
    add_box(
        col,
        "Terrain_Ground",
        (35.0, 35.0, -0.5),
        (70.0, 70.0, 1.0),
        mats["limestone_paving"],
    )
    # Outer sand dunes
    add_box(
        col,
        "Dune_Perimeter_East",
        (68.0, 35.0, 0.5),
        (8.0, 70.0, 3.0),
        mats["desert_sand"],
    )
    add_box(
        col,
        "Dune_Perimeter_South",
        (35.0, 2.0, 0.5),
        (70.0, 8.0, 3.0),
        mats["desert_sand"],
    )
    add_box(
        col,
        "Dune_Perimeter_West",
        (1.0, 35.0, 0.5),
        (6.0, 70.0, 3.0),
        mats["desert_sand"],
    )

    # High Sandstone Fortress Perimeter Walls (height 10.0m)
    add_box(
        col,
        "Wall_Perimeter_South",
        (35.0, 4.0, 5.0),
        (62.0, 1.4, 10.0),
        mats["sandstone_ochre"],
    )
    add_box(
        col,
        "Wall_Perimeter_North",
        (35.0, 66.0, 5.0),
        (62.0, 1.4, 10.0),
        mats["sandstone_ochre"],
    )
    add_box(
        col,
        "Wall_Perimeter_East",
        (66.0, 35.0, 5.0),
        (1.4, 62.0, 10.0),
        mats["sandstone_ochre"],
    )
    add_box(
        col,
        "Wall_Perimeter_West",
        (4.0, 35.0, 5.0),
        (1.4, 62.0, 10.0),
        mats["sandstone_ochre"],
    )

    # Wall Copings & Cornices (top molding)
    add_box(
        col,
        "Cornice_Perimeter_S_NonCol",
        (35.0, 4.0, 10.15),
        (62.8, 1.65, 0.3),
        mats["sandstone_light"],
    )
    add_box(
        col,
        "Cornice_Perimeter_N_NonCol",
        (35.0, 66.0, 10.15),
        (62.8, 1.65, 0.3),
        mats["sandstone_light"],
    )
    add_box(
        col,
        "Cornice_Perimeter_E_NonCol",
        (66.0, 35.0, 10.15),
        (1.65, 62.8, 0.3),
        mats["sandstone_light"],
    )
    add_box(
        col,
        "Cornice_Perimeter_W_NonCol",
        (4.0, 35.0, 10.15),
        (1.65, 62.8, 0.3),
        mats["sandstone_light"],
    )

    # Crenellated battlements along South & North fortress walls
    add_crenellations(
        col, "Crenell_South", (6.0, 4.0, 10.3), (64.0, 4.0, 10.3), 0.8, mats
    )
    add_crenellations(
        col, "Crenell_North", (6.0, 66.0, 10.3), (64.0, 66.0, 10.3), 0.8, mats
    )

    # Moorish blue & gold glazed tile decorative mosaic frieze
    add_box(
        col,
        "Frieze_Perimeter_N_NonCol",
        (35.0, 65.25, 9.4),
        (62.0, 0.1, 0.45),
        mats["moorish_tile_blue"],
    )
    add_box(
        col,
        "Frieze_Gold_N_NonCol",
        (35.0, 65.24, 9.15),
        (62.0, 0.08, 0.12),
        mats["moorish_tile_gold"],
    )
    add_box(
        col,
        "Frieze_Perimeter_S_NonCol",
        (35.0, 4.75, 9.4),
        (62.0, 0.1, 0.45),
        mats["moorish_tile_blue"],
    )
    add_box(
        col,
        "Frieze_Gold_S_NonCol",
        (35.0, 4.76, 9.15),
        (62.0, 0.08, 0.12),
        mats["moorish_tile_gold"],
    )


def wall_with_openings(
    col, name, axis, line, lo, hi, thick, height, material, openings
):
    """A full-height wall with doorways and windows cut through it.

    The wall runs along `axis` ("x" or "y") at `line` on the other axis, from `lo`
    to `hi`. Each opening is `(a, b, sill, top)`: from `a` to `b` along the wall,
    open between heights `sill` and `top`. Solid sill below and lintel above, so an
    opening is a real hole in the collision and not a painted one. All in the
    authored (pre-cut) frame, through the same wrappers as everything else, so a
    cut that crosses a section stretches it and the openings move with their walls.
    """
    cursor = lo

    def piece(a, b, z0, z1, label):
        if b - a < 1e-6 or z1 - z0 < 1e-6:
            return
        along, length, zc = (a + b) / 2.0, b - a, (z0 + z1) / 2.0
        if axis == "y":
            center, size = (line, along, zc), (thick, length, z1 - z0)
        else:
            center, size = (along, line, zc), (length, thick, z1 - z0)
        add_box(col, f"{name}_{label}", center, size, material)

    for i, (a, b, sill, top) in enumerate(sorted(openings)):
        piece(cursor, a, 0.0, height, f"S{i}")
        piece(a, b, 0.0, sill, f"Sill{i}")
        piece(a, b, top, height, f"Lintel{i}")
        cursor = b
    piece(cursor, hi, 0.0, height, f"S{len(openings)}")


def build_long_a_and_pit(col, mats):
    """Long A corridor, sunken Pit, corner, and Long A Archway."""
    # Long A dividing west wall, with a door through it from lower Mid (the Long
    # Doors) and a window further up that looks over upper Mid from the ledge.
    # The door is an arch 2.4 wide between 0.8 pillars; the window's sill clears a
    # jump from the ledge, so it is a sightline and not a way through.
    wall_with_openings(
        col,
        "Wall_LongA_West",
        "y",
        44.0,
        8.0,
        48.0,
        1.4,
        9.0,
        mats["sandstone_light"],
        [(24.0, 28.0, 0.0, 4.0), (33.0, 36.0, 2.6, 3.7)],
    )
    add_arch(
        col,
        "Arch_LongA_Doors_Mid",
        (44.0, 26.0, 0.0),
        span=2.4,
        height=3.6,
        depth=1.6,
        material=mats["sandstone_ochre"],
        yaw=math.pi / 2,
    )
    add_hanging_lantern(col, "Lantern_LongA_Doors_Mid", (44.0, 26.0, 4.6), mats)
    # The Long ledge: a stone terrace against the wall, 1.2 m up, reached by a
    # ramp from the north. Its top is eye height to the window above it.
    add_box(
        col,
        "Long_Ledge",
        (46.85, 34.75, 0.6),
        (4.3, 10.5, 1.2),
        mats["limestone_paving"],
    )
    add_wedge(
        col,
        "Long_Ledge_Ramp",
        (46.1, 42.0, 0.6),
        (2.9, 4.0, 1.2),
        mats["limestone_paving"],
        "-y",
    )
    add_box(
        col,
        "Cornice_LongA_West_NonCol",
        (44.0, 28.0, 9.15),
        (1.65, 40.2, 0.3),
        mats["sandstone_ochre"],
    )

    # Long A Double Sandstone Archway (T entrance into Long at Y: 16)
    add_arch(
        col,
        "Arch_LongA_Doors",
        (52.0, 16.0, 0.0),
        span=4.4,
        height=5.2,
        depth=1.8,
        material=mats["sandstone_ochre"],
    )
    add_hanging_lantern(col, "Lantern_LongA_Doors", (52.0, 16.0, 5.0), mats)

    # Sunken Pit: floor at Z = -1.4m from Y: 6.0 to Y: 18.0, X: 52.0 to 65.0
    add_box(
        col,
        "Pit_Floor",
        (58.5, 12.0, -0.7),
        (13.0, 12.0, 1.4),
        mats["limestone_paving"],
    )
    # Pit retaining wall with stone coping curb
    add_box(
        col,
        "Pit_Curb_West",
        (50.5, 12.0, 0.4),
        (1.0, 12.0, 0.8),
        mats["sandstone_dark"],
    )
    add_box(
        col,
        "Pit_Curb_Cap_NonCol",
        (50.5, 12.0, 0.85),
        (1.2, 12.2, 0.12),
        mats["sandstone_light"],
    )
    # Stepped Ramp out of Pit
    add_wedge(
        col,
        "Pit_Ramp",
        (54.0, 19.5, -0.7),
        (4.5, 5.0, 1.4),
        mats["limestone_paving"],
        "+y",
    )

    # Abandoned rusted vehicle barricade in Pit
    add_box(
        col,
        "Pit_Car_Chassis",
        (58.0, 12.0, -0.2),
        (4.2, 2.0, 1.0),
        mats["metal_iron_rusted"],
    )
    add_box(
        col, "Pit_Car_Cabin", (57.5, 12.0, 0.7), (2.2, 1.8, 0.9), mats["sandstone_dark"]
    )
    for wx, wy in [(56.5, 10.9), (59.5, 10.9), (56.5, 13.1), (59.5, 13.1)]:
        add_cylinder(
            col,
            f"Pit_Car_Wheel_{int(wx * 10)}_{int(wy * 10)}",
            (wx, wy, -0.4),
            radius=0.42,
            height=0.3,
            material=mats["metal_iron_rusted"],
            segments=12,
        )

    # Long A Corner: wall protrusion, barrels, and crate cache
    add_box(
        col,
        "Wall_LongA_Corner",
        (60.0, 36.0, 4.5),
        (10.0, 1.4, 9.0),
        mats["sandstone_light"],
    )
    for i, by in enumerate([33.5, 34.8, 36.0]):
        add_cylinder(
            col,
            f"LongA_Barrel_{i}",
            (54.2, by, 0.6),
            radius=0.45,
            height=1.2,
            material=mats["wood_cedar_weathered"],
        )
        # Metal hoops on barrel
        add_cylinder(
            col,
            f"LongA_Barrel_Hoop1_{i}_NonCol",
            (54.2, by, 0.35),
            radius=0.46,
            height=0.06,
            material=mats["metal_iron_rusted"],
            segments=12,
        )
        add_cylinder(
            col,
            f"LongA_Barrel_Hoop2_{i}_NonCol",
            (54.2, by, 0.85),
            radius=0.46,
            height=0.06,
            material=mats["metal_iron_rusted"],
            segments=12,
        )

    add_crate_stack(col, "LongA_Corner", (54.5, 38.0, 0.0), mats)

    # Long A Ramp ascending to Site A
    add_wedge(
        col,
        "LongA_Ramp",
        (52.0, 46.0, 0.6),
        (8.5, 8.5, 1.2),
        mats["limestone_paving"],
        "+y",
    )


def build_site_a(col, mats):
    """Bomb Site A elevated plateau (+1.2m), double boxes, goose corner, and CT connection."""
    # Elevated Site A Plateau
    add_box(
        col,
        "SiteA_Plateau",
        (50.0, 55.0, 0.6),
        (16.0, 14.0, 1.2),
        mats["limestone_paving"],
    )
    # Stone retaining edge around plateau
    add_box(
        col,
        "SiteA_Edge_West_NonCol",
        (41.9, 55.0, 0.6),
        (0.2, 14.0, 1.25),
        mats["sandstone_dark"],
    )
    add_box(
        col,
        "SiteA_Edge_South_NonCol",
        (50.0, 47.9, 0.6),
        (16.0, 0.2, 1.25),
        mats["sandstone_dark"],
    )

    # "Goose" Corner Wall (North-East corner of A)
    add_box(
        col,
        "SiteA_Goose_Wall",
        (58.0, 62.0, 3.8),
        (14.0, 1.4, 5.6),
        mats["sandstone_ochre"],
    )
    add_box(
        col,
        "Cornice_Goose_NonCol",
        (58.0, 62.0, 6.7),
        (14.2, 1.6, 0.25),
        mats["sandstone_light"],
    )
    # Goose graffiti symbol plaque
    add_box(
        col,
        "SiteA_Goose_Symbol_NonCol",
        (54.0, 61.25, 2.5),
        (1.5, 0.05, 1.5),
        mats["moorish_tile_blue"],
    )

    # Iconic Double Wooden Crates (Default Plant Box) at (46.0, 52.0)
    add_crate_stack(col, "SiteA_Plant_Lower1", (46.0, 52.0, 1.2), mats)
    add_crate_stack(col, "SiteA_Plant_Lower2", (46.0, 53.4, 1.2), mats)
    add_crate_stack(col, "SiteA_Plant_Upper", (46.0, 52.0, 2.4), mats)

    # Triple stack near ramp
    add_crate_stack(col, "SiteA_Ramp_Stack1", (52.0, 56.0, 1.2), mats)
    add_crate_stack(col, "SiteA_Ramp_Stack2", (52.0, 57.4, 1.2), mats)

    # CT Spawn Ramp connection down from Site A to CT street level
    add_wedge(
        col,
        "SiteA_CT_Ramp",
        (44.0, 58.0, 0.6),
        (6.0, 6.0, 1.2),
        mats["limestone_paving"],
        "+x",
    )


def build_catwalk_and_short_a(col, mats):
    """Elevated Catwalk (+2.4m) running from Mid to Site A with wooden pergola and wrought-iron railings."""
    # Catwalk bridge / ledge (X: 34.0 .. 44.0, Y: 42.0 .. 48.0, Z = +2.4m)
    add_box(
        col,
        "Catwalk_Floor",
        (39.0, 45.0, 1.2),
        (10.0, 5.0, 2.4),
        mats["sandstone_light"],
    )

    # Timber Pergola with overhead shade beams across Catwalk. The posts collide:
    # tagged NonCol, a body walked into them, and from inside a mesh every face is a
    # back face, so a post vanished for whoever stood in it while hiding them.
    for px in [35.5, 38.5, 41.5]:
        add_cylinder(
            col,
            f"Pergola_Post_{int(px * 10)}",
            (px, 42.6, 3.8),
            radius=0.08,
            height=2.8,
            material=mats["wood_cedar_weathered"],
            segments=8,
        )
        # Overhead cross beam
        add_box(
            col,
            f"Pergola_Rafter_{int(px * 10)}_NonCol",
            (px, 45.0, 5.25),
            (0.12, 5.2, 0.16),
            mats["wood_cedar_weathered"],
        )

    # Short A Wall with archway entering Site A
    add_arch(
        col,
        "ShortA_Portal",
        (43.0, 48.0, 2.4),
        span=3.4,
        height=4.0,
        depth=1.4,
        material=mats["sandstone_ochre"],
    )

    # Wrought iron guard railing along edge of Catwalk looking into Mid
    add_box(
        col,
        "Catwalk_Railing_Mid",
        (34.0, 45.0, 2.95),
        (0.1, 5.0, 1.1),
        mats["metal_iron_rusted"],
    )
    # Railing pickets
    for ry in range(43, 48):
        add_cylinder(
            col,
            f"Catwalk_Picket_{ry}_NonCol",
            (34.0, ry + 0.5, 2.95),
            radius=0.02,
            height=1.0,
            material=mats["metal_iron_rusted"],
            segments=6,
        )

    # Stairs descending from Catwalk to Lower Dark Tunnel
    add_box(
        col,
        "Stairs_Catwalk_LowerDark",
        (30.0, 42.0, 1.2),
        (4.5, 3.2, 2.4),
        mats["limestone_paving"],
    )


def build_middle_and_mid_doors(col, mats):
    """Middle corridor, iconic Mid Double Doors with sniper slit, and Xbox crate."""
    # Mid corridor canyon walls. A door through the west one, the Lower Dark door,
    # joins Mid to the alley between it and the tunnels' inner wall: the way from
    # Mid to B that does not go over the catwalk.
    wall_with_openings(
        col,
        "Wall_Mid_West",
        "y",
        26.0,
        16.0,
        48.0,
        1.4,
        9.0,
        mats["sandstone_light"],
        [(33.4, 37.6, 0.0, 4.0)],
    )
    add_arch(
        col,
        "Arch_LowerDark_Door",
        (26.0, 35.5, 0.0),
        span=2.6,
        height=3.6,
        depth=1.6,
        material=mats["sandstone_ochre"],
        yaw=math.pi / 2,
    )
    add_hanging_lantern(col, "Lantern_LowerDark_Door", (24.4, 35.5, 3.9), mats)
    add_box(
        col,
        "Cornice_Mid_West_NonCol",
        (26.0, 32.0, 9.15),
        (1.65, 32.2, 0.3),
        mats["sandstone_ochre"],
    )

    # Mid Double Doors: Heavy cedar wooden doors with iron strap hinges and forged rings
    # Left Door swung slightly forward
    add_box(
        col,
        "Mid_Door_Left",
        (29.1, 32.2, 2.2),
        (2.0, 0.28, 4.4),
        mats["wood_cedar_weathered"],
    )
    # Iron hinge straps & studs on Left door
    for hz in [1.0, 3.4]:
        add_box(
            col,
            f"Mid_Door_L_Hinge_{int(hz * 10)}_NonCol",
            (28.8, 32.35, hz),
            (1.5, 0.04, 0.12),
            mats["metal_iron_rusted"],
        )
    # Right Door swung slightly back, leaving 0.45m center sniper slit!
    add_box(
        col,
        "Mid_Door_Right",
        (32.3, 31.8, 2.2),
        (2.0, 0.28, 4.4),
        mats["wood_cedar_weathered"],
    )
    for hz in [1.0, 3.4]:
        add_box(
            col,
            f"Mid_Door_R_Hinge_{int(hz * 10)}_NonCol",
            (32.6, 31.65, hz),
            (1.5, 0.04, 0.12),
            mats["metal_iron_rusted"],
        )

    # Arch lintel and stone voussoirs above doors
    add_box(
        col,
        "Mid_Door_Arch_Lintel",
        (30.7, 32.0, 4.9),
        (6.4, 1.4, 1.4),
        mats["sandstone_ochre"],
    )
    add_hanging_lantern(col, "Lantern_Mid_Doors", (30.7, 32.0, 4.2), mats)

    # Xbox Jump Crate in Mid (X: 30.0, Y: 38.0): 1.6m cube wood box for Catwalk boost
    add_box(
        col, "Xbox_Crate_Body", (30.0, 38.0, 0.8), (1.6, 1.6, 1.6), mats["wood_crate"]
    )
    add_box(
        col,
        "Xbox_Crate_Brackets_NonCol",
        (30.0, 38.0, 0.8),
        (1.64, 0.15, 1.64),
        mats["metal_iron_rusted"],
    )

    # Suicide Ramp from T Spawn leading down into Mid
    add_wedge(
        col,
        "Mid_Suicide_Ramp",
        (31.0, 20.0, 0.6),
        (5.5, 8.5, 1.2),
        mats["limestone_paving"],
        "-y",
    )


def build_dark_tunnels(col, mats):
    """Subterranean vaulted Dark Tunnels (Upper Dark and Lower Dark) with vaulted stone arches and lanterns."""
    # Tunnel ceiling slab at Z = 4.2m enclosing the darkness
    add_box(
        col,
        "Tunnel_Roof_Slab",
        (16.0, 34.0, 4.4),
        (18.5, 24.5, 0.8),
        mats["sandstone_dark"],
    )

    # Tunnel interior walls
    add_box(
        col,
        "Tunnel_Wall_Outer_West",
        (8.0, 34.0, 2.1),
        (1.4, 24.0, 4.2),
        mats["sandstone_dark"],
    )
    add_box(
        col,
        "Tunnel_Wall_Inner_East",
        (22.0, 30.0, 2.1),
        (1.4, 16.0, 4.2),
        mats["sandstone_dark"],
    )

    # Tunnel decorative stone arches every 6 meters with hanging lanterns
    for ty in [26.0, 32.0, 38.0]:
        add_arch(
            col,
            f"Tunnel_Arch_{int(ty)}",
            (15.0, ty, 0.0),
            span=4.8,
            height=3.8,
            depth=1.2,
            material=mats["sandstone_ochre"],
        )
        add_hanging_lantern(
            col,
            f"Lantern_Tunnel_{int(ty)}",
            (15.0, ty, 4.1),
            mats,
            radius=8.0,
            intensity=2.5,
        )


def build_site_b(col, mats):
    """Bomb Site B fortress courtyard, Upper B exit, B Window, B Doors, and Back Platform."""
    # B Site Fortress Perimeter Walls (height 8.0m)
    add_box(
        col,
        "SiteB_North_Wall",
        (16.0, 62.0, 4.2),
        (22.0, 1.4, 8.4),
        mats["sandstone_ochre"],
    )
    add_box(
        col,
        "SiteB_West_Wall",
        (6.0, 52.0, 4.2),
        (1.4, 20.0, 8.4),
        mats["sandstone_ochre"],
    )
    # The east wall has the B Doors through it: the way from the CT street into B.
    wall_with_openings(
        col,
        "SiteB_East_Wall",
        "y",
        26.0,
        46.0,
        62.0,
        1.4,
        8.4,
        mats["sandstone_ochre"],
        [(55.1, 58.9, 0.0, 4.0)],
    )
    add_arch(
        col,
        "Arch_B_Doors",
        (26.0, 57.0, 0.0),
        span=2.2,
        height=3.6,
        depth=1.6,
        material=mats["sandstone_ochre"],
        yaw=math.pi / 2,
    )

    # Wall copings on B walls
    add_box(
        col,
        "Cornice_SiteB_N_NonCol",
        (16.0, 62.0, 8.55),
        (22.4, 1.65, 0.3),
        mats["sandstone_light"],
    )
    add_crenellations(
        col, "Crenell_SiteB_N", (6.0, 62.0, 8.7), (26.0, 62.0, 8.7), 0.8, mats
    )

    # Raised Bomb Plant Platform (X: 12.0 .. 20.0, Y: 50.0 .. 58.0, Z = +0.6m)
    add_box(
        col,
        "SiteB_Plant_Platform",
        (16.0, 54.0, 0.3),
        (8.5, 8.5, 0.6),
        mats["limestone_paving"],
    )
    add_crate_stack(col, "SiteB_Plant_Box1", (15.0, 53.0, 0.6), mats)
    add_crate_stack(col, "SiteB_Plant_Box2", (16.4, 53.0, 0.6), mats)

    # Upper B Tunnel exit doorway at (16.0, 44.0) with raised lip
    add_arch(
        col,
        "SiteB_UpperDark_Exit",
        (16.0, 44.0, 0.0),
        span=3.8,
        height=4.0,
        depth=1.4,
        material=mats["sandstone_ochre"],
    )

    # B Window opening overlooking tunnels at (22.0, 46.0, Z: 2.0)
    add_box(
        col,
        "SiteB_Window_Wall",
        (22.0, 46.0, 1.0),
        (1.4, 4.2, 2.0),
        mats["sandstone_light"],
    )
    add_box(
        col,
        "SiteB_Window_Top",
        (22.0, 46.0, 4.6),
        (1.4, 4.2, 3.2),
        mats["sandstone_light"],
    )

    # Scaffolding and jumping crates next to window
    add_crate_stack(col, "SiteB_Window", (20.5, 46.0, 0.0), mats)
    add_box(
        col,
        "SiteB_Scaffold_Pole1",
        (23.5, 44.5, 2.5),
        (0.1, 0.1, 5.0),
        mats["metal_scaffolding"],
    )
    add_box(
        col,
        "SiteB_Scaffold_Pole2",
        (23.5, 47.5, 2.5),
        (0.1, 0.1, 5.0),
        mats["metal_scaffolding"],
    )
    add_box(
        col,
        "SiteB_Scaffold_Plank",
        (23.5, 46.0, 2.2),
        (1.0, 3.2, 0.1),
        mats["wood_cedar_weathered"],
    )

    # B Double Doors connecting B Site to CT street
    # Swung back against the wall either side of the opening, which they used to
    # fill; each is a leaf of the pair, stood off the wall face by a hand's width.
    add_box(
        col,
        "SiteB_Door_Left",
        (24.9, 54.0, 1.9),
        (0.22, 1.8, 3.8),
        mats["wood_cedar_weathered"],
    )
    add_box(
        col,
        "SiteB_Door_Right",
        (24.9, 60.0, 1.9),
        (0.22, 1.8, 3.8),
        mats["wood_cedar_weathered"],
    )

    # Back Platform & Wooden Crates
    add_box(
        col,
        "SiteB_Back_Platform",
        (9.0, 56.0, 0.5),
        (4.5, 8.5, 1.0),
        mats["limestone_paving"],
    )
    add_crate_stack(col, "SiteB_Back", (9.0, 57.0, 1.0), mats)


def build_spawns_and_props(col, mats):
    """T and CT Spawns, souk market stalls, Persian rugs, and desert palm trees."""
    # CT Spawn area (X: 30.0 .. 42.0, Y: 56.0 .. 64.0)
    add_palm_tree(col, "CT_Palm_1", (36.0, 62.0, 0.0), mats)
    add_palm_tree(col, "CT_Palm_2", (30.0, 63.0, 0.0), mats)

    # T Spawn Souk area (X: 24.0 .. 40.0, Y: 6.0 .. 16.0)
    # Market Stalls with fabric awnings
    add_market_stall(col, "T_Souk_Stall_1", (28.0, 10.0, 0.0), (5.5, 4.0, 3.8), mats)
    add_market_stall(col, "T_Souk_Stall_2", (36.0, 10.0, 0.0), (5.5, 4.0, 3.8), mats)

    # Decorative Persian rugs, hung 5 mm proud of real walls. The first used to
    # float in the open souk at x 24.6, where a wall had been before the layout
    # revision, and players walked through it. The second sat inside the perimeter
    # wall (face at y 4.7), so it was never seen.
    add_box(
        col,
        "Persian_Rug_Wall1_NonCol",
        (43.27, 12.0, 2.5),
        (0.05, 3.0, 2.0),
        mats["canopy_crimson"],
    )  # Wall_LongA_West, x 43.3
    add_box(
        col,
        "Persian_Rug_Wall2_NonCol",
        (38.0, 4.73, 2.5),
        (3.0, 0.05, 2.0),
        mats["moorish_tile_blue"],
    )  # Wall_Perimeter_South, y 4.7

    # Terracotta amphoras / water jars. Solid: a 1 m jar is cover, and as NonCol a
    # crouching body hid inside one, and the jar vanished from its own view.
    for ax, ay in [(26.0, 7.0), (26.8, 7.2), (38.0, 7.0), (53.0, 42.0), (28.0, 58.0)]:
        add_cylinder(
            col,
            f"Amphora_{int(ax * 10)}_{int(ay * 10)}",
            (ax, ay, 0.5),
            radius=0.35,
            height=1.0,
            material=mats["sandstone_ochre"],
            segments=12,
        )

    # Palm trees in T courtyard & Long A exterior
    add_palm_tree(col, "T_Palm_1", (22.0, 8.0, 0.0), mats)
    add_palm_tree(col, "T_Palm_2", (40.0, 8.0, 0.0), mats)
    add_palm_tree(col, "LongA_Palm_1", (62.0, 28.0, 0.0), mats)
    add_palm_tree(col, "LongA_Palm_2", (62.0, 44.0, 0.0), mats)

    # No breakable windows. The four this map had (two in the souk, one at each
    # site) stood in open space with no wall around them: free-standing panes of
    # glass. A window needs a wall with an opening; add both together.


def add_wall_sconce(collection, name, pos, facing, mats, radius=6.0, intensity=2.2):
    """An iron bracket and a caged flame on a wall, lighting the stone around it.

    `facing` is the unit (x, y) the wall faces, so the fixture stands off it.
    """
    x, y, z = pos
    fx, fy = facing
    add_box(
        collection,
        f"{name}_bracket_NonCol",
        (x + fx * 0.18, y + fy * 0.18, z - 0.2),
        (0.36 if fx else 0.08, 0.36 if fy else 0.08, 0.08),
        mats["metal_iron_rusted"],
    )
    add_cylinder(
        collection,
        f"{name}_cage_NonCol",
        (x + fx * 0.34, y + fy * 0.34, z),
        radius=0.13,
        height=0.34,
        material=mats["lantern_brass"],
        segments=6,
    )
    add_cylinder(
        collection,
        f"{name}_flame_NonCol",
        (x + fx * 0.34, y + fy * 0.34, z),
        radius=0.07,
        height=0.2,
        material=mats["lantern_flame"],
        segments=6,
    )
    add_light(
        (x + fx * 0.5, y + fy * 0.5, z),
        color=(255, 176, 102),
        radius=radius,
        intensity=intensity,
    )


def build_layout_revision(col, mats):
    """Layout changes over the original blockout.

    - Mid doors are a choke: the lane is 18 m wide and the doors spanned 6 of it,
      so the 9 m beside them was an open street. A wall now closes it.
    - Catwalk can be walked onto from mid (a ramp at its south edge) instead of
      only jumped onto from the Xbox crate or Site A.
    - Cover where a lane had none: a cart in Long A, a planter on the CT side of
      Short, a well in B.
    """
    add_box(
        col,
        "Wall_MidDoors_East",
        (38.6, 32.0, 3.0),
        (9.4, 1.2, 6.0),
        mats["sandstone_light"],
    )
    add_box(
        col,
        "Cornice_MidDoors_East_NonCol",
        (38.6, 32.0, 6.12),
        (9.6, 1.4, 0.24),
        mats["sandstone_ochre"],
    )
    add_wedge(
        col,
        "Catwalk_Ramp",
        (39.5, 39.75, 1.2),
        (5.0, 5.5, 2.4),
        mats["limestone_paving"],
        "+y",
    )

    add_box(
        col, "LongA_Cart_Bed", (60.0, 26.0, 0.75), (2.4, 1.4, 0.9), mats["wood_crate"]
    )
    add_box(
        col,
        "LongA_Cart_Shaft_NonCol",
        (61.9, 26.0, 0.55),
        (1.6, 0.12, 0.1),
        mats["wood_cedar_weathered"],
    )
    for wx in (59.1, 60.9):
        for wy in (25.2, 26.8):
            add_cylinder(
                col,
                f"LongA_Cart_Wheel_{int(wx * 10)}_{int(wy * 10)}_NonCol",
                (wx, wy, 0.35),
                radius=0.35,
                height=0.1,
                material=mats["wood_cedar_weathered"],
                segments=10,
            )

    add_box(
        col, "CT_Planter", (38.0, 53.0, 0.45), (4.0, 0.9, 0.9), mats["sandstone_dark"]
    )
    add_box(
        col,
        "CT_Planter_Soil_NonCol",
        (38.0, 53.0, 0.92),
        (3.7, 0.6, 0.06),
        mats["desert_sand"],
    )

    add_cylinder(
        col,
        "SiteB_Well",
        (11.0, 48.0, 0.5),
        radius=1.1,
        height=1.0,
        material=mats["sandstone_dark"],
        segments=14,
    )
    # The beam clears a standing eye on the rim (1.0 + 1.5 m): at 2.3 m a body
    # standing up on the well passed its eye through it.
    add_box(
        col,
        "SiteB_Well_Beam_NonCol",
        (11.0, 48.0, 2.67),
        (2.6, 0.14, 0.14),
        mats["wood_cedar_weathered"],
    )
    for px in (9.95, 12.05):
        add_box(
            col,
            f"SiteB_Well_Post_{int(px * 100)}",
            (px, 48.0, 1.8),
            (0.14, 0.14, 1.6),
            mats["wood_cedar_weathered"],
        )


def build_dressing(col, mats):
    """Trim and clutter. Merged per material so it costs a handful of nodes."""
    # Plinths: a darker, proud course at the foot of the long walls, which is
    # what stops a 60 m wall reading as one flat plane.
    plinth = []
    for x, y0, y1 in ((44.0, 8.0, 48.0), (26.0, 16.0, 48.0)):
        for side in (-1, 1):
            plinth.append(
                ((x + side * 0.76, (y0 + y1) / 2, 0.25), (0.14, y1 - y0, 0.5))
            )
    for y in (4.0, 66.0):
        plinth.append(
            ((35.0, y + (0.76 if y < 35 else -0.76), 0.25), (62.0, 0.14, 0.5))
        )
    for x in (4.0, 66.0):
        plinth.append(
            ((x + (0.76 if x < 35 else -0.76), 35.0, 0.25), (0.14, 62.0, 0.5))
        )
    add_boxes(col, "Trim_Plinths_NonCol", plinth, mats["sandstone_dark"])

    # Vigas: roof timbers poking through the top of the long walls, every 2 m.
    vigas = []
    for x, y0, y1 in ((44.0, 9.0, 47.0), (26.0, 17.0, 47.0)):
        y = y0
        while y <= y1:
            vigas.append(((x, y, 8.3), (2.2, 0.22, 0.22)))
            y += 2.0
    add_boxes(col, "Trim_Vigas_NonCol", vigas, mats["wood_cedar_weathered"])

    # Awnings over the doorways people fight through.
    add_boxes(
        col,
        "Awning_Doors_NonCol",
        [
            ((52.0, 16.0, 6.5), (6.4, 1.6, 0.06)),
            ((24.5, 57.0, 4.2), (1.4, 4.4, 0.06)),
            ((30.7, 31.1, 5.8), (6.8, 1.2, 0.06)),
        ],
        mats["canopy_saffron"],
    )

    # Hung cloth on the perimeter, alternating colours.
    banners_red, banners_gold = [], []
    for i, x in enumerate(range(10, 64, 9)):
        (banners_red if i % 2 == 0 else banners_gold).append(
            ((x, 65.2, 6.5), (1.2, 0.04, 3.0))
        )
        (banners_gold if i % 2 == 0 else banners_red).append(
            ((x, 4.8, 6.5), (1.2, 0.04, 3.0))
        )
    add_boxes(col, "Banners_Red_NonCol", banners_red, mats["canopy_crimson"])
    add_boxes(col, "Banners_Gold_NonCol", banners_gold, mats["canopy_saffron"])

    # Lines strung across mid, well over head height.
    add_boxes(
        col,
        "Cable_Mid_NonCol",
        [
            ((35.0, y, 7.2 - (i % 2) * 0.4), (18.0, 0.03, 0.03))
            for i, y in enumerate((20.0, 26.0, 38.0, 44.0))
        ],
        mats["metal_iron_rusted"],
    )

    # Rubble and broken paving at wall corners.
    add_boxes(
        col,
        "Rubble_NonCol",
        [
            ((45.1, 9.0, 0.12), (0.5, 0.4, 0.24)),
            ((45.3, 9.6, 0.08), (0.3, 0.3, 0.16)),
            ((27.0, 47.0, 0.1), (0.45, 0.5, 0.2)),
            ((65.0, 64.9, 0.14), (0.6, 0.5, 0.28)),
            ((5.2, 5.4, 0.12), (0.5, 0.5, 0.24)),
            ((21.0, 60.8, 0.1), (0.4, 0.4, 0.2)),
        ],
        mats["sandstone_dark"],
    )


def build_lights(col, mats):
    """Sconces where the sun cannot reach, and the map's sky."""
    # Dark: the roofed tunnel between T and B, lit only by what hangs in it.
    for i, y in enumerate((23.5, 29.0, 35.0, 41.0)):
        add_wall_sconce(col, f"Sconce_Tunnel_W_{i}", (8.7, y, 2.6), (1, 0), mats)
    # The east wall stops at y 38, where the tunnel opens toward Upper B.
    for i, y in enumerate((26.0, 32.0, 37.0)):
        add_wall_sconce(col, f"Sconce_Tunnel_E_{i}", (21.3, y, 2.6), (-1, 0), mats)
    # B: either side of the double doors, and over the exit from Upper Dark.
    add_wall_sconce(col, "Sconce_BDoors_N", (25.3, 59.6, 3.0), (-1, 0), mats)
    add_wall_sconce(col, "Sconce_BDoors_S", (25.3, 54.4, 3.0), (-1, 0), mats)
    add_hanging_lantern(
        col, "Lantern_UpperB", (16.0, 44.0, 4.4), mats, radius=7.0, intensity=2.0
    )
    # CT and the Short A portal.
    add_hanging_lantern(
        col, "Lantern_ShortA", (43.0, 48.0, 6.6), mats, radius=6.0, intensity=1.8
    )
    add_wall_sconce(col, "Sconce_Goose", (57.5, 61.3, 3.2), (0, -1), mats)

    maplib.set_atmosphere("desert_noon")


# ---------------------------------------------------------------------------
# Layout: the map is cut along a few empty lines and the halves spread apart (see
# `cutlayout.py`, and `dust2_layout.py` for the cuts). Every primitive goes through
# the warper; the composites move as one by their anchor point.
# ---------------------------------------------------------------------------
LAYOUT_CUTS = dust2_layout.LAYOUT_CUTS
W = cutlayout.Warper(dust2_layout.CUTS)
_w = W.w

add_box = W.box(add_box)
add_cylinder = W.cylinder(add_cylinder)
add_wedge = W.wedge(maplib.add_wedge)
add_boxes = W.boxes(maplib.add_boxes)
add_light = W.light(maplib.add_light)
add_crate_stack = W.composite(add_crate_stack, 2)
add_palm_tree = W.composite(add_palm_tree, 2)
add_market_stall = W.composite(add_market_stall, 2)
add_hanging_lantern = W.composite(add_hanging_lantern, 2)
add_wall_sconce = W.composite(add_wall_sconce, 2)
add_arch = W.composite(add_arch, 2)
add_crenellations = W.line(add_crenellations, 2, 3)


# Where the clutter gathers, in the authored (pre-cut) frame so each zone stretches
# with the map: (x0, y0, x1, y1), density, vignette weights, lane direction or None.
# A lane gets a little deliberate cover; the rest of the open floor is left alone.
_BUSY = {"crates": 2, "barrels": 2, "jars": 3, "sacks": 3}
_WORK = {"crates": 4, "barrels": 4, "jars": 1, "sacks": 1}
_ZONES = [
    ((20, 5, 44, 17), 1.5, _BUSY, None),  # the T souk
    ((44, 8, 66, 48), 0.6, _WORK, (0.0, 1.0)),  # Long A
    ((26, 16, 44, 48), 0.5, _WORK, (0.0, 1.0)),  # Mid
    (
        (4, 20, 24, 48),
        0.55,
        {"crates": 4, "barrels": 4, "jars": 2, "sacks": 1},
        None,
    ),  # the tunnels
    (
        (42, 46, 66, 66),
        0.9,
        {"crates": 5, "barrels": 3, "jars": 1, "sacks": 2},
        None,
    ),  # A site
    (
        (4, 44, 26, 66),
        0.9,
        {"crates": 4, "barrels": 3, "jars": 2, "sacks": 2},
        None,
    ),  # B site
    (
        (26, 46, 44, 66),
        0.8,
        {"jars": 4, "sacks": 3, "crates": 2, "barrels": 1},
        None,
    ),  # CT
]
_DRESS_SEED = 20261006
# Anything with one of these in its name is a way through: no clutter within 2 m.
_WAYS = ("Arch", "Door", "Portal", "Exit", "Window", "Stairs", "Ramp")


def _place_stalls(plan, rng, col, mats, rect, count, size=(5.5, 4.0, 3.8)):
    """A bazaar: market stalls on open floor in `rect`, each with room to walk round.

    The stall is the existing `add_market_stall`; this chooses where, so a bigger
    souk gets more of them instead of the same two further apart.
    """
    placed = []
    for _ in range(600):
        if len(placed) >= count:
            break
        x = rng.uniform(rect[0] + 3.0, rect[2] - 3.0)
        y = rng.uniform(rect[1] + 3.0, rect[3] - 3.0)
        hx, hy = size[0] / 2 + 1.2, size[1] / 2 + 1.2
        if not plan.rect_free(x - hx, x + hx, y - hy, y + hy):
            continue
        if any(
            abs(x - px) < size[0] + 4.0 and abs(y - py) < size[1] + 4.0
            for px, py in placed
        ):
            continue
        plan.mark_rect(
            plan.placed,
            x - size[0] / 2,
            x + size[0] / 2,
            y - size[1] / 2,
            y + size[1] / 2,
        )
        with W.raw():  # already in final metres: not through the cuts again
            add_market_stall(col, f"Souk_Stall_{len(placed)}", (x, y, 0.0), size, mats)
        placed.append((x, y))
    return len(placed)


def build_natural_dressing(col, mats):
    """Clutter laid out against the finished map rather than typed in.

    Plans on everything already built (so it must run last among the builders),
    keeps clear of spawns, bomb sites, doorways, ramps and the pit, and places
    vignettes where a place gets cluttered: against walls, in corners, and a few
    pieces of cover in the open lanes. See `props.py`.
    """
    import json
    import random

    with open(
        os.path.join(
            maplib.REPO_ROOT, "backend", "modules", "hassault", "maps", "hd_dust2.json"
        ),
        encoding="utf-8",
    ) as f:
        placed = json.load(f)
    plan = W.plan(
        props,
        extent=(4.0, 4.0, 78.0, 82.0),
        domain=(5.0, 77.0, 5.0, 81.0),
        placed=placed,
        ways=_WAYS,
    )
    zone_of = W.zone_of(_ZONES, (0.5, _WORK, None))

    rng = random.Random(_DRESS_SEED)
    stalls = _place_stalls(
        plan,
        rng,
        col,
        mats,
        (_w(18, "x"), _w(5, "y"), _w(46, "x", upper=True), _w(24, "y", upper=True)),
        count=4,
    )
    vignettes = props.dress(plan, rng, zone_of, lane_budget=10)
    counts = props.emit(vignettes, col, mats)
    counts["stalls"] = stalls
    tally = {}
    for name, _ in vignettes:
        tally[name] = tally.get(name, 0) + 1
    print("dressing:", tally, counts)
    if os.environ.get("HASSAULT_DUST2_PLAN"):
        with open(os.environ["HASSAULT_DUST2_PLAN"], "w") as f:
            json.dump(
                [
                    [n, [[p.kind, p.x, p.y, p.size[0], p.size[1], p.yaw] for p in ps]]
                    for n, ps in vignettes
                ],
                f,
            )


def build_dust2_scene():
    clear_scene()
    mats = setup_materials()

    c_terrain = get_or_create_collection("Terrain")
    c_long = get_or_create_collection("LongA")
    c_site_a = get_or_create_collection("SiteA")
    c_catwalk = get_or_create_collection("Catwalk")
    c_mid = get_or_create_collection("Middle")
    c_tunnels = get_or_create_collection("Tunnels")
    c_site_b = get_or_create_collection("SiteB")
    c_props = get_or_create_collection("Props")

    build_dust2_terrain_and_perimeter(c_terrain, mats)
    build_long_a_and_pit(c_long, mats)
    build_site_a(c_site_a, mats)
    build_catwalk_and_short_a(c_catwalk, mats)
    build_middle_and_mid_doors(c_mid, mats)
    build_dark_tunnels(c_tunnels, mats)
    build_site_b(c_site_b, mats)
    build_spawns_and_props(c_props, mats)
    build_layout_revision(get_or_create_collection("Revision"), mats)
    build_dressing(get_or_create_collection("Dressing"), mats)
    build_lights(get_or_create_collection("Lights"), mats)
    build_natural_dressing(
        get_or_create_collection("Clutter"), mats
    )  # last: it plans on all of the above

    print("=== Desert Citadel II (hd_dust2) Built Successfully! ===")
    if os.environ.get("HASSAULT_DUST2_SCAN"):
        W.dump_scan(os.environ["HASSAULT_DUST2_SCAN"])


def export_glb():
    """Scale the metre-authored scene to cubes and export it (see maplib)."""
    maplib.export_map_glb("hd_dust2")


if __name__ == "__main__":
    build_dust2_scene()
    export_glb()
