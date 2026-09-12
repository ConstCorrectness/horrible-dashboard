#!/usr/bin/env python3
"""Procedural 3D Tactical Utility Grenades Generator for Blender 4.2+

Generates 3 detailed tactical grenades:
1. HE Frag Grenade (`hassault-grenade-he.glb`):
   - Cast steel segmented/checkered fragmentation body (olive drab / steel).
   - Threaded fuze head, safety spoon lever, and steel pull ring with cotter pin.
2. Flashbang / Stun Grenade (`hassault-grenade-flash.glb`):
   - Cylindrical aluminum canister body with dual circular top/bottom flash vents.
   - Blue identification stripe, safety spoon, and ring pin.
3. Smoke Grenade (`hassault-grenade-smoke.glb`):
   - Heavy cylindrical military canister with top smoke emission ports.
   - Distinct smoke green body with white markings and safety pin assembly.
"""

import sys
import math
from pathlib import Path

try:
    import bpy
    import mathutils
except ImportError:
    print("Error: Run this script inside Blender: blender -b -P tools/blender/generate_grenades.py")
    sys.exit(1)


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def create_material(name, base_color, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def add_box(col, name, min_pt, max_pt, material=None, bevel=False, bevel_width=0.002):
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
        mod = obj.modifiers.new(name="Bevel", type='BEVEL')
        mod.width = bevel_width
        mod.segments = 2
        mod.limit_method = 'ANGLE'
        mod.angle_limit = math.radians(30)
        bpy.ops.object.modifier_apply(modifier="Bevel")

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def add_cylinder(col, name, center, radius, height, material=None, segments=16, rot_axis='Z', rot_angle=0.0):
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius,
        depth=height,
        vertices=segments,
        location=center
    )
    obj = bpy.context.active_object
    obj.name = name

    if rot_angle != 0.0:
        if rot_axis == 'X':
            obj.rotation_euler[0] = rot_angle
        elif rot_axis == 'Y':
            obj.rotation_euler[1] = rot_angle
        elif rot_axis == 'Z':
            obj.rotation_euler[2] = rot_angle
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def build_he_grenade(col):
    mat_body = create_material("HE_Body", (0.28, 0.32, 0.24), metallic=0.35, roughness=0.65)
    mat_steel = create_material("HE_Steel", (0.25, 0.27, 0.30), metallic=0.85, roughness=0.35)
    mat_lever = create_material("HE_Lever", (0.55, 0.52, 0.45), metallic=0.70, roughness=0.40)
    mat_ring = create_material("HE_Ring", (0.85, 0.88, 0.90), metallic=0.95, roughness=0.20)

    # 1. Main Egg/Canister Body
    add_cylinder(col, "HE_Body_Mid", (0, 0, 0), radius=0.032, height=0.055, material=mat_body, segments=20)
    add_cylinder(col, "HE_Body_Top", (0, 0, 0.032), radius=0.026, height=0.015, material=mat_body, segments=16)
    add_cylinder(col, "HE_Body_Bottom", (0, 0, -0.032), radius=0.026, height=0.015, material=mat_body, segments=16)

    # Fragmentation ridges (checkering)
    for ring_z in [-0.018, 0.0, 0.018]:
        add_cylinder(col, f"HE_Ring_{int((ring_z+0.05)*1000)}", (0, 0, ring_z), radius=0.0335, height=0.005, material=mat_steel, segments=20)

    # 2. Fuze Head & Neck
    add_cylinder(col, "HE_Fuze_Collar", (0, 0, 0.045), radius=0.012, height=0.012, material=mat_steel, segments=16)

    # 3. Safety Spoon Lever (curving down one side)
    add_box(col, "HE_Spoon_Top", (-0.008, -0.005, 0.048), (0.008, 0.022, 0.053), mat_lever)
    add_box(col, "HE_Spoon_Side", (-0.007, 0.028, -0.025), (0.007, 0.033, 0.050), mat_lever)

    # 4. Pull Ring & Cotter Pin
    add_cylinder(col, "HE_Pin", (0.0, 0.010, 0.048), radius=0.002, height=0.028, material=mat_ring, segments=8, rot_axis='X', rot_angle=math.radians(90))
    add_cylinder(col, "HE_Pull_Ring", (0.020, 0.010, 0.048), radius=0.012, height=0.0025, material=mat_ring, segments=16, rot_axis='Y', rot_angle=math.radians(90))


def build_flash_grenade(col):
    mat_body = create_material("Flash_Body", (0.75, 0.78, 0.82), metallic=0.75, roughness=0.30)
    mat_band = create_material("Flash_Band_Blue", (0.15, 0.35, 0.75), metallic=0.20, roughness=0.50)
    mat_steel = create_material("Flash_Steel", (0.25, 0.27, 0.30), metallic=0.85, roughness=0.35)
    mat_lever = create_material("Flash_Lever", (0.55, 0.52, 0.45), metallic=0.70, roughness=0.40)
    mat_ring = create_material("Flash_Ring", (0.85, 0.88, 0.90), metallic=0.95, roughness=0.20)

    # Flashbang Stun Grenade (~13cm tall, ~4.5cm diameter)
    add_cylinder(col, "Flash_Body", (0, 0, 0), radius=0.022, height=0.080, material=mat_body, segments=20)
    add_cylinder(col, "Flash_Stripe_Blue", (0, 0, 0.022), radius=0.0225, height=0.012, material=mat_band, segments=20)

    # Radial expulsion vent ports
    for v_angle in [0, 60, 120, 180, 240, 300]:
        rad = math.radians(v_angle)
        vx = math.cos(rad) * 0.022
        vy = math.sin(rad) * 0.022
        add_cylinder(col, f"Flash_Vent_Top_{v_angle}", (vx, vy, 0.032), radius=0.003, height=0.004, material=mat_steel, segments=8)
        add_cylinder(col, f"Flash_Vent_Bot_{v_angle}", (vx, vy, -0.032), radius=0.003, height=0.004, material=mat_steel, segments=8)

    # Fuze & Spoon
    add_cylinder(col, "Flash_Fuze", (0, 0, 0.048), radius=0.010, height=0.016, material=mat_steel, segments=16)
    add_box(col, "Flash_Spoon", (-0.006, 0.016, -0.020), (0.006, 0.021, 0.052), mat_lever)
    add_cylinder(col, "Flash_Ring", (0.016, 0.008, 0.050), radius=0.011, height=0.0025, material=mat_ring, segments=16, rot_axis='Y', rot_angle=math.radians(90))


def build_smoke_grenade(col):
    mat_body = create_material("Smoke_Body", (0.35, 0.42, 0.32), metallic=0.25, roughness=0.60)
    mat_top = create_material("Smoke_Cap", (0.85, 0.85, 0.82), metallic=0.30, roughness=0.50)
    mat_steel = create_material("Smoke_Steel", (0.25, 0.27, 0.30), metallic=0.85, roughness=0.35)
    mat_lever = create_material("Smoke_Lever", (0.55, 0.52, 0.45), metallic=0.70, roughness=0.40)
    mat_ring = create_material("Smoke_Ring", (0.85, 0.88, 0.90), metallic=0.95, roughness=0.20)

    # Smoke Canister (~14cm tall, ~5.5cm diameter)
    add_cylinder(col, "Smoke_Body", (0, 0, 0), radius=0.028, height=0.095, material=mat_body, segments=20)
    add_cylinder(col, "Smoke_Cap", (0, 0, 0.050), radius=0.0275, height=0.008, material=mat_top, segments=20)

    # Emission ports on top
    for p_angle in [45, 135, 225, 315]:
        rad = math.radians(p_angle)
        px = math.cos(rad) * 0.016
        py = math.sin(rad) * 0.016
        add_cylinder(col, f"Smoke_Port_{p_angle}", (px, py, 0.054), radius=0.0035, height=0.003, material=mat_steel, segments=8)

    # Fuze & Lever
    add_cylinder(col, "Smoke_Fuze", (0, 0, 0.058), radius=0.011, height=0.014, material=mat_steel, segments=16)
    add_box(col, "Smoke_Spoon", (-0.007, 0.022, -0.025), (0.007, 0.027, 0.062), mat_lever)
    add_cylinder(col, "Smoke_Ring", (0.018, 0.010, 0.060), radius=0.012, height=0.0025, material=mat_ring, segments=16, rot_axis='Y', rot_angle=math.radians(90))


def export_single_prop(col, out_name):
    repo_root = Path(__file__).resolve().parent.parent.parent
    web_public = repo_root / f"apps/web/public/{out_name}"
    backend_maps = repo_root / f"backend/modules/hassault/maps/{out_name}"

    bpy.ops.object.select_all(action='DESELECT')
    for obj in col.objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = col.objects[0]
    bpy.ops.object.join()
    final_prop = bpy.context.active_object
    final_prop.name = out_name.replace(".glb", "")

    print(f"Exporting {out_name} with {len(final_prop.data.polygons)} polygons to: {web_public}")
    bpy.ops.export_scene.gltf(
        filepath=str(web_public),
        export_format='GLB',
        use_selection=True,
        export_apply=True,
        export_yup=True,
        export_materials='EXPORT',
        export_lights=False,
        export_cameras=False
    )
    import shutil
    shutil.copyfile(web_public, backend_maps)


def generate_all_grenades():
    # 1. HE Grenade
    clear_scene()
    c_he = bpy.data.collections.new("HE_Grenade")
    bpy.context.scene.collection.children.link(c_he)
    build_he_grenade(c_he)
    export_single_prop(c_he, "hassault-grenade-he.glb")

    # 2. Flashbang
    clear_scene()
    c_flash = bpy.data.collections.new("Flash_Grenade")
    bpy.context.scene.collection.children.link(c_flash)
    build_flash_grenade(c_flash)
    export_single_prop(c_flash, "hassault-grenade-flash.glb")

    # 3. Smoke Grenade
    clear_scene()
    c_smoke = bpy.data.collections.new("Smoke_Grenade")
    bpy.context.scene.collection.children.link(c_smoke)
    build_smoke_grenade(c_smoke)
    export_single_prop(c_smoke, "hassault-grenade-smoke.glb")


if __name__ == "__main__":
    generate_all_grenades()
    print("=== All Tactical Grenades Generated Successfully! ===")
