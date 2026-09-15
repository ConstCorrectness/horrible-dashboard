#!/usr/bin/env python3
"""
Render cinematic proof screenshots for Tuscan Citadel (hd_inferno) arena using Blender.
"""

import os
import sys
import math
import bpy
from mathutils import Vector, Euler

# Import map generator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_inferno

ARTIFACT_DIR = "/home/horrible/.gemini/antigravity-cli/brain/629f7f1c-b610-40f5-90bf-b196b5283e82"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

def setup_scene():
    generate_inferno.clear_scene()
    generate_inferno.build_inferno_scene()

    # World sky: Warm Mediterranean cyan-blue sky
    if bpy.context.scene.world:
        world = bpy.context.scene.world
        world.use_nodes = True
        bg_node = world.node_tree.nodes.get("Background")
        if bg_node:
            bg_node.inputs["Color"].default_value = (0.50, 0.70, 0.92, 1.0)
            bg_node.inputs["Strength"].default_value = 1.0

    # Lighting: Golden Tuscan Sunlight
    sun_data = bpy.data.lights.new(name="Tuscan_Sun", type="SUN")
    sun_data.energy = 5.2
    sun_data.color = (1.0, 0.94, 0.82)
    sun_obj = bpy.data.objects.new(name="Tuscan_Sun", object_data=sun_data)
    sun_obj.rotation_euler = Euler((math.radians(48.0), math.radians(20.0), math.radians(55.0)), "XYZ")
    bpy.context.scene.collection.objects.link(sun_obj)

    # Ambient Sky Fill
    amb_data = bpy.data.lights.new(name="Sky_Ambient", type="POINT")
    amb_data.energy = 5000.0
    amb_data.color = (0.85, 0.90, 1.0)
    amb_obj = bpy.data.objects.new(name="Sky_Ambient", object_data=amb_data)
    amb_obj.location = (35.0, 35.0, 14.0)
    bpy.context.scene.collection.objects.link(amb_obj)

    # South ambient bounce light for shadows
    south_amb = bpy.data.lights.new(name="South_Ambient", type="POINT")
    south_amb.energy = 3000.0
    south_amb.color = (0.95, 0.90, 0.82)
    south_obj = bpy.data.objects.new(name="South_Ambient", object_data=south_amb)
    south_obj.location = (20.0, 20.0, 6.0)
    bpy.context.scene.collection.objects.link(south_obj)

    # Local warm lantern fills
    for lx, ly, lz in [(35.0, 31.0, 3.2), (18.0, 46.0, 2.8), (48.0, 56.0, 3.0), (52.0, 48.0, 3.0)]:
        l_data = bpy.data.lights.new(name=f"Light_{int(lx)}_{int(ly)}", type="POINT")
        l_data.energy = 800.0
        l_data.color = (1.0, 0.82, 0.50)
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
    print("Setting up HD Inferno 3D scene...")
    setup_scene()

    shots = [
        ("Cam_Overview", (62.0, 12.0, 28.0), (32.0, 36.0, 2.0), "inferno_proof_01_overview.png"),
        ("Cam_Banana", (15.0, 21.0, 2.2), (18.0, 28.0, 1.2), "inferno_proof_02_banana_car.png"),
        ("Cam_SiteB", (25.0, 44.0, 2.4), (18.0, 51.0, 1.4), "inferno_proof_03_site_b_fountain.png"),
        ("Cam_Balcony_View", (54.0, 38.0, 2.5), (46.0, 42.0, 2.8), "inferno_proof_04_apartments_interior.png"),
        ("Cam_SiteA", (46.0, 46.0, 2.8), (53.0, 52.0, 1.2), "inferno_proof_05_site_a_pit.png"),
    ]

    for name, loc, target, filename in shots:
        render_shot(name, loc, target, filename)

    print("All Inferno proof renders completed successfully!")

if __name__ == "__main__":
    main()
