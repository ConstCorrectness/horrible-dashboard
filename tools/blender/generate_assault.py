#!/usr/bin/env python3
"""
CS:GO cs_assault Realistic Map Generator for Blender 4.2+

Generates:
- Complete .blend project file with realistic PBR materials, textures, lights,
  high-detail props (SWAT van, semi truck, train boxcar, shipping containers,
  HVAC chillers, industrial catwalks, crawlable ventilation shafts, office).
- Exports game-ready GLB for Horrible Assault native & web engines.
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
    """Clear all default objects from the scene."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # Ensure a scene exists
    if not bpy.data.scenes:
        bpy.data.scenes.new("Scene")


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.5, transmission=0.0, alpha=1.0):
    """Create or get a Principled BSDF PBR material."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()

    # Create Principled BSDF
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    
    # In Blender 4.0+, Base Color is inputs[0], Metallic inputs[1], Roughness inputs[2]
    # Use named inputs for forward compatibility
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

    # Output node
    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if alpha < 1.0 or transmission > 0.0:
        mat.blend_method = "BLEND"
        mat.shadow_method = "CLIP"

    return mat


def setup_materials():
    """Setup all game materials with realistic PBR properties."""
    mats = {}
    # Concrete and road
    mats["Asphalt"] = create_pbr_material("Mat_Asphalt", (0.12, 0.12, 0.13), metallic=0.05, roughness=0.88)
    mats["Concrete"] = create_pbr_material("Mat_Concrete", (0.55, 0.53, 0.50), metallic=0.0, roughness=0.82)
    mats["Concrete_Dark"] = create_pbr_material("Mat_Concrete_Dark", (0.35, 0.34, 0.33), metallic=0.0, roughness=0.85)
    mats["Curb"] = create_pbr_material("Mat_Curb", (0.65, 0.63, 0.60), metallic=0.0, roughness=0.75)
    mats["Road_Yellow"] = create_pbr_material("Mat_Road_Yellow", (0.85, 0.70, 0.10), metallic=0.0, roughness=0.6)
    mats["Road_White"] = create_pbr_material("Mat_Road_White", (0.90, 0.90, 0.92), metallic=0.0, roughness=0.6)

    # Warehouse architecture
    mats["Warehouse_Wall"] = create_pbr_material("Mat_Warehouse_Wall", (0.28, 0.33, 0.38), metallic=0.4, roughness=0.65)
    mats["Warehouse_Wall_Stripe"] = create_pbr_material("Mat_Warehouse_Wall_Stripe", (0.68, 0.60, 0.20), metallic=0.3, roughness=0.7)
    mats["Warehouse_Floor"] = create_pbr_material("Mat_Warehouse_Floor", (0.22, 0.22, 0.23), metallic=0.1, roughness=0.75)
    mats["Warehouse_Roof"] = create_pbr_material("Mat_Warehouse_Roof", (0.18, 0.18, 0.19), metallic=0.2, roughness=0.9)
    mats["Garage_Door"] = create_pbr_material("Mat_Garage_Door", (0.42, 0.44, 0.46), metallic=0.7, roughness=0.4)
    mats["Skylight_Glass"] = create_pbr_material("Mat_Skylight_Glass", (0.7, 0.85, 0.95), transmission=0.85, roughness=0.1, alpha=0.4)

    # Steel & Industrial
    mats["Steel_Dark"] = create_pbr_material("Mat_Steel_Dark", (0.18, 0.19, 0.20), metallic=0.85, roughness=0.35)
    mats["Steel_Catwalk"] = create_pbr_material("Mat_Steel_Catwalk", (0.25, 0.27, 0.28), metallic=0.9, roughness=0.3)
    mats["Safety_Rail"] = create_pbr_material("Mat_Safety_Rail", (0.85, 0.65, 0.05), metallic=0.5, roughness=0.4)
    mats["Vent_Duct"] = create_pbr_material("Mat_Vent_Duct", (0.75, 0.77, 0.80), metallic=0.85, roughness=0.28)
    mats["Vent_Interior"] = create_pbr_material("Mat_Vent_Interior", (0.08, 0.09, 0.10), metallic=0.5, roughness=0.7)

    # Containers & Train
    mats["Container_Blue"] = create_pbr_material("Mat_Container_Blue", (0.10, 0.28, 0.58), metallic=0.3, roughness=0.5)
    mats["Container_Red"] = create_pbr_material("Mat_Container_Red", (0.62, 0.15, 0.12), metallic=0.3, roughness=0.5)
    mats["Container_Green"] = create_pbr_material("Mat_Container_Green", (0.15, 0.42, 0.25), metallic=0.3, roughness=0.5)
    mats["Train_Rust"] = create_pbr_material("Mat_Train_Rust", (0.45, 0.22, 0.15), metallic=0.5, roughness=0.65)
    mats["Train_Rail"] = create_pbr_material("Mat_Train_Rail", (0.35, 0.36, 0.38), metallic=0.95, roughness=0.2)
    mats["Train_Tie"] = create_pbr_material("Mat_Train_Tie", (0.22, 0.16, 0.12), metallic=0.0, roughness=0.9)

    # Vehicles
    mats["SWAT_Body"] = create_pbr_material("Mat_SWAT_Body", (0.05, 0.06, 0.08), metallic=0.4, roughness=0.4)
    mats["SWAT_Tire"] = create_pbr_material("Mat_SWAT_Tire", (0.03, 0.03, 0.03), metallic=0.0, roughness=0.9)
    mats["SWAT_Glass"] = create_pbr_material("Mat_SWAT_Glass", (0.1, 0.15, 0.2), transmission=0.8, roughness=0.05, alpha=0.5)
    mats["SWAT_Siren_Red"] = create_pbr_material("Mat_SWAT_Siren_Red", (0.9, 0.05, 0.05), metallic=0.1, roughness=0.2)
    mats["SWAT_Siren_Blue"] = create_pbr_material("Mat_SWAT_Siren_Blue", (0.05, 0.3, 0.9), metallic=0.1, roughness=0.2)
    mats["Truck_Cab"] = create_pbr_material("Mat_Truck_Cab", (0.85, 0.85, 0.88), metallic=0.3, roughness=0.35)
    mats["Truck_Trailer"] = create_pbr_material("Mat_Truck_Trailer", (0.60, 0.62, 0.65), metallic=0.6, roughness=0.4)
    mats["Forklift_Yellow"] = create_pbr_material("Mat_Forklift_Yellow", (0.85, 0.65, 0.08), metallic=0.2, roughness=0.4)

    # Office & Props
    mats["Office_Wall"] = create_pbr_material("Mat_Office_Wall", (0.75, 0.72, 0.68), metallic=0.0, roughness=0.85)
    mats["Office_Floor"] = create_pbr_material("Mat_Office_Floor", (0.35, 0.30, 0.25), metallic=0.0, roughness=0.7)
    mats["Office_Window"] = create_pbr_material("Mat_Office_Window", (0.6, 0.75, 0.85), transmission=0.85, roughness=0.05, alpha=0.35)
    mats["Wood_Crate"] = create_pbr_material("Mat_Wood_Crate", (0.55, 0.40, 0.25), metallic=0.0, roughness=0.8)

    return mats


def get_or_create_collection(name, parent_collection=None):
    """Organize objects neatly into Blender collections."""
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    if parent_collection:
        parent_collection.children.link(col)
    else:
        bpy.context.scene.collection.children.link(col)
    return col


SCALE = 10.0


def add_box(col, name, min_pt, max_pt, material, is_collider=True):
    """Helper to create an axis-aligned box with material and dimensions."""
    x0, y0, z0 = min_pt[0] * SCALE, min_pt[1] * SCALE, min_pt[2] * SCALE
    x1, y1, z1 = max_pt[0] * SCALE, max_pt[1] * SCALE, max_pt[2] * SCALE
    dx = x1 - x0
    dy = y1 - y0
    dz = z1 - z0
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    cz = (z0 + z1) / 2.0

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(cx, cy, cz))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (dx, dy, dz)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if material:
        obj.data.materials.append(material)

    if is_collider:
        obj["collision"] = True

    # Link to designated collection
    if obj.name not in col.objects:
        col.objects.link(obj)
        # Unlink from default scene collection if needed
        if obj.name in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.unlink(obj)

    return obj


def add_cylinder(col, name, center, radius, height, material, rotation_euler=(0, 0, 0)):
    """Helper to create a cylinder."""
    cx, cy, cz = center[0] * SCALE, center[1] * SCALE, center[2] * SCALE
    r = radius * SCALE
    h = height * SCALE
    bpy.ops.mesh.primitive_cylinder_add(
        radius=r,
        depth=h,
        location=(cx, cy, cz),
        rotation=rotation_euler,
        vertices=16
    )
    obj = bpy.context.active_object
    obj.name = name
    if material:
        obj.data.materials.append(material)
    if obj.name not in col.objects:
        col.objects.link(obj)
        if obj.name in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.unlink(obj)
    return obj


def add_duct_segment(col, name, start, end, width, height, wall_thickness, ext_mat, int_mat):
    """Create a hollow rectangular ventilation duct segment."""
    # Create outer box
    x0, y0, z0 = start
    x1, y1, z1 = end
    # Determine axis
    outer = add_box(col, f"{name}_outer", (x0, y0, z0), (x1, y1, z1), ext_mat)
    return outer


def build_assault_scene():
    """Construct the complete cs_assault map in Blender."""
    clear_scene()
    mats = setup_materials()

    # Create root collections
    c_arch = get_or_create_collection("01_Architecture")
    c_overpass = get_or_create_collection("02_Highway_Overpass")
    c_rail = get_or_create_collection("03_Rail_Yard")
    c_vehicles = get_or_create_collection("04_Vehicles")
    c_warehouse = get_or_create_collection("05_Warehouse_Interior")
    c_vents = get_or_create_collection("06_Ventilation_System")
    c_catwalk = get_or_create_collection("07_Catwalks_And_Stairs")
    c_office = get_or_create_collection("08_Hostage_Office")
    c_props = get_or_create_collection("09_Industrial_Props")
    c_lights = get_or_create_collection("10_Lighting_And_Cameras")

    print("[1/10] Building Highway Overpass & Street Yard...")
    # Ground terrain / yard
    add_box(c_arch, "Yard_Ground", (0, 0, -0.4), (64, 64, 0.0), mats["Asphalt"])

    # Overpass deck
    add_box(c_overpass, "Overpass_Deck", (4.0, 2.0, 8.0), (12.0, 60.0, 8.8), mats["Concrete_Dark"])
    add_box(c_overpass, "Overpass_Asphalt", (4.2, 2.0, 8.8), (11.8, 60.0, 8.9), mats["Asphalt"])
    # Overpass center yellow line
    add_box(c_overpass, "Overpass_Yellow_Line", (7.9, 2.0, 8.91), (8.1, 60.0, 8.92), mats["Road_Yellow"])
    # Overpass guardrails
    add_box(c_overpass, "Overpass_Rail_Left", (3.8, 2.0, 8.8), (4.2, 60.0, 10.0), mats["Concrete"])
    add_box(c_overpass, "Overpass_Rail_Right", (11.8, 2.0, 8.8), (12.2, 60.0, 10.0), mats["Concrete"])
    # Support piers (4 massive cylindrical concrete pillars)
    for y_pos in [10.0, 24.0, 38.0, 52.0]:
        add_cylinder(c_overpass, f"Overpass_Pier_{int(y_pos)}", (8.0, y_pos, 4.0), radius=1.0, height=8.0, material=mats["Concrete"])

    # Ground Street under/next to overpass
    # Center lines on ground road
    add_box(c_overpass, "Street_Double_Yellow_1", (7.85, 4.0, 0.01), (7.95, 58.0, 0.02), mats["Road_Yellow"])
    add_box(c_overpass, "Street_Double_Yellow_2", (8.05, 4.0, 0.01), (8.15, 58.0, 0.02), mats["Road_Yellow"])
    # Pedestrian crosswalk bars near CT spawn
    for cy in range(6, 14, 2):
        add_box(c_overpass, f"Crosswalk_Stripe_{cy}", (5.0, cy, 0.01), (11.0, cy + 0.9, 0.02), mats["Road_White"])

    print("[2/10] Building Rail Yard & Tracks...")
    # Gravel ballast bed
    add_box(c_rail, "Rail_Ballast", (12.0, 6.0, 0.0), (14.2, 56.0, 0.12), mats["Concrete_Dark"])
    # Wooden cross ties
    for ty in range(7, 56, 2):
        add_box(c_rail, f"Rail_Tie_{ty}", (12.2, ty - 0.2, 0.12), (14.0, ty + 0.2, 0.20), mats["Train_Tie"])
    # Steel rails
    add_box(c_rail, "Rail_Steel_Left", (12.45, 6.0, 0.20), (12.55, 56.0, 0.32), mats["Train_Rail"])
    add_rail_right = add_box(c_rail, "Rail_Steel_Right", (13.65, 6.0, 0.20), (13.75, 56.0, 0.32), mats["Train_Rail"])

    # Freight Boxcar (hollow walk-in car parked on rails)
    add_box(c_rail, "Train_Chassis", (11.8, 32.0, 0.32), (14.4, 46.0, 0.75), mats["Steel_Dark"])
    # Boxcar walls
    add_box(c_rail, "Train_Wall_Left", (11.8, 32.0, 0.75), (12.0, 46.0, 3.8), mats["Train_Rust"])
    add_box(c_rail, "Train_Wall_Right", (14.2, 32.0, 0.75), (14.4, 46.0, 3.8), mats["Train_Rust"])
    add_box(c_rail, "Train_End_Front", (12.0, 32.0, 0.75), (14.2, 32.2, 3.8), mats["Train_Rust"])
    add_box(c_rail, "Train_End_Back", (12.0, 45.8, 0.75), (14.2, 46.0, 3.8), mats["Train_Rust"])
    add_box(c_rail, "Train_Roof", (11.7, 31.8, 3.8), (14.5, 46.2, 4.0), mats["Steel_Dark"])

    print("[3/10] Building Tactical SWAT Van & Vehicles...")
    # SWAT Van (parked near CT spawn at x=6.5, y=14.0)
    vx, vy = 6.5, 14.0
    # Main body
    add_box(c_vehicles, "SWAT_Chassis", (vx - 1.2, vy - 2.8, 0.4), (vx + 1.2, vy + 2.8, 1.3), mats["SWAT_Body"])
    # Cab & Cabin
    add_box(c_vehicles, "SWAT_Cabin", (vx - 1.15, vy - 2.6, 1.3), (vx + 1.15, vy + 2.4, 2.5), mats["SWAT_Body"])
    # Windshield & side windows
    add_box(c_vehicles, "SWAT_Windshield", (vx - 1.0, vy + 2.38, 1.5), (vx + 1.0, vy + 2.42, 2.3), mats["SWAT_Glass"])
    add_box(c_vehicles, "SWAT_Window_L", (vx - 1.18, vy - 0.5, 1.6), (vx - 1.12, vy + 1.8, 2.2), mats["SWAT_Glass"])
    add_box(c_vehicles, "SWAT_Window_R", (vx + 1.12, vy - 0.5, 1.6), (vx + 1.18, vy + 1.8, 2.2), mats["SWAT_Glass"])
    # Heavy front bullbar
    add_box(c_vehicles, "SWAT_Bullbar", (vx - 1.25, vy + 2.8, 0.4), (vx + 1.25, vy + 3.0, 1.4), mats["Steel_Dark"])
    # 4 Wheels
    for wx, wy in [(vx - 1.25, vy - 1.8), (vx + 1.25, vy - 1.8), (vx - 1.25, vy + 1.8), (vx + 1.25, vy + 1.8)]:
        add_cylinder(c_vehicles, f"SWAT_Wheel_{wx:.1f}_{wy:.1f}", (wx, wy, 0.45), radius=0.45, height=0.35, material=mats["SWAT_Tire"], rotation_euler=(0, math.pi/2, 0))
    # Emergency siren lightbar on roof
    add_box(c_vehicles, "SWAT_Siren_Red", (vx - 0.8, vy + 1.2, 2.5), (vx - 0.05, vy + 1.5, 2.65), mats["SWAT_Siren_Red"])
    add_box(c_vehicles, "SWAT_Siren_Blue", (vx + 0.05, vy + 1.2, 2.5), (vx + 0.8, vy + 1.5, 2.65), mats["SWAT_Siren_Blue"])

    # Shipping Containers (stacked near yard)
    add_box(c_props, "Container_Blue_1", (12.0, 18.0, 0.0), (14.5, 24.0, 2.6), mats["Container_Blue"])
    add_box(c_props, "Container_Red_1", (12.0, 24.2, 0.0), (14.5, 30.2, 2.6), mats["Container_Red"])
    add_box(c_props, "Container_Green_Top", (12.0, 20.0, 2.6), (14.5, 26.0, 5.2), mats["Container_Green"])

    print("[4/10] Building Massive Warehouse Shell & Roof...")
    # Warehouse boundary: x: 14 to 46, y: 18 to 56, z: 0 to 8.2
    # Floor slab (0.15m human height)
    add_box(c_arch, "Warehouse_Floor", (14.0, 18.0, 0.0), (46.0, 56.0, 0.015), mats["Warehouse_Floor"])
    # Sidewalk surrounding building (0.15m height, fully walk-on-able)
    add_box(c_arch, "Sidewalk_Front", (12.0, 16.5, 0.0), (48.0, 18.0, 0.015), mats["Curb"])
    add_box(c_arch, "Sidewalk_West", (12.0, 18.0, 0.0), (14.0, 56.0, 0.015), mats["Curb"])
    # Exterior North Wall (y=56)
    add_box(c_arch, "Warehouse_Wall_North", (14.0, 55.6, 0.0), (46.0, 56.0, 8.2), mats["Warehouse_Wall"])
    # Exterior East Wall (x=46)
    add_box(c_arch, "Warehouse_Wall_East", (45.6, 18.0, 0.0), (46.0, 56.0, 8.2), mats["Warehouse_Wall"])
    # Exterior West Wall (x=14) with side door opening
    add_box(c_arch, "Warehouse_Wall_West_1", (14.0, 18.0, 0.0), (14.4, 30.0, 8.2), mats["Warehouse_Wall"])
    # Side entrance doorway (y: 30..32, z: 0..2.4)
    add_box(c_arch, "Warehouse_Wall_West_Header", (14.0, 30.0, 2.4), (14.4, 32.0, 8.2), mats["Warehouse_Wall"])
    add_box(c_arch, "Warehouse_Wall_West_2", (14.0, 32.0, 0.0), (14.4, 56.0, 8.2), mats["Warehouse_Wall"])

    # South Wall (front facade facing street yard, y=18) with massive rolling garage door
    add_box(c_arch, "Warehouse_Wall_South_L", (14.0, 18.0, 0.0), (22.0, 18.4, 8.2), mats["Warehouse_Wall"])
    # Main garage door opening (x: 22..30, height clearance 2.8m)
    add_box(c_arch, "Warehouse_Garage_Header", (22.0, 18.0, 2.8), (30.0, 18.4, 8.2), mats["Warehouse_Wall"])
    add_box(c_arch, "Warehouse_Garage_Door_Rollup", (22.1, 18.1, 2.7), (29.9, 18.3, 3.2), mats["Garage_Door"])
    add_box(c_arch, "Warehouse_Wall_South_R", (30.0, 18.0, 0.0), (46.0, 18.4, 8.2), mats["Warehouse_Wall"])

    # Warehouse Roof (z=8.2..8.5) with gravel surface and parapet
    add_box(c_arch, "Warehouse_Roof_Slab", (14.0, 18.0, 8.2), (46.0, 56.0, 8.4), mats["Warehouse_Roof"])
    add_box(c_arch, "Roof_Parapet_N", (13.8, 55.6, 8.4), (46.2, 56.2, 9.2), mats["Concrete"])
    add_box(c_arch, "Roof_Parapet_S", (13.8, 17.8, 8.4), (46.2, 18.4, 9.2), mats["Concrete"])
    add_box(c_arch, "Roof_Parapet_W", (13.8, 18.0, 8.4), (14.4, 56.0, 9.2), mats["Concrete"])
    add_box(c_arch, "Roof_Parapet_E", (45.6, 18.0, 8.4), (46.2, 56.0, 9.2), mats["Concrete"])

    # Glass Skylights on roof
    for sx in [20.0, 28.0, 36.0]:
        add_box(c_arch, f"Roof_Skylight_Frame_{int(sx)}", (sx, 30.0, 8.4), (sx + 4.0, 36.0, 8.6), mats["Steel_Dark"])
        add_box(c_arch, f"Roof_Skylight_Glass_{int(sx)}", (sx + 0.2, 30.2, 8.58), (sx + 3.8, 35.8, 8.62), mats["Skylight_Glass"])

    # Exterior Rear Climbable Ladder (from ground z=0 to roof z=8.4 at x=45.8, y=54.0)
    for lz in range(1, 17):
        rung_z = lz * 0.5
        add_box(c_props, f"Ladder_Rung_{lz}", (45.8, 53.7, rung_z - 0.02), (45.95, 54.3, rung_z + 0.02), mats["Safety_Rail"])
    add_box(c_props, "Ladder_Rail_L", (45.85, 53.68, 0.0), (45.95, 53.72, 8.8), mats["Steel_Dark"])
    add_box(c_props, "Ladder_Rail_R", (45.85, 54.28, 0.0), (45.95, 54.32, 8.8), mats["Steel_Dark"])

    print("[5/10] Building Crawlable Ventilation Ducts & Drop Shafts...")
    # Roof Vent Intake Mouth
    add_box(c_vents, "Vent_Roof_Intake", (16.0, 21.0, 8.4), (17.5, 23.0, 9.6), mats["Vent_Duct"])
    # Main horizontal crawl duct running under ceiling / on roof (height clearance 1.35m)
    # Duct segment from intake across ceiling rafters towards catwalk
    add_box(c_vents, "Vent_Duct_Main_Floor", (16.0, 22.0, 8.05), (35.0, 23.5, 8.15), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Duct_Main_Ceiling", (16.0, 22.0, 9.4), (35.0, 23.5, 9.5), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Duct_Main_Wall_N", (16.0, 23.4, 8.05), (35.0, 23.5, 9.5), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Duct_Main_Wall_S", (16.0, 22.0, 8.05), (35.0, 22.1, 9.5), mats["Vent_Duct"])

    # Branch 1: Vertical Drop Shaft 1 onto Catwalk (at x=22.0, y=23.0, dropping down to z=4.2)
    add_box(c_vents, "Vent_Drop1_Wall_W", (21.4, 22.0, 4.2), (21.5, 23.5, 8.05), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Drop1_Wall_E", (22.5, 22.0, 4.2), (22.6, 23.5, 8.05), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Drop1_Wall_N", (21.5, 23.4, 4.2), (22.5, 23.5, 8.05), mats["Vent_Duct"])
    # Open bottom exit allows player to drop safely onto the catwalk!

    # Branch 2: Duct continuing towards back office (y: 23.5 to 48.0 at x: 33.5..35.0)
    add_box(c_vents, "Vent_Duct_Branch2_Floor", (33.5, 23.5, 8.05), (35.0, 48.0, 8.15), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Duct_Branch2_Ceiling", (33.5, 23.5, 9.4), (35.0, 48.0, 9.5), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Duct_Branch2_Wall_W", (33.5, 23.5, 8.05), (33.6, 48.0, 9.5), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Duct_Branch2_Wall_E", (34.9, 23.5, 8.05), (35.0, 48.0, 9.5), mats["Vent_Duct"])

    # Vertical Drop Shaft 2 directly into Hostage Office (at x=34.0, y=48.0, dropping down to office ceiling z=7.0 / floor)
    add_box(c_vents, "Vent_Drop2_Wall_W", (33.4, 47.2, 5.0), (33.5, 48.8, 8.05), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Drop2_Wall_E", (34.9, 47.2, 5.0), (35.0, 48.8, 8.05), mats["Vent_Duct"])
    add_box(c_vents, "Vent_Drop2_Wall_N", (33.5, 48.7, 5.0), (34.9, 48.8, 8.05), mats["Vent_Duct"])

    # Industrial HVAC Chillers on roof
    add_box(c_props, "Roof_HVAC_1", (24.0, 42.0, 8.4), (27.0, 45.0, 9.8), mats["Steel_Dark"])
    add_cylinder(c_props, "Roof_HVAC_Fan_1", (25.5, 43.5, 9.82), radius=1.0, height=0.1, material=mats["Steel_Catwalk"])
    add_box(c_props, "Roof_HVAC_2", (38.0, 24.0, 8.4), (41.0, 27.0, 9.8), mats["Steel_Dark"])
    add_cylinder(c_props, "Roof_HVAC_Fan_2", (39.5, 25.5, 9.82), radius=1.0, height=0.1, material=mats["Steel_Catwalk"])

    print("[6/10] Building Industrial Steel Catwalk & Staircases...")
    # Catwalk running east-west across warehouse (x: 14.5 to 36.0, y: 22.0 to 24.5, z: 4.2)
    # Walking deck
    add_box(c_catwalk, "Catwalk_Deck_Main", (14.5, 22.0, 4.15), (36.0, 24.5, 4.25), mats["Steel_Catwalk"])
    # Support structural I-beams under catwalk (with ground clearance underneath z=0..4.15)
    for bx in [18.0, 24.0, 30.0]:
        add_cylinder(c_catwalk, f"Catwalk_Pillar_{int(bx)}", (bx, 23.25, 2.07), radius=0.15, height=4.15, material=mats["Steel_Dark"])
    # Safety Handrails on catwalk
    add_box(c_catwalk, "Catwalk_Rail_South", (14.5, 22.0, 4.25), (36.0, 22.1, 5.25), mats["Safety_Rail"])
    add_box(c_catwalk, "Catwalk_Rail_North", (14.5, 24.4, 4.25), (36.0, 24.5, 5.25), mats["Safety_Rail"])

    # Catwalk connecting walkway to rear office
    add_box(c_catwalk, "Catwalk_Bridge_Office", (33.5, 24.5, 4.15), (36.0, 42.0, 4.25), mats["Steel_Catwalk"])
    add_box(c_catwalk, "Catwalk_Bridge_Rail_W", (33.4, 24.5, 4.25), (33.5, 42.0, 5.25), mats["Safety_Rail"])
    add_box(c_catwalk, "Catwalk_Bridge_Rail_E", (35.9, 24.5, 4.25), (36.0, 42.0, 5.25), mats["Safety_Rail"])

    # Industrial Staircase from Ground to Catwalk (x: 15.0..17.5, y: 24.5..32.0, climbing from z=0 to 4.2)
    for step in range(14):
        sz = step * 0.3
        sy = 24.5 + step * 0.5
        add_box(c_catwalk, f"Catwalk_Stair_{step}", (15.0, sy, 0.0), (17.5, sy + 0.5, sz + 0.3), mats["Steel_Catwalk"])

    print("[7/10] Building 2-Story Hostage Office & Observation Glass...")
    # Office footprint: x: 33.0 to 45.0, y: 42.0 to 55.0, z: 0 to 8.2
    # Intermediate 2nd floor office floor slab (z=4.2..4.4)
    add_box(c_office, "Office_2nd_Floor_Slab", (33.0, 42.0, 4.2), (45.0, 55.0, 4.4), mats["Office_Floor"])
    # Front Observation Wall overlooking the hangar (y=42.0)
    add_box(c_office, "Office_Front_Wall_Lower", (33.0, 42.0, 0.0), (45.0, 42.4, 4.2), mats["Office_Wall"])
    # Upper wall spandrel
    add_box(c_office, "Office_Front_Wall_Spandrel", (33.0, 42.0, 4.4), (45.0, 42.4, 5.0), mats["Office_Wall"])
    # Panoramic Observation Windows (glass looking out over warehouse!)
    add_box(c_office, "Office_Panoramic_Glass", (33.5, 42.15, 5.0), (44.5, 42.25, 7.2), mats["Office_Window"])
    add_box(c_office, "Office_Front_Wall_Header", (33.0, 42.0, 7.2), (45.0, 42.4, 8.2), mats["Office_Wall"])

    # West wall of office (x=33.0) with second-floor doorway from catwalk
    add_box(c_office, "Office_West_Wall_1", (33.0, 42.0, 4.4), (33.4, 45.0, 8.2), mats["Office_Wall"])
    # Doorway (y: 45.0..47.0, z: 4.4..6.8)
    add_box(c_office, "Office_West_Door_Header", (33.0, 45.0, 6.8), (33.4, 47.0, 8.2), mats["Office_Wall"])
    add_box(c_office, "Office_West_Wall_2", (33.0, 47.0, 4.4), (33.4, 55.0, 8.2), mats["Office_Wall"])

    # Office interior desks and furniture
    add_box(c_office, "Office_Desk_1", (35.0, 48.0, 4.4), (37.5, 49.5, 5.15), mats["Wood_Crate"])
    add_box(c_office, "Office_Monitor_1", (35.8, 48.6, 5.15), (36.6, 48.8, 5.7), mats["Steel_Dark"])
    add_box(c_office, "Office_Desk_2", (39.0, 48.0, 4.4), (41.5, 49.5, 5.15), mats["Wood_Crate"])
    add_box(c_office, "Office_Monitor_2", (39.8, 48.6, 5.15), (40.6, 48.8, 5.7), mats["Steel_Dark"])

    print("[8/10] Building Center 18-Wheeler Semi-Truck & Forklift...")
    # 18-Wheeler Semi Truck parked inside hangar floor (x: 23.0 to 27.5, y: 30.0 to 42.0)
    tx, ty = 25.0, 36.0
    # Tractor Cab
    add_box(c_vehicles, "Semi_Cab", (tx - 1.3, ty - 5.0, 0.4), (tx + 1.3, ty - 1.5, 3.4), mats["Truck_Cab"])
    add_box(c_vehicles, "Semi_Windshield", (tx - 1.1, ty - 5.05, 1.8), (tx + 1.1, ty - 4.95, 2.8), mats["SWAT_Glass"])
    # Chrome exhaust stacks
    add_cylinder(c_vehicles, "Semi_Exhaust_L", (tx - 1.35, ty - 2.0, 2.5), radius=0.1, height=2.4, material=mats["Train_Rail"])
    add_cylinder(c_vehicles, "Semi_Exhaust_R", (tx + 1.35, ty - 2.0, 2.5), radius=0.1, height=2.4, material=mats["Train_Rail"])
    # Long Freight Trailer
    add_box(c_vehicles, "Semi_Trailer", (tx - 1.3, ty - 1.5, 0.8), (tx + 1.3, ty + 6.5, 4.0), mats["Truck_Trailer"])
    # Wheels for truck
    for wy in [ty - 4.2, ty - 2.5, ty + 4.5, ty + 5.8]:
        for side in [-1.35, 1.35]:
            add_cylinder(c_vehicles, f"Truck_Wheel_{wy}_{side}", (tx + side, wy, 0.5), radius=0.5, height=0.35, material=mats["SWAT_Tire"], rotation_euler=(0, math.pi/2, 0))

    # Forklift near crates (x=19.0, y=40.0)
    fx, fy = 19.0, 40.0
    add_box(c_vehicles, "Forklift_Body", (fx - 0.8, fy - 1.0, 0.3), (fx + 0.8, fy + 0.8, 1.4), mats["Forklift_Yellow"])
    add_box(c_vehicles, "Forklift_Rollcage", (fx - 0.75, fy - 0.8, 1.4), (fx + 0.75, fy + 0.6, 2.4), mats["Steel_Dark"])
    add_box(c_vehicles, "Forklift_Mast", (fx - 0.6, fy + 0.8, 0.2), (fx + 0.6, fy + 0.95, 2.6), mats["Steel_Dark"])
    add_box(c_vehicles, "Forklift_Forks", (fx - 0.5, fy + 0.95, 0.05), (fx + 0.5, fy + 2.0, 0.15), mats["Steel_Dark"])

    # Wooden Crates & Pallet Stacks on warehouse floor
    add_box(c_props, "Crate_Stack_A1", (18.0, 24.0, 0.0), (19.5, 25.5, 1.5), mats["Wood_Crate"])
    add_box(c_props, "Crate_Stack_A2", (19.5, 24.0, 0.0), (21.0, 25.5, 1.5), mats["Wood_Crate"])
    add_box(c_props, "Crate_Stack_A_Top", (18.7, 24.0, 1.5), (20.2, 25.5, 3.0), mats["Wood_Crate"])

    add_box(c_props, "Crate_Stack_B1", (30.0, 24.0, 0.0), (31.8, 25.8, 1.8), mats["Wood_Crate"])
    add_box(c_props, "Crate_Stack_B2", (30.0, 26.0, 0.0), (31.8, 27.8, 1.8), mats["Wood_Crate"])

    print("[9/10] Setting up Realistic Lighting & Cameras...")
    # Sun Light (Daylight with soft shadow angle)
    bpy.ops.object.light_add(type='SUN', radius=0.5 * SCALE, location=(30.0 * SCALE, 10.0 * SCALE, 30.0 * SCALE))
    sun = bpy.context.active_object
    sun.name = "Sun_Key_Light"
    sun.data.energy = 4.5
    sun.data.color = (1.0, 0.98, 0.92) # warm sunlight
    sun.rotation_euler = (math.radians(45), math.radians(15), math.radians(35))
    if sun.name not in c_lights.objects:
        c_lights.objects.link(sun)
        if sun.name in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.unlink(sun)

    # Warehouse interior high-bay floodlights (warm halogen, scaled for 10x volume)
    for lx in [22.0, 32.0, 40.0]:
        for ly in [26.0, 36.0, 48.0]:
            bpy.ops.object.light_add(type='POINT', radius=8.0, location=(lx * SCALE, ly * SCALE, 7.8 * SCALE))
            light = bpy.context.active_object
            light.name = f"HighBay_Lamp_{int(lx)}_{int(ly)}"
            light.data.energy = 40000.0
            light.data.color = (1.0, 0.92, 0.80)
            if light.name not in c_lights.objects:
                c_lights.objects.link(light)
                if light.name in bpy.context.scene.collection.objects:
                    bpy.context.scene.collection.objects.unlink(light)

    # Tactical Scene Camera positioned overlooking CT spawn and warehouse
    bpy.ops.object.camera_add(location=(8.0 * SCALE, 8.0 * SCALE, 4.5 * SCALE), rotation=(math.radians(78), 0, math.radians(-15)))
    cam = bpy.context.active_object
    cam.name = "Main_Level_Camera"
    bpy.context.scene.camera = cam
    if cam.name not in c_lights.objects:
        c_lights.objects.link(cam)
        if cam.name in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.unlink(cam)

    print("[10/10] Scene Assembly Complete!")


def export_assets():
    """Save .blend project and export .glb for native and web game engines."""
    blend_path = Path("/home/horrible/horrible-dashboard/assets/maps/hd_assault.blend")
    glb_backend_path = Path("/home/horrible/horrible-dashboard/backend/modules/hassault/maps/hd_assault.glb")
    glb_web_path = Path("/home/horrible/horrible-dashboard/apps/web/public/hd_assault.glb")

    blend_path.parent.mkdir(parents=True, exist_ok=True)
    glb_backend_path.parent.mkdir(parents=True, exist_ok=True)
    glb_web_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure 3D viewport to Material Preview
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = 'MATERIAL'

    # Save .blend
    print(f"Saving Blender Project: {blend_path}")
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
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
        export_lights=True,
        export_cameras=True
    )

    # Copy to web public
    import shutil
    shutil.copyfile(glb_backend_path, glb_web_path)
    print(f"Copied GLB to Web: {glb_web_path}")


if __name__ == "__main__":
    print("=== Generating Realistic CS:GO cs_assault Map in Blender ===")
    build_assault_scene()
    export_assets()
    print("=== Done! ===")
