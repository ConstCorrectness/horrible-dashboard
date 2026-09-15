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


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.7, emission_color=None, emission_strength=0.0, alpha=1.0):
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

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    mats = {}
    # Sun-bleached limestone & ochre sandstone
    mats["sandstone_light"] = create_pbr_material("mat_sandstone_light", (0.84, 0.74, 0.58), metallic=0.02, roughness=0.85)
    mats["sandstone_ochre"] = create_pbr_material("mat_sandstone_ochre", (0.76, 0.58, 0.40), metallic=0.02, roughness=0.90)
    mats["sandstone_dark"] = create_pbr_material("mat_sandstone_dark", (0.58, 0.46, 0.35), metallic=0.02, roughness=0.92)
    mats["limestone_paving"] = create_pbr_material("mat_limestone_paving", (0.78, 0.72, 0.62), metallic=0.01, roughness=0.80)
    mats["desert_sand"] = create_pbr_material("mat_desert_sand", (0.88, 0.76, 0.54), metallic=0.0, roughness=0.95)

    # Moroccan architectural accents
    mats["moorish_tile_blue"] = create_pbr_material("mat_moorish_tile_blue", (0.12, 0.35, 0.58), metallic=0.10, roughness=0.25)
    mats["moorish_tile_gold"] = create_pbr_material("mat_moorish_tile_gold", (0.82, 0.62, 0.18), metallic=0.15, roughness=0.30)
    mats["wood_cedar_weathered"] = create_pbr_material("mat_wood_cedar_weathered", (0.38, 0.28, 0.20), metallic=0.0, roughness=0.75)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.52, 0.38, 0.24), metallic=0.0, roughness=0.68)
    mats["metal_iron_rusted"] = create_pbr_material("mat_metal_iron_rusted", (0.25, 0.20, 0.18), metallic=0.80, roughness=0.55)
    mats["metal_scaffolding"] = create_pbr_material("mat_metal_scaffolding", (0.45, 0.45, 0.48), metallic=0.85, roughness=0.35)

    # Fabrics & foliage
    mats["canopy_crimson"] = create_pbr_material("mat_canopy_crimson", (0.58, 0.12, 0.14), metallic=0.0, roughness=0.88)
    mats["canopy_saffron"] = create_pbr_material("mat_canopy_saffron", (0.85, 0.55, 0.10), metallic=0.0, roughness=0.88)
    mats["palm_bark"] = create_pbr_material("mat_palm_bark", (0.30, 0.22, 0.16), metallic=0.0, roughness=0.95)
    mats["palm_fronds"] = create_pbr_material("mat_palm_fronds", (0.18, 0.38, 0.12), metallic=0.0, roughness=0.70)

    # Lanterns & lighting
    mats["lantern_brass"] = create_pbr_material("mat_lantern_brass", (0.75, 0.60, 0.22), metallic=0.88, roughness=0.25)
    mats["lantern_flame"] = create_pbr_material("mat_lantern_flame", (1.0, 0.70, 0.30), metallic=0.0, roughness=0.10, emission_color=(1.0, 0.70, 0.30), emission_strength=5.0)

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
    """Adds a Moorish-style arched portal cutout."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    # Left pillar
    pillar_w = 0.8
    pillar_h = height - span * 0.5
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((pillar_w, depth, pillar_h)), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((-span * 0.5 + pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=bm.verts)

    # Right pillar
    r_bm = bmesh.new()
    bmesh.ops.create_cube(r_bm, size=1.0)
    bmesh.ops.scale(r_bm, vec=Vector((pillar_w, depth, pillar_h)), verts=r_bm.verts)
    bmesh.ops.translate(r_bm, vec=Vector((span * 0.5 - pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=r_bm.verts)
    for v in r_bm.verts:
        bm.verts.new(v.co)
    r_bm.free()

    # Lintel top beam
    top_bm = bmesh.new()
    bmesh.ops.create_cube(top_bm, size=1.0)
    bmesh.ops.scale(top_bm, vec=Vector((span + pillar_w * 2.0, depth, 0.8)), verts=top_bm.verts)
    bmesh.ops.translate(top_bm, vec=Vector((0.0, 0.0, height + 0.4)), verts=top_bm.verts)
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
    """Classic CS wooden crate stack."""
    bx, by, bz = base_pos
    # Base crate
    add_box(collection, f"{prefix}_crate_base", (bx, by, bz + 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    # Metal corner brackets
    add_box(collection, f"{prefix}_bracket", (bx, by, bz + 0.6), (1.24, 0.1, 1.24), mats["metal_iron_rusted"])


def add_palm_tree(collection, name, pos, mats):
    px, py, pz = pos
    # Curved trunk
    add_cylinder(collection, f"{name}_trunk", (px, py, pz + 3.0), radius=0.25, height=6.0, material=mats["palm_bark"], segments=10)
    # Fronds canopy
    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        rad = math.radians(angle)
        fx = px + math.cos(rad) * 1.5
        fy = py + math.sin(rad) * 1.5
        add_box(collection, f"{name}_frond_{angle}", (fx, fy, pz + 5.8), (1.8, 0.6, 0.08), mats["palm_fronds"])


def build_dust2_terrain_and_perimeter(col, mats):
    """Main ground terrain, desert sand bed, and perimeter defensive fortress walls."""
    # Outer desert perimeter bounds: (4.0 .. 66.0, 4.0 .. 66.0)
    # Ground paving
    add_box(col, "Terrain_Ground", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["limestone_paving"])
    # Outer sand perimeter dune
    add_box(col, "Dune_Perimeter_East", (68.0, 35.0, 0.5), (8.0, 70.0, 3.0), mats["desert_sand"])
    add_box(col, "Dune_Perimeter_South", (35.0, 2.0, 0.5), (70.0, 8.0, 3.0), mats["desert_sand"])

    # High Sandstone Fortress Perimeter Walls (height 10.0m)
    add_box(col, "Wall_Perimeter_South", (35.0, 4.0, 5.0), (62.0, 1.2, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_North", (35.0, 66.0, 5.0), (62.0, 1.2, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_East", (66.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_West", (4.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["sandstone_ochre"])

    # Moorish blue tile accent strip along top of main fortress walls
    add_box(col, "Accent_Tile_Perimeter_N", (35.0, 65.4, 9.6), (62.0, 0.1, 0.4), mats["moorish_tile_blue"])
    add_box(col, "Accent_Tile_Perimeter_S", (35.0, 4.6, 9.6), (62.0, 0.1, 0.4), mats["moorish_tile_blue"])


def build_long_a_and_pit(col, mats):
    """Long A corridor, sunken Pit, corner, and Long A Archway."""
    # Long A dividing west wall (separating Long from Mid/Catwalk)
    add_box(col, "Wall_LongA_West", (44.0, 28.0, 4.5), (1.2, 40.0, 9.0), mats["sandstone_light"])

    # Long A Double Sandstone Archway (T entrance into Long at Y: 16)
    add_arch(col, "Arch_LongA_Doors", (52.0, 16.0, 0.0), span=4.0, height=4.8, depth=1.6, material=mats["sandstone_ochre"])

    # Sunken Pit: floor at Z = -1.4m from Y: 6.0 to Y: 18.0, X: 52.0 to 65.0
    add_box(col, "Pit_Floor", (58.0, 12.0, -0.7), (14.0, 12.0, 1.4), mats["limestone_paving"])
    # Pit retaining curb & slope
    add_box(col, "Pit_Curb_West", (50.5, 12.0, 0.4), (0.8, 12.0, 0.8), mats["sandstone_dark"])
    add_box(col, "Pit_Ramp", (54.0, 19.0, -0.4), (4.0, 4.0, 0.8), mats["limestone_paving"])

    # Abandoned rusted vehicle / cover barricade in Pit
    add_box(col, "Pit_Car_Chassis", (58.0, 12.0, -0.2), (3.8, 1.8, 1.0), mats["metal_iron_rusted"])
    add_box(col, "Pit_Car_Cabin", (57.5, 12.0, 0.6), (2.0, 1.6, 0.8), mats["sandstone_dark"])

    # Long A Corner (X: 54.0, Y: 36.0): Wall protrusion & wooden barrels
    add_box(col, "Wall_LongA_Corner", (60.0, 36.0, 4.0), (10.0, 1.2, 8.0), mats["sandstone_light"])
    add_cylinder(col, "LongA_Barrel_1", (54.0, 34.0, 0.6), radius=0.45, height=1.2, material=mats["wood_cedar_weathered"])
    add_cylinder(col, "LongA_Barrel_2", (54.8, 34.0, 0.6), radius=0.45, height=1.2, material=mats["wood_cedar_weathered"])

    # Long A Ramp ascending to Site A (Y: 42.0 to 50.0)
    add_box(col, "LongA_Ramp", (52.0, 46.0, 0.6), (8.0, 8.0, 1.2), mats["limestone_paving"])


def build_site_a(col, mats):
    """Bomb Site A elevated plateau (+1.2m), double boxes, goose corner, and CT connection."""
    # Elevated Site A Plateau (X: 42.0 .. 58.0, Y: 48.0 .. 62.0, Z = +1.2m)
    add_box(col, "SiteA_Plateau", (50.0, 55.0, 0.6), (16.0, 14.0, 1.2), mats["limestone_paving"])

    # "Goose" Corner Wall (North-East corner of A)
    add_box(col, "SiteA_Goose_Wall", (58.0, 62.0, 3.5), (14.0, 1.2, 5.0), mats["sandstone_ochre"])
    # Goose graffiti symbol plaque
    add_box(col, "SiteA_Goose_Symbol", (54.0, 61.35, 2.5), (1.5, 0.05, 1.5), mats["moorish_tile_blue"])

    # Iconic Double Wooden Crates (Default Plant Box) at (46.0, 52.0)
    add_box(col, "SiteA_Box_Lower1", (46.0, 52.0, 1.8), (1.3, 1.3, 1.2), mats["wood_crate"])
    add_box(col, "SiteA_Box_Lower2", (46.0, 53.3, 1.8), (1.3, 1.3, 1.2), mats["wood_crate"])
    add_box(col, "SiteA_Box_Upper", (46.0, 52.0, 3.0), (1.3, 1.3, 1.2), mats["wood_crate"])

    # Triple stack near ramp
    add_box(col, "SiteA_Crate_Ramp1", (52.0, 56.0, 1.8), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "SiteA_Crate_Ramp2", (52.0, 57.2, 1.8), (1.2, 1.2, 1.2), mats["wood_crate"])

    # CT Spawn Ramp connection down from Site A to CT street level
    add_box(col, "SiteA_CT_Ramp", (44.0, 58.0, 0.6), (6.0, 6.0, 1.2), mats["limestone_paving"])


def build_catwalk_and_short_a(col, mats):
    """Elevated Catwalk (+2.4m) running from Mid to Site A."""
    # Catwalk bridge / ledge (X: 34.0 .. 44.0, Y: 42.0 .. 48.0, Z = +2.4m)
    add_box(col, "Catwalk_Floor", (39.0, 45.0, 1.2), (10.0, 5.0, 2.4), mats["sandstone_light"])

    # Short A Wall with archway entering Site A
    add_arch(col, "ShortA_Portal", (43.0, 48.0, 2.4), span=3.2, height=3.6, depth=1.2, material=mats["sandstone_ochre"])

    # Wrought iron guard railing along edge of Catwalk looking into Mid
    add_box(col, "Catwalk_Railing_Mid", (34.0, 45.0, 2.9), (0.1, 5.0, 1.0), mats["metal_iron_rusted"])

    # Stairs descending from Catwalk to Lower Dark Tunnel
    add_box(col, "Stairs_Catwalk_LowerDark", (30.0, 42.0, 1.2), (4.0, 3.0, 2.4), mats["limestone_paving"])


def build_middle_and_mid_doors(col, mats):
    """Middle corridor, iconic Mid Double Doors with sniper slit, and Xbox crate."""
    # Mid corridor canyon walls
    add_box(col, "Wall_Mid_West", (26.0, 32.0, 4.0), (1.2, 32.0, 8.0), mats["sandstone_light"])

    # Mid Double Doors (X: 31.0, Y: 32.0): Heavy cedar wooden doors with iron reinforcement
    # Left Door swung slightly forward
    add_box(col, "Mid_Door_Left", (29.2, 32.2, 2.0), (1.8, 0.25, 4.0), mats["wood_cedar_weathered"])
    # Right Door swung slightly back, leaving 0.45m center sniper slit!
    add_box(col, "Mid_Door_Right", (32.2, 31.8, 2.0), (1.8, 0.25, 4.0), mats["wood_cedar_weathered"])
    # Arch lintel above doors
    add_box(col, "Mid_Door_Arch_Lintel", (30.7, 32.0, 4.5), (6.0, 1.2, 1.2), mats["sandstone_ochre"])

    # Xbox Jump Crate in Mid (X: 30.0, Y: 38.0): 1.6m cube wood box for Catwalk boost
    add_box(col, "Xbox_Crate_Body", (30.0, 38.0, 0.8), (1.6, 1.6, 1.6), mats["wood_crate"])
    add_box(col, "Xbox_Crate_Brackets", (30.0, 38.0, 0.8), (1.64, 0.15, 1.64), mats["metal_iron_rusted"])

    # Suicide Ramp from T Spawn leading down into Mid
    add_box(col, "Mid_Suicide_Ramp", (31.0, 20.0, 0.6), (5.0, 8.0, 1.2), mats["limestone_paving"])


def build_dark_tunnels(col, mats):
    """Subterranean vaulted Dark Tunnels (Upper Dark and Lower Dark)."""
    # Tunnel ceiling slab at Z = 4.2m enclosing the darkness
    add_box(col, "Tunnel_Roof_Slab", (16.0, 34.0, 4.2), (18.0, 24.0, 0.6), mats["sandstone_dark"])

    # Tunnel interior walls
    add_box(col, "Tunnel_Wall_Outer_West", (8.0, 34.0, 2.0), (1.2, 24.0, 4.0), mats["sandstone_dark"])
    add_box(col, "Tunnel_Wall_Inner_East", (22.0, 30.0, 2.0), (1.2, 16.0, 4.0), mats["sandstone_dark"])

    # Tunnel decorative stone arches every 6 meters
    for ty in [26.0, 32.0, 38.0]:
        add_arch(col, f"Tunnel_Arch_{int(ty)}", (15.0, ty, 0.0), span=4.5, height=3.6, depth=1.0, material=mats["sandstone_ochre"])
        # Hanging brass lantern
        add_box(col, f"Lantern_Chain_{int(ty)}", (15.0, ty, 3.4), (0.05, 0.05, 0.8), mats["metal_iron_rusted"])
        add_box(col, f"Lantern_Body_{int(ty)}", (15.0, ty, 2.9), (0.3, 0.3, 0.4), mats["lantern_brass"])
        add_box(col, f"Lantern_Flame_{int(ty)}", (15.0, ty, 2.9), (0.15, 0.15, 0.25), mats["lantern_flame"])


def build_site_b(col, mats):
    """Bomb Site B fortress courtyard, Upper B exit, B Window, B Doors, and Back Platform."""
    # B Site Fortress Perimeter Walls (height 8.0m)
    add_box(col, "SiteB_North_Wall", (16.0, 62.0, 4.0), (22.0, 1.2, 8.0), mats["sandstone_ochre"])
    add_box(col, "SiteB_West_Wall", (6.0, 52.0, 4.0), (1.2, 20.0, 8.0), mats["sandstone_ochre"])
    add_box(col, "SiteB_East_Wall", (26.0, 54.0, 4.0), (1.2, 16.0, 8.0), mats["sandstone_ochre"])

    # Raised Bomb Plant Platform (X: 12.0 .. 20.0, Y: 50.0 .. 58.0, Z = +0.6m)
    add_box(col, "SiteB_Plant_Platform", (16.0, 54.0, 0.3), (8.0, 8.0, 0.6), mats["limestone_paving"])

    # Upper B Tunnel exit doorway at (16.0, 44.0) with raised lip
    add_arch(col, "SiteB_UpperDark_Exit", (16.0, 44.0, 0.0), span=3.6, height=3.8, depth=1.2, material=mats["sandstone_ochre"])

    # B Window opening overlooking tunnels at (22.0, 46.0, Z: 2.0)
    add_box(col, "SiteB_Window_Wall", (22.0, 46.0, 1.0), (1.2, 4.0, 2.0), mats["sandstone_light"])
    add_box(col, "SiteB_Window_Top", (22.0, 46.0, 4.5), (1.2, 4.0, 3.0), mats["sandstone_light"])

    # Scaffolding and jumping crates next to window
    add_box(col, "SiteB_Window_Crate", (20.5, 46.0, 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "SiteB_Scaffold_Pole1", (23.5, 44.5, 2.5), (0.1, 0.1, 5.0), mats["metal_scaffolding"])
    add_box(col, "SiteB_Scaffold_Pole2", (23.5, 47.5, 2.5), (0.1, 0.1, 5.0), mats["metal_scaffolding"])
    add_box(col, "SiteB_Scaffold_Plank", (23.5, 46.0, 2.2), (1.0, 3.2, 0.1), mats["wood_cedar_weathered"])

    # B Double Doors connecting B Site to CT street
    add_box(col, "SiteB_Door_Left", (24.5, 56.0, 1.8), (0.2, 1.6, 3.6), mats["wood_cedar_weathered"])
    add_box(col, "SiteB_Door_Right", (24.5, 58.0, 1.8), (0.2, 1.6, 3.6), mats["wood_cedar_weathered"])

    # Back Platform & Wooden Crates
    add_box(col, "SiteB_Back_Platform", (9.0, 56.0, 0.5), (4.0, 8.0, 1.0), mats["limestone_paving"])
    add_box(col, "SiteB_Back_Crate", (9.0, 57.0, 1.6), (1.2, 1.2, 1.2), mats["wood_crate"])


def build_spawns_and_props(col, mats):
    """T and CT Spawns, souk canopies, Persian rugs, and desert palm trees."""
    # CT Spawn area (X: 30.0 .. 42.0, Y: 56.0 .. 64.0)
    add_palm_tree(col, "CT_Palm_1", (36.0, 62.0, 0.0), mats)

    # T Spawn Souk area (X: 24.0 .. 40.0, Y: 6.0 .. 16.0)
    # Fabric sun canopies
    add_box(col, "Canopy_Crimson_1", (28.0, 10.0, 4.2), (6.0, 5.0, 0.05), mats["canopy_crimson"])
    add_box(col, "Canopy_Saffron_1", (36.0, 10.0, 4.4), (6.0, 5.0, 0.05), mats["canopy_saffron"])
    # Canopy timber poles
    for px, py in [(25.0, 8.0), (31.0, 8.0), (33.0, 8.0), (39.0, 8.0)]:
        add_cylinder(col, f"Canopy_Pole_{int(px)}_{int(py)}", (px, py, 2.1), radius=0.08, height=4.2, material=mats["wood_cedar_weathered"], segments=8)

    # Decorative Persian rugs hung against sandstone walls
    add_box(col, "Persian_Rug_Wall1", (24.6, 12.0, 2.5), (0.05, 3.0, 2.0), mats["canopy_crimson"])
    add_box(col, "Persian_Rug_Wall2", (38.0, 4.6, 2.5), (3.0, 0.05, 2.0), mats["moorish_tile_blue"])

    # Terracotta amphoras / pots
    for ax, ay in [(26.0, 7.0), (26.8, 7.2), (38.0, 7.0)]:
        add_cylinder(col, f"Amphora_{int(ax*10)}", (ax, ay, 0.5), radius=0.35, height=1.0, material=mats["sandstone_ochre"], segments=12)

    # Palm trees in T courtyard & Long A exterior
    add_palm_tree(col, "T_Palm_1", (22.0, 8.0, 0.0), mats)
    add_palm_tree(col, "LongA_Palm_1", (62.0, 28.0, 0.0), mats)
    add_palm_tree(col, "LongA_Palm_2", (62.0, 44.0, 0.0), mats)


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
