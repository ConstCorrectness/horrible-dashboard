#!/usr/bin/env python3
"""Studio Lighting & Cinematic Showcase Renderer for Knives & Rarity Skins

Renders high-definition studio showcases of all 6 knife models with their respective PBR rarity finishes:
1. Default Tactical Tanto Knife (Clean vanilla brushed steel & matte G10)
2. Karambit | Fade (Chromatic amber-magenta-cyan gradient & glossy carbon)
3. Butterfly Knife | Marble Fade (Tricolor balisong with skeletonized handles)
4. M9 Bayonet | Lore (24k gold leaf & Celtic knotwork with muzzle-ring guard)
5. Skeleton Knife | Crimson Web (Blood-red enamel spiderweb with center ring)
6. Huntsman Knife | Case Hardened "Blue Gem" (Heat-tempered cobalt blue & fire scale)
7. Complete Master Armory Collection (All 6 knives displayed in a dramatic showcase layout)
"""

import os
import sys
import math
from pathlib import Path

try:
    import bpy
    import mathutils
    from mathutils import Vector, Euler
except ImportError:
    print("Error: Run this script inside Blender: blender -b -P tools/blender/render_knives_showcase.py")
    sys.exit(1)

# Import knife generator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_all_knives

ARTIFACT_DIR = "/home/horrible/.gemini/antigravity-cli/brain/03b8c088-d353-4034-8c44-f2f6f75b57e6"
os.makedirs(ARTIFACT_DIR, exist_ok=True)


def setup_studio_environment():
    # World background: Deep obsidian studio atmosphere
    world = bpy.context.scene.world
    if not world:
        world = bpy.data.worlds.new("Studio_World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs["Color"].default_value = (0.015, 0.018, 0.025, 1.0)
        bg.inputs["Strength"].default_value = 0.5

    # Key Light (Warm balanced studio illumination)
    key_data = bpy.data.lights.new(name="Key_Light", type="AREA")
    key_data.energy = 10.0
    key_data.color = (1.0, 0.96, 0.90)
    key_data.size = 0.6
    key_obj = bpy.data.objects.new(name="Key_Light", object_data=key_data)
    key_obj.location = (0.35, -0.30, 0.35)
    key_obj.rotation_euler = Euler((math.radians(45), math.radians(15), math.radians(45)), 'XYZ')
    bpy.context.scene.collection.objects.link(key_obj)

    # Rim Light (Cool azure metallic highlight)
    rim_data = bpy.data.lights.new(name="Rim_Light", type="AREA")
    rim_data.energy = 14.0
    rim_data.color = (0.70, 0.88, 1.0)
    rim_data.size = 0.7
    rim_obj = bpy.data.objects.new(name="Rim_Light", object_data=rim_data)
    rim_obj.location = (-0.35, 0.30, 0.25)
    rim_obj.rotation_euler = Euler((math.radians(-35), math.radians(-20), math.radians(-135)), 'XYZ')
    bpy.context.scene.collection.objects.link(rim_obj)

    # Top Fill Light (Gentle downward specular glow)
    fill_data = bpy.data.lights.new(name="Fill_Light", type="AREA")
    fill_data.energy = 6.0
    fill_data.color = (0.95, 0.95, 1.0)
    fill_data.size = 0.8
    fill_obj = bpy.data.objects.new(name="Fill_Light", object_data=fill_data)
    fill_obj.location = (0.0, 0.0, 0.45)
    fill_obj.rotation_euler = Euler((0, 0, 0), 'XYZ')
    bpy.context.scene.collection.objects.link(fill_obj)

    # Dark Carbon Pedestal / Reflective Studio Floor
    mat_floor = bpy.data.materials.new(name="Mat_Floor")
    mat_floor.use_nodes = True
    bsdf_floor = next((n for n in mat_floor.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf_floor:
        bsdf_floor.inputs["Base Color"].default_value = (0.02, 0.022, 0.028, 1.0)
        bsdf_floor.inputs["Metallic"].default_value = 0.88
        bsdf_floor.inputs["Roughness"].default_value = 0.22

    bpy.ops.mesh.primitive_plane_add(size=3.0, location=(0.0, 0.0, -0.055))
    floor_obj = bpy.context.active_object
    floor_obj.name = "Studio_Floor"
    floor_obj.data.materials.append(mat_floor)

    # Render settings: EEVEE Next with high sampling
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items else "BLENDER_EEVEE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.image_settings.file_format = "PNG"


def render_camera_shot(cam_name, loc, target, filename):
    cam_data = bpy.data.cameras.new(name=cam_name)
    cam_data.lens = 52.0
    cam_data.clip_start = 0.05
    cam_data.clip_end = 50.0
    cam_obj = bpy.data.objects.new(name=cam_name, object_data=cam_data)
    cam_obj.location = Vector(loc)

    direction = Vector(target) - Vector(loc)
    rot_quat = direction.to_track_quat("-Z", "Y")
    cam_obj.rotation_euler = rot_quat.to_euler()

    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    filepath = os.path.join(ARTIFACT_DIR, filename)
    bpy.context.scene.render.filepath = filepath
    print(f"Rendering {filename}...")
    bpy.ops.render.render(write_still=True)
    print(f"Saved {filepath}")

    bpy.data.objects.remove(cam_obj, do_unlink=True)
    bpy.data.cameras.remove(cam_data, do_unlink=True)


def main():
    print("=== [1/2] Initializing Blender Studio Lighting & Floor ===")
    generate_all_knives.full_clear()
    setup_studio_environment()

    # Recreate procedural textures and materials
    t_fade = generate_all_knives.create_texture_image("Tex_Fade", 256, 256, generate_all_knives.tex_fade)
    t_marble = generate_all_knives.create_texture_image("Tex_Marble_Fade", 256, 256, generate_all_knives.tex_marble_fade)
    t_case = generate_all_knives.create_texture_image("Tex_Case_Hardened", 256, 256, generate_all_knives.tex_case_hardened)
    t_crimson = generate_all_knives.create_texture_image("Tex_Crimson_Web", 256, 256, generate_all_knives.tex_crimson_web)
    t_lore = generate_all_knives.create_texture_image("Tex_Lore", 256, 256, generate_all_knives.tex_lore_gold)

    mat_steel = generate_all_knives.create_pbr_material("Mat_Steel", (0.85, 0.86, 0.88), metallic=0.96, roughness=0.20)
    mat_edge = generate_all_knives.create_pbr_material("Mat_Edge", (0.95, 0.96, 0.98), metallic=0.98, roughness=0.10)
    mat_grip = generate_all_knives.create_pbr_material("Mat_Grip", (0.12, 0.13, 0.15), metallic=0.10, roughness=0.65)
    mat_guard = generate_all_knives.create_pbr_material("Mat_Guard", (0.12, 0.13, 0.15), metallic=0.75, roughness=0.35)
    mat_pommel = generate_all_knives.create_pbr_material("Mat_Pommel", (0.45, 0.46, 0.48), metallic=0.90, roughness=0.30)

    mat_fade = generate_all_knives.create_pbr_material("Mat_Skin_Fade", (0.9, 0.5, 0.5), metallic=0.98, roughness=0.15, tex_img=t_fade)
    mat_marble = generate_all_knives.create_pbr_material("Mat_Skin_Marble", (0.8, 0.5, 0.5), metallic=0.98, roughness=0.15, tex_img=t_marble)
    mat_case = generate_all_knives.create_pbr_material("Mat_Skin_CaseHardened", (0.4, 0.6, 0.9), metallic=0.95, roughness=0.18, tex_img=t_case)
    mat_crimson = generate_all_knives.create_pbr_material("Mat_Skin_Crimson", (0.8, 0.1, 0.1), metallic=0.85, roughness=0.22, tex_img=t_crimson)
    mat_lore = generate_all_knives.create_pbr_material("Mat_Skin_Lore", (0.98, 0.82, 0.20), metallic=0.98, roughness=0.12, tex_img=t_lore)
    mat_paracord = generate_all_knives.create_pbr_material("Mat_Paracord", (0.14, 0.16, 0.14), metallic=0.10, roughness=0.85)

    single_shots = [
        ("Default Tactical Tanto Knife", "showcase_knife_default_vanilla.png", lambda: generate_all_knives.build_default_knife(mat_steel, mat_edge, mat_grip, mat_guard, mat_pommel)),
        ("Karambit | Fade", "showcase_knife_karambit_fade.png", lambda: generate_all_knives.build_karambit(mat_fade, mat_edge, mat_grip, mat_pommel)),
        ("Butterfly Knife | Marble Fade", "showcase_knife_butterfly_marble.png", lambda: generate_all_knives.build_butterfly(mat_marble, mat_edge, mat_grip, mat_guard)),
        ("M9 Bayonet | Lore", "showcase_knife_bayonet_lore.png", lambda: generate_all_knives.build_bayonet(mat_lore, mat_edge, mat_grip, mat_guard, mat_pommel)),
        ("Skeleton Knife | Crimson Web", "showcase_knife_skeleton_crimson.png", lambda: generate_all_knives.build_skeleton_knife(mat_crimson, mat_edge, mat_paracord, mat_guard)),
        ("Huntsman Knife | Case Hardened", "showcase_knife_huntsman_case_hardened.png", lambda: generate_all_knives.build_huntsman(mat_case, mat_edge, mat_grip, mat_pommel)),
    ]

    print("=== [2/2] Rendering Individual Knife Showcases ===")
    for title, filename, build_fn in single_shots:
        # Clear previous knife objects (preserving lights and floor)
        for obj in list(bpy.context.scene.collection.objects):
            if "Light" not in obj.name and "Floor" not in obj.name:
                bpy.data.objects.remove(obj, do_unlink=True)
        for col in list(bpy.data.collections):
            bpy.data.collections.remove(col)

        print(f"\nBuilding & Positioning {title}...")
        knife = build_fn()
        # Rotate so the broad flat side faces the camera with a dynamic angle
        knife.rotation_euler = Euler((math.radians(12.0), math.radians(25.0), math.radians(72.0)), 'XYZ')
        knife.location = Vector((0.0, 0.01, 0.01))

        # Position camera looking directly at the broad face from a 3/4 perspective
        render_camera_shot("Cam_Studio", (0.02, -0.36, 0.14), (0.0, 0.01, 0.01), filename)

    # --- Master Armory Lineup ---
    print("\n=== Building Master Armory Lineup (All 6 Knives) ===")
    for obj in list(bpy.context.scene.collection.objects):
        if "Light" not in obj.name and "Floor" not in obj.name:
            bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    knives_configs = [
        (generate_all_knives.build_default_knife(mat_steel, mat_edge, mat_grip, mat_guard, mat_pommel), -0.32),
        (generate_all_knives.build_karambit(mat_fade, mat_edge, mat_grip, mat_pommel), -0.19),
        (generate_all_knives.build_butterfly(mat_marble, mat_edge, mat_grip, mat_guard), -0.06),
        (generate_all_knives.build_bayonet(mat_lore, mat_edge, mat_grip, mat_guard, mat_pommel), 0.07),
        (generate_all_knives.build_skeleton_knife(mat_crimson, mat_edge, mat_paracord, mat_guard), 0.20),
        (generate_all_knives.build_huntsman(mat_case, mat_edge, mat_grip, mat_pommel), 0.33),
    ]

    for k_obj, x_pos in knives_configs:
        k_obj.location = Vector((x_pos, 0.0, 0.005))
        k_obj.rotation_euler = Euler((math.radians(15.0), math.radians(18.0), math.radians(70.0)), 'XYZ')

    render_camera_shot("Cam_Armory", (0.0, -0.76, 0.28), (0.0, 0.0, 0.01), "showcase_knife_collection_hero.png")

    print("\nAll Knife Showcases Rendered Successfully!")


if __name__ == "__main__":
    main()
