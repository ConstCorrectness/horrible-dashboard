#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Nuclear Containment Facility (hd_nuke).
Authentic multi-level competitive tournament arena on a 70m x 70m footprint:
- Bomb Site A: Upper Reactor Hall (z=0.0m), overhead rafters, gantry crane, Hut, and vent hatch.
- Bomb Site B: Lower Reactor Silo (z=-4.5m) directly underneath Site A, featuring central glowing reactor core, coolant tanks, and decon chamber.
- Ventilation Ducts: Vertical shaft linking Site A floor, Site B ceiling, and Decon corridor.
- Ramp Room & Lobby: Sloped ramp connecting ground level down to Site B silo, Radio room, and Squeaky door.
- Outside Yard: Silo tower with ladder, shipping containers, and Garage overlook.

Outputs:
  - backend/modules/hassault/maps/hd_nuke.glb
  - apps/web/public/hd_nuke.glb
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
    print("Error: generate_nuke.py must be run from within Blender (e.g. `blender --background --python ...`)")
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

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    mats = {}
    mats["concrete_floor"] = create_pbr_material("mat_concrete_floor", (0.64, 0.65, 0.66), metallic=0.02, roughness=0.75)
    mats["concrete_wall"] = create_pbr_material("mat_concrete_wall", (0.72, 0.74, 0.76), metallic=0.01, roughness=0.82)
    mats["hazard_yellow"] = create_pbr_material("mat_hazard_yellow", (0.86, 0.75, 0.12), metallic=0.45, roughness=0.35)
    mats["hazard_black"] = create_pbr_material("mat_hazard_black", (0.16, 0.16, 0.18), metallic=0.2, roughness=0.6)
    mats["steel_grate"] = create_pbr_material("mat_steel_grate", (0.42, 0.44, 0.46), metallic=0.85, roughness=0.35)
    mats["reactor_glow_cyan"] = create_pbr_material("mat_reactor_glow_cyan", (0.12, 0.85, 0.95), emission_color=(0.12, 0.85, 0.95), emission_strength=5.0)
    mats["reactor_hull"] = create_pbr_material("mat_reactor_hull", (0.32, 0.35, 0.38), metallic=0.8, roughness=0.3)
    mats["coolant_blue"] = create_pbr_material("mat_coolant_blue", (0.20, 0.42, 0.72), metallic=0.5, roughness=0.4)
    mats["container_red"] = create_pbr_material("mat_container_red", (0.72, 0.20, 0.16), metallic=0.3, roughness=0.6)
    mats["container_blue"] = create_pbr_material("mat_container_blue", (0.18, 0.32, 0.62), metallic=0.3, roughness=0.6)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.54, 0.40, 0.26), metallic=0.0, roughness=0.65)
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


def build_nuke_perimeter_and_ground(col, mats):
    # Main Ground Floor (70m x 70m)
    add_box(col, "Terrain_Concrete_Ground", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["concrete_floor"])

    # High Perimeter Concrete Security Walls (Height 12m)
    add_box(col, "Wall_Perimeter_S", (35.0, 4.0, 6.0), (62.0, 1.2, 12.0), mats["concrete_wall"])
    add_box(col, "Wall_Perimeter_N", (35.0, 66.0, 6.0), (62.0, 1.2, 12.0), mats["concrete_wall"])
    add_box(col, "Wall_Perimeter_E", (66.0, 35.0, 6.0), (1.2, 62.0, 12.0), mats["concrete_wall"])
    add_box(col, "Wall_Perimeter_W", (4.0, 35.0, 6.0), (1.2, 62.0, 12.0), mats["concrete_wall"])

    # Yellow Hazard Security Trim along top
    add_box(col, "Hazard_Trim_S", (35.0, 4.6, 11.6), (62.0, 0.4, 0.6), mats["hazard_yellow"])
    add_box(col, "Hazard_Trim_N", (35.0, 65.4, 11.6), (62.0, 0.4, 0.6), mats["hazard_yellow"])


def build_site_a_upper_hall(col, mats):
    """Bomb Site A Upper Reactor Hall at Z=0.0m with Hut, Rafters, and Crane."""
    # Outer containment building walls (X: 22..50, Y: 28..52, Z: 0..8.0)
    add_box(col, "SiteA_Wall_N", (36.0, 52.0, 4.0), (28.0, 1.2, 8.0), mats["concrete_wall"])
    add_box(col, "SiteA_Wall_E", (50.0, 40.0, 4.0), (1.2, 24.0, 8.0), mats["concrete_wall"])
    add_box(col, "SiteA_Wall_W", (22.0, 40.0, 4.0), (1.2, 24.0, 8.0), mats["concrete_wall"])
    add_box(col, "SiteA_Wall_S", (36.0, 28.0, 4.0), (28.0, 1.2, 8.0), mats["concrete_wall"])

    # Hut Structure inside Site A (X: 24..28, Y: 34..40, Z: 0..2.8)
    add_box(col, "SiteA_Hut_Roof", (26.0, 37.0, 2.7), (4.4, 6.4, 0.2), mats["concrete_wall"])
    add_box(col, "SiteA_Hut_Wall_W", (24.0, 37.0, 1.3), (0.4, 6.0, 2.6), mats["concrete_wall"])
    add_box(col, "SiteA_Hut_Wall_S", (26.0, 34.0, 1.3), (4.0, 0.4, 2.6), mats["concrete_wall"])
    add_box(col, "SiteA_Hut_Doorframe", (28.0, 37.0, 2.3), (0.4, 6.0, 0.6), mats["hazard_yellow"])

    # Overhead Rafters / Catwalks at Z = 3.8m
    add_box(col, "SiteA_Catwalk_East", (48.0, 40.0, 3.8), (2.0, 22.0, 0.2), mats["steel_grate"])
    add_box(col, "SiteA_Catwalk_North", (36.0, 50.0, 3.8), (22.0, 2.0, 0.2), mats["steel_grate"])
    add_box(col, "SiteA_Catwalk_Railing", (47.0, 40.0, 4.4), (0.1, 22.0, 1.0), mats["hazard_yellow"])

    # Yellow Gantry Crane Beam across ceiling at Z = 6.8m
    add_box(col, "SiteA_Crane_Beam", (36.0, 40.0, 6.8), (26.0, 1.4, 0.8), mats["hazard_yellow"])
    add_box(col, "SiteA_Crane_Hoist", (36.0, 40.0, 5.8), (1.8, 1.8, 1.2), mats["hazard_black"])

    # Plant Marker A Disc at (36.0, 40.0, 0.0)
    add_cylinder(col, "SiteA_Plant_Marker", (36.0, 40.0, 0.05), radius=2.5, height=0.1, material=mats["hazard_yellow"], segments=16)

    # Vent Hatch at (31.0, 36.0, 0.0)
    add_box(col, "SiteA_Vent_Hatch_Coaming", (31.0, 36.0, 0.15), (1.4, 1.4, 0.3), mats["hazard_yellow"])


def build_site_b_lower_silo(col, mats):
    """Bomb Site B Subterranean Reactor Silo at Z=-4.5m directly beneath Site A."""
    # Silo Excavation Floor at Z = -4.5m
    add_box(col, "SiteB_Silo_Floor", (36.0, 40.0, -4.7), (26.0, 22.0, 0.4), mats["concrete_floor"])

    # Central Nuclear Reactor Core at (36.0, 40.0, -4.5)
    # Reactor lower base pedestal
    add_cylinder(col, "Reactor_Base_Pedestal", (36.0, 40.0, -4.1), radius=3.2, height=0.8, material=mats["concrete_floor"], segments=16)
    # Main containment pressure vessel
    add_cylinder(col, "Reactor_Pressure_Vessel", (36.0, 40.0, -2.25), radius=2.6, height=4.5, material=mats["reactor_hull"], segments=20)
    # Glowing Core Ring
    add_cylinder(col, "Reactor_Core_Glow_Ring", (36.0, 40.0, -2.25), radius=2.7, height=0.6, material=mats["reactor_glow_cyan"], segments=20)
    # Core top dome
    add_cylinder(col, "Reactor_Top_Dome", (36.0, 40.0, 0.0), radius=2.4, height=0.5, material=mats["hazard_yellow"], segments=16)

    # Coolant Containment Tanks
    add_cylinder(col, "Coolant_Tank_W", (25.0, 44.0, -3.0), radius=1.4, height=3.0, material=mats["coolant_blue"], segments=12)
    add_cylinder(col, "Coolant_Tank_E", (47.0, 44.0, -3.0), radius=1.4, height=3.0, material=mats["coolant_blue"], segments=12)

    # Decon Chamber Portal at North side of B
    add_box(col, "SiteB_Decon_Frame", (36.0, 50.0, -3.0), (4.0, 0.8, 3.0), mats["hazard_yellow"])
    add_box(col, "SiteB_Decon_Doors", (36.0, 50.0, -3.0), (3.2, 0.3, 2.8), mats["steel_grate"])

    # Plant Marker B Disc at (36.0, 40.0, -4.5)
    add_cylinder(col, "SiteB_Plant_Marker", (36.0, 40.0, -4.45), radius=3.8, height=0.08, material=mats["hazard_yellow"], segments=16)


def build_connecting_ramps_and_vents(col, mats):
    """Vertical ventilation shaft, Ramp room, and Decon corridor."""
    # Vertical Vent Shaft: connects (31.0, 36.0, 0.0) to (31.0, 36.0, -4.5)
    # 4 walls of ductwork
    add_box(col, "Vent_Shaft_W", (30.4, 36.0, -2.25), (0.2, 1.2, 4.5), mats["steel_grate"])
    add_box(col, "Vent_Shaft_E", (31.6, 36.0, -2.25), (0.2, 1.2, 4.5), mats["steel_grate"])
    add_box(col, "Vent_Shaft_S", (31.0, 35.4, -2.25), (1.2, 0.2, 4.5), mats["steel_grate"])
    add_box(col, "Vent_Shaft_N", (31.0, 36.6, -2.25), (1.2, 0.2, 4.5), mats["steel_grate"])

    # Ramp Room: connects Lobby (z=0) at y=20 down to Site B (z=-4.5) at y=28
    # Ramp slope
    add_box(col, "Ramp_Floor", (20.0, 24.0, -2.25), (4.0, 8.0, 0.4), mats["concrete_floor"])
    # Ramp side safety railings
    add_box(col, "Ramp_Railing_W", (18.0, 24.0, -1.6), (0.2, 8.0, 1.0), mats["hazard_yellow"])

    # Decon connector corridor floor
    add_box(col, "Decon_Corridor_Floor", (36.0, 54.0, -4.7), (6.0, 8.0, 0.4), mats["concrete_floor"])


def build_outside_yard(col, mats):
    """Outside Yard with Silo tower, shipping containers, and Garage."""
    # Silo Tower at (16.0, 20.0, 0.0)
    add_cylinder(col, "Outside_Silo_Base", (16.0, 20.0, 2.5), radius=2.4, height=5.0, material=mats["concrete_wall"], segments=16)
    add_cylinder(col, "Outside_Silo_Dome", (16.0, 20.0, 5.3), radius=2.2, height=0.6, material=mats["hazard_yellow"], segments=16)

    # Red & Blue Shipping Containers in Yard
    add_box(col, "Container_Red_1", (16.0, 36.0, 1.3), (2.8, 6.0, 2.6), mats["container_red"])
    add_box(col, "Container_Blue_1", (16.0, 44.0, 1.3), (2.8, 6.0, 2.6), mats["container_blue"])

    # Garage Structure at East Yard (X: 52..60, Y: 40..52, Z: 0..5.0)
    add_box(col, "Garage_Building", (56.0, 46.0, 2.5), (8.0, 12.0, 5.0), mats["concrete_wall"])
    add_box(col, "Garage_Door_Frame", (52.0, 46.0, 2.0), (0.4, 4.0, 4.0), mats["hazard_yellow"])


def build_nuke_scene():
    clear_scene()
    mats = setup_materials()

    c_terrain = get_or_create_collection("Terrain")
    c_site_a = get_or_create_collection("SiteA")
    c_site_b = get_or_create_collection("SiteB")
    c_connectors = get_or_create_collection("Connectors")
    c_yard = get_or_create_collection("Yard")

    build_nuke_perimeter_and_ground(c_terrain, mats)
    build_site_a_upper_hall(c_site_a, mats)
    build_site_b_lower_silo(c_site_b, mats)
    build_connecting_ramps_and_vents(c_connectors, mats)
    build_outside_yard(c_yard, mats)


def export_glb(filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        use_selection=False,
        export_apply=True,
        export_yup=True,
    )
    print(f"Exported GLB to {filepath} ({os.path.getsize(filepath):,} bytes)")


def main():
    print("Building Nuclear Containment Facility (hd_nuke)...")
    build_nuke_scene()

    backend_glb = os.path.abspath("backend/modules/hassault/maps/hd_nuke.glb")
    export_glb(backend_glb)

    web_glb = os.path.abspath("apps/web/public/hd_nuke.glb")
    shutil.copyfile(backend_glb, web_glb)
    print(f"Mirrored GLB to {web_glb}")
    print("=== hd_nuke 3D Generation Complete! ===")


if __name__ == "__main__":
    main()
