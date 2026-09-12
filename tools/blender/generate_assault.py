#!/usr/bin/env python3
"""
CS:GO cs_assault Ultra-Detailed Realistic Map Generator for Blender 4.2+
Natively 1:1 metric scale on dense 56m x 56m CS:GO arena footprint (bounds: 4.0..60.0).

Features:
- CT Spawn, outside street with asphalt markings, streetlamps, curbs, crosswalk, storm drains, manholes.
- High-Clearance Highway Overpass with precast concrete box girders, cylindrical piers, jersey barriers, sign gantry.
- Detailed SWAT Tactical Van (armored chassis, recessed wheel wells, 32-segment rubber tires, bullbar, siren lightbars).
- East Rail Yard with crushed ballast gravel bed, dual train tracks with tie plates, detailed freight boxcar, chain-link security fencing.
- Commercial 18-wheeler semi-truck & delivery trailer backed into Bay 2.
- Main Warehouse (x: 8.0..46.0, y: 22.0..56.0, z: 9.0m) with brick foundation skirt, corrugated siding, roll-up shutter doors, dock bumpers.
- Warehouse Roof with parapet coping, skylights, HVAC chillers, ductwork, and exterior fire escape stairs.
- Complete Ventilation System (louvered intake mouths, drop shafts into catwalk and hostage office).
- Interior Catwalks (z=4.2m) with diamond-plate steel, yellow safety railings, stairs, and emergency exit signs.
- 2nd-Floor Hostage Office: panoramic observation window, acoustic ceiling, desks, PC workstations, CCTV security monitor console, chairs, water cooler.
- Industrial Warehouse Props: 3-tier pallet storage racking, high-detail forklift with pallets, chemical drums, oil drums, cardboard cartons, conduit runs, breaker panels, fire extinguishers.
"""

import sys
import os
import math
from pathlib import Path

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: This script must be run inside Blender: blender -b -P generate_assault.py")
    sys.exit(1)


def clear_scene():
    """Clear all objects and materials from the scene without resetting Blender factory state."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.5, transmission=0.0, alpha=1.0, emission_color=(0, 0, 0), emission_strength=0.0):
    """Create or get a Principled BSDF PBR material."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)

    if "Base Color" in bsdf.inputs:
        bsdf.inputs["Base Color"].default_value = (*base_color[:3], alpha)
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = metallic
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = roughness
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = transmission
    elif "Transmission" in bsdf.inputs:
        bsdf.inputs["Transmission"].default_value = transmission
    if "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = alpha

    # Emission for screens/sirens/lights
    if emission_strength > 0.0:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*emission_color[:3], 1.0)
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = (*emission_color[:3], 1.0)
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission_strength

    node_out = nodes.new(type="ShaderNodeOutputMaterial")
    node_out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], node_out.inputs["Surface"])

    if alpha < 1.0 or transmission > 0.0:
        mat.blend_method = "BLEND"
        mat.shadow_method = "CLIP"

    return mat


def setup_materials():
    """Setup all game materials with realistic PBR properties."""
    mats = {}
    # Concrete and road
    mats["Asphalt"] = create_pbr_material("Mat_Asphalt", (0.15, 0.15, 0.16), metallic=0.05, roughness=0.88)
    mats["Concrete"] = create_pbr_material("Mat_Concrete", (0.55, 0.53, 0.50), metallic=0.0, roughness=0.82)
    mats["Concrete_Dark"] = create_pbr_material("Mat_Concrete_Dark", (0.35, 0.34, 0.33), metallic=0.0, roughness=0.85)
    mats["Curb"] = create_pbr_material("Mat_Curb", (0.62, 0.60, 0.58), metallic=0.0, roughness=0.75)
    mats["Road_Yellow"] = create_pbr_material("Mat_Road_Yellow", (0.88, 0.72, 0.15), metallic=0.0, roughness=0.6)
    mats["Road_White"] = create_pbr_material("Mat_Road_White", (0.88, 0.88, 0.88), metallic=0.0, roughness=0.6)
    mats["Highway_Green"] = create_pbr_material("Mat_Highway_Green", (0.05, 0.42, 0.20), metallic=0.1, roughness=0.5)

    # Architectural brick & cladding
    mats["Brick_Base"] = create_pbr_material("Mat_Brick_Base", (0.45, 0.22, 0.17), metallic=0.0, roughness=0.88)
    mats["Warehouse_Wall"] = create_pbr_material("Mat_Warehouse_Wall", (0.38, 0.40, 0.44), metallic=0.4, roughness=0.65)
    mats["Warehouse_Wall_Stripe"] = create_pbr_material("Mat_Warehouse_Wall_Stripe", (0.78, 0.65, 0.15), metallic=0.2, roughness=0.6)
    mats["Warehouse_Floor"] = create_pbr_material("Mat_Warehouse_Floor", (0.42, 0.42, 0.40), metallic=0.1, roughness=0.75)
    mats["Warehouse_Roof"] = create_pbr_material("Mat_Warehouse_Roof", (0.28, 0.29, 0.31), metallic=0.2, roughness=0.9)
    mats["Garage_Door"] = create_pbr_material("Mat_Garage_Door", (0.42, 0.44, 0.46), metallic=0.75, roughness=0.4)
    mats["Hazard_Yellow"] = create_pbr_material("Mat_Hazard_Yellow", (0.90, 0.75, 0.10), metallic=0.1, roughness=0.5)
    mats["Hazard_Black"] = create_pbr_material("Mat_Hazard_Black", (0.10, 0.10, 0.12), metallic=0.1, roughness=0.5)
    mats["Skylight_Glass"] = create_pbr_material("Mat_Skylight_Glass", (0.7, 0.85, 0.95), transmission=0.85, roughness=0.1, alpha=0.4)

    # Steel & Industrial
    mats["Steel_Dark"] = create_pbr_material("Mat_Steel_Dark", (0.18, 0.19, 0.20), metallic=0.88, roughness=0.35)
    mats["Steel_Catwalk"] = create_pbr_material("Mat_Steel_Catwalk", (0.28, 0.30, 0.32), metallic=0.90, roughness=0.3)
    mats["Safety_Rail"] = create_pbr_material("Mat_Safety_Rail", (0.85, 0.70, 0.12), metallic=0.5, roughness=0.4)
    mats["Vent_Duct"] = create_pbr_material("Mat_Vent_Duct", (0.45, 0.48, 0.50), metallic=0.85, roughness=0.28)
    mats["Conduit_Silver"] = create_pbr_material("Mat_Conduit_Silver", (0.65, 0.68, 0.70), metallic=0.92, roughness=0.25)
    mats["Diamond_Plate"] = create_pbr_material("Mat_Diamond_Plate", (0.35, 0.37, 0.40), metallic=0.90, roughness=0.25)
    mats["Chainlink_Steel"] = create_pbr_material("Mat_Chainlink_Steel", (0.38, 0.40, 0.42), metallic=0.85, roughness=0.35)

    # Racks, Storage & Props
    mats["Rack_Upright_Blue"] = create_pbr_material("Mat_Rack_Upright_Blue", (0.12, 0.35, 0.65), metallic=0.35, roughness=0.4)
    mats["Rack_Beam_Orange"] = create_pbr_material("Mat_Rack_Beam_Orange", (0.85, 0.36, 0.08), metallic=0.25, roughness=0.4)
    mats["Cardboard_Box"] = create_pbr_material("Mat_Cardboard_Box", (0.62, 0.48, 0.32), metallic=0.0, roughness=0.85)
    mats["Plastic_Shrinkwrap"] = create_pbr_material("Mat_Plastic_Shrinkwrap", (0.88, 0.90, 0.94), transmission=0.75, roughness=0.15, alpha=0.45)
    mats["Drum_Blue"] = create_pbr_material("Mat_Drum_Blue", (0.10, 0.28, 0.58), metallic=0.5, roughness=0.45)
    mats["Drum_Red"] = create_pbr_material("Mat_Drum_Red", (0.68, 0.14, 0.12), metallic=0.5, roughness=0.45)
    mats["Drum_Black"] = create_pbr_material("Mat_Drum_Black", (0.12, 0.12, 0.14), metallic=0.5, roughness=0.45)
    mats["Wood_Crate"] = create_pbr_material("Mat_Wood_Crate", (0.60, 0.45, 0.30), metallic=0.0, roughness=0.8)

    # Safety & Signs
    mats["Fire_Extinguisher_Red"] = create_pbr_material("Mat_Fire_Extinguisher_Red", (0.85, 0.08, 0.08), metallic=0.4, roughness=0.3)
    mats["Exit_Sign_Green"] = create_pbr_material("Mat_Exit_Sign_Green", (0.10, 0.85, 0.25), emission_color=(0.15, 0.95, 0.3), emission_strength=5.0)
    mats["Fluorescent_Glow"] = create_pbr_material("Mat_Fluorescent_Glow", (0.95, 0.98, 1.0), emission_color=(0.95, 0.98, 1.0), emission_strength=8.0)
    mats["Traffic_Cone_Orange"] = create_pbr_material("Mat_Traffic_Cone_Orange", (0.95, 0.38, 0.05), metallic=0.0, roughness=0.5)
    mats["Lamp_Glow"] = create_pbr_material("Mat_Lamp_Glow", (1.0, 0.95, 0.8), emission_color=(1.0, 0.95, 0.8), emission_strength=6.0)

    # Containers & Train
    mats["Container_Blue"] = create_pbr_material("Mat_Container_Blue", (0.16, 0.32, 0.55), metallic=0.3, roughness=0.5)
    mats["Container_Red"] = create_pbr_material("Mat_Container_Red", (0.65, 0.22, 0.18), metallic=0.3, roughness=0.5)
    mats["Container_Green"] = create_pbr_material("Mat_Container_Green", (0.18, 0.40, 0.26), metallic=0.3, roughness=0.5)
    mats["Train_Rust"] = create_pbr_material("Mat_Train_Rust", (0.52, 0.20, 0.16), metallic=0.5, roughness=0.65)
    mats["Train_Rail"] = create_pbr_material("Mat_Train_Rail", (0.24, 0.25, 0.27), metallic=0.95, roughness=0.2)
    mats["Train_Tie"] = create_pbr_material("Mat_Train_Tie", (0.22, 0.16, 0.12), metallic=0.0, roughness=0.9)

    # Vehicles
    mats["SWAT_Navy"] = create_pbr_material("Mat_SWAT_Navy", (0.12, 0.16, 0.24), metallic=0.5, roughness=0.4)
    mats["SWAT_Tire"] = create_pbr_material("Mat_SWAT_Tire", (0.08, 0.08, 0.09), metallic=0.0, roughness=0.92)
    mats["SWAT_Chrome"] = create_pbr_material("Mat_SWAT_Chrome", (0.82, 0.85, 0.88), metallic=0.95, roughness=0.15)
    mats["SWAT_Glass"] = create_pbr_material("Mat_SWAT_Glass", (0.68, 0.82, 0.92), transmission=0.8, roughness=0.05, alpha=0.5)
    mats["SWAT_Headlight"] = create_pbr_material("Mat_SWAT_Headlight", (0.95, 0.95, 0.75), emission_color=(1.0, 1.0, 0.8), emission_strength=5.0)
    mats["SWAT_Siren_Red"] = create_pbr_material("Mat_SWAT_Siren_Red", (0.95, 0.08, 0.08), emission_color=(1.0, 0.05, 0.05), emission_strength=5.0)
    mats["SWAT_Siren_Blue"] = create_pbr_material("Mat_SWAT_Siren_Blue", (0.08, 0.35, 0.95), emission_color=(0.1, 0.4, 1.0), emission_strength=5.0)
    mats["Truck_Cab"] = create_pbr_material("Mat_Truck_Cab", (0.88, 0.88, 0.90), metallic=0.3, roughness=0.35)
    mats["Truck_Trailer"] = create_pbr_material("Mat_Truck_Trailer", (0.65, 0.67, 0.70), metallic=0.6, roughness=0.4)
    mats["Forklift_Yellow"] = create_pbr_material("Mat_Forklift_Yellow", (0.85, 0.65, 0.10), metallic=0.2, roughness=0.4)

    # Office & Electronics
    mats["Office_Wall"] = create_pbr_material("Mat_Office_Wall", (0.75, 0.72, 0.68), metallic=0.0, roughness=0.85)
    mats["Office_Floor"] = create_pbr_material("Mat_Office_Floor", (0.36, 0.38, 0.42), metallic=0.0, roughness=0.7)
    mats["Office_Window"] = create_pbr_material("Mat_Office_Window", (0.68, 0.82, 0.92), transmission=0.88, roughness=0.05, alpha=0.35)
    mats["Desk_Wood"] = create_pbr_material("Mat_Desk_Wood", (0.38, 0.32, 0.26), metallic=0.0, roughness=0.7)
    mats["Computer_Chassis"] = create_pbr_material("Mat_Computer_Chassis", (0.70, 0.68, 0.64), metallic=0.1, roughness=0.6)
    mats["Screen_CCTV"] = create_pbr_material("Mat_Screen_CCTV", (0.12, 0.28, 0.22), emission_color=(0.18, 0.55, 0.38), emission_strength=3.0)
    mats["Water_Bottle_Blue"] = create_pbr_material("Mat_Water_Bottle_Blue", (0.2, 0.5, 0.85), transmission=0.9, roughness=0.05, alpha=0.4)

    return mats


def get_or_create_collection(name):
    """Get or create a Blender scene collection."""
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def add_box(col, name, p0, p1, material, is_collider=True, bevel=False, bevel_width=0.03):
    """Create a box from min (p0) to max (p1) coordinates."""
    x0, y0, z0 = p0
    x1, y1, z1 = p1

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    cz = (z0 + z1) / 2.0

    sx = abs(x1 - x0)
    sy = abs(y1 - y0)
    sz = abs(z1 - z0)

    if sx < 0.001 or sy < 0.001 or sz < 0.001:
        return None

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = (cx, cy, cz)
    obj.scale = (sx / 2.0, sy / 2.0, sz / 2.0)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if material:
        obj.data.materials.append(material)

    if bevel and max(sx, sy, sz) > bevel_width * 3:
        bev = obj.modifiers.new(name="Bevel", type='BEVEL')
        bev.width = bevel_width
        bev.segments = 2
        bev.limit_method = 'ANGLE'
        bev.angle_limit = math.radians(40)

    obj["is_collider"] = is_collider
    return obj


def add_cylinder(col, name, center, radius, height, material, segments=32, is_collider=True, smooth=True):
    """Create a smooth vertical cylinder."""
    cx, cy, cz = center
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = (cx, cy, cz)

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
    bm.to_mesh(mesh)
    bm.free()

    if smooth:
        for p in mesh.polygons:
            p.use_smooth = True

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = is_collider
    return obj


def add_i_beam(col, name, center_x, center_y, z0, z1, depth=0.35, flange_w=0.30, web_t=0.04, flange_t=0.04, material=None, is_collider=True):
    """Create a structural steel I-beam column with web and flanges."""
    # Central web
    add_box(col, f"{name}_Web", (center_x - web_t/2, center_y - depth/2 + flange_t, z0), (center_x + web_t/2, center_y + depth/2 - flange_t, z1), material, is_collider=False)
    # Front flange
    add_box(col, f"{name}_Flange_Front", (center_x - flange_w/2, center_y - depth/2, z0), (center_x + flange_w/2, center_y - depth/2 + flange_t, z1), material, is_collider=is_collider, bevel=True)
    # Back flange
    add_box(col, f"{name}_Flange_Back", (center_x - flange_w/2, center_y + depth/2 - flange_t, z0), (center_x + flange_w/2, center_y + depth/2, z1), material, is_collider=is_collider, bevel=True)


def add_pallet_rack(col, mats, x0, y0, x1, y1, num_tiers=3, total_height=5.5):
    """Build heavy-duty warehouse storage pallet racking with multiple shelf tiers and loaded pallets."""
    length = abs(x1 - x0)
    num_bays = max(1, int(length / 3.0))
    bay_w = length / num_bays
    depth = abs(y1 - y0)
    cy = (y0 + y1) / 2.0

    # Uprights (Blue)
    for i in range(num_bays + 1):
        rx = x0 + i * bay_w
        # Front and back vertical posts
        add_box(col, f"Rack_Post_F_{i}", (rx - 0.05, cy - depth/2, 0.0), (rx + 0.05, cy - depth/2 + 0.1, total_height), mats["Rack_Upright_Blue"], is_collider=True)
        add_box(col, f"Rack_Post_B_{i}", (rx - 0.05, cy + depth/2 - 0.1, 0.0), (rx + 0.05, cy + depth/2, total_height), mats["Rack_Upright_Blue"], is_collider=True)
        # Footplate
        add_box(col, f"Rack_Foot_F_{i}", (rx - 0.1, cy - depth/2 - 0.05, 0.0), (rx + 0.1, cy - depth/2 + 0.15, 0.02), mats["Steel_Dark"], is_collider=False)
        add_box(col, f"Rack_Foot_B_{i}", (rx - 0.1, cy + depth/2 - 0.15, 0.0), (rx + 0.1, cy + depth/2 + 0.05, 0.02), mats["Steel_Dark"], is_collider=False)
        # Side lacing diagonal ties
        for tz in [1.2, 2.6, 4.0]:
            add_box(col, f"Rack_Lace_{i}_{int(tz)}", (rx - 0.03, cy - depth/2 + 0.08, tz), (rx + 0.03, cy + depth/2 - 0.08, tz + 0.06), mats["Steel_Dark"], is_collider=False)

    # Shelf Beams (Orange) & Pallets
    tier_heights = [0.8, 2.4, 4.0]
    drum_colors = [mats["Drum_Blue"], mats["Drum_Red"], mats["Drum_Black"]]
    for b in range(num_bays):
        bx0 = x0 + b * bay_w + 0.06
        bx1 = x0 + (b + 1) * bay_w - 0.06
        for tid, tz in enumerate(tier_heights):
            # Front beam
            add_box(col, f"Rack_Beam_F_{b}_{tid}", (bx0, cy - depth/2, tz), (bx1, cy - depth/2 + 0.08, tz + 0.12), mats["Rack_Beam_Orange"], is_collider=True)
            # Back beam
            add_box(col, f"Rack_Beam_B_{b}_{tid}", (bx0, cy + depth/2 - 0.08, tz), (bx1, cy + depth/2, tz + 0.12), mats["Rack_Beam_Orange"], is_collider=True)
            # Wire mesh / decking
            add_box(col, f"Rack_Deck_{b}_{tid}", (bx0, cy - depth/2 + 0.06, tz + 0.1), (bx1, cy + depth/2 - 0.06, tz + 0.12), mats["Steel_Dark"], is_collider=True)

            # Place 2 pallets on each bay tier
            for pid, px in enumerate([bx0 + 0.65, bx1 - 0.65]):
                pz = tz + 0.12
                # Wood pallet base
                add_box(col, f"Pallet_{b}_{tid}_{pid}", (px - 0.6, cy - 0.5, pz), (px + 0.6, cy + 0.5, pz + 0.14), mats["Wood_Crate"], is_collider=True)
                # Cargo on top: alternate cardboard boxes, crates, or drums
                pattern = (b + tid + pid) % 3
                if pattern == 0:
                    # Cardboard box stack with shrinkwrap
                    add_box(col, f"Cargo_Box_{b}_{tid}_{pid}", (px - 0.55, cy - 0.45, pz + 0.14), (px + 0.55, cy + 0.45, pz + 1.1), mats["Cardboard_Box"], is_collider=True, bevel=True, bevel_width=0.03)
                    add_box(col, f"Shrink_{b}_{tid}_{pid}", (px - 0.56, cy - 0.46, pz + 0.15), (px + 0.56, cy + 0.46, pz + 1.12), mats["Plastic_Shrinkwrap"], is_collider=False)
                elif pattern == 1:
                    # Large wooden machinery crate
                    add_box(col, f"Cargo_Crate_{b}_{tid}_{pid}", (px - 0.5, cy - 0.4, pz + 0.14), (px + 0.5, cy + 0.4, pz + 0.95), mats["Wood_Crate"], is_collider=True, bevel=True, bevel_width=0.04)
                else:
                    # 4 Steel chemical/oil drums on pallet
                    for di, (dx, dy) in enumerate([(-0.25, -0.22), (0.25, -0.22), (-0.25, 0.22), (0.25, 0.22)]):
                        dmat = drum_colors[(b + di) % len(drum_colors)]
                        add_cylinder(col, f"Drum_{b}_{tid}_{pid}_{di}", (px + dx, cy + dy, pz + 0.14 + 0.45), radius=0.22, height=0.9, material=dmat, segments=24, is_collider=True)


def add_forklift(col, mats, x, y, z=0.0):
    """Create a detailed industrial counterbalanced forklift carrying a loaded pallet."""
    # Main yellow chassis
    add_box(col, "Forklift_Chassis", (x - 0.7, y - 1.2, z + 0.2), (x + 0.7, y + 0.8, z + 0.9), mats["Forklift_Yellow"], bevel=True, bevel_width=0.05)
    # Heavy rear cast iron counterweight
    add_box(col, "Forklift_Counterweight", (x - 0.68, y - 1.35, z + 0.25), (x + 0.68, y - 1.15, z + 0.85), mats["Steel_Dark"], bevel=True, bevel_width=0.06)
    # Welded driver safety overhead guard cage
    for cx in [-0.65, 0.65]:
        for cy in [-1.0, 0.5]:
            add_box(col, f"Forklift_Cage_Pillar_{cx}_{cy}", (x + cx - 0.03, y + cy - 0.03, z + 0.9), (x + cx + 0.03, y + cy + 0.03, z + 2.15), mats["Steel_Dark"], is_collider=False)
    add_box(col, "Forklift_Cage_Roof", (x - 0.68, y - 1.05, z + 2.15), (x + 0.68, y + 0.55, z + 2.22), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    # Driver seat and steering wheel
    add_box(col, "Forklift_Seat", (x - 0.3, y - 0.6, z + 0.9), (x + 0.3, y - 0.2, z + 1.25), mats["Hazard_Black"], bevel=True, bevel_width=0.03)
    add_cylinder(col, "Forklift_Steering_Wheel", (x, y + 0.1, z + 1.45), radius=0.2, height=0.04, material=mats["Steel_Dark"], segments=16, is_collider=False)

    # 2-Stage vertical lifting mast channels (Front)
    add_box(col, "Forklift_Mast_L", (x - 0.45, y + 0.8, z), (x - 0.35, y + 0.9, z + 2.6), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    add_box(col, "Forklift_Mast_R", (x + 0.35, y + 0.8, z), (x + 0.45, y + 0.9, z + 2.6), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    add_box(col, "Forklift_Fork_Backrest", (x - 0.5, y + 0.91, z + 0.1), (x + 0.5, y + 0.96, z + 0.8), mats["Steel_Dark"], is_collider=False)
    # Heavy steel lifting forks
    add_box(col, "Forklift_Fork_L", (x - 0.35, y + 0.95, z + 0.02), (x - 0.25, y + 2.1, z + 0.08), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    add_box(col, "Forklift_Fork_R", (x + 0.25, y + 0.95, z + 0.02), (x + 0.35, y + 2.1, z + 0.08), mats["Steel_Dark"], bevel=True, bevel_width=0.02)

    # Solid industrial rubber tires
    for wx, wy in [(-0.75, 0.45), (0.75, 0.45)]:
        add_cylinder(col, f"Forklift_Tire_F_{wx}", (x + wx, y + wy, z + 0.35), radius=0.35, height=0.25, material=mats["SWAT_Tire"], segments=24)
    for wx, wy in [(-0.72, -0.95), (0.72, -0.95)]:
        add_cylinder(col, f"Forklift_Tire_R_{wx}", (x + wx, y + wy, z + 0.28), radius=0.28, height=0.22, material=mats["SWAT_Tire"], segments=24)

    # Loaded pallet on forks
    add_box(col, "Forklift_Pallet", (x - 0.6, y + 1.0, z + 0.08), (x + 0.6, y + 2.0, z + 0.22), mats["Wood_Crate"])
    add_box(col, "Forklift_Crate_1", (x - 0.55, y + 1.05, z + 0.22), (x + 0.05, y + 1.95, z + 0.95), mats["Wood_Crate"], bevel=True, bevel_width=0.03)
    add_box(col, "Forklift_Crate_2", (x + 0.05, y + 1.05, z + 0.22), (x + 0.55, y + 1.95, z + 0.85), mats["Cardboard_Box"], bevel=True, bevel_width=0.03)


def add_crane_system(col, mats):
    """Build overhead traveling bridge crane system spanning the warehouse ceiling."""
    # Longitudinal runway rails along East and West walls
    add_box(col, "Crane_Rail_W", (8.1, 22.0, 7.6), (8.4, 56.0, 7.85), mats["Hazard_Yellow"], is_collider=False)
    add_box(col, "Crane_Rail_E", (45.6, 22.0, 7.6), (45.9, 56.0, 7.85), mats["Hazard_Yellow"], is_collider=False)

    # Crane transverse double-box bridge girder across the bay at y=36
    cy = 36.0
    add_box(col, "Crane_Bridge_1", (8.3, cy - 0.4, 7.7), (45.7, cy - 0.15, 8.2), mats["Hazard_Yellow"], is_collider=False)
    add_box(col, "Crane_Bridge_2", (8.3, cy + 0.15, 7.7), (45.7, cy + 0.4, 8.2), mats["Hazard_Yellow"], is_collider=False)
    add_box(col, "Crane_Hazard_Stripe", (8.3, cy - 0.42, 7.7), (45.7, cy - 0.38, 7.85), mats["Hazard_Black"], is_collider=False)

    # Motorized hoist trolley carriage
    tx = 26.0
    add_box(col, "Crane_Trolley", (tx - 0.8, cy - 0.5, 8.1), (tx + 0.8, cy + 0.5, 8.65), mats["Steel_Dark"], bevel=True, bevel_width=0.03, is_collider=False)
    add_cylinder(col, "Crane_Cable_Drum", (tx, cy, 8.35), radius=0.25, height=0.6, material=mats["Steel_Dark"], segments=16, is_collider=False)
    # Steel wire rope drop and lifting hook
    add_cylinder(col, "Crane_Cable", (tx, cy, 6.7), radius=0.02, height=2.8, material=mats["Steel_Dark"], segments=8, is_collider=False)
    add_box(col, "Crane_Hook_Block", (tx - 0.18, cy - 0.15, 5.1), (tx + 0.18, cy + 0.15, 5.4), mats["Hazard_Yellow"], bevel=True, bevel_width=0.02, is_collider=False)
    add_cylinder(col, "Crane_Hook", (tx, cy, 4.9), radius=0.1, height=0.3, material=mats["Steel_Dark"], segments=12, is_collider=False)


def add_lighting_and_utilities(col, mats):
    """Add suspended fluorescent troffer fixtures, electrical conduits, breaker boxes, and fire safety gear."""
    # 8 Suspended Fluorescent Troffers in 2 rows across warehouse ceiling
    for fy in [26.0, 33.0, 41.0, 48.0]:
        for fx in [18.0, 34.0]:
            # Reflector fixture housing
            add_box(col, f"Fluo_Fixture_{int(fx)}_{int(fy)}", (fx - 1.2, fy - 0.3, 8.1), (fx + 1.2, fy + 0.3, 8.25), mats["Steel_Dark"], is_collider=False)
            # Emissive glowing tubes
            add_cylinder(col, f"Fluo_Tube1_{int(fx)}_{int(fy)}", (fx, fy - 0.1, 8.12), radius=0.03, height=2.2, material=mats["Fluorescent_Glow"], segments=12, is_collider=False)
            add_cylinder(col, f"Fluo_Tube2_{int(fx)}_{int(fy)}", (fx, fy + 0.1, 8.12), radius=0.03, height=2.2, material=mats["Fluorescent_Glow"], segments=12, is_collider=False)
            # Suspension chains
            for cx in [fx - 1.0, fx + 1.0]:
                add_cylinder(col, f"Fluo_Chain_{cx}_{fy}", (cx, fy, 8.55), radius=0.012, height=0.6, material=mats["Steel_Dark"], segments=6, is_collider=False)

    # Electrical Conduit Pipe runs along walls
    add_cylinder(col, "Conduit_West_Low", (8.12, 38.0, 1.8), radius=0.025, height=32.0, material=mats["Conduit_Silver"], segments=8, is_collider=False)
    add_cylinder(col, "Conduit_North_Low", (26.0, 55.88, 1.8), radius=0.025, height=36.0, material=mats["Conduit_Silver"], segments=8, is_collider=False)

    # Wall-mounted Electrical Switchgear & Breaker Panels
    for px, py in [(8.15, 29.0), (8.15, 45.0), (27.2, 55.85)]:
        add_box(col, f"Breaker_Panel_{int(px)}_{int(py)}", (px - 0.08, py - 0.45, 1.4), (px + 0.08, py + 0.45, 2.3), mats["Steel_Dark"], bevel=True, bevel_width=0.02, is_collider=False)
        add_box(col, f"Breaker_Hazard_Sign_{int(px)}_{int(py)}", (px - 0.09, py - 0.12, 1.8), (px + 0.09, py + 0.12, 2.04), mats["Hazard_Yellow"], is_collider=False)

    # Fire Extinguisher Stations with inspection signage
    for ex, ey, ez in [(8.15, 24.0, 1.2), (45.85, 24.0, 1.2), (28.45, 42.0, 5.4)]:
        add_box(col, f"Fire_Sign_{int(ex)}_{int(ey)}", (ex - 0.06, ey - 0.15, ez + 0.6), (ex + 0.06, ey + 0.15, ez + 0.9), mats["Fire_Extinguisher_Red"], is_collider=False)
        add_cylinder(col, f"Fire_Extinguisher_{int(ex)}_{int(ey)}", (ex, ey, ez), radius=0.09, height=0.55, material=mats["Fire_Extinguisher_Red"], segments=16, is_collider=False)

    # Illuminated Green "EXIT" signs over all exit portals
    for sx, sy, sz in [(25.5, 55.85, 2.75), (45.85, 45.5, 6.95), (28.45, 49.5, 6.75)]:
        add_box(col, f"Exit_Sign_{int(sx)}_{int(sy)}", (sx - 0.35, sy - 0.08, sz - 0.15), (sx + 0.35, sy + 0.08, sz + 0.15), mats["Exit_Sign_Green"], is_collider=False)


def add_hostage_office_props(col, mats):
    """Add detailed office furniture, computer stations, CCTV console, water cooler, and hostage restraints."""
    # Workstations with wood laminate desks
    for dx, dy in [(31.0, 52.0), (37.0, 52.0)]:
        add_box(col, f"Desk_{int(dx)}", (dx - 1.1, dy - 0.5, 4.2), (dx + 1.1, dy + 0.5, 4.95), mats["Desk_Wood"], bevel=True, bevel_width=0.02)
        # PC Tower under desk
        add_box(col, f"PC_Tower_{int(dx)}", (dx + 0.7, dy - 0.3, 4.2), (dx + 0.95, dy + 0.3, 4.75), mats["Computer_Chassis"], bevel=True, bevel_width=0.02, is_collider=False)
        # Dual monitors on desk
        add_box(col, f"Monitor_L_{int(dx)}", (dx - 0.45, dy + 0.2, 4.95), (dx - 0.05, dy + 0.28, 5.45), mats["Steel_Dark"], is_collider=False)
        add_box(col, f"Monitor_R_{int(dx)}", (dx + 0.05, dy + 0.2, 4.95), (dx + 0.45, dy + 0.28, 5.45), mats["Steel_Dark"], is_collider=False)
        # Keyboard and mousepad
        add_box(col, f"Keyboard_{int(dx)}", (dx - 0.25, dy - 0.2, 4.95), (dx + 0.25, dy, 4.98), mats["Steel_Dark"], is_collider=False)

        # Swivel office chair with 5-star wheeled base
        add_cylinder(col, f"Chair_Base_{int(dx)}", (dx, dy - 0.9, 4.25), radius=0.3, height=0.1, material=mats["Steel_Dark"], segments=12, is_collider=False)
        add_cylinder(col, f"Chair_Post_{int(dx)}", (dx, dy - 0.9, 4.45), radius=0.04, height=0.3, material=mats["Steel_Dark"], segments=8, is_collider=False)
        add_box(col, f"Chair_Seat_{int(dx)}", (dx - 0.25, dy - 1.15, 4.58), (dx + 0.25, dy - 0.75, 4.68), mats["Hazard_Black"], bevel=True, bevel_width=0.03, is_collider=False)
        add_box(col, f"Chair_Back_{int(dx)}", (dx - 0.25, dy - 1.18, 4.68), (dx + 0.25, dy - 1.12, 5.15), mats["Hazard_Black"], bevel=True, bevel_width=0.03, is_collider=False)

    # 4-Drawer steel filing cabinets
    for fx in [43.0, 44.2]:
        add_box(col, f"Filing_Cabinet_{int(fx*10)}", (fx - 0.45, 53.5, 4.2), (fx + 0.45, 54.5, 5.6), mats["Steel_Dark"], bevel=True, bevel_width=0.02)

    # CCTV 4-Monitor Security Console on south counter
    add_box(col, "CCTV_Console_Desk", (33.0, 45.0, 4.2), (37.0, 45.8, 4.95), mats["Desk_Wood"])
    for mi, (mx, mz) in enumerate([(-0.6, 5.0), (0.6, 5.0), (-0.6, 5.55), (0.6, 5.55)]):
        add_box(col, f"CCTV_Screen_{mi}", (35.0 + mx - 0.45, 45.5, mz), (35.0 + mx + 0.45, 45.6, mz + 0.45), mats["Screen_CCTV"], is_collider=False)

    # Water cooler station
    add_box(col, "Water_Cooler_Base", (43.5, 47.0, 4.2), (44.3, 47.8, 5.2), mats["Computer_Chassis"], bevel=True, bevel_width=0.03)
    add_cylinder(col, "Water_Bottle", (43.9, 47.4, 5.55), radius=0.25, height=0.6, material=mats["Water_Bottle_Blue"], segments=16, is_collider=False)

    # Foldable hostage chairs
    for hx in [33.5, 36.5]:
        add_box(col, f"Hostage_Chair_{int(hx*10)}", (hx - 0.25, 48.0, 4.2), (hx + 0.25, 48.5, 4.8), mats["Wood_Crate"], bevel=True, bevel_width=0.02)
        add_box(col, f"Hostage_Chair_Back_{int(hx*10)}", (hx - 0.25, 48.4, 4.8), (hx + 0.25, 48.5, 5.3), mats["Wood_Crate"], bevel=True, bevel_width=0.02)


def add_street_and_civil_details(col, mats):
    """Add stormwater catch basins, manholes, chain-link security fencing, traffic cones, and highway signs."""
    # Catch basins / storm drains with slotted grates along curbs
    for sx, sy in [(12.0, 7.85), (28.0, 7.85), (42.0, 7.85), (22.0, 20.65)]:
        add_box(col, f"Storm_Drain_{int(sx)}_{int(sy)}", (sx - 0.45, sy - 0.25, 0.0), (sx + 0.45, sy + 0.25, 0.02), mats["Steel_Dark"], is_collider=False)
        for g in range(5):
            add_box(col, f"Drain_Grate_{int(sx)}_{g}", (sx - 0.35 + g * 0.15, sy - 0.22, 0.015), (sx - 0.30 + g * 0.15, sy + 0.22, 0.025), mats["Hazard_Black"], is_collider=False)

    # Cast Iron Sewer Manhole Covers in the street
    for mx, my in [(19.0, 12.0), (33.0, 16.0), (45.0, 12.0)]:
        add_cylinder(col, f"Manhole_{int(mx)}_{int(my)}", (mx, my, 0.01), radius=0.45, height=0.02, material=mats["Steel_Dark"], segments=24, is_collider=False)

    # Traffic Safety Cones near SWAT van
    for tx, ty in [(17.5, 12.5), (22.5, 12.5), (23.5, 15.5)]:
        add_box(col, f"Cone_Base_{int(tx*10)}_{int(ty*10)}", (tx - 0.2, ty - 0.2, 0.0), (tx + 0.2, ty + 0.2, 0.03), mats["Traffic_Cone_Orange"], is_collider=False)
        add_cylinder(col, f"Cone_Body_{int(tx*10)}_{int(ty*10)}", (tx, ty, 0.38), radius=0.12, height=0.7, material=mats["Traffic_Cone_Orange"], segments=16, is_collider=False)
        add_cylinder(col, f"Cone_Reflector_{int(tx*10)}_{int(ty*10)}", (tx, ty, 0.45), radius=0.125, height=0.15, material=mats["Road_White"], segments=16, is_collider=False)

    # Precast Box Girders under highway overpass deck (under deck bottom z=6.9)
    for gx in [10.0, 20.0, 30.0, 40.0]:
        add_box(col, f"Bridge_Girder_{int(gx)}", (gx - 1.2, 4.0, 6.3), (gx + 1.2, 8.0, 6.9), mats["Concrete_Dark"], bevel=True, bevel_width=0.08)

    # Highway Overhead Directional Sign Gantry
    add_cylinder(col, "Gantry_Post_S", (12.0, 4.3, 9.8), radius=0.15, height=4.6, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_cylinder(col, "Gantry_Post_N", (12.0, 7.7, 9.8), radius=0.15, height=4.6, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_box(col, "Gantry_Beam", (11.85, 4.15, 11.9), (12.15, 7.85, 12.2), mats["Steel_Dark"], is_collider=False)
    add_box(col, "Gantry_Sign", (11.92, 4.5, 10.4), (12.08, 7.5, 11.8), mats["Highway_Green"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(col, "Gantry_Sign_Border", (11.9, 4.45, 10.35), (12.1, 7.55, 11.85), mats["Road_White"], is_collider=False)

    # Chain-Link Perimeter Security Fence enclosing rail yard
    for fx in range(48, 60, 3):
        add_cylinder(col, f"Fence_Post_{fx}", (float(fx), 21.0, 1.25), radius=0.04, height=2.5, material=mats["Chainlink_Steel"], segments=12, is_collider=True)
    add_box(col, "Fence_Top_Rail", (48.0, 20.96, 2.45), (59.0, 21.04, 2.5), mats["Chainlink_Steel"], is_collider=False)
    add_box(col, "Fence_Fabric", (48.0, 20.98, 0.0), (59.0, 21.02, 2.45), mats["Chainlink_Steel"], is_collider=True)


def build_assault_scene():
    """Construct the complete realistic cs_assault map at 1:1 human scale on 56m x 56m footprint."""
    clear_scene()
    mats = setup_materials()

    # Collections
    c_arch = get_or_create_collection("01_Architecture")
    c_overpass = get_or_create_collection("02_Highway_Overpass")
    c_rail = get_or_create_collection("03_Rail_Yard")
    c_vehicles = get_or_create_collection("04_Vehicles")
    c_warehouse = get_or_create_collection("05_Warehouse_Interior")
    c_vents = get_or_create_collection("06_Ventilation_System")
    c_catwalk = get_or_create_collection("07_Catwalks_And_Stairs")
    c_office = get_or_create_collection("08_Hostage_Office")
    c_props = get_or_create_collection("09_Industrial_Props")

    print("[1/10] Building Ground Terrain, Streets, and Sidewalks...")
    # Map footprint: (4.0..60.0, 4.0..60.0) -> Exactly 56m x 56m
    # Street asphalt: (4.0..60.0, 8.0..22.0)
    add_box(c_arch, "Street_Asphalt", (4.0, 8.0, -0.2), (60.0, 22.0, 0.0), mats["Asphalt"])

    # Double Yellow Centerline on Street (y=15.0)
    add_box(c_arch, "Road_Stripe_Yellow_1", (4.0, 14.85, 0.005), (60.0, 14.95, 0.01), mats["Road_Yellow"], is_collider=False)
    add_box(c_arch, "Road_Stripe_Yellow_2", (4.0, 15.05, 0.005), (60.0, 15.15, 0.01), mats["Road_Yellow"], is_collider=False)

    # White Crosswalk Markings in front of Warehouse Garage Bay 1 (x: 18..22, y: 11..21)
    for cx in [18.2, 19.0, 19.8, 20.6, 21.4]:
        add_box(c_arch, f"Crosswalk_Stripe_{cx}", (cx - 0.25, 11.5, 0.005), (cx + 0.25, 20.5, 0.01), mats["Road_White"], is_collider=False)

    # South Sidewalk (CT Spawn zone under highway bridge: y: 4.0..8.0, z: 0.18m)
    add_box(c_arch, "Sidewalk_CT_Spawn", (4.0, 4.0, 0.0), (60.0, 8.0, 0.18), mats["Concrete"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Sidewalk_CT_Curb", (4.0, 7.85, 0.0), (60.0, 8.0, 0.2), mats["Curb"])

    # North Sidewalk (Warehouse front walkway: y: 20.8..22.0, z: 0.18m)
    add_box(c_arch, "Sidewalk_Warehouse_Front", (4.0, 20.8, 0.0), (48.0, 22.0, 0.18), mats["Concrete"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Sidewalk_Warehouse_Curb", (4.0, 20.8, 0.0), (48.0, 20.95, 0.2), mats["Curb"])

    print("[2/10] Building Highway Overpass Structure...")
    # Elevated Highway Bridge Deck (y: 4.0..8.0, walking surface z=7.5m, thickness 0.6m)
    add_box(c_overpass, "Overpass_Deck", (4.0, 4.0, 6.9), (60.0, 8.0, 7.5), mats["Concrete_Dark"])
    add_box(c_overpass, "Overpass_Asphalt", (4.0, 4.2, 7.48), (60.0, 7.8, 7.5), mats["Asphalt"], is_collider=False)

    # Concrete Retaining Wall along South border
    add_box(c_overpass, "Retaining_Wall_South", (4.0, 3.6, 0.0), (60.0, 4.0, 11.0), mats["Concrete"])

    # Concrete Guardrails (Jersey Barriers) along Overpass Edges (z: 7.5..8.5m)
    add_box(c_overpass, "Overpass_Barrier_N", (4.0, 7.8, 7.5), (60.0, 8.1, 8.5), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_overpass, "Overpass_Barrier_S", (4.0, 3.9, 7.5), (60.0, 4.2, 8.5), mats["Concrete"], bevel=True, bevel_width=0.04)

    # Cylindrical Heavy Concrete Overpass Support Pillars (up to pier cap z=6.3, pier cap to 6.9)
    pillar_x_coords = [8.0, 20.0, 32.0, 44.0, 56.0]
    for i, px in enumerate(pillar_x_coords):
        add_cylinder(c_overpass, f"Overpass_Pillar_{i}", (px, 6.0, 3.15), radius=0.65, height=6.3, material=mats["Concrete"], segments=32)
        add_box(c_overpass, f"Pillar_Pier_Cap_{i}", (px - 0.8, 4.8, 6.3), (px + 0.8, 7.2, 6.9), mats["Concrete_Dark"], bevel=True, bevel_width=0.05)

    print("[3/10] Building Rail Yard and Train Tracks...")
    # Rail yard gravel ballast ground (x: 48.0..60.0, y: 22.0..56.0)
    add_box(c_rail, "Rail_Yard_Ballast", (48.0, 22.0, 0.0), (60.0, 56.0, 0.15), mats["Concrete_Dark"])

    # Dual Train Tracks along East yard
    for tx in [51.5, 54.5]:
        # Railroad Ties (sleepers)
        for ty in range(23, 56, 1):
            add_box(c_rail, f"Tie_{tx}_{ty}", (tx - 0.7, float(ty) - 0.12, 0.15), (tx + 0.7, float(ty) + 0.12, 0.23), mats["Train_Tie"])
        # Dual Steel Rails (T-rail head)
        add_box(c_rail, f"Rail_L_{tx}", (tx - 0.52, 22.0, 0.23), (tx - 0.44, 56.0, 0.35), mats["Train_Rail"])
        add_box(c_rail, f"Rail_R_{tx}", (tx + 0.44, 22.0, 0.23), (tx + 0.52, 56.0, 0.35), mats["Train_Rail"])

    # Freight Boxcar parked on track (x: 53.5..56.5, y: 32.0..46.0, z: 0.35..4.0m)
    add_box(c_rail, "Boxcar_Body", (53.6, 32.2, 0.9), (56.4, 45.8, 3.8), mats["Train_Rust"], bevel=True, bevel_width=0.05)
    add_box(c_rail, "Boxcar_Roof", (53.4, 31.9, 3.8), (56.6, 46.1, 4.0), mats["Steel_Dark"])
    add_box(c_rail, "Boxcar_Underframe", (53.8, 32.5, 0.4), (56.2, 45.5, 0.9), mats["Steel_Dark"])

    # 32-Segment Bogie Wheels for the Boxcar
    for bz, by_coords in [(0.45, [33.5, 35.0, 43.0, 44.5])]:
        for wy in by_coords:
            for wx in [53.5, 56.5]:
                add_cylinder(c_rail, f"Boxcar_Wheel_{wx}_{wy}", (wx, wy, bz), radius=0.35, height=0.12, material=mats["Steel_Dark"], segments=32)

    # Boxcar Ladder (CT access to train roof)
    for lz in [1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6]:
        add_box(c_rail, f"Boxcar_Rung_{lz}", (53.38, 32.4, lz), (53.48, 32.9, lz + 0.04), mats["Safety_Rail"], is_collider=False)

    # Stacked Shipping Containers in the Rail Yard
    add_box(c_rail, "Container_Blue_1", (48.2, 24.0, 0.15), (50.6, 30.0, 2.75), mats["Container_Blue"], bevel=True, bevel_width=0.04)
    add_box(c_rail, "Container_Red_1", (48.2, 31.0, 0.15), (50.6, 37.0, 2.75), mats["Container_Red"], bevel=True, bevel_width=0.04)
    add_box(c_rail, "Container_Green_1", (48.2, 31.0, 2.75), (50.6, 37.0, 5.35), mats["Container_Green"], bevel=True, bevel_width=0.04)

    print("[4/10] Building SWAT Tactical Assault Van...")
    # SWAT Van positioned near CT spawn (x: 18.0..22.0, y: 13.0..18.0)
    vx, vy = 20.0, 15.5
    # Beveled Van Armored Body
    add_box(c_vehicles, "SWAT_Body_Main", (vx - 1.2, vy - 2.5, 0.5), (vx + 1.2, vy + 1.2, 2.6), mats["SWAT_Navy"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "SWAT_Hood", (vx - 1.15, vy + 1.2, 0.5), (vx + 1.15, vy + 2.4, 1.6), mats["SWAT_Navy"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "SWAT_Front_Bumper", (vx - 1.2, vy + 2.38, 0.3), (vx + 1.2, vy + 2.55, 0.8), mats["Steel_Dark"], bevel=True, bevel_width=0.04)
    # Heavy tubular bullbar push bumper
    add_cylinder(c_vehicles, "SWAT_Bullbar_Top", (vx, vy + 2.6, 1.2), radius=0.04, height=1.8, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_box(c_vehicles, "SWAT_Bullbar_Post_L", (vx - 0.7, vy + 2.5, 0.5), (vx - 0.62, vy + 2.62, 1.25), mats["Steel_Dark"], is_collider=False)
    add_box(c_vehicles, "SWAT_Bullbar_Post_R", (vx + 0.62, vy + 2.5, 0.5), (vx + 0.7, vy + 2.62, 1.25), mats["Steel_Dark"], is_collider=False)

    # Windshield and Windows
    add_box(c_vehicles, "SWAT_Windshield", (vx - 1.05, vy + 1.1, 1.65), (vx + 1.05, vy + 1.25, 2.45), mats["SWAT_Glass"], is_collider=False)
    add_box(c_vehicles, "SWAT_Side_Glass_L", (vx - 1.22, vy - 0.5, 1.7), (vx - 1.18, vy + 1.0, 2.4), mats["SWAT_Glass"], is_collider=False)
    add_box(c_vehicles, "SWAT_Side_Glass_R", (vx + 1.18, vy - 0.5, 1.7), (vx + 1.22, vy + 1.0, 2.4), mats["SWAT_Glass"], is_collider=False)

    # 32-Segment Smooth Rubber Tires with chrome rims
    wheel_coords = [(-1.25, -1.4), (1.25, -1.4), (-1.25, 1.5), (1.25, 1.5)]
    for i, (wx, wy) in enumerate(wheel_coords):
        add_cylinder(c_vehicles, f"SWAT_Tire_{i}", (vx + wx, vy + wy, 0.42), radius=0.42, height=0.3, material=mats["SWAT_Tire"], segments=32)
        add_cylinder(c_vehicles, f"SWAT_Rim_{i}", (vx + wx * 1.05, vy + wy, 0.42), radius=0.24, height=0.31, material=mats["SWAT_Chrome"], segments=32, is_collider=False)

    # Emergency Strobe Lightbar on Roof
    add_box(c_vehicles, "SWAT_Lightbar_Mount", (vx - 0.8, vy + 0.2, 2.6), (vx + 0.8, vy + 0.4, 2.65), mats["Steel_Dark"], is_collider=False)
    add_box(c_vehicles, "SWAT_Siren_Red", (vx - 0.75, vy + 0.22, 2.65), (vx - 0.05, vy + 0.38, 2.78), mats["SWAT_Siren_Red"], is_collider=False)
    add_box(c_vehicles, "SWAT_Siren_Blue", (vx + 0.05, vy + 0.22, 2.65), (vx + 0.75, vy + 0.38, 2.78), mats["SWAT_Siren_Blue"], is_collider=False)

    print("[5/10] Building Semi-Truck & Trailer at Bay 2...")
    # 18-Wheeler backed into Bay 2 (x: 34.0, y: 16.0..24.0)
    tx, ty = 34.0, 19.0
    add_box(c_vehicles, "Truck_Trailer_Body", (tx - 1.3, ty - 1.0, 0.6), (tx + 1.3, ty + 5.0, 3.8), mats["Truck_Trailer"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "Truck_Cab_Body", (tx - 1.25, ty - 4.5, 0.5), (tx + 1.25, ty - 1.0, 3.2), mats["Truck_Cab"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "Truck_Windshield", (tx - 1.1, ty - 4.52, 1.8), (tx + 1.1, ty - 4.48, 2.8), mats["SWAT_Glass"], is_collider=False)

    # 32-Segment Truck Tires
    truck_wheels = [(-1.3, -3.8), (1.3, -3.8), (-1.3, -2.0), (1.3, -2.0), (-1.3, 3.0), (1.3, 3.0), (-1.3, 4.2), (1.3, 4.2)]
    for i, (wx, wy) in enumerate(truck_wheels):
        add_cylinder(c_vehicles, f"Truck_Tire_{i}", (tx + wx, ty + wy, 0.5), radius=0.5, height=0.32, material=mats["SWAT_Tire"], segments=32)

    print("[6/10] Building Main Warehouse Shell & Architecture...")
    # Warehouse footprint: (8.0..46.0, 22.0..56.0, z: 0.0..9.0m)
    # Warehouse ground slab
    add_box(c_arch, "Warehouse_Ground_Slab", (8.0, 22.0, -0.2), (46.0, 56.0, 0.0), mats["Warehouse_Floor"])

    # Brick Masonry Foundation Skirt (z: 0.0..0.8m)
    add_box(c_arch, "Warehouse_Brick_Skirt_S", (7.5, 21.5, 0.0), (46.5, 22.0, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_W", (7.5, 22.0, 0.0), (8.0, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_N", (7.5, 56.0, 0.0), (46.5, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_E", (46.0, 22.0, 0.0), (46.5, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)

    # South Facade Walls (y = 22.0m) with Bay 1, Bay 2, and Pedestrian Entrance
    # Left wall (x: 8.0..14.0)
    add_box(c_arch, "Wall_South_1", (8.0, 21.6, 0.8), (14.0, 22.0, 9.0), mats["Warehouse_Wall"])
    # Bay 1 door header (x: 14.0..22.0, z: 3.8..9.0)
    add_box(c_arch, "Wall_South_Bay1_Header", (14.0, 21.6, 3.8), (22.0, 22.0, 9.0), mats["Warehouse_Wall"])
    # Center pier between Bay 1 & Bay 2 (x: 22.0..30.0)
    add_box(c_arch, "Wall_South_2", (22.0, 21.6, 0.8), (30.0, 22.0, 9.0), mats["Warehouse_Wall"])
    # Bay 2 door header (x: 30.0..38.0, z: 4.0..9.0)
    add_box(c_arch, "Wall_South_Bay2_Header", (30.0, 21.6, 4.0), (38.0, 22.0, 9.0), mats["Warehouse_Wall"])
    # Entrance door header (x: 38.0..42.0, z: 2.6..9.0)
    add_box(c_arch, "Wall_South_Door_Header", (38.0, 21.6, 2.6), (42.0, 22.0, 9.0), mats["Warehouse_Wall"])
    # Right corner wall (x: 42.0..46.0)
    add_box(c_arch, "Wall_South_3", (42.0, 21.6, 0.8), (46.0, 22.0, 9.0), mats["Warehouse_Wall"])

    # Structural Steel I-Beam Pilasters on South Facade
    for px in [14.0, 22.0, 30.0, 38.0, 42.0]:
        add_i_beam(c_arch, f"Pilaster_{int(px)}", px, 21.45, 0.8, 9.0, depth=0.35, flange_w=0.30, material=mats["Steel_Dark"])
        add_box(c_arch, f"Pilaster_Base_{int(px)}", (px - 0.25, 21.25, 0.0), (px + 0.25, 21.6, 0.8), mats["Concrete"], bevel=True, bevel_width=0.03)

    # 3D Architectural Horizontal Relief Bands on South Facade
    for rz in [4.2, 4.6, 5.0, 5.4, 7.2, 7.6, 8.0]:
        add_box(c_arch, f"Facade_Relief_Band_{int(rz*10)}", (8.1, 21.38, rz), (45.9, 21.62, rz + 0.12), mats["Warehouse_Wall_Stripe"], is_collider=False)

    # Roll-up garage shutter slats (Bay 1 rolled up at z: 3.2..3.8)
    for sz in [3.25, 3.40, 3.55, 3.70]:
        add_box(c_arch, f"Bay1_Slat_{int(sz*100)}", (14.05, 21.55, sz), (21.95, 21.65, sz + 0.12), mats["Garage_Door"], bevel=True, bevel_width=0.02)
    add_cylinder(c_arch, "Bay1_Shutter_Coil", (18.0, 21.6, 3.85), radius=0.22, height=8.0, material=mats["Garage_Door"], segments=24)

    # Heavy Rubber Dock Bumpers on Bay loading walls
    for bx in [14.2, 21.8, 30.2, 37.8]:
        add_box(c_arch, f"Dock_Bumper_{int(bx*10)}", (bx - 0.15, 21.35, 0.2), (bx + 0.15, 21.55, 1.0), mats["Hazard_Black"], bevel=True, bevel_width=0.03)

    # Heavy Concrete Safety Bollards with Hazard Stripes
    add_cylinder(c_arch, "Bay1_Bollard_L", (13.5, 20.8, 0.55), radius=0.14, height=1.1, material=mats["Hazard_Yellow"], segments=24)
    add_cylinder(c_arch, "Bay1_Bollard_L_Cap", (13.5, 20.8, 1.035), radius=0.15, height=0.17, material=mats["Hazard_Black"], segments=24, is_collider=False)
    add_cylinder(c_arch, "Bay1_Bollard_R", (22.5, 20.8, 0.55), radius=0.14, height=1.1, material=mats["Hazard_Yellow"], segments=24)
    add_cylinder(c_arch, "Bay1_Bollard_R_Cap", (22.5, 20.8, 1.035), radius=0.15, height=0.17, material=mats["Hazard_Black"], segments=24, is_collider=False)

    # Weathered Industrial Signboard ("ASSAULT FREIGHT & LOGISTICS")
    add_box(c_arch, "Signboard_Back", (15.5, 21.42, 5.6), (24.5, 21.46, 6.8), mats["Concrete"], is_collider=False)
    add_box(c_arch, "Signboard_Band_Bottom", (15.3, 21.40, 5.5), (24.7, 21.44, 5.6), mats["Hazard_Yellow"], is_collider=False)
    add_box(c_arch, "Signboard_Band_Top", (15.3, 21.40, 6.8), (24.7, 21.44, 6.9), mats["Hazard_Yellow"], is_collider=False)
    add_box(c_arch, "Signboard_Logo_Bar", (16.0, 21.47, 6.2), (24.0, 21.48, 6.5), mats["Steel_Dark"], is_collider=False)

    # Exterior warehouse walls
    add_box(c_arch, "Wall_West", (7.6, 22.0, 0.0), (8.0, 56.0, 9.0), mats["Warehouse_Wall"])
    # North Wall (with rear exit portal at x: 24..27)
    add_box(c_arch, "Wall_North_L", (8.0, 56.0, 0.0), (24.0, 56.4, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_North_Door_Header", (24.0, 56.0, 2.6), (27.0, 56.4, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_North_R", (27.0, 56.0, 0.0), (46.0, 56.4, 9.0), mats["Warehouse_Wall"])
    # East Wall (with fire escape door at y: 44..47, z: 4.2..6.8)
    add_box(c_arch, "Wall_East_1", (46.0, 22.0, 0.0), (46.4, 44.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_East_Door_Header", (46.0, 44.0, 6.8), (46.4, 47.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_East_2", (46.0, 47.0, 0.0), (46.4, 56.0, 9.0), mats["Warehouse_Wall"])

    # Exterior Fire Escape Staircase on East Wall
    add_box(c_props, "Fire_Landing_Low", (46.4, 36.0, 2.0), (48.8, 38.5, 2.1), mats["Steel_Catwalk"], bevel=True, bevel_width=0.03)
    add_box(c_props, "Fire_Landing_Mid", (46.4, 44.0, 4.1), (48.8, 47.0, 4.2), mats["Steel_Catwalk"], bevel=True, bevel_width=0.03)

    # Warehouse Roof (z = 9.0m walking surface) with parapets and metal coping
    add_box(c_arch, "Roof_Slab", (8.0, 22.0, 8.8), (46.0, 56.0, 9.0), mats["Warehouse_Roof"])
    add_box(c_arch, "Roof_Parapet_S", (7.8, 21.8, 9.0), (46.2, 22.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Parapet_N", (7.8, 55.8, 9.0), (46.2, 56.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Parapet_W", (7.8, 21.8, 9.0), (8.2, 56.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Parapet_E", (45.8, 21.8, 9.0), (46.2, 56.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    # Sheet metal coping caps on top of parapets
    add_box(c_arch, "Roof_Coping_S", (7.75, 21.75, 9.8), (46.25, 22.25, 9.85), mats["Steel_Dark"], is_collider=False)
    add_box(c_arch, "Roof_Coping_N", (7.75, 55.75, 9.8), (46.25, 56.25, 9.85), mats["Steel_Dark"], is_collider=False)

    # Roof Vents & HVAC chillers
    add_box(c_vents, "Roof_Vent_1", (14.0, 28.0, 9.0), (18.0, 32.0, 10.2), mats["Vent_Duct"], bevel=True, bevel_width=0.05)
    add_box(c_vents, "Roof_Vent_2", (28.0, 44.0, 9.0), (32.0, 48.0, 10.2), mats["Vent_Duct"], bevel=True, bevel_width=0.05)
    add_box(c_props, "Roof_Chiller", (36.0, 28.0, 9.0), (40.0, 34.0, 10.4), mats["Steel_Dark"], bevel=True, bevel_width=0.05)

    print("[7/10] Building Interior Catwalks, Columns, Trusses & Stairs...")
    # Interior Steel I-Beam Support Columns
    for cx in [18.0, 27.0, 36.0]:
        for cy in [28.0, 38.0, 48.0]:
            if cx >= 28.0 and cy >= 40.0:
                continue  # inside office footprint
            add_i_beam(c_warehouse, f"Int_Col_{int(cx)}_{int(cy)}", cx, cy, 0.0, 9.0, depth=0.4, flange_w=0.35, material=mats["Steel_Dark"])
            add_box(c_warehouse, f"Int_Col_Plinth_{int(cx)}_{int(cy)}", (cx - 0.35, cy - 0.35, 0.0), (cx + 0.35, cy + 0.35, 0.4), mats["Concrete"], bevel=True, bevel_width=0.04)

    # Roof Trusses spanning across ceiling (z: 8.2..8.8)
    for ty in [26.0, 34.0, 42.0, 50.0]:
        add_box(c_warehouse, f"Truss_Chord_Top_{int(ty)}", (8.2, ty - 0.1, 8.65), (45.8, ty + 0.1, 8.8), mats["Steel_Dark"], is_collider=False)
        add_box(c_warehouse, f"Truss_Chord_Bot_{int(ty)}", (8.2, ty - 0.1, 8.2), (45.8, ty + 0.1, 8.35), mats["Steel_Dark"], is_collider=False)

    # Elevated Catwalk (z = 4.2m)
    # West catwalk (x: 8.0..13.0, y: 22.0..56.0)
    add_box(c_catwalk, "Catwalk_West_Deck", (8.0, 22.0, 4.15), (13.0, 56.0, 4.2), mats["Diamond_Plate"])
    # East catwalk connecting to office
    add_box(c_catwalk, "Catwalk_Office_Runway", (13.0, 38.0, 4.15), (28.0, 42.0, 4.2), mats["Diamond_Plate"])

    # Safety Yellow Handrails on Catwalks (z: 4.2..5.2m)
    add_box(c_catwalk, "Rail_West_Top", (12.92, 22.0, 5.15), (13.08, 38.0, 5.25), mats["Safety_Rail"], is_collider=False)
    add_box(c_catwalk, "Rail_West_Mid", (12.94, 22.0, 4.65), (13.06, 38.0, 4.75), mats["Safety_Rail"], is_collider=False)
    add_box(c_catwalk, "Rail_Runway_Top", (13.0, 37.92, 5.15), (28.0, 38.08, 5.25), mats["Safety_Rail"], is_collider=False)
    add_box(c_catwalk, "Rail_Runway_Mid", (13.0, 37.94, 4.65), (28.0, 38.06, 4.75), mats["Safety_Rail"], is_collider=False)

    print("[8/10] Building Upstairs Hostage Office...")
    # Office Floor Slab (z = 4.2m)
    add_box(c_office, "Office_Floor", (28.0, 40.0, 4.15), (45.5, 55.5, 4.2), mats["Office_Floor"])
    add_box(c_office, "Office_Front_Wall_Low", (28.0, 39.8, 4.2), (45.5, 40.0, 5.0), mats["Office_Wall"])
    add_box(c_office, "Office_Panoramic_Window", (29.0, 39.85, 5.0), (44.5, 39.95, 7.2), mats["Office_Window"], is_collider=False)
    # Steel frame window mullions
    for i, mx in enumerate([32.0, 35.0, 38.0, 41.0]):
        add_box(c_office, f"Office_Mullion_V_{i}", (mx - 0.08, 39.82, 5.0), (mx + 0.08, 39.98, 7.2), mats["Steel_Dark"], is_collider=False)
    add_box(c_office, "Office_Mullion_H", (29.0, 39.82, 6.05), (44.5, 39.98, 6.15), mats["Steel_Dark"], is_collider=False)
    add_box(c_office, "Office_Front_Wall_High", (28.0, 39.8, 7.2), (45.5, 40.0, 8.8), mats["Office_Wall"])
    # West Office Wall with Catwalk door
    add_box(c_office, "Office_West_Wall_1", (28.0, 40.0, 4.2), (28.4, 48.0, 8.8), mats["Office_Wall"])
    add_box(c_office, "Office_West_Door_Header", (28.0, 48.0, 6.6), (28.4, 51.0, 8.8), mats["Office_Wall"])
    add_box(c_office, "Office_West_Wall_2", (28.0, 51.0, 4.2), (28.4, 55.5, 8.8), mats["Office_Wall"])

    # Detailed Office Props
    add_hostage_office_props(c_office, mats)

    print("[9/10] Building Industrial Storage Racks, Forklift, Crane & Utilities...")
    # Multi-Tier Heavy Pallet Storage Racking in East Bay (x: 33.0..44.0, y: 28.0..34.0)
    add_pallet_rack(c_props, mats, 34.0, 30.0, 44.0, 33.0, num_tiers=3, total_height=5.5)

    # Detailed Industrial Forklift near Center Bay
    add_forklift(c_props, mats, 23.0, 32.0, z=0.0)

    # Overhead Traveling Bridge Crane System
    add_crane_system(c_warehouse, mats)

    # Suspended Fluorescent Lighting Grid & Utilities
    add_lighting_and_utilities(c_warehouse, mats)

    # Exterior Civil & Street Details
    add_street_and_civil_details(c_arch, mats)

    # Green Waste Dumpster near curb
    add_box(c_props, "Street_Dumpster_Body", (13.0, 8.5, 0.2), (15.5, 10.5, 1.8), mats["Container_Green"], bevel=True, bevel_width=0.05)
    add_box(c_props, "Street_Dumpster_Lid", (12.9, 8.4, 1.8), (15.6, 10.6, 1.95), mats["Steel_Dark"], bevel=True, bevel_width=0.04)

    # 32-Segment Fire Hydrant on sidewalk
    add_cylinder(c_props, "Fire_Hydrant_Base", (26.0, 7.2, 0.4), radius=0.22, height=0.4, material=mats["Fire_Extinguisher_Red"], segments=32)
    add_cylinder(c_props, "Fire_Hydrant_Body", (26.0, 7.2, 0.8), radius=0.18, height=0.6, material=mats["Fire_Extinguisher_Red"], segments=32)
    add_cylinder(c_props, "Fire_Hydrant_Cap", (26.0, 7.2, 1.15), radius=0.2, height=0.15, material=mats["Fire_Extinguisher_Red"], segments=32)
    add_cylinder(c_props, "Fire_Hydrant_Nozzle_L", (25.75, 7.2, 0.75), radius=0.08, height=0.35, material=mats["Steel_Dark"], segments=24)
    add_cylinder(c_props, "Fire_Hydrant_Nozzle_R", (26.25, 7.2, 0.75), radius=0.08, height=0.35, material=mats["Steel_Dark"], segments=24)

    print("[10/10] Setting up Scene Collections and Physics Tags...")
    return mats


def export_scene_glb():
    """Export the constructed scene to GLB for backend and frontend."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    glb_backend_path = repo_root / "backend/modules/hassault/maps/hd_assault.glb"
    glb_web_path = repo_root / "apps/web/public/hd_assault.glb"

    glb_backend_path.parent.mkdir(parents=True, exist_ok=True)
    glb_web_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        bpy.ops.wm.save_userpref()
    except Exception:
        pass

    # Export GLB (Y-Up standard, materials, apply modifiers)
    print(f"Exporting GLB to: {glb_backend_path}")
    bpy.ops.export_scene.gltf(
        filepath=str(glb_backend_path),
        export_format='GLB',
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials='EXPORT',
        export_lights=False,
        export_cameras=False
    )

    # Copy to web public
    import shutil
    shutil.copyfile(glb_backend_path, glb_web_path)
    print(f"Copied GLB to Web: {glb_web_path}")


if __name__ == "__main__":
    print("=== Generating Ultra-Detailed CS:GO cs_assault Map in Blender (56m Scale) ===")
    build_assault_scene()
    export_scene_glb()
    print("=== Ultra-Detailed Generation Complete! ===")
