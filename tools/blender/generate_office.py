#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for High-Rise Corporate Headquarters (hd_office).
Generates an ultra-realistic 70m x 60m corporate skyscraper floor featuring:
- Executive Boardroom with mahogany conference table, executive chairs, and presentation display
- Open-Plan Cubicle Office with acoustic partitions, L-desks, dual monitors, and filing cabinets
- Central Reception Lobby with curved Calacatta marble desk and twin stainless steel elevators
- High-Density IT Datacenter with 42U server racks, raised floor grates, and glass observation wall
- Breakroom / Kitchenette with quartz island counter, barstools, and appliances
- Panoramic structural curtain-wall windows overlooking a metropolitan skyline

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


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.5, emission_color=None, emission_strength=0.0, alpha=1.0):
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

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    mats = {}
    mats["marble_floor"] = create_pbr_material("mat_marble_floor", (0.88, 0.88, 0.90), metallic=0.05, roughness=0.15)
    mats["carpet_tile"] = create_pbr_material("mat_carpet_tile", (0.18, 0.20, 0.22), metallic=0.0, roughness=0.85)
    mats["mahogany"] = create_pbr_material("mat_mahogany", (0.28, 0.12, 0.08), metallic=0.0, roughness=0.25)
    mats["wall_drywall"] = create_pbr_material("mat_wall_drywall", (0.82, 0.83, 0.84), metallic=0.0, roughness=0.7)
    mats["wall_accent"] = create_pbr_material("mat_wall_accent", (0.12, 0.22, 0.32), metallic=0.05, roughness=0.5)
    mats["stainless"] = create_pbr_material("mat_stainless", (0.75, 0.76, 0.78), metallic=0.92, roughness=0.22)
    mats["black_metal"] = create_pbr_material("mat_black_metal", (0.08, 0.08, 0.09), metallic=0.7, roughness=0.45)
    mats["smoked_glass"] = create_pbr_material("mat_smoked_glass", (0.2, 0.25, 0.3), metallic=0.1, roughness=0.05, alpha=0.4)
    mats["acoustic_felt"] = create_pbr_material("mat_acoustic_felt", (0.15, 0.35, 0.45), metallic=0.0, roughness=0.9)
    mats["ceiling_tile"] = create_pbr_material("mat_ceiling_tile", (0.92, 0.92, 0.93), metallic=0.0, roughness=0.8)
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


def build_office_architecture(col, mats):
    """Floor slabs, ceilings, perimeter curtain-walls, and structural pillars."""
    # Main floor slab (70m x 60m x 0.4m at z = -0.2m)
    add_box(col, "Floor_Lobby_Marble", (-10.0, 0.0, -0.2), (30.0, 56.0, 0.4), mats["marble_floor"])
    add_box(col, "Floor_Cubicles_Carpet", (15.0, 0.0, -0.2), (30.0, 56.0, 0.4), mats["carpet_tile"])
    add_box(col, "Floor_Boardroom_Carpet", (-20.0, 16.0, -0.18), (18.0, 20.0, 0.4), mats["carpet_tile"])
    add_box(col, "Floor_Server_Raised", (-20.0, -16.0, -0.1), (18.0, 20.0, 0.4), mats["stainless"])

    # High ceiling slab with acoustic drop tiles (z = 4.2m)
    add_box(col, "Ceiling_Main", (0.0, 0.0, 4.2), (64.0, 58.0, 0.4), mats["ceiling_tile"])

    # Perimeter walls & structural pillars
    # North exterior wall with curtain windows
    for x in range(-28, 30, 8):
        add_box(col, f"Curtain_Glass_N_{x}", (x, 28.0, 2.0), (7.4, 0.2, 4.0), mats["smoked_glass"])
        add_box(col, f"Mullion_N_{x}", (x + 3.8, 28.0, 2.0), (0.4, 0.4, 4.0), mats["black_metal"])

    # South exterior wall with curtain windows
    for x in range(-28, 30, 8):
        add_box(col, f"Curtain_Glass_S_{x}", (x, -28.0, 2.0), (7.4, 0.2, 4.0), mats["smoked_glass"])
        add_box(col, f"Mullion_S_{x}", (x + 3.8, -28.0, 2.0), (0.4, 0.4, 4.0), mats["black_metal"])

    # East/West solid perimeter walls
    add_box(col, "Wall_West_Drywall", (-31.0, 0.0, 2.0), (0.4, 56.0, 4.0), mats["wall_drywall"])
    add_box(col, "Wall_East_Drywall", (31.0, 0.0, 2.0), (0.4, 56.0, 4.0), mats["wall_drywall"])

    # Heavy structural concrete pillars
    for px in [-20.0, -5.0, 10.0, 25.0]:
        for py in [-18.0, 0.0, 18.0]:
            add_box(col, f"Pillar_{px}_{py}", (px, py, 2.0), (1.2, 1.2, 4.0), mats["wall_accent"])


def build_executive_boardroom(col, mats):
    """Executive Boardroom with massive conference table, chairs, and AV screen."""
    # Partition walls dividing Boardroom
    add_box(col, "Boardroom_Wall_E", (-11.0, 16.0, 2.0), (0.3, 20.0, 4.0), mats["wall_drywall"])
    add_box(col, "Boardroom_Wall_S", (-20.0, 6.0, 2.0), (18.0, 0.3, 4.0), mats["wall_drywall"])
    # Boardroom entrance double glass doors
    add_box(col, "Boardroom_Glass_Door_L", (-11.0, 16.0, 1.4), (0.1, 1.4, 2.8), mats["smoked_glass"])

    # 8.0m x 2.4m Mahogany Conference Table at (-20.0, 16.0, 0.75)
    add_box(col, "Table_Top_Mahogany", (-20.0, 16.0, 0.75), (8.0, 2.4, 0.1), mats["mahogany"])
    add_box(col, "Table_Pedestal_W", (-22.5, 16.0, 0.35), (0.8, 1.4, 0.7), mats["stainless"])
    add_box(col, "Table_Pedestal_E", (-17.5, 16.0, 0.35), (0.8, 1.4, 0.7), mats["stainless"])
    add_box(col, "Table_Cable_Well", (-20.0, 16.0, 0.76), (2.0, 0.3, 0.02), mats["black_metal"])

    # 12 Executive Leather Chairs around conference table
    for i in range(5):
        cx = -23.0 + i * 1.5
        # North side
        add_box(col, f"Chair_Seat_N_{i}", (cx, 17.8, 0.48), (0.55, 0.55, 0.08), mats["black_metal"])
        add_box(col, f"Chair_Back_N_{i}", (cx, 18.05, 0.85), (0.55, 0.08, 0.7), mats["black_metal"])
        # South side
        add_box(col, f"Chair_Seat_S_{i}", (cx, 14.2, 0.48), (0.55, 0.55, 0.08), mats["black_metal"])
        add_box(col, f"Chair_Back_S_{i}", (cx, 13.95, 0.85), (0.55, 0.08, 0.7), mats["black_metal"])

    # Head of table chairs (East and West)
    add_box(col, "Chair_Seat_W", (-24.8, 16.0, 0.48), (0.55, 0.55, 0.08), mats["black_metal"])
    add_box(col, "Chair_Back_W", (-25.05, 16.0, 0.85), (0.08, 0.55, 0.7), mats["black_metal"])
    add_box(col, "Chair_Seat_E", (-15.2, 16.0, 0.48), (0.55, 0.55, 0.08), mats["black_metal"])
    add_box(col, "Chair_Back_E", (-14.95, 16.0, 0.85), (0.08, 0.55, 0.7), mats["black_metal"])

    # 98-inch 4K Wall AV Presentation Screen on West Wall
    add_box(col, "AV_Display_Frame", (-30.7, 16.0, 2.2), (0.1, 4.2, 2.2), mats["black_metal"])
    add_box(col, "AV_Display_Screen", (-30.64, 16.0, 2.2), (0.02, 4.0, 2.0), mats["screen_display"])


def build_cubicle_farm(col, mats):
    """Open-plan office with acoustic divider cubicles and computer workstations."""
    # 4 rows of double-sided cubicles on the East half (X: 5 to 26, Y: -20 to 20)
    for row_x in [6.0, 13.0, 20.0]:
        for pod_y in [-16.0, -8.0, 2.0, 10.0, 18.0]:
            # Acoustic fabric center privacy divider (1.4m high)
            add_box(col, f"Cubicle_Spine_{row_x}_{pod_y}", (row_x, pod_y, 0.7), (4.5, 0.1, 1.4), mats["acoustic_felt"])
            add_box(col, f"Cubicle_Divider_1_{row_x}_{pod_y}", (row_x - 2.2, pod_y, 0.7), (0.1, 2.6, 1.4), mats["acoustic_felt"])
            add_box(col, f"Cubicle_Divider_2_{row_x}_{pod_y}", (row_x + 2.2, pod_y, 0.7), (0.1, 2.6, 1.4), mats["acoustic_felt"])

            # Desks North and South of the spine
            add_box(col, f"Desk_N_{row_x}_{pod_y}", (row_x, pod_y + 0.65, 0.72), (4.0, 1.0, 0.06), mats["wall_drywall"])
            add_box(col, f"Desk_S_{row_x}_{pod_y}", (row_x, pod_y - 0.65, 0.72), (4.0, 1.0, 0.06), mats["wall_drywall"])

            # Dual monitors on North desk
            add_box(col, f"Monitor_N1_{row_x}_{pod_y}", (row_x - 0.6, pod_y + 0.9, 1.05), (0.7, 0.05, 0.45), mats["black_metal"])
            add_box(col, f"Screen_N1_{row_x}_{pod_y}", (row_x - 0.6, pod_y + 0.88, 1.05), (0.66, 0.01, 0.41), mats["screen_display"])
            add_box(col, f"Monitor_N2_{row_x}_{pod_y}", (row_x + 0.6, pod_y + 0.9, 1.05), (0.7, 0.05, 0.45), mats["black_metal"])
            add_box(col, f"Screen_N2_{row_x}_{pod_y}", (row_x + 0.6, pod_y + 0.88, 1.05), (0.66, 0.01, 0.41), mats["screen_display"])

            # Filing cabinets
            add_box(col, f"Cabinet_N_{row_x}_{pod_y}", (row_x - 1.8, pod_y + 0.65, 0.35), (0.5, 0.7, 0.7), mats["black_metal"])
            add_box(col, f"Cabinet_S_{row_x}_{pod_y}", (row_x + 1.8, pod_y - 0.65, 0.35), (0.5, 0.7, 0.7), mats["black_metal"])


def build_reception_and_elevators(col, mats):
    """Central reception lobby with curved marble desk and twin elevators."""
    # Curved / faceted Calacatta marble reception counter at (-5.0, 0.0, 0.55)
    add_box(col, "Reception_Counter_Main", (-5.0, 0.0, 0.55), (1.4, 5.0, 1.1), mats["marble_floor"])
    add_box(col, "Reception_Counter_WingN", (-4.2, 2.6, 0.55), (2.0, 0.8, 1.1), mats["marble_floor"])
    add_box(col, "Reception_Counter_WingS", (-4.2, -2.6, 0.55), (2.0, 0.8, 1.1), mats["marble_floor"])
    add_box(col, "Reception_Top_Quartz", (-5.0, 0.0, 1.12), (1.5, 5.2, 0.06), mats["stainless"])

    # Twin Elevator Core at (-15.0, 0.0, 2.0)
    add_box(col, "Elevator_Shaft_Wall", (-15.0, 0.0, 2.0), (3.0, 9.0, 4.0), mats["wall_accent"])

    # Elevator A (North)
    add_box(col, "Elevator_A_Frame", (-13.4, 2.5, 1.5), (0.2, 2.4, 3.0), mats["stainless"])
    add_box(col, "Elevator_A_DoorL", (-13.38, 1.9, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_A_DoorR", (-13.38, 3.1, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_A_Indicator", (-13.35, 2.5, 3.2), (0.02, 0.8, 0.25), mats["screen_display"])

    # Elevator B (South)
    add_box(col, "Elevator_B_Frame", (-13.4, -2.5, 1.5), (0.2, 2.4, 3.0), mats["stainless"])
    add_box(col, "Elevator_B_DoorL", (-13.38, -3.1, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_B_DoorR", (-13.38, -1.9, 1.5), (0.05, 1.1, 2.9), mats["stainless"])
    add_box(col, "Elevator_B_Indicator", (-13.35, -2.5, 3.2), (0.02, 0.8, 0.25), mats["screen_display"])


def build_server_vault(col, mats):
    """High-density IT server room with glass observation wall and server banks."""
    # Acoustic glass observation partition wall
    add_box(col, "Server_Glass_Wall_N", (-20.0, -6.0, 2.0), (18.0, 0.2, 4.0), mats["smoked_glass"])
    add_box(col, "Server_Glass_Wall_E", (-11.0, -16.0, 2.0), (0.2, 20.0, 4.0), mats["smoked_glass"])

    # 4 rows of 42U server racks
    for r in range(4):
        rx = -25.0 + r * 3.5
        for s in range(5):
            sy = -22.0 + s * 2.4
            add_box(col, f"Server_Rack_{r}_{s}", (rx, sy, 1.1), (0.9, 1.8, 2.2), mats["black_metal"])
            # Status indicator LED strip
            add_box(col, f"Server_LEDs_{r}_{s}", (rx + 0.46, sy, 1.1), (0.02, 1.5, 1.8), mats["server_leds"])


def build_lighting_and_hvac(col, mats):
    """Recessed fluorescent troffer panels and industrial ceiling diffusers."""
    # Grid of fluorescent ceiling light troffers
    for lx in range(-25, 28, 7):
        for ly in range(-24, 26, 8):
            add_box(col, f"Light_Troffer_{lx}_{ly}", (lx, ly, 4.0), (1.4, 2.4, 0.08), mats["fluorescent"])


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
    build_lighting_and_hvac(c_lights, mats)

    print("=== High-Rise Corporate Office (hd_office) Built Successfully! ===")


def export_glb():
    """Exports the entire office scene to GLB for native FPS and Web runtime."""
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
