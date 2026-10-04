"""Render a player's-eye view of a modelled map from its spawns, for review.

    blender --background --factory-startup --python tools/blender/render_map_views.py -- hd_dust2 [out_dir] [glb]

Imports the map's GLB (the committed one unless a path is given), lights it with
a sun and a sky, and renders one frame from the first spawn of each team: eye
height above the floor under the spawn, looking at the map's centre. A GLB is in
cube units with Blender's own axes, so the JSON's spawn coordinates place the
camera directly.

Not a substitute for the game's renderer (no detail textures, a different light
rig). It is for judging shape: silhouettes, edges, how blocky a skyline reads.
"""

from __future__ import annotations

import json
import math
import os
import sys

import bpy
from mathutils import Vector

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
MAPS_DIR = os.path.join(REPO_ROOT, "backend", "modules", "hassault", "maps")
EYE_HEIGHT = 4.5


def _args() -> tuple[str, str, str]:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: ... -- <map> [out_dir] [glb]")
    name = argv[0]
    out_dir = argv[1] if len(argv) > 1 else os.path.join(REPO_ROOT, "logs", "map-views")
    glb = argv[2] if len(argv) > 2 else os.path.join(MAPS_DIR, f"{name}.glb")
    return name, out_dir, glb


def _floor_under(x: float, y: float, top: float) -> float:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, location, *_ = bpy.context.scene.ray_cast(
        depsgraph, Vector((x, y, top)), Vector((0, 0, -1))
    )
    return location.z if hit else 0.0


def main() -> None:
    name, out_dir, glb = _args()
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=glb)

    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        if "ColOnly" in obj.name or "Invisible" in obj.name:
            obj.hide_render = True
            continue
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    centre = (lo + hi) / 2

    scene = bpy.context.scene
    world = bpy.data.worlds.new("Sky")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (
        0.55,
        0.68,
        0.85,
        1,
    )
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
    scene.world = world
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.5
    sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
    scene.collection.objects.link(sun)

    engines = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    scene.render.engine = (
        "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    )
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"

    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.data.lens = 18
    cam.data.clip_end = 2000
    scene.collection.objects.link(cam)
    scene.camera = cam

    with open(os.path.join(MAPS_DIR, f"{name}.json"), encoding="utf-8") as f:
        source = json.load(f)
    spawns = source.get("spawns") or [
        e for e in source.get("entities", []) if e.get("type") == "playerstart"
    ]
    seen: set[int] = set()
    for spawn in spawns:
        team = int(spawn.get("team", 0))
        if team in seen:
            continue
        seen.add(team)
        x, y = float(spawn["x"]), float(spawn["y"])
        # From just above the spawn, not from the sky: a map with a roof or a
        # second storey would otherwise put the camera on top of it.
        floor = _floor_under(x, y, float(spawn.get("z", 0)) + 6.0)
        cam.location = Vector((x, y, floor + EYE_HEIGHT))
        target = Vector((centre.x, centre.y, floor + EYE_HEIGHT))
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(out_dir, f"{name}_team{team}.png")
        bpy.ops.render.render(write_still=True)
        print(f"rendered {scene.render.filepath}")


main()
