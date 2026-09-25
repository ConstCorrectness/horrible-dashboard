#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Nuclear Containment Facility (hd_nuke).
Authentic multi-level competitive tournament arena on a 70m x 70m footprint:
- Bomb Site A: Upper Reactor Hall (z=0.0m), overhead rafters, gantry crane, Hut, and vent hatch.
- Bomb Site B: Lower Reactor Silo (z=-4.5m) directly underneath Site A, featuring central glowing reactor core, coolant tanks, and decon chamber.
- Ventilation Ducts: Vertical shaft linking Site A floor, Site B ceiling, and Decon corridor.
- Ramp Room & Lobby: Sloped ramp connecting ground level down to Site B silo, Radio room, and Squeaky door.
- Outside Yard: Silo tower with ladder, shipping containers, electrical substation, and Garage overlook.

Outputs:
  - backend/modules/hassault/maps/hd_nuke.glb
  - apps/web/public/hd_nuke.glb
"""

import os
import sys
import math

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: generate_nuke.py must be run from within Blender (e.g. `blender --background --python ...`)")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maplib  # noqa: E402  (a sibling, found via the path above)


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


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.7, emission_color=None, emission_strength=0.0, alpha=1.0, bump_strength=0.0, bump_scale=24.0):
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
    mats["concrete_floor"] = create_pbr_material("mat_concrete_floor", (0.58, 0.60, 0.62), metallic=0.02, roughness=0.78, bump_strength=0.15, bump_scale=20.0)
    mats["concrete_dark"] = create_pbr_material("mat_concrete_dark", (0.38, 0.40, 0.42), metallic=0.04, roughness=0.82, bump_strength=0.18, bump_scale=18.0)
    mats["concrete_wall"] = create_pbr_material("mat_concrete_wall", (0.72, 0.74, 0.76), metallic=0.01, roughness=0.82, bump_strength=0.14, bump_scale=22.0)
    mats["hazard_yellow"] = create_pbr_material("mat_hazard_yellow", (0.88, 0.76, 0.12), metallic=0.45, roughness=0.35, bump_strength=0.08, bump_scale=30.0)
    mats["hazard_black"] = create_pbr_material("mat_hazard_black", (0.16, 0.16, 0.18), metallic=0.2, roughness=0.6, bump_strength=0.08, bump_scale=30.0)
    mats["steel_industrial"] = create_pbr_material("mat_steel_industrial", (0.42, 0.45, 0.48), metallic=0.85, roughness=0.35, bump_strength=0.12, bump_scale=32.0)
    mats["steel_dark"] = create_pbr_material("mat_steel_dark", (0.24, 0.26, 0.28), metallic=0.90, roughness=0.30, bump_strength=0.12, bump_scale=32.0)
    mats["steel_grate"] = create_pbr_material("mat_steel_grate", (0.46, 0.48, 0.50), metallic=0.80, roughness=0.40, bump_strength=0.20, bump_scale=40.0)
    mats["reactor_glow_cyan"] = create_pbr_material("mat_reactor_glow_cyan", (0.10, 0.88, 0.98), emission_color=(0.10, 0.88, 0.98), emission_strength=6.0)
    mats["reactor_hull"] = create_pbr_material("mat_reactor_hull", (0.30, 0.34, 0.38), metallic=0.82, roughness=0.28, bump_strength=0.15, bump_scale=24.0)
    mats["coolant_blue"] = create_pbr_material("mat_coolant_blue", (0.18, 0.40, 0.75), metallic=0.6, roughness=0.35)
    mats["pipe_yellow"] = create_pbr_material("mat_pipe_yellow", (0.85, 0.68, 0.10), metallic=0.5, roughness=0.4, bump_strength=0.08, bump_scale=28.0)
    mats["container_red"] = create_pbr_material("mat_container_red", (0.68, 0.18, 0.14), metallic=0.35, roughness=0.55, bump_strength=0.15, bump_scale=25.0)
    mats["container_blue"] = create_pbr_material("mat_container_blue", (0.16, 0.30, 0.60), metallic=0.35, roughness=0.55, bump_strength=0.15, bump_scale=25.0)
    mats["container_green"] = create_pbr_material("mat_container_green", (0.18, 0.45, 0.26), metallic=0.35, roughness=0.55, bump_strength=0.15, bump_scale=25.0)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.54, 0.40, 0.26), metallic=0.0, roughness=0.65, bump_strength=0.25, bump_scale=14.0)
    mats["light_fluor"] = create_pbr_material("mat_light_fluor", (0.95, 0.98, 1.0), emission_color=(0.95, 0.98, 1.0), emission_strength=4.0)
    mats["insulator_ceramic"] = create_pbr_material("mat_insulator_ceramic", (0.65, 0.38, 0.25), metallic=0.1, roughness=0.3)
    mats["glass_window"] = create_pbr_material("mat_glass_window", (0.80, 0.92, 0.98), metallic=0.05, roughness=0.05, alpha=0.32)
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


def add_pipe_run(collection, name_prefix, start, end, radius, material, has_flanges=True):
    """Adds a realistic industrial piping segment with mounting flanges."""
    p1 = Vector(start)
    p2 = Vector(end)
    diff = p2 - p1
    length = diff.length
    if length < 1e-4:
        return
    center = (p1 + p2) * 0.5

    mesh = bpy.data.meshes.new(f"{name_prefix}_Pipe")
    obj = bpy.data.objects.new(f"{name_prefix}_Pipe", mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=12,
        radius1=radius,
        radius2=radius,
        depth=length
    )
    # Default cylinder is along Z, orient towards diff
    z_axis = Vector((0, 0, 1))
    dir_norm = diff.normalized()
    rot = z_axis.rotation_difference(dir_norm).to_matrix().to_4x4()
    bm.transform(rot)
    bm.transform(Matrix.Translation(center))
    bm.to_mesh(mesh)
    bm.free()
    if material:
        obj.data.materials.append(material)

    if has_flanges and length > 2.0:
        # Add bolted flanges at 1/4 and 3/4 length
        for t in [0.25, 0.75]:
            pos = p1 + diff * t
            add_cylinder(collection, f"{name_prefix}_Flange_{int(t*100)}_NonCol", pos, radius * 1.5, 0.12, material, segments=12)


def add_shipping_container(collection, name_prefix, center, yaw_deg, material, mats):
    """Detailed intermodal ISO shipping container with corrugated ribs, corner castings, and latch doors."""
    cx, cy, cz = center
    # Main container body (6.0m L x 2.44m W x 2.59m H)
    L, W, H = 6.0, 2.44, 2.59
    rad = math.radians(yaw_deg)
    cos_y = math.cos(rad)
    sin_y = math.sin(rad)

    # Base box
    add_box(collection, f"{name_prefix}_Body", (cx, cy, cz + H * 0.5), (L, W, H), material)

    # Corrugated side ribs (decorative NonCol)
    num_ribs = 9
    for i in range(num_ribs):
        offset = -L * 0.45 + (L * 0.9 / (num_ribs - 1)) * i
        rx = cx + offset * cos_y - (W * 0.51) * (-sin_y)
        ry = cy + offset * sin_y - (W * 0.51) * cos_y
        add_box(collection, f"{name_prefix}_RibL_{i}_NonCol", (rx, ry, cz + H * 0.5), (0.1, 0.08, H * 0.94), material)
        rx2 = cx + offset * cos_y + (W * 0.51) * (-sin_y)
        ry2 = cy + offset * sin_y + (W * 0.51) * cos_y
        add_box(collection, f"{name_prefix}_RibR_{i}_NonCol", (rx2, ry2, cz + H * 0.5), (0.1, 0.08, H * 0.94), material)

    # Corner casting blocks
    for dx in [-L * 0.48, L * 0.48]:
        for dy in [-W * 0.48, W * 0.48]:
            for dz in [0.15, H - 0.15]:
                ccx = cx + dx * cos_y - dy * sin_y
                ccy = cy + dx * sin_y + dy * cos_y
                add_box(collection, f"{name_prefix}_Cast_NonCol", (ccx, ccy, cz + dz), (0.24, 0.24, 0.28), mats["steel_dark"])

    # Rear door locking cam rods
    for rod_dy in [-W * 0.2, W * 0.2]:
        rx = cx - (L * 0.51) * cos_y - rod_dy * sin_y
        ry = cy - (L * 0.51) * sin_y + rod_dy * cos_y
        add_cylinder(collection, f"{name_prefix}_LockRod_NonCol", (rx, ry, cz + H * 0.5), 0.04, H * 0.88, mats["steel_industrial"], segments=8)


def add_gantry_crane(collection, mats):
    """Overhead industrial gantry crane across Site A ceiling."""
    # Main twin I-beams across the ceiling span from X=24 to X=48 at Y=40, Z=6.8
    span_len = 24.0
    add_box(collection, "Gantry_Beam_A", (36.0, 39.5, 6.8), (span_len, 0.4, 0.8), mats["hazard_yellow"])
    add_box(collection, "Gantry_Beam_B", (36.0, 40.5, 6.8), (span_len, 0.4, 0.8), mats["hazard_yellow"])

    # End carriages running along North/South support rails
    add_box(collection, "Gantry_Carriage_W", (24.2, 40.0, 6.8), (0.8, 2.6, 0.9), mats["hazard_black"])
    add_box(collection, "Gantry_Carriage_E", (47.8, 40.0, 6.8), (0.8, 2.6, 0.9), mats["hazard_black"])

    # Central motorized trolley
    add_box(collection, "Gantry_Trolley", (36.0, 40.0, 6.4), (2.2, 2.0, 0.6), mats["hazard_yellow"])
    add_box(collection, "Gantry_Motor_Housing_NonCol", (36.0, 39.0, 6.7), (1.4, 0.8, 0.7), mats["steel_dark"])

    # Steel hoist wire cables hanging down to hook block (decorative NonCol)
    add_cylinder(collection, "Gantry_Cable_1_NonCol", (35.6, 40.0, 5.0), 0.03, 2.4, mats["steel_industrial"], segments=6)
    add_cylinder(collection, "Gantry_Cable_2_NonCol", (36.4, 40.0, 5.0), 0.03, 2.4, mats["steel_industrial"], segments=6)

    # Pulley block and heavy lifting hook
    add_box(collection, "Gantry_HookBlock", (36.0, 40.0, 3.7), (0.8, 0.6, 0.8), mats["hazard_black"])
    add_cylinder(collection, "Gantry_HookRing_NonCol", (36.0, 40.0, 3.1), 0.25, 0.4, mats["steel_industrial"], segments=10)

    # Strobe warning light on trolley
    add_cylinder(collection, "Gantry_Warning_Strobe_NonCol", (36.0, 40.0, 6.8), 0.14, 0.24, mats["hazard_yellow"], segments=8)


def add_substation(collection, mats):
    """Outdoor high-voltage electrical substation with transformer and insulator bushings."""
    cx, cy, cz = 18.0, 52.0, 0.0
    # Concrete gravel pad
    add_box(collection, "Substation_Pad", (cx, cy, cz + 0.15), (7.0, 8.0, 0.3), mats["concrete_dark"])

    # Main transformer tank (3.2m x 2.2m x 2.4m)
    add_box(collection, "Transformer_MainTank", (cx, cy, cz + 1.5), (3.2, 2.2, 2.4), mats["steel_dark"])

    # External oil cooling radiators on sides
    for side_x in [-1.8, 1.8]:
        for fin in range(6):
            fy = cy - 0.8 + fin * 0.32
            add_box(collection, f"Transformer_Fin_{side_x}_{fin}_NonCol", (cx + side_x, fy, cz + 1.5), (0.35, 0.05, 1.8), mats["steel_industrial"])

    # Conservator oil tank drum on top
    add_cylinder(collection, "Transformer_Drum_NonCol", (cx, cy, cz + 2.9), 0.45, 2.6, mats["steel_dark"], segments=12)

    # High-voltage ceramic insulator bushings with ribbed skirts
    for bx in [-0.8, 0.0, 0.8]:
        b_pos = (cx + bx, cy + 0.6, cz + 3.2)
        add_cylinder(collection, f"Bushing_Post_{bx}_NonCol", b_pos, 0.12, 0.9, mats["insulator_ceramic"], segments=10)
        # Ribbed skirts
        for skirt in range(3):
            skirt_pos = (cx + bx, cy + 0.6, cz + 2.9 + skirt * 0.22)
            add_cylinder(collection, f"Bushing_Skirt_{bx}_{skirt}_NonCol", skirt_pos, 0.24, 0.06, mats["insulator_ceramic"], segments=10)


def build_nuke_perimeter_and_ground(col, mats):
    # Main Ground Floor (70m x 70m)
    add_box(col, "Terrain_Concrete_Ground", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["concrete_floor"])

    # High Perimeter Concrete Security Walls (Height 12m)
    add_box(col, "Wall_Perimeter_S", (35.0, 4.0, 6.0), (62.0, 1.2, 12.0), mats["concrete_wall"])
    add_box(col, "Wall_Perimeter_N", (35.0, 66.0, 6.0), (62.0, 1.2, 12.0), mats["concrete_wall"])
    add_box(col, "Wall_Perimeter_E", (66.0, 35.0, 6.0), (1.2, 62.0, 12.0), mats["concrete_wall"])
    add_box(col, "Wall_Perimeter_W", (4.0, 35.0, 6.0), (1.2, 62.0, 12.0), mats["concrete_wall"])

    # Yellow Hazard Security Trim along top
    add_box(col, "Hazard_Trim_S", (35.0, 4.6, 11.6), (62.0, 0.4, 0.6), mats["hazard_yellow"])
    add_box(col, "Hazard_Trim_N", (35.0, 65.4, 11.6), (62.0, 0.4, 0.6), mats["hazard_yellow"])
    add_box(col, "Hazard_Trim_E", (65.4, 35.0, 11.6), (0.4, 62.0, 0.6), mats["hazard_yellow"])
    add_box(col, "Hazard_Trim_W", (4.6, 35.0, 11.6), (0.4, 62.0, 0.6), mats["hazard_yellow"])


def build_site_a_upper_hall(col, mats):
    """Bomb Site A Upper Reactor Hall at Z=0.0m with Hut, Rafters, and Crane."""
    # Outer containment building walls (X: 22..50, Y: 28..52, Z: 0..8.0)
    # Doorways on the T (south), CT (north) and west sides. The hall was once four
    # unbroken 8 m walls: Site A was sealed, reachable by nobody.
    maplib.add_wall_with_door(col, "SiteA_Wall_N", (36.0, 52.0, 4.0), (28.0, 1.2, 8.0), mats["concrete_wall"], 30.0, 3.5, 3.2)
    add_box(col, "SiteA_Wall_E", (50.0, 40.0, 4.0), (1.2, 24.0, 8.0), mats["concrete_wall"])
    maplib.add_wall_with_door(col, "SiteA_Wall_W", (22.0, 40.0, 4.0), (1.2, 24.0, 8.0), mats["concrete_wall"], 44.0, 3.5, 3.2)
    maplib.add_wall_with_door(col, "SiteA_Wall_S", (36.0, 28.0, 4.0), (28.0, 1.2, 8.0), mats["concrete_wall"], 40.0, 3.5, 3.2)

    # Hall ceiling at Z=8.0
    add_box(col, "SiteA_Roof_Deck", (36.0, 40.0, 8.2), (28.0, 24.0, 0.4), mats["concrete_dark"])

    # Overhead high-bay fluorescent light troffers (NonCol)
    for lx in [28.0, 36.0, 44.0]:
        for ly in [34.0, 46.0]:
            add_box(col, f"SiteA_Light_Fluor_{int(lx)}_{int(ly)}_NonCol", (lx, ly, 7.85), (2.4, 0.6, 0.2), mats["light_fluor"])

    # Hut Structure inside Site A (X: 24..28, Y: 34..40, Z: 0..2.8)
    add_box(col, "SiteA_Hut_Roof", (26.0, 37.0, 2.7), (4.4, 6.4, 0.2), mats["concrete_wall"])
    add_box(col, "SiteA_Hut_Wall_W", (24.0, 37.0, 1.3), (0.4, 6.0, 2.6), mats["concrete_wall"])
    add_box(col, "SiteA_Hut_Wall_S", (26.0, 34.0, 1.3), (4.0, 0.4, 2.6), mats["concrete_wall"])
    add_box(col, "SiteA_Hut_Doorframe", (28.0, 37.0, 2.3), (0.4, 6.0, 0.6), mats["hazard_yellow"])

    # Hut window opening on East wall
    add_box(col, "SiteA_Hut_Window_Sill", (28.0, 38.5, 1.1), (0.4, 1.8, 0.1), mats["steel_industrial"])
    add_box(col, "Window_Glass_SiteA_Hut", (28.0, 38.5, 1.6), (0.1, 1.8, 0.9), mats["glass_window"])

    # Observation / Control Room Breakable Windows overlooking Site A
    add_box(col, "Window_Glass_ControlRoom_1", (23.8, 44.0, 1.6), (0.1, 2.0, 1.2), mats["glass_window"])
    add_box(col, "Window_Glass_ControlRoom_2", (23.8, 47.0, 1.6), (0.1, 2.0, 1.2), mats["glass_window"])

    # Overhead Rafters / Catwalks at Z = 3.8m
    add_box(col, "SiteA_Catwalk_East", (48.0, 40.0, 3.8), (2.0, 22.0, 0.2), mats["steel_grate"])
    add_box(col, "SiteA_Catwalk_North", (36.0, 50.0, 3.8), (22.0, 2.0, 0.2), mats["steel_grate"])
    add_box(col, "SiteA_Catwalk_Railing_E", (47.0, 40.0, 4.4), (0.1, 22.0, 1.0), mats["hazard_yellow"])
    add_box(col, "SiteA_Catwalk_Railing_N", (36.0, 49.0, 4.4), (22.0, 0.1, 1.0), mats["hazard_yellow"])

    # Yellow Gantry Crane Beam across ceiling at Z = 6.8m
    add_gantry_crane(col, mats)

    # Plant Marker A Disc at (36.0, 40.0, 0.0) with hazard chevron ring
    add_cylinder(col, "SiteA_Plant_Marker", (36.0, 40.0, 0.05), radius=2.5, height=0.1, material=mats["hazard_yellow"], segments=20)
    add_cylinder(col, "SiteA_Plant_Inner_Marker_NonCol", (36.0, 40.0, 0.07), radius=1.8, height=0.06, material=mats["hazard_black"], segments=20)

    # Vent Hatch at (31.0, 36.0, 0.0)
    add_box(col, "SiteA_Vent_Hatch_Coaming", (31.0, 36.0, 0.15), (1.4, 1.4, 0.3), mats["hazard_yellow"])


def build_site_b_lower_silo(col, mats):
    """Bomb Site B Subterranean Reactor Silo at Z=-4.5m directly beneath Site A."""
    # Silo Excavation Floor at Z = -4.5m
    add_box(col, "SiteB_Silo_Floor", (36.0, 40.0, -4.7), (26.0, 22.0, 0.4), mats["concrete_floor"])

    # Silo Retaining Walls (-4.5m to 0.0m)
    add_box(col, "SiteB_Silo_Wall_S", (36.0, 29.0, -2.25), (26.0, 0.8, 4.5), mats["concrete_dark"])
    add_box(col, "SiteB_Silo_Wall_N", (36.0, 51.0, -2.25), (26.0, 0.8, 4.5), mats["concrete_dark"])
    add_box(col, "SiteB_Silo_Wall_W", (23.0, 40.0, -2.25), (0.8, 22.0, 4.5), mats["concrete_dark"])
    add_box(col, "SiteB_Silo_Wall_E", (49.0, 40.0, -2.25), (0.8, 22.0, 4.5), mats["concrete_dark"])

    # Central Nuclear Reactor Core at (36.0, 40.0, -4.5)
    # Reactor lower base pedestal
    add_cylinder(col, "Reactor_Base_Pedestal", (36.0, 40.0, -4.1), radius=3.2, height=0.8, material=mats["concrete_floor"], segments=24)
    # Main containment pressure vessel
    add_cylinder(col, "Reactor_Pressure_Vessel", (36.0, 40.0, -2.25), radius=2.6, height=4.5, material=mats["reactor_hull"], segments=24)
    # Glowing Core Cherenkov Radiation Ring
    add_cylinder(col, "Reactor_Core_Glow_Ring", (36.0, 40.0, -2.25), radius=2.7, height=0.8, material=mats["reactor_glow_cyan"], segments=24)
    # Core top dome
    add_cylinder(col, "Reactor_Top_Dome", (36.0, 40.0, 0.0), radius=2.4, height=0.5, material=mats["hazard_yellow"], segments=20)

    # Vertical cooling pipes around reactor core
    for ang_deg in [0, 60, 120, 180, 240, 300]:
        rad = math.radians(ang_deg)
        px = 36.0 + math.cos(rad) * 2.85
        py = 40.0 + math.sin(rad) * 2.85
        add_cylinder(col, f"Reactor_Cooling_Pipe_{ang_deg}", (px, py, -2.25), radius=0.18, height=4.2, material=mats["pipe_yellow"], segments=10)

    # Coolant Containment Tanks
    add_cylinder(col, "Coolant_Tank_W", (26.0, 44.0, -3.0), radius=1.6, height=3.0, material=mats["coolant_blue"], segments=16)
    add_cylinder(col, "Coolant_Tank_E", (46.0, 44.0, -3.0), radius=1.6, height=3.0, material=mats["coolant_blue"], segments=16)
    add_cylinder(col, "Coolant_Tank_W_Dome_NonCol", (26.0, 44.0, -1.3), radius=1.5, height=0.4, material=mats["steel_industrial"], segments=16)
    add_cylinder(col, "Coolant_Tank_E_Dome_NonCol", (46.0, 44.0, -1.3), radius=1.5, height=0.4, material=mats["steel_industrial"], segments=16)

    # Industrial coolant piping runs from tanks to reactor
    add_pipe_run(col, "Coolant_Run_W", (26.0, 44.0, -1.5), (33.5, 42.0, -1.5), 0.16, mats["coolant_blue"])
    add_pipe_run(col, "Coolant_Run_E", (46.0, 44.0, -1.5), (38.5, 42.0, -1.5), 0.16, mats["coolant_blue"])

    # Decon Chamber Portal at North side of B
    add_box(col, "SiteB_Decon_Frame", (36.0, 50.0, -3.0), (4.0, 0.8, 3.0), mats["hazard_yellow"])
    add_box(col, "SiteB_Decon_Doors", (36.0, 50.0, -3.0), (3.2, 0.3, 2.8), mats["steel_grate"])
    # Decon nozzles overhead
    for nx in [35.2, 36.8]:
        add_cylinder(col, f"Decon_Nozzle_{int(nx*10)}_NonCol", (nx, 50.0, -1.6), 0.08, 0.2, mats["hazard_yellow"], segments=8)

    # Plant Marker B Disc at (36.0, 40.0, -4.5)
    add_cylinder(col, "SiteB_Plant_Marker", (36.0, 40.0, -4.45), radius=3.8, height=0.08, material=mats["hazard_yellow"], segments=24)


def build_connecting_ramps_and_vents(col, mats):
    """Vertical ventilation shaft, Ramp room, and Decon corridor."""
    # Vertical Vent Shaft: connects (31.0, 36.0, 0.0) to (31.0, 36.0, -4.5)
    add_box(col, "Vent_Shaft_W", (30.3, 36.0, -2.25), (0.2, 1.4, 4.5), mats["steel_grate"])
    add_box(col, "Vent_Shaft_E", (31.7, 36.0, -2.25), (0.2, 1.4, 4.5), mats["steel_grate"])
    add_box(col, "Vent_Shaft_S", (31.0, 35.3, -2.25), (1.4, 0.2, 4.5), mats["steel_grate"])
    add_box(col, "Vent_Shaft_N", (31.0, 36.7, -2.25), (1.4, 0.2, 4.5), mats["steel_grate"])

    # Vent interior ladder rungs (decorative NonCol)
    for r in range(10):
        rz = -4.0 + r * 0.4
        add_cylinder(col, f"Vent_Rung_{r}_NonCol", (31.0, 36.5, rz), 0.02, 0.5, mats["hazard_yellow"], segments=6)

    # Ramp Room: connects Lobby (z=0) at y=20 down to Site B (z=-4.5) at y=28
    # Ramp slope
    add_box(col, "Ramp_Floor", (20.0, 24.0, -2.25), (4.0, 8.0, 0.4), mats["concrete_floor"])
    # Ramp side safety railings
    add_box(col, "Ramp_Railing_W", (18.0, 24.0, -1.6), (0.2, 8.0, 1.0), mats["hazard_yellow"])
    add_box(col, "Ramp_Railing_E", (22.0, 24.0, -1.6), (0.2, 8.0, 1.0), mats["hazard_yellow"])

    # Decon connector corridor floor
    add_box(col, "Decon_Corridor_Floor", (36.0, 54.0, -4.7), (6.0, 8.0, 0.4), mats["concrete_floor"])
    add_box(col, "Decon_Corridor_Wall_W", (32.8, 54.0, -3.0), (0.4, 8.0, 3.4), mats["concrete_wall"])
    add_box(col, "Decon_Corridor_Wall_E", (39.2, 54.0, -3.0), (0.4, 8.0, 3.4), mats["concrete_wall"])


def build_outside_yard(col, mats):
    """Outside Yard with Silo tower, shipping containers, electrical substation, and Garage."""
    # Silo Tower at (16.0, 20.0, 0.0)
    add_cylinder(col, "Outside_Silo_Base", (16.0, 20.0, 3.0), radius=2.8, height=6.0, material=mats["concrete_wall"], segments=24)
    add_cylinder(col, "Outside_Silo_Dome", (16.0, 20.0, 6.3), radius=2.6, height=0.8, material=mats["hazard_yellow"], segments=20)
    # Silo external safety cage ladder (NonCol)
    for step in range(12):
        sz = 0.5 + step * 0.45
        add_box(col, f"Silo_Ladder_Rung_{step}_NonCol", (18.9, 20.0, sz), (0.05, 0.6, 0.04), mats["hazard_yellow"])

    # Red, Blue, and Green Shipping Containers in Yard
    add_shipping_container(col, "Container_Red_1", (16.0, 34.0, 0.0), 0.0, mats["container_red"], mats)
    add_shipping_container(col, "Container_Blue_1", (16.0, 42.0, 0.0), 12.0, mats["container_blue"], mats)
    add_shipping_container(col, "Container_Green_1", (23.0, 48.0, 0.0), 90.0, mats["container_green"], mats)

    # Electrical Substation in North Yard
    add_substation(col, mats)

    # Garage Structure at East Yard (X: 52..60, Y: 40..52, Z: 0..5.0)
    add_box(col, "Garage_Building", (56.0, 46.0, 2.5), (8.0, 12.0, 5.0), mats["concrete_wall"])
    add_box(col, "Garage_Door_Frame", (51.8, 46.0, 2.0), (0.4, 4.0, 4.0), mats["hazard_yellow"])
    add_box(col, "Garage_Rollup_Door", (52.0, 46.0, 2.0), (0.1, 3.8, 3.8), mats["steel_grate"])

    # Forklift / wooden pallets near Garage
    add_box(col, "Yard_Pallet_Stack_1", (48.0, 32.0, 0.4), (2.0, 2.0, 0.8), mats["wood_crate"])
    add_box(col, "Yard_Pallet_Stack_2", (50.5, 32.0, 0.6), (1.8, 1.8, 1.2), mats["wood_crate"])


def build_nuke_scene():
    clear_scene()
    mats = setup_materials()

    c_terrain = get_or_create_collection("Terrain")
    c_site_a = get_or_create_collection("SiteA")
    c_site_b = get_or_create_collection("SiteB")
    c_connectors = get_or_create_collection("Connectors")
    c_yard = get_or_create_collection("Yard")

    build_nuke_perimeter_and_ground(c_terrain, mats)
    build_site_a_upper_hall(c_site_a, mats)
    build_site_b_lower_silo(c_site_b, mats)
    build_connecting_ramps_and_vents(c_connectors, mats)
    build_outside_yard(c_yard, mats)


def export_glb():
    """Scale the metre-authored scene to cubes and export it (see maplib)."""
    maplib.export_map_glb("hd_nuke")


def main():
    print("Building High-Detail Nuclear Containment Facility (hd_nuke)...")
    build_nuke_scene()

    export_glb()
    print("=== hd_nuke 3D Generation Complete! ===")


if __name__ == "__main__":
    main()
