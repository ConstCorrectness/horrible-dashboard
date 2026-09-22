#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for High-Rise Corporate Headquarters (hd_office).
Generates an ultra-realistic corporate skyscraper floor on a 64m x 64m footprint (centered at 32, 32):
- Executive Boardroom (X: 6..24, Y: 40..58) with mahogany conference table, executive chairs, and AV screen.
- IT Datacenter / Server Vault (X: 6..24, Y: 6..24) with 42U server racks, status LEDs, and observation window.
- Central Reception Lobby (X: 24..38, Y: 24..40) with marble desk and stainless steel elevators.
- Open-Plan Cubicle Office (X: 38..60, Y: 10..54) with acoustic partitions, L-desks, dual monitors, and filing cabinets.
- Perimeter curtain-wall mullioned windows with metropolitan views.
- Suspended ceiling grid with recessed fluorescent troffer lighting.

Outputs:
  - backend/modules/hassault/maps/hd_office.glb
  - apps/web/public/hd_office.glb
"""

import os
import sys
import math
import shutil

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: generate_office.py must be run from within Blender 4.2+ (e.g. `blender --background --python ...`)")
    sys.exit(1)


def clear_scene():
    """Remove all default objects, meshes, materials, and collections."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def get_or_create_collection(name):
    col = bpy.data.collections.get(name)
    if not col:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.5, emission_color=None, emission_strength=0.0, alpha=1.0, bump_strength=0.0, bump_scale=24.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    bsdf.inputs["Base Color"].default_value = (*base_color[:3], alpha)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness

    if emission_color and emission_strength > 0.0:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*emission_color[:3], 1.0)
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = (*emission_color[:3], 1.0)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission_strength

    if alpha < 1.0:
        if "Transmission Weight" in bsdf.inputs:
            bsdf.inputs["Transmission Weight"].default_value = 1.0 - alpha
        elif "Transmission" in bsdf.inputs:
            bsdf.inputs["Transmission"].default_value = 1.0 - alpha
        mat.blend_method = 'BLEND'

    if bump_strength > 0.0:
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-600, -200)
        noise = nodes.new(type="ShaderNodeTexNoise")
        noise.location = (-400, -200)
        noise.inputs["Scale"].default_value = bump_scale
        noise.inputs["Detail"].default_value = 4.0
        bump = nodes.new(type="ShaderNodeBump")
        bump.location = (-200, -200)
        bump.inputs["Strength"].default_value = bump_strength
        mat.node_tree.links.new(tex_coord.outputs["Generated"], noise.inputs["Vector"])
        mat.node_tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        mat.node_tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    mats = {}
    mats["marble_floor"] = create_pbr_material("mat_marble_floor", (0.88, 0.88, 0.90), metallic=0.05, roughness=0.15, bump_strength=0.06, bump_scale=30.0)
    mats["carpet_tile"] = create_pbr_material("mat_carpet_tile", (0.20, 0.22, 0.24), metallic=0.0, roughness=0.85, bump_strength=0.25, bump_scale=35.0)
    mats["mahogany"] = create_pbr_material("mat_mahogany", (0.28, 0.12, 0.08), metallic=0.0, roughness=0.25, bump_strength=0.22, bump_scale=14.0)
    mats["wall_drywall"] = create_pbr_material("mat_wall_drywall", (0.84, 0.85, 0.86), metallic=0.0, roughness=0.7, bump_strength=0.12, bump_scale=22.0)
    mats["wall_accent"] = create_pbr_material("mat_wall_accent", (0.14, 0.24, 0.35), metallic=0.05, roughness=0.5, bump_strength=0.10, bump_scale=25.0)
    mats["stainless"] = create_pbr_material("mat_stainless", (0.75, 0.76, 0.78), metallic=0.92, roughness=0.22, bump_strength=0.08, bump_scale=32.0)
    mats["black_metal"] = create_pbr_material("mat_black_metal", (0.08, 0.08, 0.09), metallic=0.7, roughness=0.45, bump_strength=0.10, bump_scale=28.0)
    mats["smoked_glass"] = create_pbr_material("mat_smoked_glass", (0.2, 0.25, 0.3), metallic=0.1, roughness=0.05, alpha=0.35)
    mats["acoustic_felt"] = create_pbr_material("mat_acoustic_felt", (0.15, 0.35, 0.45), metallic=0.0, roughness=0.9, bump_strength=0.30, bump_scale=40.0)
    mats["ceiling_tile"] = create_pbr_material("mat_ceiling_tile", (0.92, 0.92, 0.93), metallic=0.0, roughness=0.8, bump_strength=0.18, bump_scale=20.0)
    mats["fluorescent"] = create_pbr_material("mat_fluorescent", (0.95, 0.97, 1.0), metallic=0.0, roughness=0.2, emission_color=(0.95, 0.97, 1.0), emission_strength=4.0)
    mats["server_leds"] = create_pbr_material("mat_server_leds", (0.0, 1.0, 0.6), metallic=0.0, roughness=0.2, emission_color=(0.0, 1.0, 0.6), emission_strength=6.0)
    mats["screen_display"] = create_pbr_material("mat_screen_display", (0.1, 0.4, 0.8), metallic=0.0, roughness=0.15, emission_color=(0.1, 0.5, 0.9), emission_strength=3.0)
    return mats


def add_box(collection, name, center, size, material):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_cylinder(collection, name, center, radius, height, material, segments=16):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=height
    )
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def build_office_architecture(col, mats):
    """Floor slab, carpet zones, server raised floor, and perimeter curtain walls."""
    # Main marble floor slab (60m x 58m, centered at 32, 32, Z = 0.0)
    add_box(col, "Floor_Main_Marble", (32.0, 32.0, -0.2), (60.0, 58.0, 0.4), mats["marble_floor"])

    # Boardroom and Server room floor offsets
    add_box(col, "Floor_Boardroom_Carpet", (12.0, 48.0, -0.18), (18.0, 20.0, 0.4), mats["carpet_tile"])
    add_box(col, "Floor_Server_Raised", (12.0, 16.0, -0.1), (18.0, 20.0, 0.4), mats["stainless"])

    # High ceiling slab with acoustic drop tiles (z = 4.2m)
    add_box(col, "Ceiling_Main", (32.0, 32.0, 4.2), (60.0, 58.0, 0.4), mats["ceiling_tile"])

    # North exterior wall with breakable curtain windows (Y = 60.0)
    for x in range(6, 60, 8):
        add_box(col, f"Window_Glass_Curtain_N_{x}", (x, 60.0, 2.0), (7.4, 0.2, 4.0), mats["smoked_glass"])
        add_box(col, f"Mullion_N_{x}_NonCol", (x + 3.8, 60.0, 2.0), (0.4, 0.4, 4.0), mats["black_metal"])

    # South exterior wall with breakable curtain windows (Y = 4.0)
    for x in range(6, 60, 8):
        add_box(col, f"Window_Glass_Curtain_S_{x}", (x, 4.0, 2.0), (7.4, 0.2, 4.0), mats["smoked_glass"])
        add_box(col, f"Mullion_S_{x}_NonCol", (x + 3.8, 4.0, 2.0), (0.4, 0.4, 4.0), mats["black_metal"])

    # East/West solid perimeter walls
    add_box(col, "Wall_West_Drywall", (3.0, 32.0, 2.0), (0.4, 56.0, 4.0), mats["wall_drywall"])
    add_box(col, "Wall_East_Drywall", (61.0, 32.0, 2.0), (0.4, 56.0, 4.0), mats["wall_drywall"])

    # Heavy structural concrete pillars
    for px in [12.0, 27.0, 42.0, 56.0]:
        for py in [14.0, 32.0, 50.0]:
            add_box(col, f"Pillar_{int(px)}_{int(py)}", (px, py, 2.0), (1.2, 1.2, 4.0), mats["wall_accent"])


def build_executive_boardroom(col, mats):
    """Executive Boardroom (X: 4..22, Y: 38..58) with conference table, chairs, and AV screen."""
    # Partition walls dividing Boardroom
    add_box(col, "Boardroom_Wall_E", (22.0, 48.0, 2.0), (0.3, 20.0, 4.0), mats["wall_drywall"])
    add_box(col, "Boardroom_Wall_S", (12.0, 38.0, 2.0), (18.0, 0.3, 4.0), mats["wall_drywall"])
    # Boardroom entrance double breakable glass doors
    add_box(col, "Window_Glass_Boardroom", (22.0, 48.0, 1.4), (0.1, 2.8, 2.8), mats["smoked_glass"])

    # 8.0m x 2.4m Mahogany Conference Table at (12.0, 48.0, 0.75) (penetrable wood)
    add_box(col, "Wood_Table_Top_Mahogany", (12.0, 48.0, 0.75), (8.0, 2.4, 0.1), mats["mahogany"])
    add_box(col, "Table_Pedestal_W", (9.5, 48.0, 0.35), (0.8, 1.4, 0.7), mats["stainless"])
    add_box(col, "Table_Pedestal_E", (14.5, 48.0, 0.35), (0.8, 1.4, 0.7), mats["stainless"])
    add_box(col, "Table_Cable_Well_NonCol", (12.0, 48.0, 0.76), (2.0, 0.3, 0.02), mats["black_metal"])

    # Executive Leather Chairs around conference table (NonCol)
    for i in range(5):
        cx = 9.0 + i * 1.5
        # North side
        add_box(col, f"Chair_Seat_N_{i}_NonCol", (cx, 49.8, 0.48), (0.55, 0.55, 0.08), mats["black_metal"])
        add_box(col, f"Chair_Back_N_{i}_NonCol", (cx, 50.05, 0.85), (0.55, 0.08, 0.7), mats["black_metal"])
        # South side
        add_box(col, f"Chair_Seat_S_{i}_NonCol", (cx, 46.2, 0.48), (0.55, 0.55, 0.08), mats["black_metal"])
        add_box(col, f"Chair_Back_S_{i}_NonCol", (cx, 45.95, 0.85), (0.55, 0.08, 0.7), mats["black_metal"])

    # 98-inch 4K Wall AV Presentation Screen on West Wall
    add_box(col, "AV_Display_Frame_NonCol", (3.3, 48.0, 2.2), (0.1, 4.2, 2.2), mats["black_metal"])
    add_box(col, "AV_Display_Screen_NonCol", (3.36, 48.0, 2.2), (0.02, 4.0, 2.0), mats["screen_display"])


def build_cubicle_farm(col, mats):
    """Open-plan office with acoustic divider cubicles and computer workstations (X: 38..60)."""
    for row_x in [42.0, 49.0, 56.0]:
        for pod_y in [16.0, 24.0, 34.0, 42.0, 50.0]:
            # Acoustic fabric center privacy divider (1.4m high) (penetrable)
            add_box(col, f"Wood_Cubicle_Spine_{int(row_x)}_{int(pod_y)}", (row_x, pod_y, 0.7), (4.5, 0.1, 1.4), mats["acoustic_felt"])
            add_box(col, f"Wood_Cubicle_Divider_1_{int(row_x)}_{int(pod_y)}", (row_x - 2.2, pod_y, 0.7), (0.1, 2.6, 1.4), mats["acoustic_felt"])
            add_box(col, f"Wood_Cubicle_Divider_2_{int(row_x)}_{int(pod_y)}", (row_x + 2.2, pod_y, 0.7), (0.1, 2.6, 1.4), mats["acoustic_felt"])

            # Desks North and South of the spine
            add_box(col, f"Desk_N_{int(row_x)}_{int(pod_y)}", (row_x, pod_y + 0.65, 0.72), (4.0, 1.0, 0.06), mats["wall_drywall"])
            add_box(col, f"Desk_S_{int(row_x)}_{int(pod_y)}", (row_x, pod_y - 0.65, 0.72), (4.0, 1.0, 0.06), mats["wall_drywall"])

            # Dual monitors (NonCol)
            add_box(col, f"Monitor_N1_{int(row_x)}_{int(pod_y)}_NonCol", (row_x - 0.6, pod_y + 0.9, 1.05), (0.7, 0.05, 0.45), mats["black_metal"])
            add_box(col, f"Screen_N1_{int(row_x)}_{int(pod_y)}_NonCol", (row_x - 0.6, pod_y + 0.88, 1.05), (0.66, 0.01, 0.41), mats["screen_display"])

            # Filing cabinets
            add_box(col, f"Cabinet_N_{int(row_x)}_{int(pod_y)}", (row_x - 1.8, pod_y + 0.65, 0.35), (0.5, 0.7, 0.7), mats["black_metal"])
            add_box(col, f"Cabinet_S_{int(row_x)}_{int(pod_y)}", (row_x + 1.8, pod_y - 0.65, 0.35), (0.5, 0.7, 0.7), mats["black_metal"])


def build_reception_and_elevators(col, mats):
    """Central reception lobby with curved marble desk and twin elevators (X: 24..38, Y: 24..40)."""
    # Reception counter at (27.0, 32.0, 0.55)
    add_box(col, "Reception_Counter_Main", (27.0, 32.0, 0.55), (1.4, 5.0, 1.1), mats["marble_floor"])
    add_box(col, "Reception_Counter_WingN", (27.8, 34.6, 0.55), (2.0, 0.8, 1.1), mats["marble_floor"])
    add_box(col, "Reception_Counter_WingS", (27.8, 29.4, 0.55), (2.0, 0.8, 1.1), mats["marble_floor"])
    add_box(col, "Reception_Top_Quartz", (27.0, 32.0, 1.12), (1.5, 5.2, 0.06), mats["stainless"])

    # Twin Elevator Core at (17.0, 32.0, 2.0)
    add_box(col, "Elevator_Shaft_Wall", (17.0, 32.0, 2.0), (3.0, 9.0, 4.0), mats["wall_accent"])

    # Elevator A (North)
    add_box(col, "Elevator_A_Frame_NonCol", (18.6, 34.5, 1.5), (0.2, 2.4, 3.0), mats["stainless"])
    add_box(col, "Elevator_A_DoorL_NonCol", (18.62, 33.9, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_A_DoorR_NonCol", (18.62, 35.1, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_A_Indicator_NonCol", (18.65, 34.5, 3.2), (0.02, 0.8, 0.25), mats["screen_display"])

    # Elevator B (South)
    add_box(col, "Elevator_B_Frame_NonCol", (18.6, 29.5, 1.5), (0.2, 2.4, 3.0), mats["stainless"])
    add_box(col, "Elevator_B_DoorL_NonCol", (18.62, 28.9, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_B_DoorR_NonCol", (18.62, 30.1, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_B_Indicator_NonCol", (18.65, 29.5, 3.2), (0.02, 0.8, 0.25), mats["screen_display"])


def build_server_vault(col, mats):
    """High-density IT server room with glass observation wall and server banks (X: 4..22, Y: 6..26)."""
    # Breakable glass observation partition walls
    add_box(col, "Window_Glass_Server_N", (12.0, 26.0, 2.0), (18.0, 0.2, 4.0), mats["smoked_glass"])
    add_box(col, "Window_Glass_Server_E", (22.0, 16.0, 2.0), (0.2, 20.0, 4.0), mats["smoked_glass"])

    # 4 rows of 42U server racks
    for r in range(4):
        rx = 7.0 + r * 3.5
        for s in range(5):
            sy = 10.0 + s * 2.4
            add_box(col, f"Server_Rack_{r}_{s}", (rx, sy, 1.1), (0.9, 1.8, 2.2), mats["black_metal"])
            # Status indicator LED strip (NonCol)
            add_box(col, f"Server_LEDs_{r}_{s}_NonCol", (rx + 0.46, sy, 1.1), (0.02, 1.5, 1.8), mats["server_leds"])


def build_lighting(col, mats):
    """Recessed fluorescent troffer panels (NonCol)."""
    for lx in range(8, 58, 8):
        for ly in range(8, 58, 8):
            add_box(col, f"Light_Troffer_{lx}_{ly}_NonCol", (lx, ly, 4.0), (1.4, 2.4, 0.08), mats["fluorescent"])


def build_office_scene():
    clear_scene()
    mats = setup_materials()

    c_arch = get_or_create_collection("Architecture")
    c_boardroom = get_or_create_collection("Boardroom")
    c_cubicles = get_or_create_collection("Cubicles")
    c_lobby = get_or_create_collection("Lobby")
    c_server = get_or_create_collection("ServerRoom")
    c_lights = get_or_create_collection("Lighting")

    build_office_architecture(c_arch, mats)
    build_executive_boardroom(c_boardroom, mats)
    build_cubicle_farm(c_cubicles, mats)
    build_reception_and_elevators(c_lobby, mats)
    build_server_vault(c_server, mats)
    build_lighting(c_lights, mats)

    print("=== High-Rise Corporate Office (hd_office) Built Successfully! ===")


def export_glb():
    backend_map_dir = "/home/horrible/horrible-dashboard/backend/modules/hassault/maps"
    web_public_dir = "/home/horrible/horrible-dashboard/apps/web/public"

    os.makedirs(backend_map_dir, exist_ok=True)
    os.makedirs(web_public_dir, exist_ok=True)

    backend_glb_path = os.path.join(backend_map_dir, "hd_office.glb")
    web_glb_path = os.path.join(web_public_dir, "hd_office.glb")

    print(f"Exporting GLB to: {backend_glb_path} ...")
    bpy.ops.export_scene.gltf(
        filepath=backend_glb_path,
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials='EXPORT',
        export_lights=False,
        export_cameras=False
    )

    print(f"Copying GLB to Web: {web_glb_path} ...")
    shutil.copyfile(backend_glb_path, web_glb_path)
    print("=== Office Generation & Export Complete! ===")


if __name__ == "__main__":
    build_office_scene()
    export_glb()
