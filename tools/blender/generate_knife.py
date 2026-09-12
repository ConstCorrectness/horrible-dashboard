#!/usr/bin/env python3
"""Procedural 3D Tactical Combat Knife Generator for Blender 4.2+

Generates a detailed, competitive-grade tactical combat knife prop:
- Tanto-style high-bevel blade with reinforced tip and secondary cutting edge.
- Milled serrated spine on the rear upper edge.
- Precision longitudinal fuller (blood groove).
- Anodized tactical crossguard / finger quillon with thumb jimping.
- Contoured G10 composite handle with finger choil and anti-slip grip texture.
- Solid steel pommel cap with lanyard ring eyelet.
- Oriented along -Z axis at human-scaled weapon prop dimensions (~30 cm total length).
- PBR metallic/roughness materials compatible with glTF standard.
"""

import sys
import math
from pathlib import Path

try:
    import bpy
    import mathutils
except ImportError:
    print("Error: Run this script inside Blender: blender -b -P tools/blender/generate_knife.py")
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


def create_material(name, base_color, metallic=0.0, roughness=0.5, with_texture=False):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        if with_texture:
            img_name = f"{name}_tex"
            img = bpy.data.images.new(img_name, width=64, height=64)
            r, g, b = base_color
            pixels = []
            for y in range(64):
                for x in range(64):
                    noise = (((x * 13 + y * 29) % 17) / 17.0 - 0.5) * 0.05
                    nr = max(0.0, min(1.0, r + noise))
                    ng = max(0.0, min(1.0, g + noise))
                    nb = max(0.0, min(1.0, b + noise))
                    pixels.extend([nr, ng, nb, 1.0])
            img.pixels = pixels
            img.pack()
            tex_node = nodes.new('ShaderNodeTexImage')
            tex_node.image = img
            mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
    return mat


def add_box(col, name, min_pt, max_pt, material=None, bevel=False, bevel_width=0.005):
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
        mod.segments = 3
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


def build_knife():
    print("[1/3] Setting up Tactical Knife Materials...")
    mat_steel = create_material("Knife_Steel", (0.85, 0.86, 0.88), metallic=0.96, roughness=0.22, with_texture=True)
    mat_edge = create_material("Knife_Blade_Edge", (0.95, 0.96, 0.98), metallic=0.98, roughness=0.12, with_texture=True)
    mat_grip = create_material("Knife_Handle", (0.08, 0.09, 0.10), metallic=0.05, roughness=0.75, with_texture=True)
    mat_guard = create_material("Knife_Guard", (0.12, 0.13, 0.15), metallic=0.75, roughness=0.35, with_texture=True)
    mat_pommel = create_material("Knife_Pommel", (0.45, 0.46, 0.48), metallic=0.90, roughness=0.30, with_texture=True)

    c_knife = bpy.data.collections.new("Tactical_Knife")
    bpy.context.scene.collection.children.link(c_knife)

    print("[2/3] Constructing Knife Geometry (Tanto Blade, Guard, Grip, Pommel)...")
    # Coordinates convention:
    # Forward pointing down -Z
    # Up is +Y
    # Lateral thickness is X
    # Total length: ~0.30m (30cm), blade: 17cm (z: -0.17 to 0.0), handle: 13cm (z: 0.0 to 0.13)

    # 1. Main Blade Spine & Body (z: -0.13 to 0.0)
    add_box(c_knife, "Blade_Body", (-0.003, -0.018, -0.13), (0.003, 0.016, 0.0), mat_steel, bevel=True, bevel_width=0.001)

    # 2. Tanto Blade Angled Tip (z: -0.17 to -0.13)
    add_box(c_knife, "Blade_Tanto_Tip", (-0.0025, -0.014, -0.17), (0.0025, 0.008, -0.13), mat_edge, bevel=True, bevel_width=0.001)

    # 3. Razor Cutting Edge Grind (along lower blade, y: -0.022 to -0.016)
    add_box(c_knife, "Blade_Cutting_Edge", (-0.001, -0.022, -0.13), (0.001, -0.014, -0.005), mat_edge)
    add_box(c_knife, "Blade_Tanto_Edge", (-0.0008, -0.018, -0.17), (0.0008, -0.012, -0.13), mat_edge)

    # 4. Milled Serrations on rear upper spine (z: -0.06 to -0.01, y: 0.014..0.018)
    for sz in range(6):
        z_pos = -0.015 - (sz * 0.007)
        add_box(c_knife, f"Blade_Serration_{sz}", (-0.0032, 0.015, z_pos - 0.002), (0.0032, 0.019, z_pos + 0.002), mat_steel)

    # 5. Fuller (Blood Groove) longitudinal cutout on both sides
    add_box(c_knife, "Blade_Fuller_L", (-0.0034, -0.003, -0.11), (-0.0026, 0.005, -0.02), mat_guard)
    add_box(c_knife, "Blade_Fuller_R", (0.0026, -0.003, -0.11), (0.0034, 0.005, -0.02), mat_guard)

    # 6. Tactical Crossguard / Quillon (z: -0.006 to 0.008, x: -0.014 to 0.014, y: -0.028 to 0.024)
    add_box(c_knife, "Knife_Crossguard", (-0.012, -0.026, -0.005), (0.012, 0.022, 0.006), mat_guard, bevel=True, bevel_width=0.002)
    for j in [-0.002, 0.001, 0.004]:
        add_box(c_knife, f"Guard_Jimping_{int((j+0.01)*1000)}", (-0.008, 0.021, j), (0.008, 0.024, j + 0.0015), mat_guard)

    # 7. Contoured Handle / Grip (z: 0.006 to 0.115)
    add_box(c_knife, "Knife_Handle_Core", (-0.011, -0.018, 0.006), (0.011, 0.018, 0.115), mat_grip, bevel=True, bevel_width=0.003)

    for f_idx, fz in enumerate([0.025, 0.050, 0.075, 0.100]):
        add_cylinder(c_knife, f"Handle_Choil_{f_idx}", (0.0, -0.019, fz), radius=0.008, height=0.024, material=mat_grip, segments=16, rot_axis='Y', rot_angle=math.radians(90))

    for r_idx in range(7):
        rz = 0.02 + (r_idx * 0.012)
        add_box(c_knife, f"Grip_Rib_L_{r_idx}", (-0.0125, -0.014, rz - 0.002), (-0.0105, 0.014, rz + 0.002), mat_guard)
        add_box(c_knife, f"Grip_Rib_R_{r_idx}", (0.0105, -0.014, rz - 0.002), (0.0125, 0.014, rz + 0.002), mat_guard)

    for s_idx, sz in enumerate([0.035, 0.085]):
        add_cylinder(c_knife, f"Handle_Screw_{s_idx}", (0.0, 0.0, sz), radius=0.0035, height=0.025, material=mat_steel, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # 8. Tactical Pommel Cap & Glass Breaker (z: 0.115 to 0.132)
    add_box(c_knife, "Knife_Pommel_Base", (-0.010, -0.017, 0.115), (0.010, 0.017, 0.126), mat_pommel, bevel=True, bevel_width=0.002)
    add_cylinder(c_knife, "Pommel_Breaker_Tip", (0.0, 0.0, 0.129), radius=0.004, height=0.006, material=mat_steel, segments=8)
    add_cylinder(c_knife, "Pommel_Lanyard_Hole", (0.0, 0.006, 0.121), radius=0.003, height=0.022, material=mat_guard, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    print("[3/3] Joining and Baking Knife Geometry...")
    bpy.ops.object.select_all(action='DESELECT')
    for obj in c_knife.objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = c_knife.objects[0]
    bpy.ops.object.join()

    final_knife = bpy.context.active_object
    final_knife.name = "Weapon_Knife"
    final_knife.rotation_euler[0] = math.radians(-90)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    print(f"Generated Knife with {len(final_knife.data.polygons)} polygons.")
    return final_knife


def export_knife():
    repo_root = Path(__file__).resolve().parent.parent.parent
    web_public = repo_root / "apps/web/public/hassault-weapon-knife.glb"
    backend_maps = repo_root / "backend/modules/hassault/maps/hassault-weapon-knife.glb"

    print(f"Exporting GLB to: {web_public}")
    bpy.ops.export_scene.gltf(
        filepath=str(web_public),
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials='EXPORT',
        export_lights=False,
        export_cameras=False
    )

    import shutil
    shutil.copyfile(web_public, backend_maps)
    print(f"Copied to: {backend_maps}")


if __name__ == "__main__":
    clear_scene()
    build_knife()
    export_knife()
    print("=== Tactical Knife Generation Complete ===")
