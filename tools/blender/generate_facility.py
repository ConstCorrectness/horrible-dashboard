#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Deadzone Facility (hd_facility).
Generates an ultra-realistic subterranean Cold War missile silo and secret weapons lab
complete with PBR materials, heavy hydraulic blast bulkheads, reactor containment tanks,
overhead industrial piping networks, 19-inch server telemetry racks, hazmat bays,
and elevated diamond-plate catwalk systems.

Outputs:
  - backend/modules/hassault/maps/hd_facility.glb
  - apps/web/public/hd_facility.glb
"""

import os
import sys
import math

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: generate_facility.py must be run from within Blender 4.2+ (e.g. `blender --background --python ...`)")
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

    node_out = nodes.new(type="ShaderNodeOutputMaterial")
    node_out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], node_out.inputs["Surface"])

    if alpha < 1.0:
        mat.blend_method = "BLEND"
        mat.shadow_method = "CLIP"

    return mat


def setup_facility_materials():
    """Industrial subterranean laboratory and missile silo PBR palette."""
    mats = {}

    # Reinforced Concrete & Subterranean Bunker Surfaces
    mats["Concrete_Bunker_Dark"] = create_pbr_material("Mat_Concrete_Bunker_Dark", (0.24, 0.25, 0.27), metallic=0.02, roughness=0.84)
    mats["Concrete_Floor_Epoxy"] = create_pbr_material("Mat_Concrete_Floor_Epoxy", (0.32, 0.34, 0.36), metallic=0.08, roughness=0.42)
    mats["Concrete_Pit_Damp"] = create_pbr_material("Mat_Concrete_Pit_Damp", (0.16, 0.18, 0.20), metallic=0.04, roughness=0.92)

    # Structural Steel & Diamond Plate
    mats["Steel_Structural_Dark"] = create_pbr_material("Mat_Steel_Structural_Dark", (0.22, 0.24, 0.26), metallic=0.88, roughness=0.34)
    mats["Steel_Diamond_Plate"] = create_pbr_material("Mat_Steel_Diamond_Plate", (0.44, 0.46, 0.48), metallic=0.92, roughness=0.32)
    mats["Steel_Grate_Perforated"] = create_pbr_material("Mat_Steel_Grate_Perforated", (0.18, 0.20, 0.22), metallic=0.85, roughness=0.45)
    mats["Steel_Stainless_Brushed"] = create_pbr_material("Mat_Steel_Stainless_Brushed", (0.70, 0.72, 0.74), metallic=0.94, roughness=0.22)
    mats["Steel_Cast_Iron"] = create_pbr_material("Mat_Steel_Cast_Iron", (0.14, 0.15, 0.16), metallic=0.90, roughness=0.40)

    # Pipe Networks & Containment Equipment
    mats["Pipe_Industrial_Green"] = create_pbr_material("Mat_Pipe_Industrial_Green", (0.15, 0.32, 0.22), metallic=0.75, roughness=0.38)
    mats["Pipe_Steam_Silver"] = create_pbr_material("Mat_Pipe_Steam_Silver", (0.65, 0.67, 0.70), metallic=0.88, roughness=0.30)
    mats["Valve_Red"] = create_pbr_material("Mat_Valve_Red", (0.78, 0.12, 0.10), metallic=0.70, roughness=0.35)
    mats["Hydraulic_Chrome"] = create_pbr_material("Mat_Hydraulic_Chrome", (0.85, 0.86, 0.88), metallic=0.96, roughness=0.15)

    # Laboratory & Control Telemetry
    mats["Server_Cabinet_Matte"] = create_pbr_material("Mat_Server_Cabinet_Matte", (0.10, 0.11, 0.12), metallic=0.80, roughness=0.42)
    mats["Server_Faceplate_Dark"] = create_pbr_material("Mat_Server_Faceplate_Dark", (0.05, 0.05, 0.06), metallic=0.50, roughness=0.60)
    mats["LED_Array_Green"] = create_pbr_material("Mat_LED_Green_Glow", (0.2, 0.9, 0.3), emission_color=(0.1, 1.0, 0.2), emission_strength=7.0)
    mats["LED_Array_Amber"] = create_pbr_material("Mat_LED_Amber_Glow", (0.95, 0.6, 0.1), emission_color=(1.0, 0.55, 0.05), emission_strength=7.0)
    mats["CRT_Phosphor_Green"] = create_pbr_material("Mat_CRT_Phosphor_Green", (0.05, 0.3, 0.1), emission_color=(0.1, 0.9, 0.3), emission_strength=5.0)

    # Safety Hazard Stripes & Emissives
    mats["Hazard_Yellow"] = create_pbr_material("Mat_Hazard_Yellow", (0.92, 0.76, 0.08), metallic=0.15, roughness=0.45)
    mats["Hazard_Black"] = create_pbr_material("Mat_Hazard_Black", (0.05, 0.05, 0.06), metallic=0.10, roughness=0.65)
    mats["Placard_White"] = create_pbr_material("Mat_Placard_White", (0.88, 0.88, 0.86), metallic=0.05, roughness=0.70)
    mats["Emergency_Siren_Red"] = create_pbr_material("Mat_Emergency_Siren_Red", (1.0, 0.15, 0.1), emission_color=(1.0, 0.08, 0.05), emission_strength=8.0)
    mats["Fluorescent_Troffer"] = create_pbr_material("Mat_Fluorescent_Troffer", (0.95, 0.96, 1.0), emission_color=(0.95, 0.96, 1.0), emission_strength=6.5)
    mats["Coolant_Fluid_Glow"] = create_pbr_material("Mat_Coolant_Fluid_Glow", (0.1, 0.75, 0.5), emission_color=(0.05, 0.65, 0.4), emission_strength=3.5, alpha=0.88)

    # Chemical Waste Drums & Tactical Cargo
    mats["Drum_Hazmat_Yellow"] = create_pbr_material("Mat_Drum_Hazmat_Yellow", (0.85, 0.70, 0.10), metallic=0.78, roughness=0.42)
    mats["Drum_Bio_Blue"] = create_pbr_material("Mat_Drum_Bio_Blue", (0.12, 0.28, 0.52), metallic=0.78, roughness=0.42)
    mats["Rubber_Bumper_Black"] = create_pbr_material("Mat_Rubber_Bumper_Black", (0.07, 0.07, 0.08), metallic=0.0, roughness=0.88)

    return mats


def add_box(collection, name, min_pt, max_pt, material, bevel=False, bevel_width=0.02, is_collider=True):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    x0, y0, z0 = min_pt
    x1, y1, z1 = max_pt

    v = [
        bm.verts.new((x0, y0, z0)),
        bm.verts.new((x1, y0, z0)),
        bm.verts.new((x1, y1, z0)),
        bm.verts.new((x0, y1, z0)),
        bm.verts.new((x0, y0, z1)),
        bm.verts.new((x1, y0, z1)),
        bm.verts.new((x1, y1, z1)),
        bm.verts.new((x0, y1, z1)),
    ]

    faces = [
        (v[0], v[3], v[2], v[1]),  # Bottom (-Z)
        (v[4], v[5], v[6], v[7]),  # Top (+Z)
        (v[0], v[1], v[5], v[4]),  # South (-Y)
        (v[2], v[3], v[7], v[6]),  # North (+Y)
        (v[3], v[0], v[4], v[7]),  # West (-X)
        (v[1], v[2], v[6], v[5]),  # East (+X)
    ]
    for f in faces:
        bm.faces.new(f)

    bm.to_mesh(mesh)
    bm.free()

    obj_name = name if is_collider else f"{name}_Detail"
    obj = bpy.data.objects.new(obj_name, mesh)
    obj.data.materials.append(material)

    if bevel:
        mod = obj.modifiers.new("Bevel", type="BEVEL")
        mod.width = bevel_width
        mod.segments = 2
        mod.limit_method = "ANGLE"

    collection.objects.link(obj)
    return obj


def add_ramp(collection, name, min_x, min_y, z0, max_x, max_y, z1, material, is_collider=True):
    """Creates a wedge ramp between two elevations with solid side skirts."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    min_z = min(z0, z1) - 0.25

    v0 = bm.verts.new((min_x, min_y, min_z))
    v1 = bm.verts.new((max_x, min_y, min_z))
    v2 = bm.verts.new((max_x, max_y, min_z))
    v3 = bm.verts.new((min_x, max_y, min_z))

    v4 = bm.verts.new((min_x, min_y, z0))
    v5 = bm.verts.new((max_x, min_y, z0))
    v6 = bm.verts.new((max_x, max_y, z1))
    v7 = bm.verts.new((min_x, max_y, z1))

    # Faces
    bm.faces.new((v4, v5, v6, v7))  # Sloped walking surface
    bm.faces.new((v0, v3, v2, v1))  # Bottom
    bm.faces.new((v0, v1, v5, v4))  # Front / South
    bm.faces.new((v2, v3, v7, v6))  # Back / North
    bm.faces.new((v3, v0, v4, v7))  # Left / West
    bm.faces.new((v1, v2, v6, v5))  # Right / East

    bm.to_mesh(mesh)
    bm.free()

    obj_name = name if is_collider else f"{name}_Detail"
    obj = bpy.data.objects.new(obj_name, mesh)
    obj.data.materials.append(material)
    collection.objects.link(obj)
    return obj


def add_cylinder(collection, name, center, radius, height, material, segments=20, is_collider=True):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    cx, cy, cz = center
    z_half = height * 0.5
    top_z = cz + z_half
    bot_z = cz - z_half

    top_verts = []
    bot_verts = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        top_verts.append(bm.verts.new((x, y, top_z)))
        bot_verts.append(bm.verts.new((x, y, bot_z)))

    bm.faces.new(top_verts)
    bm.faces.new(reversed(bot_verts))

    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((bot_verts[i], bot_verts[i_next], top_verts[i_next], top_verts[i]))

    bm.to_mesh(mesh)
    bm.free()

    obj_name = name if is_collider else f"{name}_Detail"
    obj = bpy.data.objects.new(obj_name, mesh)
    obj.data.materials.append(material)
    collection.objects.link(obj)
    return obj


def add_pipe(collection, name, p1, p2, radius, material, segments=12, is_collider=False):
    """Draws an industrial pipe cylinder between two arbitrary 3D points."""
    v1 = Vector(p1)
    v2 = Vector(p2)
    delta = v2 - v1
    length = delta.length
    if length < 0.001:
        return None

    mid = (v1 + v2) * 0.5
    dir_norm = delta.normalized()

    up = Vector((0, 0, 1))
    rot_quat = up.rotation_difference(dir_norm)

    obj_name = f"{name}_Detail" if not is_collider else name
    mesh = bpy.data.meshes.new(obj_name)
    obj = bpy.data.objects.new(obj_name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=length
    )
    bm.to_mesh(mesh)
    bm.free()

    obj.location = mid
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = rot_quat
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    if material:
        obj.data.materials.append(material)
    return obj


def build_catwalk_guardrails(collection, name, p_start, p_end, mats, z_base=6.0):
    """Builds OSHA-spec double safety pipe handrails along a straight segment."""
    p1 = Vector(p_start)
    p2 = Vector(p_end)
    seg_vec = p2 - p1
    seg_len = seg_vec.length
    if seg_len < 0.1:
        return

    dir_step = seg_vec.normalized()
    n_posts = max(2, int(seg_len / 2.0) + 1)
    step_dist = seg_len / (n_posts - 1)

    mat_pipe = mats["Steel_Structural_Dark"]
    mat_kick = mats["Hazard_Yellow"]

    # Posts
    for i in range(n_posts):
        pt = p1 + dir_step * (i * step_dist)
        add_cylinder(collection, f"{name}_Post_{i}", (pt.x, pt.y, z_base + 0.55), radius=0.035, height=1.1, material=mat_pipe, segments=8, is_collider=False)

    # Top rail (z = z_base + 1.05m)
    add_pipe(collection, f"{name}_Rail_Top", (p1.x, p1.y, z_base + 1.05), (p2.x, p2.y, z_base + 1.05), radius=0.028, material=mat_pipe, segments=8, is_collider=False)
    # Mid rail (z = z_base + 0.55m)
    add_pipe(collection, f"{name}_Rail_Mid", (p1.x, p1.y, z_base + 0.55), (p2.x, p2.y, z_base + 0.55), radius=0.022, material=mat_pipe, segments=8, is_collider=False)

    # Toe-board / Kick-plate (z = z_base + 0.075m, height = 0.15m)
    perp = Vector((-dir_step.y, dir_step.x, 0)) * 0.02
    min_k = Vector((min(p1.x, p2.x) - 0.03, min(p1.y, p2.y) - 0.03, z_base))
    max_k = Vector((max(p1.x, p2.x) + 0.03, max(p1.y, p2.y) + 0.03, z_base + 0.15))
    add_box(collection, f"{name}_KickPlate", min_k, max_k, mat_kick, is_collider=False)


def build_reactor_silo_core(collection, mats):
    """Subterranean Missile Silo / Reactor Core at center of the pit (z = -5.0m)."""
    cx, cy, cz = 32.0, 32.0, -5.0

    # 1. Silo Foundation Rim & Heavy Collar (diameter = 11m)
    add_cylinder(collection, "Silo_Rim_Base", (cx, cy, cz + 0.15), radius=5.6, height=0.3, material=mats["Steel_Cast_Iron"], segments=36, is_collider=True)
    add_cylinder(collection, "Silo_Collar_Ring", (cx, cy, cz + 0.35), radius=5.2, height=0.2, material=mats["Steel_Structural_Dark"], segments=36, is_collider=True)
    # Yellow hazard outer ring
    add_cylinder(collection, "Silo_Hazard_Ring", (cx, cy, cz + 0.46), radius=5.0, height=0.04, material=mats["Hazard_Yellow"], segments=32, is_collider=False)

    # 2. Main Missile Silo Door / Reactor Vault Shield (Octagonal / Circular reinforced hatch)
    add_cylinder(collection, "Silo_Shield_Hatch", (cx, cy, cz + 0.65), radius=4.5, height=0.35, material=mats["Steel_Structural_Dark"], segments=32, is_collider=True)
    add_cylinder(collection, "Silo_Hatch_Center_Hub", (cx, cy, cz + 0.90), radius=1.8, height=0.25, material=mats["Steel_Cast_Iron"], segments=24, is_collider=False)

    # 3. Radial Hydraulic Locking Lugs (8 radial pistons)
    for i in range(8):
        angle = i * (math.pi / 4.0)
        px = cx + 4.2 * math.cos(angle)
        py = cy + 4.2 * math.sin(angle)
        add_box(collection, f"Silo_Lug_{i}", (px - 0.25, py - 0.25, cz + 0.45), (px + 0.25, py + 0.25, cz + 0.85), mats["Hydraulic_Chrome"], is_collider=False)
        # Hydraulic ram cylinder
        hx = cx + 3.2 * math.cos(angle)
        hy = cy + 3.2 * math.sin(angle)
        add_cylinder(collection, f"Silo_Hydraulic_Ram_{i}", (hx, hy, cz + 0.65), radius=0.18, height=0.7, material=mats["Pipe_Industrial_Green"], segments=12, is_collider=False)

    # 4. Center Observation / Core Access Port with Bioluminescent Coolant Vent
    add_cylinder(collection, "Silo_Core_Vent_Port", (cx, cy, cz + 1.05), radius=0.9, height=0.15, material=mats["Coolant_Fluid_Glow"], segments=20, is_collider=False)
    add_cylinder(collection, "Silo_Vent_Grate", (cx, cy, cz + 1.13), radius=0.85, height=0.02, material=mats["Steel_Grate_Perforated"], segments=16, is_collider=False)

    # 5. Coolant Pool / Deep Drainage Trench around Silo (waterlevel = -3.5m)
    add_box(collection, "Coolant_Liquid_Surface", (20.5, 20.5, -3.55), (43.5, 43.5, -3.45), mats["Coolant_Fluid_Glow"], is_collider=False)


def build_containment_tanks(collection, mats):
    """Heavy vertical stainless steel chemical coolant vats with pipes and gauges."""
    tank_coords = [
        (22.5, 23.0),
        (41.5, 23.0),
        (22.5, 41.0),
        (41.5, 41.0),
    ]

    for idx, (tx, ty) in enumerate(tank_coords):
        tz = -5.0
        # Concrete pad base
        add_cylinder(collection, f"Vat_Base_{idx}", (tx, ty, tz + 0.2), radius=1.65, height=0.4, material=mats["Concrete_Bunker_Dark"], segments=20, is_collider=True)
        # Stainless pressure cylinder (height 4.2m)
        add_cylinder(collection, f"Vat_Body_{idx}", (tx, ty, tz + 2.4), radius=1.45, height=4.0, material=mats["Steel_Stainless_Brushed"], segments=24, is_collider=True)
        # Top domed cap
        add_cylinder(collection, f"Vat_Dome_{idx}", (tx, ty, tz + 4.5), radius=1.35, height=0.3, material=mats["Steel_Stainless_Brushed"], segments=20, is_collider=False)
        # Bolted flange rings
        for fz in [tz + 1.2, tz + 2.8, tz + 4.2]:
            add_cylinder(collection, f"Vat_Flange_{idx}_{int(fz*10)}", (tx, ty, fz), radius=1.52, height=0.08, material=mats["Steel_Cast_Iron"], segments=20, is_collider=False)

        # Vertical liquid level sight gauge tube with glowing fluid
        add_box(collection, f"Vat_Gauge_Frame_{idx}", (tx + 1.42, ty - 0.08, tz + 1.0), (tx + 1.54, ty + 0.08, tz + 3.8), mats["Steel_Cast_Iron"], is_collider=False)
        add_box(collection, f"Vat_Gauge_Glass_{idx}", (tx + 1.45, ty - 0.04, tz + 1.1), (tx + 1.51, ty + 0.04, tz + 3.7), mats["Coolant_Fluid_Glow"], is_collider=False)

        # Overhead feeder pipe connecting into wall
        wall_x = 19.8 if tx < 32.0 else 44.2
        add_pipe(collection, f"Vat_Feeder_{idx}", (tx, ty, tz + 4.4), (wall_x, ty, tz + 4.4), radius=0.12, material=mats["Pipe_Industrial_Green"], segments=12, is_collider=False)
        add_cylinder(collection, f"Vat_Valve_{idx}", ((tx + wall_x) * 0.5, ty, tz + 4.4), radius=0.22, height=0.08, material=mats["Valve_Red"], segments=12, is_collider=False)


def build_blast_bulkhead_portals(collection, mats):
    """Massive hydraulic blast doors and reinforced bulkhead portals at key chokepoints."""
    portals = [
        # South approach to center
        (32.0, 13.5, 0.0, 0.0),
        # North approach to center
        (32.0, 50.5, 0.0, 180.0),
        # West lab corridor portal
        (19.5, 32.0, 0.0, 90.0),
        # East lab corridor portal
        (44.5, 32.0, 0.0, 270.0),
    ]

    for idx, (px, py, pz, yaw_deg) in enumerate(portals):
        rad = math.radians(yaw_deg)
        cos_y = math.cos(rad)
        sin_y = math.sin(rad)

        is_ns = abs(cos_y) > 0.5  # North-South portal vs East-West

        if is_ns:
            # Vault frame spanning X (width = 8.8m, height = 4.5m)
            add_box(collection, f"Bulkhead_Jamb_L_{idx}", (px - 4.4, py - 0.5, pz), (px - 3.6, py + 0.5, pz + 4.5), mats["Steel_Structural_Dark"], is_collider=True)
            add_box(collection, f"Bulkhead_Jamb_R_{idx}", (px + 3.6, py - 0.5, pz), (px + 4.4, py + 0.5, pz + 4.5), mats["Steel_Structural_Dark"], is_collider=True)
            add_box(collection, f"Bulkhead_Lintel_{idx}", (px - 4.4, py - 0.5, pz + 3.8), (px + 4.4, py + 0.5, pz + 4.5), mats["Steel_Structural_Dark"], is_collider=True)

            # Hazard chevron stripe trim along lintel
            add_box(collection, f"Bulkhead_Hazard_Trim_{idx}", (px - 4.3, py - 0.52, pz + 3.6), (px + 4.3, py - 0.48, pz + 3.8), mats["Hazard_Yellow"], is_collider=False)

            # Retracted heavy steel blast door slab
            add_box(collection, f"Bulkhead_Slab_{idx}", (px - 3.5, py - 0.35, pz), (px - 1.8, py + 0.35, pz + 3.8), mats["Steel_Cast_Iron"], is_collider=True)

            # Hydraulic closing ram above slab
            add_pipe(collection, f"Bulkhead_Ram_{idx}", (px - 2.6, py, pz + 4.0), (px + 0.5, py, pz + 4.0), radius=0.09, material=mats["Hydraulic_Chrome"], is_collider=False)

            # Manual override circular steel wheel
            add_cylinder(collection, f"Bulkhead_Wheel_{idx}", (px - 2.8, py - 0.42, pz + 1.8), radius=0.35, height=0.06, material=mats["Valve_Red"], segments=16, is_collider=False)
            # Strobe siren on lintel
            add_cylinder(collection, f"Bulkhead_Siren_{idx}", (px, py - 0.55, pz + 4.3), radius=0.14, height=0.22, material=mats["Emergency_Siren_Red"], segments=12, is_collider=False)

        else:
            # East-West portal spanning Y
            add_box(collection, f"Bulkhead_Jamb_L_{idx}", (px - 0.5, py - 4.4, pz), (px + 0.5, py - 3.6, pz + 4.5), mats["Steel_Structural_Dark"], is_collider=True)
            add_box(collection, f"Bulkhead_Jamb_R_{idx}", (px - 0.5, py + 3.6, pz), (px + 0.5, py + 4.4, pz + 4.5), mats["Steel_Structural_Dark"], is_collider=True)
            add_box(collection, f"Bulkhead_Lintel_{idx}", (px - 0.5, py - 4.4, pz + 3.8), (px + 0.5, py + 4.4, pz + 4.5), mats["Steel_Structural_Dark"], is_collider=True)

            # Hazard chevron stripe trim
            add_box(collection, f"Bulkhead_Hazard_Trim_{idx}", (px - 0.52, py - 4.3, pz + 3.6), (px - 0.48, py + 4.3, pz + 3.8), mats["Hazard_Yellow"], is_collider=False)

            # Retracted heavy blast door slab
            add_box(collection, f"Bulkhead_Slab_{idx}", (px - 0.35, py - 3.5, pz), (px + 0.35, py - 1.8, pz + 3.8), mats["Steel_Cast_Iron"], is_collider=True)

            # Hydraulic ram
            add_pipe(collection, f"Bulkhead_Ram_{idx}", (px, py - 2.6, pz + 4.0), (px, py + 0.5, pz + 4.0), radius=0.09, material=mats["Hydraulic_Chrome"], is_collider=False)

            # Wheel
            add_cylinder(collection, f"Bulkhead_Wheel_{idx}", (px - 0.42, py - 2.8, pz + 1.8), radius=0.35, height=0.06, material=mats["Valve_Red"], segments=16, is_collider=False)
            # Siren
            add_cylinder(collection, f"Bulkhead_Siren_{idx}", (px - 0.55, py, pz + 4.3), radius=0.14, height=0.22, material=mats["Emergency_Siren_Red"], segments=12, is_collider=False)


def build_server_telemetry_bank(collection, mats):
    """Mainframe telemetry suites with 19-inch racks, CRT monitors, and status LEDs."""
    server_banks = [
        # West lab server row
        (8.5, 26.0, 0.0, 0.0),
        (8.5, 30.0, 0.0, 0.0),
        (8.5, 34.0, 0.0, 0.0),
        (8.5, 38.0, 0.0, 0.0),
        # East lab server row
        (55.5, 26.0, 0.0, 180.0),
        (55.5, 30.0, 0.0, 180.0),
        (55.5, 34.0, 0.0, 180.0),
        (55.5, 38.0, 0.0, 180.0),
    ]

    for idx, (sx, sy, sz, yaw) in enumerate(server_banks):
        # 19-inch Rack Cabinet (width = 0.9m, depth = 0.9m, height = 2.4m)
        add_box(collection, f"Server_Rack_{idx}", (sx - 0.45, sy - 0.45, sz), (sx + 0.45, sy + 0.45, sz + 2.4), mats["Server_Cabinet_Matte"], bevel=True, bevel_width=0.02, is_collider=True)

        # Front Faceplate Modules
        fx = sx + (0.46 if yaw == 0.0 else -0.46)
        # Unit 1: CRT Monitor / Telemetry Display
        add_box(collection, f"Server_CRT_{idx}", (fx - 0.02, sy - 0.35, sz + 1.5), (fx + 0.02, sy + 0.35, sz + 2.05), mats["CRT_Phosphor_Green"], is_collider=False)
        # Bezel frame
        add_box(collection, f"Server_CRT_Bezel_{idx}", (fx - 0.03, sy - 0.38, sz + 1.47), (fx + 0.03, sy + 0.38, sz + 2.08), mats["Server_Faceplate_Dark"], is_collider=False)

        # Unit 2: Blinking LED diagnostic arrays
        for r in range(4):
            lz = sz + 0.7 + r * 0.18
            mat_led = mats["LED_Array_Green"] if r % 2 == 0 else mats["LED_Array_Amber"]
            add_box(collection, f"Server_LED_Row_{idx}_{r}", (fx - 0.02, sy - 0.32, lz), (fx + 0.02, sy + 0.32, lz + 0.08), mat_led, is_collider=False)

        # Overhead cable tray
        add_box(collection, f"Cable_Tray_{idx}", (sx - 0.3, sy - 0.5, sz + 2.4), (sx + 0.3, sy + 0.5, sz + 2.55), mats["Steel_Grate_Perforated"], is_collider=False)


def build_decon_and_hazmat_props(collection, mats):
    """Decontamination showers, wash basins, stacked hazmat drums, and crates."""
    # 1. Hazmat 55-Gallon Drum clusters
    drum_clusters = [
        # South-West lab staging
        (14.0, 15.0, 0.0, mats["Drum_Hazmat_Yellow"]),
        (15.2, 14.8, 0.0, mats["Drum_Bio_Blue"]),
        (14.6, 16.0, 0.0, mats["Drum_Hazmat_Yellow"]),
        # North-East bunker staging
        (49.0, 49.0, 0.0, mats["Drum_Hazmat_Yellow"]),
        (50.2, 48.8, 0.0, mats["Drum_Bio_Blue"]),
        (49.6, 50.0, 0.0, mats["Drum_Hazmat_Yellow"]),
    ]

    for idx, (dx, dy, dz, mat_drum) in enumerate(drum_clusters):
        # 55-gallon drum (radius 0.3m, height 0.95m)
        add_cylinder(collection, f"Facility_Drum_{idx}", (dx, dy, dz + 0.475), radius=0.30, height=0.95, material=mat_drum, segments=16, is_collider=True)
        # Chime rings
        add_cylinder(collection, f"Facility_Drum_Chime_T_{idx}", (dx, dy, dz + 0.85), radius=0.315, height=0.03, material=mats["Steel_Cast_Iron"], segments=16, is_collider=False)
        add_cylinder(collection, f"Facility_Drum_Chime_B_{idx}", (dx, dy, dz + 0.10), radius=0.315, height=0.03, material=mats["Steel_Cast_Iron"], segments=16, is_collider=False)

    # 2. Decontamination Safety Shower Stanchion
    decon_spots = [(6.0, 20.0), (58.0, 44.0)]
    for idx, (dx, dy) in enumerate(decon_spots):
        # Vertical supply pipe
        add_pipe(collection, f"Decon_Pipe_{idx}", (dx, dy, 0.0), (dx, dy, 3.2), radius=0.045, material=mats["Pipe_Industrial_Green"], is_collider=False)
        # Horizontal shower arm
        add_pipe(collection, f"Decon_Arm_{idx}", (dx, dy, 3.1), (dx + 0.8, dy, 3.1), radius=0.035, material=mats["Pipe_Industrial_Green"], is_collider=False)
        # Shower rose cone
        add_cylinder(collection, f"Decon_Rose_{idx}", (dx + 0.8, dy, 3.0), radius=0.22, height=0.10, material=mats["Steel_Stainless_Brushed"], segments=14, is_collider=False)
        # Floor drainage grate
        add_box(collection, f"Decon_Grate_{idx}", (dx + 0.4, dy - 0.4, 0.005), (dx + 1.2, dy + 0.4, 0.02), mats["Steel_Grate_Perforated"], is_collider=False)


def build_overhead_fluorescents(collection, mats):
    """Suspended industrial fluorescent troffers along high ceilings and catwalks."""
    light_positions = [
        # Catwalk Bridge Lights (z = 9.0m)
        (32.0, 16.0, 9.0),
        (32.0, 24.0, 9.0),
        (32.0, 32.0, 9.0),
        (32.0, 40.0, 9.0),
        (32.0, 48.0, 9.0),
        # West Lab Corridor (z = 5.0m)
        (10.0, 16.0, 5.0),
        (10.0, 32.0, 5.0),
        (10.0, 48.0, 5.0),
        # East Lab Corridor (z = 5.0m)
        (54.0, 16.0, 5.0),
        (54.0, 32.0, 5.0),
        (54.0, 48.0, 5.0),
    ]

    for idx, (lx, ly, lz) in enumerate(light_positions):
        # Fixture Housing
        add_box(collection, f"Light_Fixture_{idx}", (lx - 1.2, ly - 0.25, lz), (lx + 1.2, ly + 0.25, lz + 0.15), mats["Server_Cabinet_Matte"], is_collider=False)
        # Emissive Tube Reflector
        add_box(collection, f"Light_Tube_{idx}", (lx - 1.1, ly - 0.18, lz - 0.04), (lx + 1.1, ly + 0.18, lz), mats["Fluorescent_Troffer"], is_collider=False)
        # Suspension wires
        add_pipe(collection, f"Light_Wire_L_{idx}", (lx - 1.0, ly, lz + 0.15), (lx - 1.0, ly, 13.8), radius=0.012, material=mats["Steel_Structural_Dark"], is_collider=False)
        add_pipe(collection, f"Light_Wire_R_{idx}", (lx + 1.0, ly, lz + 0.15), (lx + 1.0, ly, 13.8), radius=0.012, material=mats["Steel_Structural_Dark"], is_collider=False)


def build_facility_scene():
    """Constructs the complete Deadzone Facility scene graph."""
    clear_scene()
    mats = setup_facility_materials()

    c_arch = get_or_create_collection("Architecture")
    c_catwalk = get_or_create_collection("Catwalks")
    c_silo = get_or_create_collection("Reactor_Silo")
    c_props = get_or_create_collection("Props_and_Telemetry")

    # =========================================================================
    # 1. PERIMETER CONCRETE BUNKER WALLS (bounds x: 0..64, y: 0..64, z: 0..14)
    # =========================================================================
    # South wall (y: -0.5..0.0)
    add_box(c_arch, "Perimeter_Wall_South", (0.0, -0.5, 0.0), (64.0, 0.0, 14.0), mats["Concrete_Bunker_Dark"])
    # North wall (y: 64.0..64.5)
    add_box(c_arch, "Perimeter_Wall_North", (0.0, 64.0, 0.0), (64.0, 64.5, 14.0), mats["Concrete_Bunker_Dark"])
    # West wall (x: -0.5..0.0)
    add_box(c_arch, "Perimeter_Wall_West", (-0.5, 0.0, 0.0), (0.0, 64.0, 14.0), mats["Concrete_Bunker_Dark"])
    # East wall (x: 64.0..64.5)
    add_box(c_arch, "Perimeter_Wall_East", (64.0, 0.0, 0.0), (64.5, 64.0, 14.0), mats["Concrete_Bunker_Dark"])

    # Decorative wall buttress columns (every 8m along perimeter)
    for w_idx, px in enumerate(range(8, 64, 8)):
        add_box(c_arch, f"Buttress_S_{w_idx}", (px - 0.4, 0.0, 0.0), (px + 0.4, 0.6, 14.0), mats["Concrete_Bunker_Dark"], is_collider=False)
        add_box(c_arch, f"Buttress_N_{w_idx}", (px - 0.4, 63.4, 0.0), (px + 0.4, 64.0, 14.0), mats["Concrete_Bunker_Dark"], is_collider=False)
    for w_idx, py in enumerate(range(8, 64, 8)):
        add_box(c_arch, f"Buttress_W_{w_idx}", (0.0, py - 0.4, 0.0), (0.6, py + 0.4, 14.0), mats["Concrete_Bunker_Dark"], is_collider=False)
        add_box(c_arch, f"Buttress_E_{w_idx}", (63.4, py - 0.4, 0.0), (64.0, py + 0.4, 14.0), mats["Concrete_Bunker_Dark"], is_collider=False)

    # =========================================================================
    # 2. GROUND FLOOR (z = 0.0m) WITH LOWER PIT CUTOUT (x: 20..44, y: 20..44)
    # =========================================================================
    # West Ground Floor slab
    add_box(c_arch, "Ground_Floor_West", (0.0, 0.0, -0.3), (20.0, 64.0, 0.0), mats["Concrete_Floor_Epoxy"])
    # East Ground Floor slab
    add_box(c_arch, "Ground_Floor_East", (44.0, 0.0, -0.3), (64.0, 64.0, 0.0), mats["Concrete_Floor_Epoxy"])
    # South Ground Floor strip
    add_box(c_arch, "Ground_Floor_South", (20.0, 0.0, -0.3), (44.0, 20.0, 0.0), mats["Concrete_Floor_Epoxy"])
    # North Ground Floor strip
    add_box(c_arch, "Ground_Floor_North", (20.0, 44.0, -0.3), (44.0, 64.0, 0.0), mats["Concrete_Floor_Epoxy"])

    # Yellow Cautionary Perimeter Line around the pit ledge
    add_box(c_arch, "Pit_Perimeter_Stripe_S", (19.8, 19.8, 0.002), (44.2, 20.2, 0.01), mats["Hazard_Yellow"], is_collider=False)
    add_box(c_arch, "Pit_Perimeter_Stripe_N", (19.8, 43.8, 0.002), (44.2, 44.2, 0.01), mats["Hazard_Yellow"], is_collider=False)
    add_box(c_arch, "Pit_Perimeter_Stripe_W", (19.8, 20.2, 0.002), (20.2, 43.8, 0.01), mats["Hazard_Yellow"], is_collider=False)
    add_box(c_arch, "Pit_Perimeter_Stripe_E", (43.8, 20.2, 0.002), (44.2, 43.8, 0.01), mats["Hazard_Yellow"], is_collider=False)

    # =========================================================================
    # 3. LOWER COOLANT PIT (z = -5.0m) & RAMPS
    # =========================================================================
    # Pit Floor slab at z = -5.0m
    add_box(c_arch, "Pit_Floor", (20.0, 20.0, -5.3), (44.0, 44.0, -5.0), mats["Concrete_Pit_Damp"])

    # Pit Inward Vertical Retaining Walls
    add_box(c_arch, "Pit_Wall_South", (20.0, 19.6, -5.0), (44.0, 20.0, 0.0), mats["Concrete_Bunker_Dark"])
    add_box(c_arch, "Pit_Wall_North", (20.0, 44.0, -5.0), (44.0, 44.4, 0.0), mats["Concrete_Bunker_Dark"])
    add_box(c_arch, "Pit_Wall_West", (19.6, 20.0, -5.0), (20.0, 44.0, 0.0), mats["Concrete_Bunker_Dark"])
    add_box(c_arch, "Pit_Wall_East", (44.0, 20.0, -5.0), (44.4, 44.0, 0.0), mats["Concrete_Bunker_Dark"])

    # Ramps into Pit:
    # South ramp: x in [28..36], y in [14..20], descending from z=0.0 down to z=-5.0
    add_ramp(c_arch, "Pit_Ramp_South", 28.0, 14.0, 0.0, 36.0, 20.0, -5.0, mats["Steel_Diamond_Plate"], is_collider=True)
    # North ramp: x in [28..36], y in [44..50], descending from z=0.0 down to z=-5.0 (slope from 44 to 50)
    add_ramp(c_arch, "Pit_Ramp_North", 28.0, 44.0, -5.0, 36.0, 50.0, 0.0, mats["Steel_Diamond_Plate"], is_collider=True)

    # =========================================================================
    # 4. HIGH STEEL CATWALKS & CENTER BRIDGE (z = 6.0m)
    # =========================================================================
    # Perimeter Catwalk Mezzanines
    # South: x: 0..64, y: 0..6, z: 6.0
    add_box(c_catwalk, "Catwalk_Perimeter_South", (0.0, 0.0, 5.85), (64.0, 6.0, 6.0), mats["Steel_Diamond_Plate"], is_collider=True)
    # North: x: 0..64, y: 58..64, z: 6.0
    add_box(c_catwalk, "Catwalk_Perimeter_North", (0.0, 58.0, 5.85), (64.0, 64.0, 6.0), mats["Steel_Diamond_Plate"], is_collider=True)
    # West: x: 0..6, y: 6..58, z: 6.0
    add_box(c_catwalk, "Catwalk_Perimeter_West", (0.0, 6.0, 5.85), (6.0, 58.0, 6.0), mats["Steel_Diamond_Plate"], is_collider=True)
    # East: x: 58..64, y: 6..58, z: 6.0
    add_box(c_catwalk, "Catwalk_Perimeter_East", (58.0, 6.0, 5.85), (64.0, 58.0, 6.0), mats["Steel_Diamond_Plate"], is_collider=True)

    # Center Catwalk Bridge: x: 26..38, y: 6..58, z: 6.0
    # Crucial: ray from (32, 32, 10) downwards hits this top surface at z = 6.0m!
    add_box(c_catwalk, "Catwalk_Bridge_Deck", (26.0, 6.0, 5.85), (38.0, 58.0, 6.0), mats["Steel_Diamond_Plate"], is_collider=True)

    # Underframe heavy I-beams supporting center bridge
    add_box(c_catwalk, "Bridge_Girder_W", (25.8, 6.0, 5.2), (26.4, 58.0, 5.85), mats["Steel_Structural_Dark"], is_collider=False)
    add_box(c_catwalk, "Bridge_Girder_E", (37.6, 6.0, 5.2), (38.2, 58.0, 5.85), mats["Steel_Structural_Dark"], is_collider=False)

    # Transverse cross-members every 6m under bridge
    for by in range(12, 58, 6):
        add_box(c_catwalk, f"Bridge_Cross_Beam_{by}", (26.4, by - 0.2, 5.3), (37.6, by + 0.2, 5.75), mats["Steel_Structural_Dark"], is_collider=False)

    # Connecting Ramps to Catwalk Mezzanine
    # West ramp: x in [2..6], y in [10..26], ascending from z=0.0 to z=6.0
    add_ramp(c_catwalk, "Catwalk_Ramp_West", 2.0, 10.0, 0.0, 6.0, 26.0, 6.0, mats["Steel_Diamond_Plate"], is_collider=True)
    # East ramp: x in [58..62], y in [38..54], descending from z=6.0 to z=0.0 (slope from 38 to 54)
    add_ramp(c_catwalk, "Catwalk_Ramp_East", 58.0, 38.0, 6.0, 62.0, 54.0, 0.0, mats["Steel_Diamond_Plate"], is_collider=True)

    # Catwalk Safety Guardrails along center bridge
    # West edge of center bridge (x = 26.0, y: 6..58)
    build_catwalk_guardrails(c_catwalk, "Bridge_Rail_W", (26.1, 6.0), (26.1, 58.0), mats, z_base=6.0)
    # East edge of center bridge (x = 38.0, y: 6..58)
    build_catwalk_guardrails(c_catwalk, "Bridge_Rail_E", (37.9, 6.0), (37.9, 58.0), mats, z_base=6.0)

    # =========================================================================
    # 5. STRUCTURAL SUPPORT PILLARS & TACTICAL COVER BLOCKS
    # =========================================================================
    # 4 Main Vertical Columns rising from ground (z=0) to bridge (z=6)
    pillars = [
        (24.0, 22.0, 26.0, 24.0),
        (38.0, 22.0, 40.0, 24.0),
        (24.0, 40.0, 26.0, 42.0),
        (38.0, 40.0, 40.0, 42.0),
    ]
    for idx, (px0, py0, px1, py1) in enumerate(pillars):
        add_box(c_arch, f"Pillar_Column_{idx}", (px0, py0, 0.0), (px1, py1, 6.0), mats["Steel_Structural_Dark"], bevel=True, bevel_width=0.05, is_collider=True)
        # Concrete base plinth
        add_box(c_arch, f"Pillar_Plinth_{idx}", (px0 - 0.2, py0 - 0.2, 0.0), (px1 + 0.2, py1 + 0.2, 0.6), mats["Concrete_Bunker_Dark"], is_collider=False)

    # Tactical Cover Blocks from procedural facility
    add_box(c_props, "Cover_Block_SW", (10.0, 12.0, 0.0), (14.0, 16.0, 3.0), mats["Server_Cabinet_Matte"], bevel=True, bevel_width=0.04, is_collider=True)
    add_box(c_props, "Cover_Block_NE", (50.0, 48.0, 0.0), (54.0, 52.0, 3.0), mats["Server_Cabinet_Matte"], bevel=True, bevel_width=0.04, is_collider=True)
    add_box(c_props, "Cover_Block_NW", (12.0, 48.0, 0.0), (16.0, 52.0, 3.5), mats["Concrete_Bunker_Dark"], bevel=True, bevel_width=0.04, is_collider=True)
    add_box(c_props, "Cover_Block_SE", (48.0, 12.0, 0.0), (52.0, 16.0, 3.5), mats["Concrete_Bunker_Dark"], bevel=True, bevel_width=0.04, is_collider=True)

    # =========================================================================
    # 6. HIGH-DENSITY FACILITY PROPS, SILO CORE, BULKHEADS, & LIGHTING
    # =========================================================================
    build_reactor_silo_core(c_silo, mats)
    build_containment_tanks(c_silo, mats)
    build_blast_bulkhead_portals(c_props, mats)
    build_server_telemetry_bank(c_props, mats)
    build_decon_and_hazmat_props(c_props, mats)
    build_overhead_fluorescents(c_props, mats)

    print("=== Deadzone Facility Scene Built Successfully! ===")


def export_glb():
    """Scale the metre-authored scene to cubes and export it (see maplib)."""
    maplib.export_map_glb("hd_facility")


if __name__ == "__main__":
    build_facility_scene()
    export_glb()
