"""Setup a complete tactical knife armory and inspect animation showroom in Blender 5.2."""

import json
import math
from pathlib import Path
import bpy
from mathutils import Euler, Matrix, Vector

REPO_ROOT = Path(r"c:\Users\Horrible\Code\horrible-dashboard")
PUBLIC_DIR = REPO_ROOT / "apps" / "web" / "public"
INSPECTS_PATH = REPO_ROOT / "packages" / "core" / "src" / "modules" / "hassault" / "models" / "inspects.json"
BLEND_OUTPUT = REPO_ROOT / "tools" / "blender" / "tactical_knives_showroom.blend"


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


def bake_action_for_object(
    obj: bpy.types.Object,
    clip_name: str,
    clip_data: dict,
    fps: int = 60,
    base_loc: Vector = None,
    base_rot: Euler = None,
) -> bpy.types.Action:
    """Bakes inspect animation keyframes directly into the object's active Blender 5.2 action and slot."""
    duration = clip_data.get("duration", 1.5)
    total_frames = int(round(duration * fps))
    origin = Vector(clip_data.get("origin", [0.0, 0.0, 0.0]))

    act_name = f"Action_{clip_name}"
    action = bpy.data.actions.get(act_name)
    if not action:
        action = bpy.data.actions.new(name=act_name)
    action.use_fake_user = True

    if not obj.animation_data:
        obj.animation_data_create()
    obj.animation_data.action = action

    if base_loc is None:
        base_loc = Vector(obj.location)
    if base_rot is None:
        base_rot = Euler(obj.rotation_euler, 'XYZ')

    base_rot_mat = base_rot.to_matrix().to_4x4()

    for frame in range(1, total_frames + 1):
        t_norm = (frame - 1) / float(max(1, total_frames - 1))
        sample = sample_clip(clip_data, t_norm)
        pos = Vector(sample["pos"])
        rot = Euler(sample["rot"], 'XYZ')

        if origin.length > 1e-6:
            r_mat = rot.to_matrix().to_4x4()
            rotated_offset = r_mat @ (-origin)
            anim_offset = origin + rotated_offset + pos
        else:
            anim_offset = pos

        # Apply animation transform relative to base pose
        transformed_anim_pos = base_rot_mat @ anim_offset
        world_pos = base_loc + transformed_anim_pos
        anim_rot_mat = rot.to_matrix().to_4x4()
        world_rot = (base_rot_mat @ anim_rot_mat).to_euler('XYZ')

        obj.location = world_pos
        obj.rotation_euler = world_rot

        obj.keyframe_insert(data_path="location", frame=frame)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)

    # In Blender 5.2 (Slotted Actions), explicitly assign and verify the active action_slot
    if hasattr(action, 'slots') and action.slots:
        for s in action.slots:
            if s.identifier == f"OB{obj.name}" or getattr(s, 'name_display', '') == obj.name:
                obj.animation_data.action_slot = s
                break
        if obj.animation_data.action_slot is None:
            obj.animation_data.action_slot = action.slots[0]

    return action


def setup_materials():
    mat_floor = bpy.data.materials.get("Mat_Studio_Floor")
    if not mat_floor:
        mat_floor = bpy.data.materials.new(name="Mat_Studio_Floor")
        mat_floor.use_nodes = True
        nodes = mat_floor.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.035, 0.035, 0.045, 1.0)
            bsdf.inputs["Metallic"].default_value = 0.6
            bsdf.inputs["Roughness"].default_value = 0.22

    mat_pedestal = bpy.data.materials.get("Mat_Pedestal")
    if not mat_pedestal:
        mat_pedestal = bpy.data.materials.new(name="Mat_Pedestal")
        mat_pedestal.use_nodes = True
        nodes = mat_pedestal.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.015, 0.015, 0.02, 1.0)
            bsdf.inputs["Metallic"].default_value = 0.9
            bsdf.inputs["Roughness"].default_value = 0.15

    return mat_floor, mat_pedestal


def build_showroom():
    # Clear existing objects
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    scene = bpy.context.scene
    scene.render.fps = 60

    mat_floor, mat_pedestal = setup_materials()

    # Studio Floor
    bpy.ops.mesh.primitive_cylinder_add(radius=2.0, depth=0.02, location=(0, 0, -0.01))
    floor_obj = bpy.context.active_object
    floor_obj.name = "Studio_Floor"
    floor_obj.data.materials.append(mat_floor)

    # 3-Point Lighting
    key_light_data = bpy.data.lights.new(name="Key_Light", type='AREA')
    key_light_data.energy = 90.0
    key_light_data.size = 0.9
    key_light_data.color = (1.0, 0.96, 0.90)
    key_light = bpy.data.objects.new(name="Key_Light", object_data=key_light_data)
    scene.collection.objects.link(key_light)
    key_light.location = (0.50, -0.75, 0.70)
    key_light.rotation_euler = (math.radians(50), math.radians(10), math.radians(25))

    rim_light_data = bpy.data.lights.new(name="Rim_Light", type='AREA')
    rim_light_data.energy = 130.0
    rim_light_data.size = 0.7
    rim_light_data.color = (0.70, 0.88, 1.0)
    rim_light = bpy.data.objects.new(name="Rim_Light", object_data=rim_light_data)
    scene.collection.objects.link(rim_light)
    rim_light.location = (-0.60, 0.70, 0.50)
    rim_light.rotation_euler = (math.radians(-45), math.radians(15), math.radians(-140))

    fill_light_data = bpy.data.lights.new(name="Fill_Light", type='AREA')
    fill_light_data.energy = 40.0
    fill_light_data.size = 1.4
    fill_light_data.color = (0.85, 0.90, 0.95)
    fill_light = bpy.data.objects.new(name="Fill_Light", object_data=fill_light_data)
    scene.collection.objects.link(fill_light)
    fill_light.location = (-0.70, -0.55, 0.35)
    fill_light.rotation_euler = (math.radians(35), math.radians(-20), math.radians(-40))

    # Showroom Camera
    cam_data = bpy.data.cameras.new(name="Showroom_Camera")
    cam_data.lens = 45.0
    cam_obj = bpy.data.objects.new(name="Showroom_Camera", object_data=cam_data)
    scene.collection.objects.link(cam_obj)
    cam_obj.location = (0.0, -0.62, 0.28)
    cam_obj.rotation_euler = (math.radians(68), 0.0, 0.0)
    scene.camera = cam_obj

    # 6 Shipped Tactical Knives Lineup
    knives_meta = [
        ("Tactical Tanto", "hassault-weapon-knife.glb", "knife-tactical", -0.55),
        ("Karambit Fade", "hassault-weapon-knife-karambit.glb", "knife-karambit", -0.33),
        ("Butterfly Marble Fade", "hassault-weapon-knife-butterfly.glb", "knife-butterfly", -0.11),
        ("M9 Bayonet Lore", "hassault-weapon-knife-bayonet.glb", "knife-bayonet", 0.11),
        ("Skeleton Crimson Web", "hassault-weapon-knife-skeleton.glb", "knife-skeleton", 0.33),
        ("Huntsman Case Hardened", "hassault-weapon-knife-huntsman.glb", "knife-huntsman", 0.55),
    ]

    clips_data = json.loads(INSPECTS_PATH.read_text(encoding="utf-8"))

    # Import and position the 6 Knives on Pedestals
    for title, glb_filename, clip_name, x_pos in knives_meta:
        glb_file = PUBLIC_DIR / glb_filename
        if not glb_file.exists():
            continue

        bpy.ops.mesh.primitive_cylinder_add(radius=0.07, depth=0.06, location=(x_pos, 0.08, 0.03))
        ped = bpy.context.active_object
        ped.name = f"Pedestal_{clip_name}"
        ped.data.materials.append(mat_pedestal)

        before_objs = set(scene.objects)
        bpy.ops.import_scene.gltf(filepath=str(glb_file))
        new_objs = list(set(scene.objects) - before_objs)
        if not new_objs:
            continue

        knife_obj = new_objs[0]
        knife_obj.name = f"Showcase_{title.replace(' ', '_')}"
        base_loc = Vector((x_pos, 0.08, 0.09))
        base_rot = Euler((math.radians(40), math.radians(15), math.radians(-35)), 'XYZ')

        # Bake each knife's inspect animation directly onto it
        clip_data = clips_data.get(clip_name, clips_data["assault"])
        bake_action_for_object(knife_obj, clip_name, clip_data, fps=60, base_loc=base_loc, base_rot=base_rot)

    # Center Hero Animated Knife (Karambit Fade)
    hero_glb = PUBLIC_DIR / "hassault-weapon-knife-karambit.glb"
    before_objs = set(scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(hero_glb))
    hero_objs = list(set(scene.objects) - before_objs)
    if hero_objs:
        hero = hero_objs[0]
        hero.name = "HERO_Karambit_Fade_Animated"
        hero_base_loc = Vector((0.0, -0.16, 0.08))
        hero_base_rot = Euler((math.radians(-25), math.radians(15), math.radians(75)), 'XYZ')

        karambit_clip = clips_data["knife-karambit"]
        bake_action_for_object(
            hero,
            "knife-karambit-hero",
            karambit_clip,
            fps=60,
            base_loc=hero_base_loc,
            base_rot=hero_base_rot,
        )

        duration = karambit_clip["duration"]
        scene.frame_start = 1
        scene.frame_end = int(round(duration * 60))
        scene.frame_set(1)

        bpy.context.view_layer.objects.active = hero
        hero.select_set(True)

    # Configure Viewport
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.region_3d.view_perspective = 'CAMERA'
                    space.shading.type = 'MATERIAL'
                    space.overlay.show_extras = False
                    space.overlay.show_relationship_lines = False
                    space.overlay.show_outline_selected = False
                    space.overlay.show_cursor = False
                    space.overlay.show_floor = False
                    space.overlay.show_axis_x = False
                    space.overlay.show_axis_y = False
                    space.overlay.show_axis_z = False

    print(f"\nShowroom setup complete! Animation range: {scene.frame_start} to {scene.frame_end} at {scene.render.fps} FPS.")


if __name__ == "__main__":
    build_showroom()
    BLEND_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_OUTPUT))
    print(f"Saved verified Blender project to: {BLEND_OUTPUT}")
