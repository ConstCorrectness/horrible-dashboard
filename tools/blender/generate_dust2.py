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
import shutil

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: generate_dust2.py must be run from within Blender (e.g. `blender --background --python ...`)")
    sys.exit(1)


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


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.7, emission_color=None, emission_strength=0.0, alpha=1.0, bump_strength=0.0, bump_scale=24.0):
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
        mat.blend_method = 'BLEND'

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
    mats["sandstone_light"] = create_pbr_material("mat_sandstone_light", (0.84, 0.74, 0.58), metallic=0.02, roughness=0.85, bump_strength=0.15, bump_scale=18.0)
    mats["sandstone_ochre"] = create_pbr_material("mat_sandstone_ochre", (0.76, 0.58, 0.40), metallic=0.02, roughness=0.90, bump_strength=0.20, bump_scale=15.0)
    mats["sandstone_dark"] = create_pbr_material("mat_sandstone_dark", (0.58, 0.46, 0.35), metallic=0.02, roughness=0.92, bump_strength=0.22, bump_scale=20.0)
    mats["limestone_paving"] = create_pbr_material("mat_limestone_paving", (0.78, 0.72, 0.62), metallic=0.01, roughness=0.80, bump_strength=0.18, bump_scale=25.0)
    mats["desert_sand"] = create_pbr_material("mat_desert_sand", (0.88, 0.76, 0.54), metallic=0.0, roughness=0.95, bump_strength=0.08, bump_scale=32.0)

    # Moroccan architectural accents
    mats["moorish_tile_blue"] = create_pbr_material("mat_moorish_tile_blue", (0.12, 0.35, 0.58), metallic=0.10, roughness=0.25, bump_strength=0.05, bump_scale=40.0)
    mats["moorish_tile_gold"] = create_pbr_material("mat_moorish_tile_gold", (0.82, 0.62, 0.18), metallic=0.15, roughness=0.30, bump_strength=0.05, bump_scale=40.0)
    mats["wood_cedar_weathered"] = create_pbr_material("mat_wood_cedar_weathered", (0.38, 0.28, 0.20), metallic=0.0, roughness=0.75, bump_strength=0.30, bump_scale=12.0)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.52, 0.38, 0.24), metallic=0.0, roughness=0.68, bump_strength=0.25, bump_scale=14.0)
    mats["metal_iron_rusted"] = create_pbr_material("mat_metal_iron_rusted", (0.25, 0.20, 0.18), metallic=0.80, roughness=0.55, bump_strength=0.20, bump_scale=28.0)
    mats["metal_scaffolding"] = create_pbr_material("mat_metal_scaffolding", (0.45, 0.45, 0.48), metallic=0.85, roughness=0.35, bump_strength=0.08, bump_scale=30.0)

    # Fabrics & foliage
    mats["canopy_crimson"] = create_pbr_material("mat_canopy_crimson", (0.58, 0.12, 0.14), metallic=0.0, roughness=0.88, bump_strength=0.12, bump_scale=35.0)
    mats["canopy_saffron"] = create_pbr_material("mat_canopy_saffron", (0.85, 0.55, 0.10), metallic=0.0, roughness=0.88, bump_strength=0.12, bump_scale=35.0)
    mats["palm_bark"] = create_pbr_material("mat_palm_bark", (0.30, 0.22, 0.16), metallic=0.0, roughness=0.95, bump_strength=0.35, bump_scale=8.0)
    mats["palm_fronds"] = create_pbr_material("mat_palm_fronds", (0.18, 0.38, 0.12), metallic=0.0, roughness=0.70)

    # Lanterns & lighting
    mats["lantern_brass"] = create_pbr_material("mat_lantern_brass", (0.75, 0.60, 0.22), metallic=0.88, roughness=0.25)
    mats["lantern_flame"] = create_pbr_material("mat_lantern_flame", (1.0, 0.70, 0.30), metallic=0.0, roughness=0.10, emission_color=(1.0, 0.70, 0.30), emission_strength=5.0)

    # Breakable glass windows
    mats["glass_window"] = create_pbr_material("mat_glass_window", (0.82, 0.92, 0.98), metallic=0.05, roughness=0.06, alpha=0.35)

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
        depth=height
    )
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_arch(collection, name, center, span, height, depth, material, segments=8):
    """Adds a detailed Moorish horseshoe arched portal cutout."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    pillar_w = 0.8
    pillar_h = height - span * 0.45
    
    # Left pillar with base plinth and carved capital
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((pillar_w, depth, pillar_h)), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((-span * 0.5 - pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=bm.verts)

    # Left base plinth
    plinth_l = bmesh.new()
    bmesh.ops.create_cube(plinth_l, size=1.0)
    bmesh.ops.scale(plinth_l, vec=Vector((pillar_w * 1.25, depth * 1.2, 0.4)), verts=plinth_l.verts)
    bmesh.ops.translate(plinth_l, vec=Vector((-span * 0.5 - pillar_w * 0.5, 0.0, 0.2)), verts=plinth_l.verts)
    for v in plinth_l.verts:
        bm.verts.new(v.co)
    plinth_l.free()

    # Right pillar
    r_bm = bmesh.new()
    bmesh.ops.create_cube(r_bm, size=1.0)
    bmesh.ops.scale(r_bm, vec=Vector((pillar_w, depth, pillar_h)), verts=r_bm.verts)
    bmesh.ops.translate(r_bm, vec=Vector((span * 0.5 + pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=r_bm.verts)
    for v in r_bm.verts:
        bm.verts.new(v.co)
    r_bm.free()

    # Right base plinth
    plinth_r = bmesh.new()
    bmesh.ops.create_cube(plinth_r, size=1.0)
    bmesh.ops.scale(plinth_r, vec=Vector((pillar_w * 1.25, depth * 1.2, 0.4)), verts=plinth_r.verts)
    bmesh.ops.translate(plinth_r, vec=Vector((span * 0.5 + pillar_w * 0.5, 0.0, 0.2)), verts=plinth_r.verts)
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
        bmesh.ops.scale(seg_bm, vec=Vector((seg_len, depth, seg_thick)), verts=seg_bm.verts)
        ang = math.atan2(dz, dx)
        rot_mat = Matrix.Rotation(ang, 4, 'Y')
        bmesh.ops.transform(seg_bm, matrix=rot_mat, verts=seg_bm.verts)
        bmesh.ops.translate(seg_bm, vec=Vector(((x0 + x1) * 0.5, 0.0, pillar_h + (z0 + z1) * 0.5)), verts=seg_bm.verts)
        for v in seg_bm.verts:
            bm.verts.new(v.co)
        seg_bm.free()

    # Top lintel and cornice
    top_bm = bmesh.new()
    bmesh.ops.create_cube(top_bm, size=1.0)
    bmesh.ops.scale(top_bm, vec=Vector((span + pillar_w * 2.6, depth * 1.1, 0.7)), verts=top_bm.verts)
    bmesh.ops.translate(top_bm, vec=Vector((0.0, 0.0, height + 0.6)), verts=top_bm.verts)
    for v in top_bm.verts:
        bm.verts.new(v.co)
    top_bm.free()

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
    add_box(collection, f"{prefix}_crate_base", (bx, by, bz + 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    # Iron corner brackets
    add_box(collection, f"{prefix}_bracket_h_NonCol", (bx, by, bz + 0.6), (1.24, 0.08, 1.24), mats["metal_iron_rusted"])
    add_box(collection, f"{prefix}_bracket_v_NonCol", (bx, by, bz + 0.6), (0.08, 1.24, 1.24), mats["metal_iron_rusted"])
    # Cross brace detail
    add_box(collection, f"{prefix}_xbrace_NonCol", (bx + 0.61, by, bz + 0.6), (0.02, 1.0, 0.12), mats["wood_cedar_weathered"])
    add_box(collection, f"{prefix}_xbrace2_NonCol", (bx - 0.61, by, bz + 0.6), (0.02, 1.0, 0.12), mats["wood_cedar_weathered"])


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
        add_cylinder(collection, f"{name}_trunk_{s}", (curr_x, curr_y, curr_z + h * 0.5), radius=rad, height=h, material=mats["palm_bark"], segments=10)
        # Bark ring collar
        add_cylinder(collection, f"{name}_ring_{s}_NonCol", (curr_x, curr_y, curr_z + h), radius=rad * 1.12, height=0.12, material=mats["palm_bark"], segments=10)
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
            add_box(collection, f"{name}_frond_{tier}_{i}_NonCol", (fx, fy, fz), (dist, 0.45, 0.05), mats["palm_fronds"])


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
            add_box(collection, f"{name}_cren_{i}", (cx, cy, sz + height * 0.5), (size_x, size_y, height), mats["sandstone_ochre"])


def add_hanging_lantern(collection, name, ceiling_pos, mats):
    """Ornate brass Moroccan lantern with chain link and glowing flame core."""
    cx, cy, cz = ceiling_pos
    # Iron chain
    add_cylinder(collection, f"{name}_chain_NonCol", (cx, cy, cz - 0.4), radius=0.03, height=0.8, material=mats["metal_iron_rusted"], segments=6)
    # Brass cap
    add_cylinder(collection, f"{name}_cap_NonCol", (cx, cy, cz - 0.82), radius=0.18, height=0.08, material=mats["lantern_brass"], segments=8)
    # 8-sided glazed lantern body
    add_cylinder(collection, f"{name}_body_NonCol", (cx, cy, cz - 1.05), radius=0.22, height=0.42, material=mats["lantern_brass"], segments=8)
    # Emissive flame core
    add_cylinder(collection, f"{name}_flame_NonCol", (cx, cy, cz - 1.05), radius=0.10, height=0.25, material=mats["lantern_flame"], segments=6)
    # Bottom finial point
    add_cylinder(collection, f"{name}_finial_NonCol", (cx, cy, cz - 1.30), radius=0.06, height=0.12, material=mats["lantern_brass"], segments=6)


def add_market_stall(collection, name, center, size, mats):
    """Detailed market stall with cedar timber framework, woven striped canopy, and goods."""
    cx, cy, cz = center
    sx, sy, sz = size
    # 4 Corner Timber Posts
    for dx in [-1, 1]:
        for dy in [-1, 1]:
            px = cx + dx * (sx * 0.5 - 0.1)
            py = cy + dy * (sy * 0.5 - 0.1)
            add_cylinder(collection, f"{name}_post_{dx}_{dy}", (px, py, cz + sz * 0.5), radius=0.07, height=sz, material=mats["wood_cedar_weathered"], segments=8)
    # Perimeter roof beams
    add_box(collection, f"{name}_beam_n_NonCol", (cx, cy + sy * 0.5 - 0.1, cz + sz - 0.06), (sx, 0.12, 0.12), mats["wood_cedar_weathered"])
    add_box(collection, f"{name}_beam_s_NonCol", (cx, cy - sy * 0.5 + 0.1, cz + sz - 0.06), (sx, 0.12, 0.12), mats["wood_cedar_weathered"])
    # Slanted Striped Fabric Canopy
    add_box(collection, f"{name}_canopy_red_NonCol", (cx, cy, cz + sz + 0.05), (sx * 1.08, sy * 0.5, 0.04), mats["canopy_crimson"])
    add_box(collection, f"{name}_canopy_gold_NonCol", (cx, cy, cz + sz + 0.05), (sx * 1.08, sy * 0.5, 0.04), mats["canopy_saffron"])
    # Display Table
    add_box(collection, f"{name}_table", (cx, cy, cz + 0.45), (sx * 0.85, sy * 0.75, 0.9), mats["wood_crate"])
    # Amphoras / Produce on table
    add_cylinder(collection, f"{name}_amphora1_NonCol", (cx - sx * 0.25, cy, cz + 1.15), radius=0.18, height=0.5, material=mats["sandstone_ochre"], segments=10)
    add_cylinder(collection, f"{name}_amphora2_NonCol", (cx + sx * 0.25, cy, cz + 1.15), radius=0.18, height=0.5, material=mats["sandstone_dark"], segments=10)


def build_dust2_terrain_and_perimeter(col, mats):
    """Main ground terrain, desert sand bed, perimeter walls, and crenellated battlements."""
    # Ground paving
    add_box(col, "Terrain_Ground", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["limestone_paving"])
    # Outer sand dunes
    add_box(col, "Dune_Perimeter_East", (68.0, 35.0, 0.5), (8.0, 70.0, 3.0), mats["desert_sand"])
    add_box(col, "Dune_Perimeter_South", (35.0, 2.0, 0.5), (70.0, 8.0, 3.0), mats["desert_sand"])
    add_box(col, "Dune_Perimeter_West", (1.0, 35.0, 0.5), (6.0, 70.0, 3.0), mats["desert_sand"])

    # High Sandstone Fortress Perimeter Walls (height 10.0m)
    add_box(col, "Wall_Perimeter_South", (35.0, 4.0, 5.0), (62.0, 1.4, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_North", (35.0, 66.0, 5.0), (62.0, 1.4, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_East", (66.0, 35.0, 5.0), (1.4, 62.0, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_West", (4.0, 35.0, 5.0), (1.4, 62.0, 10.0), mats["sandstone_ochre"])

    # Wall Copings & Cornices (top molding)
    add_box(col, "Cornice_Perimeter_S_NonCol", (35.0, 4.0, 10.15), (62.8, 1.65, 0.3), mats["sandstone_light"])
    add_box(col, "Cornice_Perimeter_N_NonCol", (35.0, 66.0, 10.15), (62.8, 1.65, 0.3), mats["sandstone_light"])
    add_box(col, "Cornice_Perimeter_E_NonCol", (66.0, 35.0, 10.15), (1.65, 62.8, 0.3), mats["sandstone_light"])
    add_box(col, "Cornice_Perimeter_W_NonCol", (4.0, 35.0, 10.15), (1.65, 62.8, 0.3), mats["sandstone_light"])

    # Crenellated battlements along South & North fortress walls
    add_crenellations(col, "Crenell_South", (6.0, 4.0, 10.3), (64.0, 4.0, 10.3), 0.8, mats)
    add_crenellations(col, "Crenell_North", (6.0, 66.0, 10.3), (64.0, 66.0, 10.3), 0.8, mats)

    # Moorish blue & gold glazed tile decorative mosaic frieze
    add_box(col, "Frieze_Perimeter_N_NonCol", (35.0, 65.25, 9.4), (62.0, 0.1, 0.45), mats["moorish_tile_blue"])
    add_box(col, "Frieze_Gold_N_NonCol", (35.0, 65.24, 9.15), (62.0, 0.08, 0.12), mats["moorish_tile_gold"])
    add_box(col, "Frieze_Perimeter_S_NonCol", (35.0, 4.75, 9.4), (62.0, 0.1, 0.45), mats["moorish_tile_blue"])
    add_box(col, "Frieze_Gold_S_NonCol", (35.0, 4.76, 9.15), (62.0, 0.08, 0.12), mats["moorish_tile_gold"])


def build_long_a_and_pit(col, mats):
    """Long A corridor, sunken Pit, corner, and Long A Archway."""
    # Long A dividing west wall
    add_box(col, "Wall_LongA_West", (44.0, 28.0, 4.5), (1.4, 40.0, 9.0), mats["sandstone_light"])
    add_box(col, "Cornice_LongA_West_NonCol", (44.0, 28.0, 9.15), (1.65, 40.2, 0.3), mats["sandstone_ochre"])

    # Long A Double Sandstone Archway (T entrance into Long at Y: 16)
    add_arch(col, "Arch_LongA_Doors", (52.0, 16.0, 0.0), span=4.4, height=5.2, depth=1.8, material=mats["sandstone_ochre"])
    add_hanging_lantern(col, "Lantern_LongA_Doors", (52.0, 16.0, 5.0), mats)

    # Sunken Pit: floor at Z = -1.4m from Y: 6.0 to Y: 18.0, X: 52.0 to 65.0
    add_box(col, "Pit_Floor", (58.5, 12.0, -0.7), (13.0, 12.0, 1.4), mats["limestone_paving"])
    # Pit retaining wall with stone coping curb
    add_box(col, "Pit_Curb_West", (50.5, 12.0, 0.4), (1.0, 12.0, 0.8), mats["sandstone_dark"])
    add_box(col, "Pit_Curb_Cap_NonCol", (50.5, 12.0, 0.85), (1.2, 12.2, 0.12), mats["sandstone_light"])
    # Stepped Ramp out of Pit
    add_box(col, "Pit_Ramp", (54.0, 19.0, -0.4), (4.5, 4.0, 0.8), mats["limestone_paving"])

    # Abandoned rusted vehicle barricade in Pit
    add_box(col, "Pit_Car_Chassis", (58.0, 12.0, -0.2), (4.2, 2.0, 1.0), mats["metal_iron_rusted"])
    add_box(col, "Pit_Car_Cabin", (57.5, 12.0, 0.7), (2.2, 1.8, 0.9), mats["sandstone_dark"])
    for wx, wy in [(56.5, 10.9), (59.5, 10.9), (56.5, 13.1), (59.5, 13.1)]:
        add_cylinder(col, f"Pit_Car_Wheel_{int(wx*10)}_{int(wy*10)}", (wx, wy, -0.4), radius=0.42, height=0.3, material=mats["metal_iron_rusted"], segments=12)

    # Long A Corner: wall protrusion, barrels, and crate cache
    add_box(col, "Wall_LongA_Corner", (60.0, 36.0, 4.5), (10.0, 1.4, 9.0), mats["sandstone_light"])
    for i, by in enumerate([33.5, 34.8, 36.0]):
        add_cylinder(col, f"LongA_Barrel_{i}", (54.2, by, 0.6), radius=0.45, height=1.2, material=mats["wood_cedar_weathered"])
        # Metal hoops on barrel
        add_cylinder(col, f"LongA_Barrel_Hoop1_{i}_NonCol", (54.2, by, 0.35), radius=0.46, height=0.06, material=mats["metal_iron_rusted"], segments=12)
        add_cylinder(col, f"LongA_Barrel_Hoop2_{i}_NonCol", (54.2, by, 0.85), radius=0.46, height=0.06, material=mats["metal_iron_rusted"], segments=12)

    add_crate_stack(col, "LongA_Corner", (54.5, 38.0, 0.0), mats)

    # Long A Ramp ascending to Site A
    add_box(col, "LongA_Ramp", (52.0, 46.0, 0.6), (8.5, 8.5, 1.2), mats["limestone_paving"])


def build_site_a(col, mats):
    """Bomb Site A elevated plateau (+1.2m), double boxes, goose corner, and CT connection."""
    # Elevated Site A Plateau
    add_box(col, "SiteA_Plateau", (50.0, 55.0, 0.6), (16.0, 14.0, 1.2), mats["limestone_paving"])
    # Stone retaining edge around plateau
    add_box(col, "SiteA_Edge_West_NonCol", (41.9, 55.0, 0.6), (0.2, 14.0, 1.25), mats["sandstone_dark"])
    add_box(col, "SiteA_Edge_South_NonCol", (50.0, 47.9, 0.6), (16.0, 0.2, 1.25), mats["sandstone_dark"])

    # "Goose" Corner Wall (North-East corner of A)
    add_box(col, "SiteA_Goose_Wall", (58.0, 62.0, 3.8), (14.0, 1.4, 5.6), mats["sandstone_ochre"])
    add_box(col, "Cornice_Goose_NonCol", (58.0, 62.0, 6.7), (14.2, 1.6, 0.25), mats["sandstone_light"])
    # Goose graffiti symbol plaque
    add_box(col, "SiteA_Goose_Symbol_NonCol", (54.0, 61.25, 2.5), (1.5, 0.05, 1.5), mats["moorish_tile_blue"])

    # Iconic Double Wooden Crates (Default Plant Box) at (46.0, 52.0)
    add_crate_stack(col, "SiteA_Plant_Lower1", (46.0, 52.0, 1.2), mats)
    add_crate_stack(col, "SiteA_Plant_Lower2", (46.0, 53.4, 1.2), mats)
    add_crate_stack(col, "SiteA_Plant_Upper", (46.0, 52.0, 2.4), mats)

    # Triple stack near ramp
    add_crate_stack(col, "SiteA_Ramp_Stack1", (52.0, 56.0, 1.2), mats)
    add_crate_stack(col, "SiteA_Ramp_Stack2", (52.0, 57.4, 1.2), mats)

    # CT Spawn Ramp connection down from Site A to CT street level
    add_box(col, "SiteA_CT_Ramp", (44.0, 58.0, 0.6), (6.0, 6.0, 1.2), mats["limestone_paving"])


def build_catwalk_and_short_a(col, mats):
    """Elevated Catwalk (+2.4m) running from Mid to Site A with wooden pergola and wrought-iron railings."""
    # Catwalk bridge / ledge (X: 34.0 .. 44.0, Y: 42.0 .. 48.0, Z = +2.4m)
    add_box(col, "Catwalk_Floor", (39.0, 45.0, 1.2), (10.0, 5.0, 2.4), mats["sandstone_light"])

    # Timber Pergola with overhead shade beams across Catwalk
    for px in [35.5, 38.5, 41.5]:
        add_cylinder(col, f"Pergola_Post_{int(px*10)}_NonCol", (px, 42.6, 3.8), radius=0.08, height=2.8, material=mats["wood_cedar_weathered"], segments=8)
        # Overhead cross beam
        add_box(col, f"Pergola_Rafter_{int(px*10)}_NonCol", (px, 45.0, 5.25), (0.12, 5.2, 0.16), mats["wood_cedar_weathered"])

    # Short A Wall with archway entering Site A
    add_arch(col, "ShortA_Portal", (43.0, 48.0, 2.4), span=3.4, height=4.0, depth=1.4, material=mats["sandstone_ochre"])

    # Wrought iron guard railing along edge of Catwalk looking into Mid
    add_box(col, "Catwalk_Railing_Mid", (34.0, 45.0, 2.95), (0.1, 5.0, 1.1), mats["metal_iron_rusted"])
    # Railing pickets
    for ry in range(43, 48):
        add_cylinder(col, f"Catwalk_Picket_{ry}_NonCol", (34.0, ry + 0.5, 2.95), radius=0.02, height=1.0, material=mats["metal_iron_rusted"], segments=6)

    # Stairs descending from Catwalk to Lower Dark Tunnel
    add_box(col, "Stairs_Catwalk_LowerDark", (30.0, 42.0, 1.2), (4.5, 3.2, 2.4), mats["limestone_paving"])


def build_middle_and_mid_doors(col, mats):
    """Middle corridor, iconic Mid Double Doors with sniper slit, and Xbox crate."""
    # Mid corridor canyon walls
    add_box(col, "Wall_Mid_West", (26.0, 32.0, 4.5), (1.4, 32.0, 9.0), mats["sandstone_light"])
    add_box(col, "Cornice_Mid_West_NonCol", (26.0, 32.0, 9.15), (1.65, 32.2, 0.3), mats["sandstone_ochre"])

    # Mid Double Doors: Heavy cedar wooden doors with iron strap hinges and forged rings
    # Left Door swung slightly forward
    add_box(col, "Mid_Door_Left", (29.1, 32.2, 2.2), (2.0, 0.28, 4.4), mats["wood_cedar_weathered"])
    # Iron hinge straps & studs on Left door
    for hz in [1.0, 3.4]:
        add_box(col, f"Mid_Door_L_Hinge_{int(hz*10)}_NonCol", (28.8, 32.35, hz), (1.5, 0.04, 0.12), mats["metal_iron_rusted"])
    # Right Door swung slightly back, leaving 0.45m center sniper slit!
    add_box(col, "Mid_Door_Right", (32.3, 31.8, 2.2), (2.0, 0.28, 4.4), mats["wood_cedar_weathered"])
    for hz in [1.0, 3.4]:
        add_box(col, f"Mid_Door_R_Hinge_{int(hz*10)}_NonCol", (32.6, 31.65, hz), (1.5, 0.04, 0.12), mats["metal_iron_rusted"])

    # Arch lintel and stone voussoirs above doors
    add_box(col, "Mid_Door_Arch_Lintel", (30.7, 32.0, 4.9), (6.4, 1.4, 1.4), mats["sandstone_ochre"])
    add_hanging_lantern(col, "Lantern_Mid_Doors", (30.7, 32.0, 4.2), mats)

    # Xbox Jump Crate in Mid (X: 30.0, Y: 38.0): 1.6m cube wood box for Catwalk boost
    add_box(col, "Xbox_Crate_Body", (30.0, 38.0, 0.8), (1.6, 1.6, 1.6), mats["wood_crate"])
    add_box(col, "Xbox_Crate_Brackets_NonCol", (30.0, 38.0, 0.8), (1.64, 0.15, 1.64), mats["metal_iron_rusted"])

    # Suicide Ramp from T Spawn leading down into Mid
    add_box(col, "Mid_Suicide_Ramp", (31.0, 20.0, 0.6), (5.5, 8.5, 1.2), mats["limestone_paving"])


def build_dark_tunnels(col, mats):
    """Subterranean vaulted Dark Tunnels (Upper Dark and Lower Dark) with vaulted stone arches and lanterns."""
    # Tunnel ceiling slab at Z = 4.2m enclosing the darkness
    add_box(col, "Tunnel_Roof_Slab", (16.0, 34.0, 4.4), (18.5, 24.5, 0.8), mats["sandstone_dark"])

    # Tunnel interior walls
    add_box(col, "Tunnel_Wall_Outer_West", (8.0, 34.0, 2.1), (1.4, 24.0, 4.2), mats["sandstone_dark"])
    add_box(col, "Tunnel_Wall_Inner_East", (22.0, 30.0, 2.1), (1.4, 16.0, 4.2), mats["sandstone_dark"])

    # Tunnel decorative stone arches every 6 meters with hanging lanterns
    for ty in [26.0, 32.0, 38.0]:
        add_arch(col, f"Tunnel_Arch_{int(ty)}", (15.0, ty, 0.0), span=4.8, height=3.8, depth=1.2, material=mats["sandstone_ochre"])
        add_hanging_lantern(col, f"Lantern_Tunnel_{int(ty)}", (15.0, ty, 4.1), mats)


def build_site_b(col, mats):
    """Bomb Site B fortress courtyard, Upper B exit, B Window, B Doors, and Back Platform."""
    # B Site Fortress Perimeter Walls (height 8.0m)
    add_box(col, "SiteB_North_Wall", (16.0, 62.0, 4.2), (22.0, 1.4, 8.4), mats["sandstone_ochre"])
    add_box(col, "SiteB_West_Wall", (6.0, 52.0, 4.2), (1.4, 20.0, 8.4), mats["sandstone_ochre"])
    add_box(col, "SiteB_East_Wall", (26.0, 54.0, 4.2), (1.4, 16.0, 8.4), mats["sandstone_ochre"])

    # Wall copings on B walls
    add_box(col, "Cornice_SiteB_N_NonCol", (16.0, 62.0, 8.55), (22.4, 1.65, 0.3), mats["sandstone_light"])
    add_crenellations(col, "Crenell_SiteB_N", (6.0, 62.0, 8.7), (26.0, 62.0, 8.7), 0.8, mats)

    # Raised Bomb Plant Platform (X: 12.0 .. 20.0, Y: 50.0 .. 58.0, Z = +0.6m)
    add_box(col, "SiteB_Plant_Platform", (16.0, 54.0, 0.3), (8.5, 8.5, 0.6), mats["limestone_paving"])
    add_crate_stack(col, "SiteB_Plant_Box1", (15.0, 53.0, 0.6), mats)
    add_crate_stack(col, "SiteB_Plant_Box2", (16.4, 53.0, 0.6), mats)

    # Upper B Tunnel exit doorway at (16.0, 44.0) with raised lip
    add_arch(col, "SiteB_UpperDark_Exit", (16.0, 44.0, 0.0), span=3.8, height=4.0, depth=1.4, material=mats["sandstone_ochre"])

    # B Window opening overlooking tunnels at (22.0, 46.0, Z: 2.0)
    add_box(col, "SiteB_Window_Wall", (22.0, 46.0, 1.0), (1.4, 4.2, 2.0), mats["sandstone_light"])
    add_box(col, "SiteB_Window_Top", (22.0, 46.0, 4.6), (1.4, 4.2, 3.2), mats["sandstone_light"])

    # Scaffolding and jumping crates next to window
    add_crate_stack(col, "SiteB_Window", (20.5, 46.0, 0.0), mats)
    add_box(col, "SiteB_Scaffold_Pole1", (23.5, 44.5, 2.5), (0.1, 0.1, 5.0), mats["metal_scaffolding"])
    add_box(col, "SiteB_Scaffold_Pole2", (23.5, 47.5, 2.5), (0.1, 0.1, 5.0), mats["metal_scaffolding"])
    add_box(col, "SiteB_Scaffold_Plank", (23.5, 46.0, 2.2), (1.0, 3.2, 0.1), mats["wood_cedar_weathered"])

    # B Double Doors connecting B Site to CT street
    add_box(col, "SiteB_Door_Left", (24.5, 56.0, 1.9), (0.22, 1.8, 3.8), mats["wood_cedar_weathered"])
    add_box(col, "SiteB_Door_Right", (24.5, 58.0, 1.9), (0.22, 1.8, 3.8), mats["wood_cedar_weathered"])

    # Back Platform & Wooden Crates
    add_box(col, "SiteB_Back_Platform", (9.0, 56.0, 0.5), (4.5, 8.5, 1.0), mats["limestone_paving"])
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

    # Decorative Persian rugs hung against sandstone walls
    add_box(col, "Persian_Rug_Wall1_NonCol", (24.6, 12.0, 2.5), (0.05, 3.0, 2.0), mats["canopy_crimson"])
    add_box(col, "Persian_Rug_Wall2_NonCol", (38.0, 4.6, 2.5), (3.0, 0.05, 2.0), mats["moorish_tile_blue"])

    # Terracotta amphoras / water jars
    for ax, ay in [(26.0, 7.0), (26.8, 7.2), (38.0, 7.0), (53.0, 42.0), (28.0, 58.0)]:
        add_cylinder(col, f"Amphora_{int(ax*10)}_{int(ay*10)}_NonCol", (ax, ay, 0.5), radius=0.35, height=1.0, material=mats["sandstone_ochre"], segments=12)

    # Palm trees in T courtyard & Long A exterior
    add_palm_tree(col, "T_Palm_1", (22.0, 8.0, 0.0), mats)
    add_palm_tree(col, "T_Palm_2", (40.0, 8.0, 0.0), mats)
    add_palm_tree(col, "LongA_Palm_1", (62.0, 28.0, 0.0), mats)
    add_palm_tree(col, "LongA_Palm_2", (62.0, 44.0, 0.0), mats)

    # Breakable windows overlooking key sightlines (tactical glass penetration)
    add_box(col, "Window_Glass_Courtyard_1", (24.0, 14.0, 3.5), (0.12, 2.2, 1.4), mats["glass_window"])
    add_box(col, "Window_Glass_Courtyard_2", (40.0, 14.0, 3.5), (0.12, 2.2, 1.4), mats["glass_window"])
    add_box(col, "Window_Glass_A_Site", (54.0, 52.0, 3.2), (0.12, 2.0, 1.4), mats["glass_window"])
    add_box(col, "Window_Glass_B_Site", (14.0, 56.0, 3.2), (0.12, 2.0, 1.4), mats["glass_window"])


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

    print("=== Desert Citadel II (hd_dust2) Built Successfully! ===")


def export_glb():
    backend_map_dir = "/home/horrible/horrible-dashboard/backend/modules/hassault/maps"
    web_public_dir = "/home/horrible/horrible-dashboard/apps/web/public"

    os.makedirs(backend_map_dir, exist_ok=True)
    os.makedirs(web_public_dir, exist_ok=True)

    backend_glb_path = os.path.join(backend_map_dir, "hd_dust2.glb")
    web_glb_path = os.path.join(web_public_dir, "hd_dust2.glb")

    print(f"Exporting GLB to: {backend_glb_path} ...")
    bpy.ops.export_scene.gltf(
        filepath=backend_glb_path,
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials='EXPORT',
        export_lights=False,
        export_cameras=False
    )

    print(f"Copying GLB to Web: {web_glb_path} ...")
    shutil.copyfile(backend_glb_path, web_glb_path)
    print("=== Dust II Generation & Export Complete! ===")


if __name__ == "__main__":
    build_dust2_scene()
    export_glb()
