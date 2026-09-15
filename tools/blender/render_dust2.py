#!/usr/bin/env python3
"""
Render cinematic proof screenshots for Desert Citadel II (hd_dust2) arena using Blender.
"""

import os
import sys
import math
import bpy
from mathutils import Vector, Euler

# Import map generator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_dust2

ARTIFACT_DIR = "/home/horrible/.gemini/antigravity-cli/brain/629f7f1c-b610-40f5-90bf-b196b5283e82"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

def setup_scene():
    generate_dust2.clear_scene()
    generate_dust2.build_dust2_scene()

    # World sky: Warm Mediterranean cyan-blue sky
    if bpy.context.scene.world:
        world = bpy.context.scene.world
        world.use_nodes = True
        bg_node = world.node_tree.nodes.get("Background")
        if bg_node:
            bg_node.inputs["Color"].default_value = (0.52, 0.72, 0.92, 1.0)
            bg_node.inputs["Strength"].default_value = 1.0

    # Lighting: Golden Moroccan Desert Sunlight
    sun_data = bpy.data.lights.new(name="Desert_Sun", type="SUN")
    sun_data.energy = 5.2
    sun_data.color = (1.0, 0.95, 0.85)
    sun_obj = bpy.data.objects.new(name="Desert_Sun", object_data=sun_data)
    sun_obj.rotation_euler = Euler((math.radians(50.0), math.radians(16.0), math.radians(45.0)), "XYZ")
    bpy.context.scene.collection.objects.link(sun_obj)

    # Ambient Sky Fill
    amb_data = bpy.data.lights.new(name="Sky_Ambient", type="POINT")
    amb_data.energy = 2800.0
    amb_data.color = (0.78, 0.90, 1.0)
    amb_obj = bpy.data.objects.new(name="Sky_Ambient", object_data=amb_data)
    amb_obj.location = (35.0, 35.0, 12.0)
    bpy.context.scene.collection.objects.link(amb_obj)

    # Tunnel lantern illumination
    for ty in [26.0, 32.0, 38.0]:
        l_data = bpy.data.lights.new(name=f"Tunnel_Light_{int(ty)}", type="POINT")
        l_data.energy = 600.0
        l_data.color = (1.0, 0.75, 0.40)
        l_obj = bpy.data.objects.new(name=f"Tunnel_Light_{int(ty)}", object_data=l_data)
        l_obj.location = (15.0, ty, 2.6)
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
    print("Setting up HD Dust II 3D scene...")
    setup_scene()

    shots = [
        ("Cam_Overview", (62.0, 10.0, 24.0), (34.0, 38.0, 2.0), "dust2_proof_01_overview.png"),
        ("Cam_SiteA", (52.0, 38.0, 2.4), (48.0, 54.0, 2.2), "dust2_proof_02_site_a.png"),
        ("Cam_MidDoors", (30.8, 22.0, 1.8), (30.8, 48.0, 2.0), "dust2_proof_03_mid_doors.png"),
        ("Cam_SiteB", (22.0, 48.0, 2.2), (14.0, 55.0, 1.5), "dust2_proof_04_site_b.png"),
        ("Cam_Tunnels", (15.0, 26.0, 1.7), (15.0, 36.0, 1.8), "dust2_proof_05_tunnels.png"),
    ]

    for name, loc, target, filename in shots:
        render_shot(name, loc, target, filename)

    print("All Dust II proof renders completed successfully!")

if __name__ == "__main__":
    main()
