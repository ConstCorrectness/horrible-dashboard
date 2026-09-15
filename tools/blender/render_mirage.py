#!/usr/bin/env python3
"""
Render cinematic proof screenshots for Desert Courtyard (hd_mirage) arena using Blender.
"""

import os
import sys
import math
import bpy
from mathutils import Vector, Euler

# Import map generator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_mirage

ARTIFACT_DIR = "/home/horrible/.gemini/antigravity-cli/brain/629f7f1c-b610-40f5-90bf-b196b5283e82"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

def setup_scene():
    generate_mirage.clear_scene()
    generate_mirage.build_mirage_scene()

    # World sky: Deep warm desert azure sky
    if bpy.context.scene.world:
        world = bpy.context.scene.world
        world.use_nodes = True
        bg_node = world.node_tree.nodes.get("Background")
        if bg_node:
            bg_node.inputs["Color"].default_value = (0.55, 0.75, 0.95, 1.0)
            bg_node.inputs["Strength"].default_value = 1.0

    # Lighting: Brilliant Desert Sun
    sun_data = bpy.data.lights.new(name="Desert_Sun", type="SUN")
    sun_data.energy = 5.6
    sun_data.color = (1.0, 0.96, 0.88)
    sun_obj = bpy.data.objects.new(name="Desert_Sun", object_data=sun_data)
    sun_obj.rotation_euler = Euler((math.radians(52.0), math.radians(18.0), math.radians(45.0)), "XYZ")
    bpy.context.scene.collection.objects.link(sun_obj)

    # Ambient Sky Fill
    amb_data = bpy.data.lights.new(name="Sky_Ambient", type="POINT")
    amb_data.energy = 5500.0
    amb_data.color = (0.85, 0.92, 1.0)
    amb_obj = bpy.data.objects.new(name="Sky_Ambient", object_data=amb_data)
    amb_obj.location = (35.0, 35.0, 16.0)
    bpy.context.scene.collection.objects.link(amb_obj)

    # South ambient bounce light for shadows
    south_amb = bpy.data.lights.new(name="South_Ambient", type="POINT")
    south_amb.energy = 3200.0
    south_amb.color = (0.96, 0.90, 0.80)
    south_obj = bpy.data.objects.new(name="South_Ambient", object_data=south_amb)
    south_obj.location = (20.0, 20.0, 8.0)
    bpy.context.scene.collection.objects.link(south_obj)

    # Local warm courtyard fills
    for lx, ly, lz in [(50.0, 48.0, 3.5), (34.0, 48.0, 3.5), (18.0, 48.0, 3.5), (48.0, 28.0, 3.5)]:
        l_data = bpy.data.lights.new(name=f"Light_{int(lx)}_{int(ly)}", type="POINT")
        l_data.energy = 850.0
        l_data.color = (1.0, 0.85, 0.55)
        l_obj = bpy.data.objects.new(name=f"Light_{int(lx)}_{int(ly)}", object_data=l_data)
        l_obj.location = (lx, ly, lz)
        bpy.context.scene.collection.objects.link(l_obj)

    # Render settings
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items else "BLENDER_EEVEE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.image_settings.file_format = "PNG"

def render_shot(cam_name, loc, target, filename):
    cam_data = bpy.data.cameras.new(name=cam_name)
    cam_data.lens = 24.0
    cam_data.clip_start = 0.1
    cam_data.clip_end = 250.0
    cam_obj = bpy.data.objects.new(name=cam_name, object_data=cam_data)
    cam_obj.location = Vector(loc)

    # Track target
    direction = Vector(target) - Vector(loc)
    rot_quat = direction.to_track_quat("-Z", "Y")
    cam_obj.rotation_euler = rot_quat.to_euler()

    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    filepath = os.path.join(ARTIFACT_DIR, filename)
    bpy.context.scene.render.filepath = filepath
    print(f"Rendering {filename} from {loc} to {target}...")
    bpy.ops.render.render(write_still=True)
    print(f"Saved {filepath}")

    # Remove camera
    bpy.data.objects.remove(cam_obj, do_unlink=True)
    bpy.data.cameras.remove(cam_data, do_unlink=True)

def main():
    print("Setting up HD Mirage 3D scene...")
    setup_scene()

    shots = [
        ("Cam_Overview", (64.0, 12.0, 28.0), (32.0, 36.0, 2.0), "mirage_proof_01_overview.png"),
        ("Cam_SiteA_Palace", (42.0, 56.0, 2.5), (49.0, 42.0, 2.2), "mirage_proof_02_site_a_palace.png"),
        ("Cam_Mid_Window", (34.0, 26.0, 2.2), (34.0, 48.0, 3.0), "mirage_proof_03_mid_window.png"),
        ("Cam_B_Apartments", (16.0, 32.0, 3.4), (18.0, 44.0, 2.0), "mirage_proof_04_b_apartments.png"),
        ("Cam_SiteB_Van", (22.0, 42.0, 2.0), (14.0, 50.0, 1.4), "mirage_proof_05_site_b_van.png"),
    ]

    for name, loc, target, filename in shots:
        render_shot(name, loc, target, filename)

    print("All Mirage proof renders completed successfully!")

if __name__ == "__main__":
    main()
