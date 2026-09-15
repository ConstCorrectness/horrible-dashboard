#!/usr/bin/env python3
"""
Photorealistic Cycles Renderer for Nuclear Containment Facility (hd_nuke).
Generates 5 proof renders to verify the spatial layout, verticality, and materials.

Outputs:
  - mirage_proof_01_overview.png -> nuke_proof_01_overview.png
  - nuke_proof_02_site_a_rafters.png
  - nuke_proof_03_site_b_reactor.png
  - nuke_proof_04_vents_shaft.png
  - nuke_proof_05_outside_silo.png
"""

import os
import sys
import math

try:
    import bpy
    from mathutils import Vector, Euler
except ImportError:
    print("Error: render_nuke.py must be run from within Blender.")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_nuke import build_nuke_scene


def setup_cycles_lighting():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 64
    scene.cycles.preview_samples = 16
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720

    # Sun lamp
    sun_data = bpy.data.lights.new(name="Sun_Key", type='SUN')
    sun_data.energy = 3.5
    sun_data.color = (1.0, 0.96, 0.90)
    sun_obj = bpy.data.objects.new(name="Sun_Key", object_data=sun_data)
    scene.collection.objects.link(sun_obj)
    sun_obj.rotation_euler = Euler((math.radians(52.0), math.radians(18.0), math.radians(-42.0)), 'XYZ')

    # Reactor interior point lights
    p1 = bpy.data.lights.new(name="Point_Reactor_Cyan", type='POINT')
    p1.energy = 850.0
    p1.color = (0.15, 0.85, 0.98)
    p1_obj = bpy.data.objects.new(name="Point_Reactor_Cyan", object_data=p1)
    p1_obj.location = (36.0, 40.0, -2.0)
    scene.collection.objects.link(p1_obj)

    p2 = bpy.data.lights.new(name="Point_SiteA_Warm", type='POINT')
    p2.energy = 600.0
    p2.color = (1.0, 0.85, 0.65)
    p2_obj = bpy.data.objects.new(name="Point_SiteA_Warm", object_data=p2)
    p2_obj.location = (36.0, 40.0, 4.5)
    scene.collection.objects.link(p2_obj)


def set_camera(cam_obj, loc, target):
    cam_obj.location = Vector(loc)
    direction = Vector(target) - Vector(loc)
    rot_quat = direction.to_track_quat('-Z', 'Y')
    cam_obj.rotation_euler = rot_quat.to_euler()


def render_shots():
    out_dir = "/home/horrible/.gemini/antigravity-cli/brain/629f7f1c-b610-40f5-90bf-b196b5283e82"
    os.makedirs(out_dir, exist_ok=True)

    cam_data = bpy.data.cameras.new("RenderCam")
    cam_data.lens = 28
    cam_data.clip_start = 0.1
    cam_data.clip_end = 250.0
    cam_obj = bpy.data.objects.new("RenderCam", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    shots = [
        ("nuke_proof_01_overview.png", (64.0, 10.0, 32.0), (35.0, 38.0, -1.0)),
        ("nuke_proof_02_site_a_rafters.png", (48.0, 48.0, 4.6), (32.0, 36.0, 1.2)),
        ("nuke_proof_03_site_b_reactor.png", (28.0, 32.0, -3.8), (36.0, 40.0, -2.0)),
        ("nuke_proof_04_vents_shaft.png", (31.0, 33.0, 0.8), (31.0, 36.0, -3.0)),
        ("nuke_proof_05_outside_silo.png", (10.0, 12.0, 2.0), (22.0, 30.0, 2.0)),
    ]

    for fname, loc, target in shots:
        print(f"Rendering {fname} from {loc} to {target}...")
        set_camera(cam_obj, loc, target)
        out_path = os.path.join(out_dir, fname)
        bpy.context.scene.render.filepath = out_path
        bpy.ops.render.render(write_still=True)
        print(f"Saved {out_path}")

    print("All Nuke proof renders completed successfully!")


def main():
    print("Setting up HD Nuke 3D scene...")
    build_nuke_scene()
    setup_cycles_lighting()
    render_shots()


if __name__ == "__main__":
    main()
