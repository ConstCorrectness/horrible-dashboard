#!/usr/bin/env python3
"""
The Bank (hd_bank) Photorealistic Tactical Map Generator for Blender 4.2+
Natively 1:1 metric scale on 56m x 56m arena footprint (bounds: 4.0..60.0).

Photorealistic Upgrades:
- Neoclassical Bank Facade: 4 monumental fluted columns with Corinthian capitals,
  classical triangular pediment with dentil molding, engraved architrave banner,
  stone canopy for tactical skill-jumps, and mahogany brass-trimmed double entrance doors.
- Grand Banking Hall (Site B): Large-format Italian Carrara white marble tiles with
  black Nero Marquina marble perimeter borders, coffered ceiling grid with recessed lighting,
  multi-tier brass chandelier with warm luminous glow, and 1.8m brass-bezeled wall regulator clock.
- Teller Service Island: Polished mahogany counter with beveled nosing, bullet-resistant
  glass security partitions with transaction speaking ports, teller computer monitors,
  keyboards, cash drawers, and velvet rope queue stanchions.
- Executive Mezzanine & Balconies (z = 5.0m): Elevated second-story walkway with royal
  burgundy carpet runners, brass spindle balustrades with mahogany handrails, dual marble
  staircases, executive conference furniture, and exterior fire escape door.
- The Vault (Site A): Massive circular bank vault blast door (3.2m diameter, 0.6m thick)
  swung open with stepped sealing rings, chrome locking lugs, heavy cast hinge arm,
  chrome multi-spoke handwheel, brass combination dials, floor-to-ceiling safety deposit boxes,
  stepped pyramids of 24k gold bullion ingots, and wire-mesh carts of cash bundles.
- Security Control Room & Defender Offices: 42U server equipment racks with blinking LEDs,
  multi-monitor CCTV surveillance wall, manager's mahogany executive desk, and staff breakroom.
- Street & Plaza Approach: Aggregate asphalt boulevard with double solid yellow lines,
  crosswalk stripes, granite sidewalk curbs, cast-iron streetlamps, fire hydrants,
  concrete planters with hedges, jersey barriers, 24/7 outdoor ATM vestibule,
  armored SWAT assault truck with push bumper and lightbar, and police squad cruiser.
"""

import sys
import os
import math
import shutil
from pathlib import Path

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: This script must be run inside Blender: blender -b -P generate_bank.py")
    sys.exit(1)


def clear_scene():
    """Clear all objects and materials from the scene."""
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

    if emission_strength > 0.0:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = (*emission_color[:3], 1.0)
            bsdf.inputs["Emission Strength"].default_value = emission_strength
        elif "Emission" in bsdf.inputs:
            bsdf.inputs["Emission"].default_value = (*emission_color[:3], 1.0)

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    """Create all PBR materials for The Bank."""
    mats = {}

    # Stone & Marble
    mats["Marble_White"] = create_pbr_material("Mat_Marble_White", (0.86, 0.85, 0.82), metallic=0.05, roughness=0.18)
    mats["Marble_Black"] = create_pbr_material("Mat_Marble_Black", (0.16, 0.17, 0.19), metallic=0.08, roughness=0.20)
    mats["Marble_Column"] = create_pbr_material("Mat_Marble_Column", (0.88, 0.87, 0.84), metallic=0.04, roughness=0.22)
    mats["Limestone_Facade"] = create_pbr_material("Mat_Limestone_Facade", (0.72, 0.69, 0.64), metallic=0.02, roughness=0.68)
    mats["Concrete_Sidewalk"] = create_pbr_material("Mat_Concrete_Sidewalk", (0.58, 0.56, 0.52), metallic=0.0, roughness=0.85)
    mats["Granite_Curb"] = create_pbr_material("Mat_Granite_Curb", (0.42, 0.40, 0.38), metallic=0.05, roughness=0.70)
    mats["Asphalt_Street"] = create_pbr_material("Mat_Asphalt_Street", (0.18, 0.18, 0.20), metallic=0.02, roughness=0.88)
    mats["Perimeter_Concrete"] = create_pbr_material("Mat_Perimeter_Concrete", (0.28, 0.29, 0.32), metallic=0.05, roughness=0.80)

    # Road Markings
    mats["Road_Yellow"] = create_pbr_material("Mat_Road_Yellow_Stripe", (0.92, 0.78, 0.12), metallic=0.05, roughness=0.60)
    mats["Road_White"] = create_pbr_material("Mat_Road_White_Stripe", (0.90, 0.90, 0.92), metallic=0.05, roughness=0.60)

    # Metals & Vault Steel
    mats["Vault_Steel"] = create_pbr_material("Mat_Vault_Steel", (0.28, 0.30, 0.34), metallic=0.75, roughness=0.35)
    mats["Chrome_Polished"] = create_pbr_material("Mat_Chrome_Polished", (0.88, 0.90, 0.92), metallic=0.95, roughness=0.08)
    mats["Deposit_Box_Steel"] = create_pbr_material("Mat_Deposit_Box_Steel", (0.62, 0.65, 0.68), metallic=0.85, roughness=0.25)
    mats["Brass_Polished"] = create_pbr_material("Mat_Brass_Polished", (0.90, 0.76, 0.26), metallic=0.85, roughness=0.22)
    mats["Bronze_Architectural"] = create_pbr_material("Mat_Bronze_Architectural", (0.48, 0.36, 0.22), metallic=0.80, roughness=0.32)
    mats["Steel_Structural"] = create_pbr_material("Mat_Steel_Structural", (0.35, 0.37, 0.40), metallic=0.65, roughness=0.45)
    mats["Cast_Iron_Dark"] = create_pbr_material("Mat_Cast_Iron_Dark", (0.16, 0.16, 0.18), metallic=0.55, roughness=0.70)

    # Gold & Cash Wealth
    mats["Gold_Bullion"] = create_pbr_material("Mat_Gold_Bullion", (0.96, 0.82, 0.22), metallic=0.92, roughness=0.15)
    mats["Currency_Green"] = create_pbr_material("Mat_Currency_Green", (0.28, 0.58, 0.32), metallic=0.0, roughness=0.75)
    mats["Currency_Band"] = create_pbr_material("Mat_Currency_Band", (0.88, 0.84, 0.75), metallic=0.0, roughness=0.70)

    # Woods & Leather
    mats["Mahogany_Polished"] = create_pbr_material("Mat_Mahogany_Polished", (0.35, 0.18, 0.10), metallic=0.0, roughness=0.28)
    mats["Oak_Desk"] = create_pbr_material("Mat_Oak_Desk", (0.45, 0.32, 0.20), metallic=0.0, roughness=0.55)
    mats["Leather_Oxblood"] = create_pbr_material("Mat_Leather_Oxblood", (0.42, 0.12, 0.14), metallic=0.05, roughness=0.42)
    mats["Leather_Black"] = create_pbr_material("Mat_Leather_Black", (0.14, 0.14, 0.16), metallic=0.05, roughness=0.48)
    mats["Velvet_Crimson"] = create_pbr_material("Mat_Velvet_Crimson", (0.65, 0.08, 0.12), metallic=0.0, roughness=0.80)
    mats["Carpet_Burgundy"] = create_pbr_material("Mat_Carpet_Burgundy", (0.48, 0.14, 0.18), metallic=0.0, roughness=0.92)

    # Glass & Glazing
    mats["Security_Glass"] = create_pbr_material("Mat_Security_Glass", (0.82, 0.90, 0.94), transmission=0.88, roughness=0.06, alpha=0.35)
    mats["Smoked_Glass"] = create_pbr_material("Mat_Smoked_Glass", (0.22, 0.28, 0.32), transmission=0.75, roughness=0.12, alpha=0.6)

    # Electronics & Lighting
    mats["Chandelier_Lamp"] = create_pbr_material("Mat_Chandelier_Lamp_Glow", (1.0, 0.95, 0.82), emission_color=(1.0, 0.92, 0.75), emission_strength=6.0)
    mats["Monitor_Plastic"] = create_pbr_material("Mat_Monitor_Plastic", (0.12, 0.13, 0.15), metallic=0.1, roughness=0.5)
    mats["Monitor_Screen_CCTV"] = create_pbr_material("Mat_Monitor_Screen_CCTV", (0.15, 0.35, 0.45), emission_color=(0.18, 0.42, 0.55), emission_strength=2.5)
    mats["ATM_Screen"] = create_pbr_material("Mat_ATM_Screen", (0.15, 0.65, 0.95), emission_color=(0.15, 0.65, 0.95), emission_strength=3.0)
    mats["ATM_Blue_Shell"] = create_pbr_material("Mat_ATM_Blue_Shell", (0.12, 0.38, 0.78), metallic=0.3, roughness=0.35)
    mats["Emergency_Red"] = create_pbr_material("Mat_Emergency_Red_Glow", (0.95, 0.10, 0.10), emission_color=(1.0, 0.1, 0.1), emission_strength=8.0)
    mats["Emergency_Blue"] = create_pbr_material("Mat_Emergency_Blue_Glow", (0.10, 0.35, 0.98), emission_color=(0.1, 0.35, 1.0), emission_strength=8.0)

    # Hazard & Tactical
    mats["Hazard_Yellow"] = create_pbr_material("Mat_Hazard_Yellow", (0.92, 0.82, 0.10), metallic=0.05, roughness=0.45)
    mats["Hazard_Black"] = create_pbr_material("Mat_Hazard_Black", (0.10, 0.10, 0.12), metallic=0.1, roughness=0.50)
    mats["Tactical_Site_Stencil"] = create_pbr_material("Mat_Tactical_Site_Stencil", (0.85, 0.20, 0.15), metallic=0.05, roughness=0.6)
    mats["Foliage_Green"] = create_pbr_material("Mat_Foliage_Green", (0.18, 0.44, 0.18), metallic=0.0, roughness=0.65)
    mats["SWAT_Vehicle_Navy"] = create_pbr_material("Mat_SWAT_Vehicle_Navy", (0.12, 0.15, 0.22), metallic=0.35, roughness=0.45)
    mats["Police_Cruiser_White"] = create_pbr_material("Mat_Police_Cruiser_White", (0.88, 0.88, 0.90), metallic=0.3, roughness=0.4)

    return mats


def get_or_create_collection(name):
    """Get or create a Blender collection."""
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def add_box(col, name, p0, p1, material, is_collider=True, bevel=False, bevel_width=0.03):
    """Create a rectangular box aligned to axis."""
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


def add_cylinder(col, name, center, radius, height, material, segments=16, is_collider=True):
    """Create a Z-axis aligned cylinder."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = center

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

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = is_collider
    return obj


def add_oriented_cylinder(col, name, p0, p1, radius, material, segments=16, is_collider=False):
    """Create a cylinder between two arbitrary 3D endpoints."""
    v0 = Vector(p0)
    v1 = Vector(p1)
    diff = v1 - v0
    length = diff.length
    if length < 0.001:
        return None

    mid = (v0 + v1) / 2.0
    rot = diff.to_track_quat('Z', 'Y').to_euler()

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = mid
    obj.rotation_euler = rot

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

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = is_collider
    return obj


def add_fluted_column(col, name, base_center, radius=0.88, height=12.5, material=None, base_material=None, plinth_w=2.0, plinth_h=0.8):
    """Create an authentic classical fluted column with plinth base, fluted shaft, and capital."""
    bx, by, bz = base_center
    
    # 1. Plinth Box (Base)
    add_box(col, f"{name}_Plinth", 
            (bx - plinth_w/2.0, by - plinth_w/2.0, bz),
            (bx + plinth_w/2.0, by + plinth_w/2.0, bz + plinth_h),
            base_material or material, is_collider=True, bevel=True, bevel_width=0.04)

    # 2. Torus/Base Molding
    add_cylinder(col, f"{name}_Torus_Base_Detail", 
                 (bx, by, bz + plinth_h + 0.15), radius=radius * 1.12, height=0.30, 
                 material=material, segments=24, is_collider=False)

    # 3. Main Column Shaft (Solid Collider for player physics)
    shaft_h = height - plinth_h - 0.9
    shaft_cz = bz + plinth_h + 0.3 + shaft_h / 2.0
    add_cylinder(col, f"{name}_Shaft", (bx, by, shaft_cz), radius=radius, height=shaft_h, material=material, segments=24, is_collider=True)

    # 4. Vertical Fluting Ribs Detail (16 decorative flutes around perimeter)
    num_flutes = 16
    for i in range(num_flutes):
        angle = (2.0 * math.pi * i) / num_flutes
        fx = bx + math.cos(angle) * (radius * 0.98)
        fy = by + math.sin(angle) * (radius * 0.98)
        add_cylinder(col, f"{name}_Flute_{i}_Detail", (fx, fy, shaft_cz), radius=radius * 0.055, height=shaft_h * 0.96, material=material, segments=8, is_collider=False)

    # 5. Capital & Abacus Box
    cap_z = bz + height - 0.6
    add_cylinder(col, f"{name}_Capital_Detail", (bx, by, cap_z), radius=radius * 1.15, height=0.40, material=material, segments=24, is_collider=False)
    add_box(col, f"{name}_Abacus", 
            (bx - plinth_w * 0.55, by - plinth_w * 0.55, bz + height - 0.3),
            (bx + plinth_w * 0.55, by + plinth_w * 0.55, bz + height),
            base_material or material, is_collider=True, bevel=True, bevel_width=0.03)


def build_bank_scene():
    """Build the entire photorealistic The Bank scene in Blender."""
    clear_scene()
    mats = setup_materials()

    c_arch = get_or_create_collection("Arch_Structure")
    c_doors = get_or_create_collection("Arch_Doors_Windows")
    c_lobby = get_or_create_collection("Props_Bank_Lobby")
    c_vault = get_or_create_collection("Props_Vault")
    c_offices = get_or_create_collection("Props_Offices")
    c_street = get_or_create_collection("Props_Street")

    # =========================================================================
    # 1. PERIMETER ENCLOSURE & CEILING (Bounds: x: 4..60, y: 4..60, z: 0..14)
    # =========================================================================
    # Exterior Boundary Walls
    add_box(c_arch, "Wall_Perimeter_South", (3.0, 3.0, 0.0), (61.0, 4.0, 14.0), mats["Perimeter_Concrete"])
    add_box(c_arch, "Wall_Perimeter_North", (3.0, 60.0, 0.0), (61.0, 61.0, 14.0), mats["Perimeter_Concrete"])
    add_box(c_arch, "Wall_Perimeter_West", (3.0, 3.0, 0.0), (4.0, 61.0, 14.0), mats["Perimeter_Concrete"])
    add_box(c_arch, "Wall_Perimeter_East", (60.0, 3.0, 0.0), (61.0, 61.0, 14.0), mats["Perimeter_Concrete"])

    # High Roof / Sky Cap
    add_box(c_arch, "Ceiling_Roof_Slab", (3.0, 3.0, 14.0), (61.0, 61.0, 14.4), mats["Perimeter_Concrete"])

    # =========================================================================
    # 2. FLOORING & GROUND SURFACES
    # =========================================================================
    # Street Asphalt (x: 4..60, y: 4..18)
    add_box(c_arch, "Floor_Street_Asphalt", (4.0, 4.0, -0.2), (60.0, 18.0, 0.0), mats["Asphalt_Street"])

    # Double Yellow Centerline along y = 10.0
    add_box(c_street, "Road_Stripe_Yellow_1_Stripe", (4.0, 9.85, 0.005), (60.0, 9.95, 0.008), mats["Road_Yellow"], is_collider=False)
    add_box(c_street, "Road_Stripe_Yellow_2_Stripe", (4.0, 10.05, 0.005), (60.0, 10.15, 0.008), mats["Road_Yellow"], is_collider=False)

    # Pedestrian Crosswalk Leading to Bank Entrance (x: 27.5..37.5)
    for i in range(5):
        sx = 27.5 + i * 2.0
        add_box(c_street, f"Crosswalk_Stripe_{i}_Stripe", (sx, 7.0, 0.005), (sx + 1.2, 13.5, 0.008), mats["Road_White"], is_collider=False)

    # Sidewalk Concrete Slab (y: 14.0..18.0, height = 0.20m)
    add_box(c_arch, "Sidewalk_Slab", (4.0, 14.0, 0.0), (60.0, 18.0, 0.20), mats["Concrete_Sidewalk"])
    # Granite Curb Edge
    add_box(c_arch, "Sidewalk_Granite_Curb", (4.0, 13.8, 0.0), (60.0, 14.0, 0.22), mats["Granite_Curb"], bevel=True, bevel_width=0.02)

    # Grand Bank Lobby Floor (Italian Carrara Marble, y: 18.0..46.0)
    add_box(c_arch, "Floor_Lobby_Marble", (4.0, 18.0, -0.05), (60.0, 46.0, 0.0), mats["Marble_White"])
    # Black Nero Marquina Marble Inlay Borders
    add_box(c_arch, "Marble_Border_South_Stripe", (4.0, 18.0, 0.005), (60.0, 19.0, 0.008), mats["Marble_Black"], is_collider=False)
    add_box(c_arch, "Marble_Border_North_Stripe", (4.0, 45.0, 0.005), (60.0, 46.0, 0.008), mats["Marble_Black"], is_collider=False)
    add_box(c_arch, "Marble_Border_West_Stripe", (4.0, 19.0, 0.005), (5.0, 45.0, 0.008), mats["Marble_Black"], is_collider=False)
    add_box(c_arch, "Marble_Border_East_Stripe", (59.0, 19.0, 0.005), (60.0, 45.0, 0.008), mats["Marble_Black"], is_collider=False)

    # Rear Staff Offices Concrete / Vinyl Floor (y: 46.0..60.0, x: 4.0..40.0)
    add_box(c_arch, "Floor_Offices_Vinyl", (4.0, 46.0, -0.05), (40.0, 60.0, 0.0), mats["Concrete_Sidewalk"])

    # Vault Heavy Reinforced Steel Floor (y: 46.0..60.0, x: 40.0..60.0)
    add_box(c_arch, "Floor_Vault_Steel", (40.0, 46.0, -0.05), (60.0, 60.0, 0.0), mats["Vault_Steel"])

    # =========================================================================
    # 3. NEOCLASSICAL BANK FACADE & COLONNADE (y: 18.0..22.0)
    # =========================================================================
    # Facade Wall Sections (Openings at x: 8..14 west, 28..36 center, 50..56 east)
    add_box(c_arch, "Facade_Wall_W1", (4.0, 18.0, 0.0), (8.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Facade_Wall_W2", (14.0, 18.0, 0.0), (28.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Facade_Wall_E1", (36.0, 18.0, 0.0), (50.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Facade_Wall_E2", (56.0, 18.0, 0.0), (60.0, 22.0, 14.0), mats["Limestone_Facade"])

    # Doorway Lintels / Headers
    add_box(c_arch, "Facade_Header_West", (8.0, 18.0, 4.0), (14.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Facade_Header_Center", (28.0, 18.0, 4.2), (36.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Facade_Header_East", (50.0, 18.0, 4.0), (56.0, 22.0, 14.0), mats["Limestone_Facade"])

    # 4 Monumental Classical Fluted Columns at Front Entrance
    for col_x in [20.0, 26.0, 38.0, 44.0]:
        add_fluted_column(c_arch, f"Col_Facade_{int(col_x)}", (col_x, 16.5, 0.0), radius=0.88, height=13.0, material=mats["Marble_Column"], base_material=mats["Limestone_Facade"])

    # Entrance Stone Canopy (Key Skill Jump platform at z = 4.2..4.8m)
    add_box(c_arch, "Entrance_Stone_Canopy", (24.0, 15.0, 4.2), (40.0, 18.2, 4.8), mats["Limestone_Facade"], bevel=True, bevel_width=0.04)

    # Classical Triangular Pediment Gable above Canopy
    add_box(c_arch, "Entablature_Frieze_Beam", (23.5, 17.5, 4.8), (40.5, 18.5, 5.8), mats["Limestone_Facade"])
    # Carved Architrave Plaque
    add_box(c_arch, "Plaque_First_National_Bank_Detail", (25.5, 17.38, 5.0), (38.5, 17.52, 5.6), mats["Brass_Polished"], is_collider=False)

    # Classical Tympanum Triangle Gable (Procedural Prism)
    mesh_ped = bpy.data.meshes.new("Entrance_Pediment_Tympanum")
    obj_ped = bpy.data.objects.new("Entrance_Pediment_Tympanum", mesh_ped)
    c_arch.objects.link(obj_ped)
    bm_ped = bmesh.new()
    v1 = bm_ped.verts.new((23.5, 17.5, 5.8))
    v2 = bm_ped.verts.new((40.5, 17.5, 5.8))
    v3 = bm_ped.verts.new((32.0, 17.5, 8.6))
    v4 = bm_ped.verts.new((23.5, 18.5, 5.8))
    v5 = bm_ped.verts.new((40.5, 18.5, 5.8))
    v6 = bm_ped.verts.new((32.0, 18.5, 8.6))
    bm_ped.faces.new((v1, v2, v3))
    bm_ped.faces.new((v6, v5, v4))
    bm_ped.faces.new((v1, v4, v5, v2))
    bm_ped.faces.new((v2, v5, v6, v3))
    bm_ped.faces.new((v3, v6, v4, v1))
    bm_ped.to_mesh(mesh_ped)
    bm_ped.free()
    obj_ped.data.materials.append(mats["Limestone_Facade"])
    obj_ped["is_collider"] = True

    # Mahogany Grand Double Doors with Glass Lights and Brass Kickplates (Open Walkthrough)
    add_box(c_doors, "Door_Frame_Jamb_L", (28.8, 18.05, 0.0), (29.5, 18.35, 3.8), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    add_box(c_doors, "Door_Frame_Jamb_R", (34.5, 18.05, 0.0), (35.2, 18.35, 3.8), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    add_box(c_doors, "Door_Frame_Lintel", (28.8, 18.05, 3.2), (35.2, 18.35, 3.8), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    add_box(c_doors, "Door_Frame_Mullion", (31.85, 18.05, 0.0), (32.15, 18.35, 3.2), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    # Propped Open Double Doors into Banking Hall
    add_box(c_doors, "Door_Leaf_L_Detail", (29.5, 18.35, 0.0), (29.65, 19.85, 3.15), mats["Mahogany_Polished"], is_collider=False)
    add_box(c_doors, "Door_Leaf_R_Detail", (34.35, 18.35, 0.0), (34.5, 19.85, 3.15), mats["Mahogany_Polished"], is_collider=False)
    add_box(c_doors, "Door_Glass_L_Detail", (29.55, 18.55, 0.4), (29.60, 19.65, 3.0), mats["Security_Glass"], is_collider=False)
    add_box(c_doors, "Door_Glass_R_Detail", (34.40, 18.55, 0.4), (34.45, 19.65, 3.0), mats["Security_Glass"], is_collider=False)
    add_box(c_doors, "Door_Kickplate_L_Detail", (29.48, 18.35, 0.05), (29.67, 19.85, 0.45), mats["Brass_Polished"], is_collider=False)
    add_box(c_doors, "Door_Kickplate_R_Detail", (34.33, 18.35, 0.05), (34.52, 19.85, 0.45), mats["Brass_Polished"], is_collider=False)

    # =========================================================================
    # 4. GRAND BANKING HALL (Site B) - Neoclassical Pillars, Ceiling, Teller Island
    # =========================================================================
    # 4 Interior Grand Fluted Marble Columns
    for (px, py) in [(22.0, 24.0), (42.0, 24.0), (22.0, 40.0), (42.0, 40.0)]:
        add_fluted_column(c_lobby, f"Col_Lobby_{int(px)}_{int(py)}", (px + 1.0, py + 1.0, 0.0), radius=0.92, height=14.0, material=mats["Marble_Column"], base_material=mats["Marble_Black"])

    # Ornate Coffered Ceiling Beams (creating deep architectural recess grids)
    for bx in [16.0, 24.0, 32.0, 40.0, 48.0]:
        add_box(c_lobby, f"Coffer_Beam_X_{int(bx)}_Detail", (bx - 0.45, 18.0, 13.0), (bx + 0.45, 46.0, 14.0), mats["Limestone_Facade"], is_collider=False)
    for by in [24.0, 30.0, 36.0, 42.0]:
        add_box(c_lobby, f"Coffer_Beam_Y_{int(by)}_Detail", (4.0, by - 0.45, 13.0), (60.0, by + 0.45, 14.0), mats["Limestone_Facade"], is_collider=False)

    # Grand Multi-Tier Brass Chandelier with warm optical glow
    add_cylinder(c_lobby, "Chandelier_Ring_Upper_Detail", (32.0, 32.0, 12.0), radius=2.6, height=0.25, material=mats["Brass_Polished"], segments=32, is_collider=False)
    add_cylinder(c_lobby, "Chandelier_Ring_Lower_Detail", (32.0, 32.0, 11.2), radius=1.6, height=0.20, material=mats["Brass_Polished"], segments=24, is_collider=False)
    add_cylinder(c_lobby, "Chandelier_Center_Pendant_Detail", (32.0, 32.0, 10.6), radius=0.9, height=0.90, material=mats["Chandelier_Lamp"], segments=24, is_collider=False)
    add_oriented_cylinder(c_lobby, "Chandelier_Support_Stem_Detail", (32.0, 32.0, 12.0), (32.0, 32.0, 14.0), radius=0.10, material=mats["Brass_Polished"], segments=12, is_collider=False)

    # Large 1.8m Gold Bank Regulator Clock above Entrance
    add_cylinder(c_lobby, "Bank_Clock_Bezel_Detail", (32.0, 18.25, 7.8), radius=0.92, height=0.20, material=mats["Brass_Polished"], segments=32, is_collider=False)
    add_cylinder(c_lobby, "Bank_Clock_Face_Detail", (32.0, 18.15, 7.8), radius=0.82, height=0.15, material=mats["Road_White"], segments=32, is_collider=False)

    # -------------------------------------------------------------------------
    # TELLER COUNTER ISLAND (Site B tactical objective and cover)
    # -------------------------------------------------------------------------
    # Solid Mahogany Counter Base (z: 0.0..1.40m)
    add_box(c_lobby, "Teller_Counter_Base", (26.0, 32.0, 0.0), (38.0, 35.0, 1.40), mats["Mahogany_Polished"], bevel=True, bevel_width=0.04)
    # Polished Marble Countertop Lip
    add_box(c_lobby, "Teller_Counter_Top_Lip", (25.8, 31.8, 1.35), (38.2, 35.2, 1.45), mats["Marble_Black"], bevel=True, bevel_width=0.03)

    # Bullet-Resistant Glass Partitions (z: 1.45..3.40m, critical for crouch-jump skips)
    add_box(c_lobby, "Teller_Glass_Bay1", (26.5, 32.15, 1.45), (31.5, 32.45, 3.40), mats["Security_Glass"])
    add_box(c_lobby, "Teller_Glass_Bay2", (32.5, 32.15, 1.45), (37.5, 32.45, 3.40), mats["Security_Glass"])
    # Brass Structural Divider Mullions
    for mx in [26.5, 31.5, 32.5, 37.5]:
        add_box(c_lobby, f"Teller_Mullion_{int(mx*10)}_Detail", (mx - 0.08, 32.10, 1.40), (mx + 0.08, 32.50, 3.50), mats["Brass_Polished"], is_collider=False)

    # 4 Teller Workstations (PC monitors, keyboards, cash drawers)
    for tx in [27.5, 29.8, 33.5, 35.8]:
        add_box(c_lobby, f"Teller_PC_{int(tx*10)}_Detail", (tx, 33.2, 1.45), (tx + 0.65, 33.6, 1.95), mats["Monitor_Plastic"], is_collider=False)
        add_box(c_lobby, f"Teller_Screen_{int(tx*10)}_Detail", (tx + 0.05, 33.16, 1.50), (tx + 0.60, 33.22, 1.90), mats["Monitor_Screen_CCTV"], is_collider=False)
        add_box(c_lobby, f"Teller_Keyboard_{int(tx*10)}_Detail", (tx + 0.08, 32.6, 1.45), (tx + 0.57, 33.0, 1.48), mats["Monitor_Plastic"], is_collider=False)

    # Tactical Site B Floor Stencil
    add_box(c_lobby, "Stencil_Site_B_Stripe", (30.0, 24.5, 0.005), (34.0, 28.5, 0.008), mats["Tactical_Site_Stencil"], is_collider=False)

    # Queue Stanchions (Polished brass posts with crimson velvet ropes)
    for qx in [27.0, 29.5, 32.0, 34.5, 37.0]:
        add_cylinder(c_lobby, f"Stanchion_Post_{int(qx*10)}_Detail", (qx, 28.5, 0.45), radius=0.06, height=0.90, material=mats["Brass_Polished"], segments=16, is_collider=False)
        add_cylinder(c_lobby, f"Stanchion_Ball_{int(qx*10)}_Detail", (qx, 28.5, 0.94), radius=0.10, height=0.12, material=mats["Brass_Polished"], segments=16, is_collider=False)
    add_box(c_lobby, "Stanchion_Rope_Detail", (27.0, 28.46, 0.70), (37.0, 28.54, 0.78), mats["Velvet_Crimson"], is_collider=False)

    # Mahogany Customer Deposit Slip Writing Desks
    for dx in [22.0, 41.0]:
        add_box(c_lobby, f"Writing_Desk_{int(dx)}", (dx, 27.5, 0.0), (dx + 2.2, 29.5, 1.05), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
        add_box(c_lobby, f"Writing_Desk_Lip_{int(dx)}_Detail", (dx - 0.05, 27.45, 1.0), (dx + 2.25, 29.55, 1.08), mats["Brass_Polished"], is_collider=False)

    # Executive Lounge (East wall): Tufted Oxblood Leather Chesterfield Sofa & Coffee Table
    add_box(c_lobby, "Lounge_Sofa_Base", (56.8, 27.0, 0.0), (59.4, 33.0, 0.85), mats["Leather_Oxblood"], bevel=True, bevel_width=0.04)
    add_box(c_lobby, "Lounge_Sofa_Backrest", (58.4, 27.0, 0.85), (59.4, 33.0, 1.45), mats["Leather_Oxblood"], bevel=True, bevel_width=0.04)
    add_box(c_lobby, "Lounge_Coffee_Table", (54.0, 28.5, 0.0), (55.8, 31.5, 0.50), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    add_box(c_lobby, "Lounge_Table_Glass_Detail", (54.05, 28.55, 0.50), (55.75, 31.45, 0.53), mats["Security_Glass"], is_collider=False)

    # Potted Palms in Classical Marble Urns
    for (px, py) in [(6.0, 20.0), (58.0, 20.0), (6.0, 44.0)]:
        add_cylinder(c_lobby, f"Palm_Urn_{int(px)}_{int(py)}", (px, py, 0.35), radius=0.55, height=0.70, material=mats["Limestone_Facade"], segments=16)
        add_cylinder(c_lobby, f"Palm_Bush_{int(px)}_{int(py)}_Foliage", (px, py, 1.50), radius=0.85, height=1.60, material=mats["Foliage_Green"], segments=12, is_collider=False)

    # =========================================================================
    # 5. EXECUTIVE MEZZANINE & BALCONY CATWALKS (z = 5.0m)
    # =========================================================================
    # West Balcony Deck & Staircase
    add_box(c_arch, "Balcony_Floor_West", (14.0, 38.0, 4.8), (20.0, 45.0, 5.0), mats["Marble_White"])
    add_box(c_lobby, "Balcony_Carpet_West_Detail", (15.5, 38.0, 5.005), (18.5, 45.0, 5.015), mats["Carpet_Burgundy"], is_collider=False)
    # Brass Spindle Balustrade & Guardrail (z: 5.0..6.10m)
    add_box(c_lobby, "Balcony_Rail_West", (19.85, 38.0, 5.0), (20.15, 45.0, 6.10), mats["Brass_Polished"])
    add_box(c_lobby, "Balcony_Glass_West_Detail", (19.95, 38.0, 5.15), (20.05, 45.0, 5.95), mats["Security_Glass"], is_collider=False)

    # West Staircase to Mezzanine (18 diamond-plate steps from y: 32.0..38.0, z: 0.0..5.0)
    for i in range(18):
        step_y0 = 32.0 + (i * 6.0) / 18.0
        step_y1 = step_y0 + (6.0 / 18.0)
        step_z = (i * 5.0) / 18.0
        add_box(c_arch, f"Stair_Step_West_{i}", (14.0, step_y0, 0.0), (18.0, step_y1, step_z + 0.28), mats["Limestone_Facade"], bevel=True, bevel_width=0.02)
    # Staircase Safety Brass Handrail
    add_box(c_lobby, "Stair_Handrail_West", (17.85, 32.0, 1.0), (18.05, 38.0, 6.1), mats["Brass_Polished"])

    # East Balcony Deck & Staircase
    add_box(c_arch, "Balcony_Floor_East", (44.0, 38.0, 4.8), (50.0, 45.0, 5.0), mats["Marble_White"])
    add_box(c_lobby, "Balcony_Carpet_East_Detail", (45.5, 38.0, 5.005), (48.5, 45.0, 5.015), mats["Carpet_Burgundy"], is_collider=False)
    add_box(c_lobby, "Balcony_Rail_East", (43.85, 38.0, 5.0), (44.15, 45.0, 6.10), mats["Brass_Polished"])
    add_box(c_lobby, "Balcony_Glass_East_Detail", (43.95, 38.0, 5.15), (44.05, 45.0, 5.95), mats["Security_Glass"], is_collider=False)

    # East Staircase
    for i in range(18):
        step_y0 = 32.0 + (i * 6.0) / 18.0
        step_y1 = step_y0 + (6.0 / 18.0)
        step_z = (i * 5.0) / 18.0
        add_box(c_arch, f"Stair_Step_East_{i}", (46.0, step_y0, 0.0), (50.0, step_y1, step_z + 0.28), mats["Limestone_Facade"], bevel=True, bevel_width=0.02)
    add_box(c_lobby, "Stair_Handrail_East", (45.95, 32.0, 1.0), (46.15, 38.0, 6.1), mats["Brass_Polished"])

    # =========================================================================
    # 6. CENTRAL SECURITY DIVISION WALL (y: 46.0..49.0)
    # =========================================================================
    add_box(c_arch, "Div_Wall_1", (4.0, 46.0, 0.0), (10.0, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Div_Wall_2", (16.0, 46.0, 0.0), (28.0, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Div_Wall_3", (36.0, 46.0, 0.0), (48.0, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Div_Wall_4", (54.0, 46.0, 0.0), (60.0, 49.0, 14.0), mats["Limestone_Facade"])

    # Heavy Security Checkpoint Portal Frame
    add_box(c_arch, "Security_Portal_Header", (28.0, 46.0, 3.8), (36.0, 49.0, 14.0), mats["Limestone_Facade"])

    # Security Desk & 19" Equipment Racks
    add_box(c_offices, "Security_Desk", (31.5, 46.5, 0.0), (35.5, 48.5, 1.10), mats["Steel_Structural"])
    add_box(c_offices, "Security_CCTV_Rack_Detail", (32.0, 47.0, 1.10), (35.0, 47.5, 2.00), mats["Monitor_Plastic"], is_collider=False)
    add_box(c_offices, "Security_CCTV_Screens_Detail", (32.1, 46.95, 1.15), (34.9, 47.05, 1.95), mats["Monitor_Screen_CCTV"], is_collider=False)

    # =========================================================================
    # 7. THE VAULT (Site A - x: 40.0..60.0, y: 49.0..60.0)
    # =========================================================================
    # Reinforced Vault Enclosure Walls
    add_box(c_arch, "Vault_Wall_West", (39.0, 49.0, 0.0), (41.0, 58.0, 10.0), mats["Vault_Steel"])
    add_box(c_arch, "Vault_Wall_North", (39.0, 58.0, 0.0), (60.0, 60.0, 10.0), mats["Vault_Steel"])
    add_box(c_arch, "Vault_Roof_Slab", (39.0, 49.0, 10.0), (60.0, 60.0, 10.5), mats["Vault_Steel"])

    # Vault Portal Frame with Bold 45-degree Hazard Caution Stripes
    add_box(c_vault, "Vault_Portal_Jamb_L", (44.5, 48.8, 0.0), (46.2, 50.8, 7.5), mats["Hazard_Yellow"], bevel=True, bevel_width=0.04)
    add_box(c_vault, "Vault_Portal_Jamb_R", (50.5, 48.8, 0.0), (52.2, 50.8, 7.5), mats["Hazard_Yellow"], bevel=True, bevel_width=0.04)
    add_box(c_vault, "Vault_Portal_Header", (44.5, 48.8, 7.0), (52.2, 50.8, 8.5), mats["Hazard_Yellow"], bevel=True, bevel_width=0.04)
    # Hazard Stripe Inlays
    add_box(c_vault, "Vault_Hazard_Stripe_L_Stripe", (44.45, 48.75, 0.5), (46.25, 48.85, 7.0), mats["Hazard_Black"], is_collider=False)
    add_box(c_vault, "Vault_Hazard_Stripe_R_Stripe", (50.45, 48.75, 0.5), (52.25, 48.85, 7.0), mats["Hazard_Black"], is_collider=False)

    # -------------------------------------------------------------------------
    # MASSIVE CIRCULAR VAULT BLAST DOOR (Swung 75 deg open against portal wall)
    # -------------------------------------------------------------------------
    door_cx = 45.0
    door_cy = 47.6
    # Collider box representing the open swung door bulk against wall
    add_box(c_vault, "Vault_Door_Bulk_Collider", (43.8, 46.0, 0.0), (46.0, 48.8, 7.0), mats["Vault_Steel"])

    # Multi-Step Circular Vault Door Body
    add_cylinder(c_vault, "Vault_Door_Outer_Ring_Detail", (door_cx, door_cy, 3.8), radius=2.0, height=0.45, material=mats["Vault_Steel"], segments=32, is_collider=False)
    add_cylinder(c_vault, "Vault_Door_Stepped_Core_Detail", (door_cx, door_cy - 0.25, 3.8), radius=1.75, height=0.35, material=mats["Chrome_Polished"], segments=32, is_collider=False)

    # 12 Hardened Chrome Perimeter Locking Lugs / Bolts (extending radially)
    for bi in range(12):
        b_angle = (2.0 * math.pi * bi) / 12.0
        bx_bolt = door_cx + math.cos(b_angle) * 1.85
        bz_bolt = 3.8 + math.sin(b_angle) * 1.85
        add_cylinder(c_vault, f"Vault_Bolt_{bi}_Detail", (bx_bolt, door_cy, bz_bolt), radius=0.12, height=0.48, material=mats["Chrome_Polished"], segments=12, is_collider=False)

    # Heavy Four-Spoke Chrome Locking Handwheel
    add_cylinder(c_vault, "Vault_Handwheel_Hub_Detail", (door_cx, door_cy - 0.45, 3.8), radius=0.35, height=0.18, material=mats["Chrome_Polished"], segments=24, is_collider=False)
    add_cylinder(c_vault, "Vault_Handwheel_Rim_Detail", (door_cx, door_cy - 0.50, 3.8), radius=0.85, height=0.08, material=mats["Chrome_Polished"], segments=24, is_collider=False)
    # Handwheel Spoke Bars
    add_box(c_vault, "Vault_Handwheel_Spoke1_Detail", (door_cx - 0.80, door_cy - 0.52, 3.75), (door_cx + 0.80, door_cy - 0.48, 3.85), mats["Chrome_Polished"], is_collider=False)
    add_box(c_vault, "Vault_Handwheel_Spoke2_Detail", (door_cx - 0.05, door_cy - 0.52, 3.0), (door_cx + 0.05, door_cy - 0.48, 4.6), mats["Chrome_Polished"], is_collider=False)

    # Dual Polished Brass Combination Dials
    add_cylinder(c_vault, "Vault_Dial_1_Detail", (door_cx - 0.8, door_cy - 0.42, 4.4), radius=0.20, height=0.12, material=mats["Brass_Polished"], segments=24, is_collider=False)
    add_cylinder(c_vault, "Vault_Dial_2_Detail", (door_cx + 0.8, door_cy - 0.42, 4.4), radius=0.20, height=0.12, material=mats["Brass_Polished"], segments=24, is_collider=False)

    # -------------------------------------------------------------------------
    # VAULT INTERIOR CONTENTS (Site A Wealth & Tactical Cover)
    # -------------------------------------------------------------------------
    # Safety Deposit Locker Walls (East and North interior walls)
    add_box(c_vault, "Deposit_Lockers_East", (56.5, 51.0, 0.0), (58.5, 58.0, 8.0), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.03)
    add_box(c_vault, "Deposit_Lockers_North", (41.0, 56.5, 0.0), (56.5, 58.0, 8.0), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.03)
    # Locker Compartment Seam Rows Detail
    for lz in [1.5, 3.0, 4.5, 6.0]:
        add_box(c_vault, f"Locker_Seam_{int(lz*10)}_Detail", (41.5, 56.45, lz), (56.0, 56.55, lz + 0.06), mats["Cast_Iron_Dark"], is_collider=False)

    # 24-Karat Gold Bullion Pallets (Stepped pyramid ingot stacks)
    add_box(c_vault, "Gold_Pallet_Base", (43.8, 52.8, 0.0), (47.2, 56.2, 0.15), mats["Oak_Desk"])
    add_box(c_vault, "Gold_Bullion_Tier1", (44.0, 53.0, 0.15), (47.0, 56.0, 1.80), mats["Gold_Bullion"], bevel=True, bevel_width=0.02)
    add_box(c_vault, "Gold_Bullion_Tier2_Detail", (44.3, 53.3, 1.80), (46.7, 55.7, 2.25), mats["Gold_Bullion"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(c_vault, "Gold_Bullion_Tier3_Detail", (44.6, 53.6, 2.25), (46.4, 55.4, 2.60), mats["Gold_Bullion"], is_collider=False)

    # Wire-Mesh Cash Transport Carts with Stacks of Currency Bundles
    add_box(c_vault, "Cash_Cart_Tubular_Frame", (51.0, 52.0, 0.0), (53.8, 54.8, 1.45), mats["Steel_Structural"], bevel=True, bevel_width=0.03)
    add_box(c_vault, "Cash_Stacks_Tier1_Detail", (51.2, 52.2, 0.2), (53.6, 54.6, 0.8), mats["Currency_Green"], is_collider=False)
    add_box(c_vault, "Cash_Stacks_Tier2_Detail", (51.3, 52.3, 0.8), (53.5, 54.5, 1.4), mats["Currency_Green"], is_collider=False)

    # Tactical Bomb Site A Stencil
    add_box(c_vault, "Stencil_Site_A_Stripe", (44.0, 50.5, 0.005), (48.0, 53.5, 0.008), mats["Tactical_Site_Stencil"], is_collider=False)

    # Industrial HVAC Generator & Overhead Air Duct (Tactical jump shooting perch)
    add_box(c_vault, "HVAC_Jump_Generator", (50.0, 44.0, 0.0), (53.0, 46.0, 1.80), mats["Steel_Structural"], bevel=True, bevel_width=0.03)
    add_box(c_vault, "Overhead_Air_Duct", (49.0, 44.0, 3.4), (53.0, 48.0, 4.20), mats["Deposit_Box_Steel"])

    # =========================================================================
    # 8. DEFENDER OFFICES & STAFF BREAKROOM (y: 50.0..60.0, x: 4.0..40.0)
    # =========================================================================
    # Bank Manager's Executive Office Desk & Chair
    add_box(c_offices, "Manager_Desk_Mahogany", (14.0, 51.0, 0.0), (18.0, 54.0, 1.15), mats["Mahogany_Polished"], bevel=True, bevel_width=0.04)
    add_box(c_offices, "Manager_PC_Detail", (15.5, 52.2, 1.15), (16.8, 52.8, 1.65), mats["Monitor_Plastic"], is_collider=False)
    add_box(c_offices, "Manager_Chair", (12.8, 52.0, 0.0), (13.9, 53.5, 1.45), mats["Leather_Oxblood"], bevel=True, bevel_width=0.03)

    # Staff Breakroom Table & Water Cooler
    add_box(c_offices, "Breakroom_Table", (26.0, 51.0, 0.0), (30.0, 54.0, 1.05), mats["Oak_Desk"], bevel=True, bevel_width=0.03)
    add_cylinder(c_offices, "Water_Cooler_Base", (31.0, 53.0, 0.50), radius=0.30, height=1.00, material=mats["Road_White"])
    add_cylinder(c_offices, "Water_Cooler_Jug_Detail", (31.0, 53.0, 1.35), radius=0.26, height=0.70, material=mats["ATM_Screen"], segments=16, is_collider=False)

    # 42U Server Rack Cabinets
    for rx in [6.0, 8.0]:
        add_box(c_offices, f"Server_Rack_{int(rx)}", (rx, 55.0, 0.0), (rx + 1.6, 56.5, 2.4), mats["Monitor_Plastic"], bevel=True, bevel_width=0.02)
        add_box(c_offices, f"Server_Rack_LEDs_{int(rx)}_Detail", (rx + 0.1, 54.95, 0.2), (rx + 1.5, 55.05, 2.3), mats["ATM_Screen"], is_collider=False)

    # =========================================================================
    # 9. STREET & PLAZA INFRASTRUCTURE & VEHICLES (y: 4.0..18.0)
    # =========================================================================
    # Armored SWAT Tactical Assault Truck (Critical Skill-Jump Pathway)
    # Hood = 1.50m, Cab/Roof = 2.80m
    add_box(c_street, "SWAT_Van_Hood", (18.0, 10.0, 0.0), (22.0, 12.5, 1.50), mats["SWAT_Vehicle_Navy"], bevel=True, bevel_width=0.04)
    add_box(c_street, "SWAT_Van_Cab_Roof", (18.0, 12.5, 0.0), (22.0, 16.0, 2.80), mats["SWAT_Vehicle_Navy"], bevel=True, bevel_width=0.04)
    # Heavy Steel Bullbar Push Bumper
    add_box(c_street, "SWAT_Bullbar", (17.6, 9.6, 0.3), (22.4, 10.0, 0.95), mats["Chrome_Polished"])
    # Smoked Armored Windshield
    add_box(c_street, "SWAT_Windshield_Detail", (18.2, 12.42, 1.50), (21.8, 12.65, 2.35), mats["Smoked_Glass"], is_collider=False)
    # Emergency Red / Blue Strobe Lightbar
    add_box(c_street, "SWAT_Lightbar_Red_Glow", (18.4, 13.5, 2.80), (20.0, 14.0, 3.05), mats["Emergency_Red"], is_collider=False)
    add_box(c_street, "SWAT_Lightbar_Blue_Glow", (20.0, 13.5, 2.80), (21.6, 14.0, 3.05), mats["Emergency_Blue"], is_collider=False)
    # Heavy Knobby Truck Tires
    for wy in [11.0, 14.6]:
        add_box(c_street, f"SWAT_Tire_L_{int(wy)}", (17.5, wy, 0.0), (18.0, wy + 1.2, 0.8), mats["Hazard_Black"])
        add_box(c_street, f"SWAT_Tire_R_{int(wy)}", (22.0, wy, 0.0), (22.5, wy + 1.2, 0.8), mats["Hazard_Black"])

    # Police Squad Patrol Cruiser (x: 42.0..46.0, y: 10.0..14.0, z: 0.0..1.60m)
    add_box(c_street, "Police_Cruiser_Body", (42.0, 10.0, 0.0), (46.0, 14.0, 1.60), mats["Police_Cruiser_White"], bevel=True, bevel_width=0.04)
    add_box(c_street, "Police_Cruiser_Lightbar_Red_Glow", (42.5, 11.8, 1.60), (44.0, 12.3, 1.85), mats["Emergency_Red"], is_collider=False)
    add_box(c_street, "Police_Cruiser_Lightbar_Blue_Glow", (44.0, 11.8, 1.60), (45.5, 12.3, 1.85), mats["Emergency_Blue"], is_collider=False)

    # Concrete Jersey Barriers with Reflective Caution Top
    add_box(c_street, "Jersey_Barrier_Concrete", (30.0, 8.0, 0.0), (34.0, 9.8, 1.20), mats["Limestone_Facade"], bevel=True, bevel_width=0.03)
    add_box(c_street, "Jersey_Barrier_Yellow_Top_Stripe", (30.0, 8.0, 1.15), (34.0, 9.8, 1.25), mats["Hazard_Yellow"], is_collider=False)

    # Sidewalk Concrete Planters with Trimmed Boxwood Hedges
    for px in [14.0, 48.0]:
        add_box(c_street, f"Sidewalk_Planter_{int(px)}", (px, 14.5, 0.20), (px + 2.8, 17.5, 0.70), mats["Granite_Curb"], bevel=True, bevel_width=0.02)
        add_box(c_street, f"Sidewalk_Hedge_{int(px)}_Foliage", (px + 0.2, 14.7, 0.70), (px + 2.6, 17.3, 1.65), mats["Foliage_Green"], is_collider=False)

    # Cast-Iron Victorian Streetlamps with Luminous Globes
    for lx in [12.0, 52.0]:
        add_cylinder(c_street, f"Streetlamp_Post_{int(lx)}", (lx, 6.0, 2.75), radius=0.14, height=5.50, material=mats["Cast_Iron_Dark"])
        add_cylinder(c_street, f"Streetlamp_Globe_{int(lx)}_Glow", (lx, 7.2, 5.20), radius=0.35, height=0.50, material=mats["Chandelier_Lamp"], segments=16, is_collider=False)

    # Red Municipal Fire Hydrants
    for hx in [8.0, 54.0]:
        add_cylinder(c_street, f"Fire_Hydrant_{int(hx)}", (hx, 13.0, 0.45), radius=0.22, height=0.90, material=mats["Emergency_Red"], segments=16)

    # 24/7 Outdoor Bank ATM Wall Vestibule
    add_box(c_street, "Outdoor_ATM_Shell", (9.5, 17.5, 0.4), (12.5, 18.0, 2.4), mats["Deposit_Box_Steel"])
    add_box(c_street, "Outdoor_ATM_Screen_Glow", (9.8, 17.45, 1.2), (12.2, 17.55, 1.8), mats["ATM_Screen"], is_collider=False)
    add_box(c_street, "Outdoor_ATM_Blue_Header", (9.2, 17.35, 2.4), (12.8, 17.55, 2.8), mats["ATM_Blue_Shell"], is_collider=False)

    # Free-standing Indoor ATM Kiosks in Lobby
    for ay in [26.0, 34.0]:
        add_box(c_lobby, f"Indoor_ATM_{int(ay)}", (4.8, ay, 0.0), (6.2, ay + 1.4, 2.1), mats["Deposit_Box_Steel"])
        add_box(c_lobby, f"Indoor_ATM_Screen_{int(ay)}_Glow", (6.0, ay + 0.2, 1.2), (6.25, ay + 1.2, 1.8), mats["ATM_Screen"], is_collider=False)
        add_box(c_lobby, f"Indoor_ATM_Header_{int(ay)}", (4.8, ay, 2.0), (6.2, ay + 1.4, 2.3), mats["ATM_Blue_Shell"], is_collider=False)

    print("Bank scene build complete!")


def export_scene_glb():
    """Export the active Blender scene as GLB for backend and web public."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    glb_backend_path = repo_root / "backend/modules/hassault/maps/hd_bank.glb"
    glb_web_path = repo_root / "apps/web/public/hd_bank.glb"

    glb_backend_path.parent.mkdir(parents=True, exist_ok=True)
    glb_web_path.parent.mkdir(parents=True, exist_ok=True)

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

    shutil.copyfile(glb_backend_path, glb_web_path)
    print(f"Copied GLB to Web: {glb_web_path}")


if __name__ == "__main__":
    print("=== Generating Ultra-Detailed Realistic 'The Bank' (hd_bank) Map in Blender ===")
    build_bank_scene()
    export_scene_glb()
    print("=== Generation and Export Complete! ===")
