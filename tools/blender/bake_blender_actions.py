"""Bake choreographed inspect animations into native Blender Actions and setup preview scene."""

import json
import math
from pathlib import Path
import bpy
from mathutils import Euler, Matrix, Vector

REPO_ROOT = Path(r"c:\Users\Horrible\Code\horrible-dashboard")
INSPECTS_PATH = REPO_ROOT / "packages" / "core" / "src" / "modules" / "hassault" / "models" / "inspects.json"


def catmull_rom_1d(p0: float, p1: float, p2: float, p3: float, u: float) -> float:
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * u
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * (u * u)
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * (u * u * u)
    )


def catmull_rom_3d(p0, p1, p2, p3, u):
    return [catmull_rom_1d(p0[i], p1[i], p2[i], p3[i], u) for i in range(3)]


def smootherstep(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def sample_clip(clip: dict, t_norm: float) -> dict:
    keys = clip["keys"]
    t_clamped = max(0.0, min(1.0, t_norm))
    if t_clamped <= keys[0]["t"]:
        return {"pos": list(keys[0]["pos"]), "rot": list(keys[0]["rot"])}
    if t_clamped >= keys[-1]["t"]:
        return {"pos": list(keys[-1]["pos"]), "rot": list(keys[-1]["rot"])}

    idx = 0
    while idx < len(keys) - 2 and keys[idx + 1]["t"] < t_clamped:
        idx += 1

    k1 = keys[idx]
    k2 = keys[idx + 1]
    k0 = keys[max(0, idx - 1)]
    k3 = keys[min(len(keys) - 1, idx + 2)]

    span = k2["t"] - k1["t"]
    u = 0.0 if span <= 1e-9 else (t_clamped - k1["t"]) / span

    pos = catmull_rom_3d(k0["pos"], k1["pos"], k2["pos"], k3["pos"], u)
    rot = catmull_rom_3d(k0["rot"], k1["rot"], k2["rot"], k3["rot"], u)

    damping = 1.0
    if t_clamped < 0.05:
        damping = smootherstep(t_clamped / 0.05)
    elif t_clamped > 0.95:
        damping = smootherstep((1.0 - t_clamped) / 0.05)

    pos = [p * damping for p in pos]
    rot = [r * damping for r in rot]
    return {"pos": pos, "rot": rot}


def bake_clip_to_action(obj: bpy.types.Object, clip_name: str, clip_data: dict, fps: int = 60) -> bpy.types.Action:
    duration = clip_data.get("duration", 1.5)
    total_frames = int(round(duration * fps))
    origin = Vector(clip_data.get("origin", [0.0, 0.0, 0.0]))

    # Ensure animation data
    if not obj.animation_data:
        obj.animation_data_create()

    action = bpy.data.actions.new(name=f"Inspect_{clip_name}")
    obj.animation_data.action = action

    # Sample and insert keyframes across the animation
    for frame in range(1, total_frames + 1):
        t_norm = (frame - 1) / float(max(1, total_frames - 1))
        sample = sample_clip(clip_data, t_norm)
        pos = Vector(sample["pos"])
        rot = Euler(sample["rot"], 'XYZ')

        # If origin is non-zero (e.g. karambit ring spin), rotate around origin point
        if origin.length > 1e-6:
            r_mat = rot.to_matrix().to_4x4()
            offset_from_origin = -origin
            rotated_offset = r_mat @ offset_from_origin
            world_pos = origin + rotated_offset + pos
        else:
            world_pos = pos

        obj.location = world_pos
        obj.rotation_euler = rot

        obj.keyframe_insert(data_path="location", frame=frame)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)

    return action


def setup_scene_and_camera():
    scene = bpy.context.scene
    scene.render.fps = 60

    # Ensure Sun / Key light
    light_name = "InspectKeyLight"
    light = bpy.data.objects.get(light_name)
    if not light:
        light_data = bpy.data.lights.new(name=light_name, type='SUN')
        light_data.energy = 4.5
        light = bpy.data.objects.new(name=light_name, object_data=light_data)
        scene.collection.objects.link(light)
        light.location = (0.5, -1.0, 1.2)
        light.rotation_euler = (math.radians(45), math.radians(15), math.radians(30))

    # Ensure Fill light
    fill_name = "InspectFillLight"
    fill = bpy.data.objects.get(fill_name)
    if not fill:
        fill_data = bpy.data.lights.new(name=fill_name, type='POINT')
        fill_data.energy = 25.0
        fill = bpy.data.objects.new(name=fill_name, object_data=fill_data)
        scene.collection.objects.link(fill)
        fill.location = (-0.4, -0.6, 0.2)

    # Ensure Camera
    cam_name = "InspectCamera"
    cam = bpy.data.objects.get(cam_name)
    if not cam:
        cam_data = bpy.data.cameras.new(name=cam_name)
        cam_data.lens = 50.0  # 50mm portrait lens
        cam = bpy.data.objects.new(name=cam_name, object_data=cam_data)
        scene.collection.objects.link(cam)
        cam.location = (0.0, -0.45, 0.05)
        cam.rotation_euler = (math.radians(85), 0.0, 0.0)

    scene.camera = cam


def main():
    clips = json.loads(INSPECTS_PATH.read_text(encoding="utf-8"))
    obj = bpy.context.scene.objects.get("Weapon_Knife_Karambit")
    if not obj:
        print("Error: Weapon_Knife_Karambit not in scene")
        return

    setup_scene_and_camera()

    actions = {}
    for clip_name, clip_data in clips.items():
        act = bake_clip_to_action(obj, clip_name, clip_data, fps=60)
        actions[clip_name] = act.name
        print(f"Baked {clip_name} -> Action '{act.name}' ({len(act.fcurves) if hasattr(act, 'fcurves') else 'ok'} curves)")

    # Set Karambit inspect as active and configure timeline
    karambit_act = bpy.data.actions.get("Inspect_knife-karambit")
    if karambit_act:
        obj.animation_data.action = karambit_act
        duration = clips["knife-karambit"]["duration"]
        bpy.context.scene.frame_start = 1
        bpy.context.scene.frame_end = int(round(duration * 60))
        bpy.context.scene.frame_set(1)

    print(f"\nSuccessfully baked all {len(actions)} inspect animations into Blender 5.2 Actions!")


if __name__ == "__main__":
    main()
