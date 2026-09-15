#!/usr/bin/env python3
"""
Render cinematic proof screenshots for hd_office arena using Blender.
"""

import os
import sys
import math
import bpy
from mathutils import Vector, Euler

# Import map generator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_office

ARTIFACT_DIR = "/home/horrible/.gemini/antigravity-cli/brain/629f7f1c-b610-40f5-90bf-b196b5283e82"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

def setup_scene():
    generate_office.clear_scene()
    col = generate_office.get_or_create_collection("HD_Office_Geometry")
    mats = generate_office.setup_materials()
    generate_office.build_office_architecture(col, mats)
    generate_office.build_executive_boardroom(col, mats)
    generate_office.build_cubicle_farm(col, mats)
    generate_office.build_reception_and_elevators(col, mats)
    generate_office.build_server_vault(col, mats)
    generate_office.build_lighting_and_hvac(col, mats)

    # Lighting
    # Sun light through windows
    sun_data = bpy.data.lights.new(name="Sun", type="SUN")
    sun_data.energy = 4.0
    sun_data.color = (1.0, 0.96, 0.90)
    sun_obj = bpy.data.objects.new(name="Sun", object_data=sun_data)
    sun_obj.rotation_euler = Euler((math.radians(45.0), math.radians(20.0), math.radians(65.0)), "XYZ")
    bpy.context.scene.collection.objects.link(sun_obj)

    # Ambient interior bounce
    amb_data = bpy.data.lights.new(name="Ambient_Fill", type="POINT")
    amb_data.energy = 2500.0
    amb_data.color = (0.9, 0.93, 1.0)
    amb_obj = bpy.data.objects.new(name="Ambient_Fill", object_data=amb_data)
    amb_obj.location = (0.0, 0.0, 3.5)
    bpy.context.scene.collection.objects.link(amb_obj)

    # Render settings
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items else "BLENDER_EEVEE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.image_settings.file_format = "PNG"

def render_shot(cam_name, loc, target, filename):
    cam_data = bpy.data.cameras.new(name=cam_name)
    cam_data.lens = 28.0
    cam_data.clip_start = 0.1
    cam_data.clip_end = 200.0
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
    print("Setting up HD Office 3D scene...")
    setup_scene()

    shots = [
        ("Cam_Overview", (24.0, -22.0, 7.5), (-5.0, 4.0, 1.0), "office_proof_01_floor_overview.png"),
        ("Cam_Boardroom", (-12.5, 12.0, 1.6), (-20.0, 16.0, 0.9), "office_proof_02_executive_boardroom.png"),
        ("Cam_Cubicles", (3.0, 6.0, 1.6), (15.0, 6.0, 1.0), "office_proof_03_cubicle_farm.png"),
        ("Cam_Datacenter", (-14.0, -12.0, 1.7), (-22.0, -18.0, 1.2), "office_proof_04_server_datacenter.png"),
        ("Cam_Reception", (2.0, 0.0, 1.6), (-12.0, 0.0, 1.2), "office_proof_05_reception_elevators.png"),
    ]

    for name, loc, target, filename in shots:
        render_shot(name, loc, target, filename)

    print("All office proof renders completed successfully!")

if __name__ == "__main__":
    main()
