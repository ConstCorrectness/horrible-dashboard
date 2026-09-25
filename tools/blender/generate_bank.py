#!/usr/bin/env python3
"""
The Bank (hd_bank) Photorealistic Tactical Map Generator for Blender 4.2+
Natively 1:1 metric scale on 56m x 56m arena footprint (bounds: 4.0..60.0).

Hyper-Realistic Architectural & Prop Engineering:
- Neoclassical Colonnade & Downtown Streetscape:
  Monhattan financial district facade with 4 monumental fluted columns, Doric entablature,
  tympanum with high-relief carved crest medallion, acroterion urns on stone pedestals,
  entrance canopy for tactical skill-jumps, and propped-open mahogany double doors with brass panic bars.
  Across the street: 3-story brick/limestone commercial buildings with storefront windows, fabric awnings,
  and modillion cornices under an open daylight sky!
- Grand Banking Hall (Site B):
  Tessellated Italian Carrara marble floor with Nero Marquina border inlays, 8-pointed star compass rose mosaic,
  finished coffered plaster ceiling with Beaux-Arts multi-tier brass chandelier and glowing frosted lantern globes,
  rich mahogany wainscoting, fluted wall pilasters, framed historical oil paintings with gilded leaf frames,
  twin-arm torchère wall sconces, analog bank clock, and ornamental cast-iron stair balustrades with mahogany handrails.
  Serpentine VIP queue line with turned brass stanchions and sagging crimson velvet catenary ropes.
- Teller Service Island:
  Polished brass architectural cage with 5 fluted pillars, 4 arched customer wickets, station nameplates ("TELLER 1..4"),
  stainless-steel speaking rosettes, recessed chrome dip scoops, and vertical brass spindle bars (85% see-through)
  revealing all 4 teller workstations (glowing CCTV monitors, keyboards, cash-counting machines with LED displays, banded cash bundles).
- The Vault (Site A):
  Massive 3.2m circular steel blast door swung 70° open against the portal wall with stepped sealing rings,
  16 radial chrome locking lugs with bronze guide bushings, dual crane-neck articulated hinge arms,
  center 4-spoke chrome marine handwheel with knurled grips, dual Sargent & Greenleaf brass combination dials,
  Swiss triple-movement mechanical time-lock case, and yellow/black hazard portal frame.
  Reinforced vault ceiling at z=8m with industrial steel conduit and yellow explosion-proof vapor-tight bulkhead lights.
- Fort Knox Gold Bullion Pallet & Cash Carts:
  Heavy oak cargo pallet with runner stringers and deckboards, dense stack of thousands of authentic chamfered
  trapezoidal 24k gold Good Delivery ingots covering ALL 4 VERTICAL SIDES and top surface with brick-bonded rows,
  horizontal wooden dunnage separator battens, high-tension galvanized steel strapping bands with tension buckles,
  stepped pyramid upper tiers, loose angled gold bars, and adjacent tubular wire cash carts packed with banded currency bundles ($10,000 straps).
- Vehicles & Street Infrastructure:
  High-detail SWAT armored tactical truck with angular hood, push-bumper winch with braided steel drum and D-ring shackles,
  recessed headlights, armored windshield, roof turret cupola, modern low-profile aerodynamic LED emergency lightbar,
  curved armored wheel flares, and treaded heavy wheels; white municipal police cruiser with push bumper, spotlight,
  and low-profile lightbar; 24/7 outdoor ATM vestibule with recessed screen and keypad; cast-iron Victorian streetlamps,
  municipal fire hydrants, concrete planters with boxwood hedges, and granite curbs.
- Defender Offices & Breakroom:
  Acoustic drop-ceiling tile grid at z=5.2m with recessed 2x4 fluorescent troffers closing the black void,
  mahogany wainscoting, executive partner desk with leather blotter, PC terminal, authentic emerald green glass Banker's Desk Lamp
  with brass gooseneck and pull chain, high-back tufted leather executive swivel chair on 5-star chrome spider caster base,
  sculpted water cooler with translucent blue ribbed 5-gal jug and hot/cold spigots, breakroom kitchenette with coffee maker and microwave,
  and 42U server equipment racks with blinking green and amber activity LEDs.
"""

import sys
import os
import math

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: This script must be run inside Blender: blender -b -P generate_bank.py")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maplib  # noqa: E402  (a sibling, found via the path above)


def clear_scene():
    """Clear all objects, meshes, materials, and collections from the scene."""
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
    """Create all high-fidelity PBR materials for The Bank."""
    mats = {}

    # Stone, Marble & Masonry
    mats["Marble_White"] = create_pbr_material("Mat_Marble_White", (0.88, 0.88, 0.86), metallic=0.04, roughness=0.16)
    mats["Marble_Black"] = create_pbr_material("Mat_Marble_Black", (0.12, 0.13, 0.15), metallic=0.06, roughness=0.18)
    mats["Marble_Cream"] = create_pbr_material("Mat_Marble_Cream", (0.84, 0.80, 0.72), metallic=0.04, roughness=0.20)
    mats["Marble_Column"] = create_pbr_material("Mat_Marble_Column", (0.90, 0.89, 0.87), metallic=0.03, roughness=0.20)
    mats["Limestone_Facade"] = create_pbr_material("Mat_Limestone_Facade", (0.76, 0.73, 0.67), metallic=0.02, roughness=0.65)
    mats["Limestone_Dark"] = create_pbr_material("Mat_Limestone_Dark", (0.58, 0.55, 0.50), metallic=0.02, roughness=0.70)
    mats["Concrete_Sidewalk"] = create_pbr_material("Mat_Concrete_Sidewalk", (0.60, 0.58, 0.55), metallic=0.0, roughness=0.85)
    mats["Granite_Curb"] = create_pbr_material("Mat_Granite_Curb", (0.42, 0.40, 0.38), metallic=0.05, roughness=0.70)
    mats["Asphalt_Street"] = create_pbr_material("Mat_Asphalt_Street", (0.16, 0.16, 0.18), metallic=0.02, roughness=0.88)
    mats["Perimeter_Concrete"] = create_pbr_material("Mat_Perimeter_Concrete", (0.28, 0.29, 0.32), metallic=0.05, roughness=0.80)
    mats["Plaster_Ceiling"] = create_pbr_material("Mat_Plaster_Ceiling", (0.92, 0.91, 0.88), metallic=0.0, roughness=0.75)
    mats["Brick_Commercial_Red"] = create_pbr_material("Mat_Brick_Commercial_Red", (0.52, 0.25, 0.18), metallic=0.02, roughness=0.88)
    mats["Storefront_Bronze"] = create_pbr_material("Mat_Storefront_Bronze", (0.22, 0.18, 0.14), metallic=0.78, roughness=0.32)
    mats["Awning_Canvas_Green"] = create_pbr_material("Mat_Awning_Canvas_Green", (0.12, 0.32, 0.18), metallic=0.0, roughness=0.85)
    mats["Awning_Canvas_Burgundy"] = create_pbr_material("Mat_Awning_Canvas_Burgundy", (0.42, 0.10, 0.14), metallic=0.0, roughness=0.85)

    # Road & Tactical Markings
    mats["Road_Yellow"] = create_pbr_material("Mat_Road_Yellow_Stripe", (0.94, 0.80, 0.10), metallic=0.05, roughness=0.55)
    mats["Road_White"] = create_pbr_material("Mat_Road_White_Stripe", (0.92, 0.92, 0.94), metallic=0.05, roughness=0.55)

    # Metals & Vault Steel
    mats["Vault_Steel"] = create_pbr_material("Mat_Vault_Steel", (0.30, 0.32, 0.36), metallic=0.80, roughness=0.32)
    mats["Vault_Steel_Dark"] = create_pbr_material("Mat_Vault_Steel_Dark", (0.18, 0.20, 0.22), metallic=0.85, roughness=0.28)
    mats["Chrome_Polished"] = create_pbr_material("Mat_Chrome_Polished", (0.92, 0.93, 0.95), metallic=0.98, roughness=0.06)
    mats["Deposit_Box_Steel"] = create_pbr_material("Mat_Deposit_Box_Steel", (0.65, 0.68, 0.72), metallic=0.88, roughness=0.22)
    mats["Brass_Polished"] = create_pbr_material("Mat_Brass_Polished", (0.92, 0.78, 0.28), metallic=0.88, roughness=0.20)
    mats["Bronze_Architectural"] = create_pbr_material("Mat_Bronze_Architectural", (0.45, 0.34, 0.20), metallic=0.82, roughness=0.30)
    mats["Steel_Structural"] = create_pbr_material("Mat_Steel_Structural", (0.32, 0.34, 0.38), metallic=0.70, roughness=0.42)
    mats["Cast_Iron_Dark"] = create_pbr_material("Mat_Cast_Iron_Dark", (0.14, 0.14, 0.16), metallic=0.60, roughness=0.68)
    mats["Bronze_Bushing"] = create_pbr_material("Mat_Bronze_Bushing", (0.75, 0.55, 0.25), metallic=0.85, roughness=0.25)
    mats["Picture_Frame_Gold"] = create_pbr_material("Mat_Picture_Frame_Gold", (0.86, 0.72, 0.22), metallic=0.90, roughness=0.18)
    mats["Steel_Banding_Strap"] = create_pbr_material("Mat_Steel_Banding_Strap", (0.85, 0.86, 0.88), metallic=0.95, roughness=0.12)

    # Wealth: 24k Gold & Paper Currency
    mats["Gold_Bullion"] = create_pbr_material("Mat_Gold_Bullion", (0.98, 0.82, 0.18), metallic=0.95, roughness=0.12)
    mats["Gold_Bullion_Dark"] = create_pbr_material("Mat_Gold_Bullion_Dark", (0.86, 0.70, 0.14), metallic=0.92, roughness=0.18)
    mats["Currency_Green"] = create_pbr_material("Mat_Currency_Green", (0.24, 0.52, 0.28), metallic=0.0, roughness=0.72)
    mats["Currency_Band"] = create_pbr_material("Mat_Currency_Band", (0.92, 0.88, 0.78), metallic=0.0, roughness=0.65)
    mats["Pallet_Wood"] = create_pbr_material("Mat_Pallet_Wood", (0.52, 0.40, 0.26), metallic=0.0, roughness=0.82)
    mats["Painting_Canvas_1"] = create_pbr_material("Mat_Painting_Canvas_1", (0.32, 0.22, 0.16), metallic=0.0, roughness=0.85)
    mats["Painting_Canvas_2"] = create_pbr_material("Mat_Painting_Canvas_2", (0.18, 0.28, 0.36), metallic=0.0, roughness=0.85)
    mats["Painting_Canvas_3"] = create_pbr_material("Mat_Painting_Canvas_3", (0.40, 0.32, 0.20), metallic=0.0, roughness=0.85)

    # Woods, Leather & Fabrics
    mats["Mahogany_Polished"] = create_pbr_material("Mat_Mahogany_Polished", (0.30, 0.14, 0.08), metallic=0.0, roughness=0.25)
    mats["Mahogany_Dark"] = create_pbr_material("Mat_Mahogany_Dark", (0.20, 0.09, 0.05), metallic=0.0, roughness=0.30)
    mats["Oak_Desk"] = create_pbr_material("Mat_Oak_Desk", (0.42, 0.28, 0.16), metallic=0.0, roughness=0.55)
    mats["Leather_Oxblood"] = create_pbr_material("Mat_Leather_Oxblood", (0.38, 0.08, 0.10), metallic=0.04, roughness=0.38)
    mats["Leather_Black"] = create_pbr_material("Mat_Leather_Black", (0.12, 0.12, 0.14), metallic=0.04, roughness=0.45)
    mats["Velvet_Crimson"] = create_pbr_material("Mat_Velvet_Crimson", (0.68, 0.06, 0.10), metallic=0.0, roughness=0.85)
    mats["Carpet_Burgundy"] = create_pbr_material("Mat_Carpet_Burgundy", (0.45, 0.10, 0.14), metallic=0.0, roughness=0.90)
    mats["Chesterfield_Leather"] = create_pbr_material("Mat_Chesterfield_Leather", (0.32, 0.08, 0.06), metallic=0.05, roughness=0.38)

    # Glass & Glazing
    mats["Security_Glass"] = create_pbr_material("Mat_Security_Glass", (0.80, 0.92, 0.96), transmission=0.90, roughness=0.04, alpha=0.30)
    mats["Smoked_Glass"] = create_pbr_material("Mat_Smoked_Glass", (0.18, 0.22, 0.26), transmission=0.78, roughness=0.08, alpha=0.55)
    mats["Green_Glass_Banker"] = create_pbr_material("Mat_Green_Glass_Banker", (0.10, 0.65, 0.25), transmission=0.85, roughness=0.10, alpha=0.45, emission_color=(0.1, 0.6, 0.2), emission_strength=1.5)
    mats["Water_Bottle_Blue"] = create_pbr_material("Mat_Water_Bottle_Blue", (0.30, 0.65, 0.95), transmission=0.85, roughness=0.08, alpha=0.40)

    # Electronics & Illuminations
    mats["Chandelier_Lamp"] = create_pbr_material("Mat_Chandelier_Lamp_Glow", (1.0, 0.96, 0.85), emission_color=(1.0, 0.94, 0.80), emission_strength=8.0)
    mats["Sconce_Lamp"] = create_pbr_material("Mat_Sconce_Lamp_Glow", (1.0, 0.92, 0.78), emission_color=(1.0, 0.90, 0.72), emission_strength=6.0)
    mats["Fluorescent_Troffer_Glow"] = create_pbr_material("Mat_Fluorescent_Troffer_Glow", (1.0, 0.98, 0.92), emission_color=(1.0, 0.98, 0.92), emission_strength=7.0)
    mats["Monitor_Plastic"] = create_pbr_material("Mat_Monitor_Plastic", (0.10, 0.11, 0.13), metallic=0.15, roughness=0.45)
    mats["Monitor_Screen_CCTV"] = create_pbr_material("Mat_Monitor_Screen_CCTV", (0.14, 0.38, 0.48), emission_color=(0.18, 0.45, 0.58), emission_strength=3.0)
    mats["ATM_Screen"] = create_pbr_material("Mat_ATM_Screen", (0.10, 0.68, 0.98), emission_color=(0.10, 0.68, 0.98), emission_strength=3.5)
    mats["ATM_Blue_Shell"] = create_pbr_material("Mat_ATM_Blue_Shell", (0.10, 0.35, 0.82), metallic=0.35, roughness=0.30)
    mats["LED_Green"] = create_pbr_material("Mat_LED_Green", (0.10, 0.95, 0.20), emission_color=(0.10, 0.95, 0.20), emission_strength=6.0)
    mats["LED_Amber"] = create_pbr_material("Mat_LED_Amber", (0.95, 0.65, 0.05), emission_color=(0.95, 0.65, 0.05), emission_strength=6.0)
    mats["LED_Blue"] = create_pbr_material("Mat_LED_Blue", (0.10, 0.45, 0.98), emission_color=(0.10, 0.45, 0.98), emission_strength=6.0)
    mats["Emergency_Red"] = create_pbr_material("Mat_Emergency_Red_Glow", (0.98, 0.08, 0.08), emission_color=(1.0, 0.08, 0.08), emission_strength=9.0)
    mats["Emergency_Blue"] = create_pbr_material("Mat_Emergency_Blue_Glow", (0.08, 0.35, 1.0), emission_color=(0.08, 0.35, 1.0), emission_strength=9.0)

    # Hazard & Tactical
    mats["Hazard_Yellow"] = create_pbr_material("Mat_Hazard_Yellow", (0.95, 0.82, 0.08), metallic=0.05, roughness=0.40)
    mats["Hazard_Black"] = create_pbr_material("Mat_Hazard_Black", (0.08, 0.08, 0.10), metallic=0.10, roughness=0.50)
    mats["Tactical_Site_Stencil"] = create_pbr_material("Mat_Tactical_Site_Stencil", (0.88, 0.18, 0.12), metallic=0.05, roughness=0.55)
    mats["Foliage_Green"] = create_pbr_material("Mat_Foliage_Green", (0.16, 0.42, 0.16), metallic=0.0, roughness=0.65)
    mats["SWAT_Vehicle_Navy"] = create_pbr_material("Mat_SWAT_Vehicle_Navy", (0.10, 0.12, 0.18), metallic=0.40, roughness=0.40)
    mats["Police_Cruiser_White"] = create_pbr_material("Mat_Police_Cruiser_White", (0.90, 0.90, 0.92), metallic=0.35, roughness=0.35)
    # Additional Architecture & Prop Materials
    mats["Aluminum_Brushed"] = create_pbr_material("Mat_Aluminum_Brushed", (0.84, 0.85, 0.88), metallic=0.80, roughness=0.22)
    mats["Tire_Rubber"] = create_pbr_material("Mat_Tire_Rubber", (0.07, 0.07, 0.08), metallic=0.02, roughness=0.92)
    mats["Amber_Reflector"] = create_pbr_material("Mat_Amber_Reflector", (0.95, 0.55, 0.05), metallic=0.10, roughness=0.25, emission_color=(0.95, 0.55, 0.05), emission_strength=4.0)
    mats["Cork_Bulletin"] = create_pbr_material("Mat_Cork_Bulletin", (0.60, 0.46, 0.30), metallic=0.0, roughness=0.88)
    mats["Paper_White"] = create_pbr_material("Mat_Paper_White", (0.92, 0.92, 0.90), metallic=0.0, roughness=0.70)
    mats["Stainless_Steel"] = create_pbr_material("Mat_Stainless_Steel", (0.80, 0.82, 0.84), metallic=0.92, roughness=0.18)
    mats["Oak_Cabinet"] = create_pbr_material("Mat_Oak_Cabinet", (0.50, 0.38, 0.25), metallic=0.0, roughness=0.50)
    mats["Terracotta_Urn"] = create_pbr_material("Mat_Terracotta_Urn", (0.72, 0.38, 0.22), metallic=0.02, roughness=0.75)
    mats["Frosted_Glass"] = create_pbr_material("Mat_Frosted_Glass", (0.85, 0.90, 0.92), transmission=0.60, roughness=0.35, alpha=0.55)
    mats["Leather_Burgundy"] = create_pbr_material("Mat_Leather_Burgundy", (0.35, 0.08, 0.10), metallic=0.04, roughness=0.36)
    mats["Book_Spine_Red"] = create_pbr_material("Mat_Book_Spine_Red", (0.55, 0.12, 0.14), metallic=0.05, roughness=0.60)
    mats["Book_Spine_Green"] = create_pbr_material("Mat_Book_Spine_Green", (0.12, 0.40, 0.20), metallic=0.05, roughness=0.60)
    mats["Book_Spine_Blue"] = create_pbr_material("Mat_Book_Spine_Blue", (0.15, 0.22, 0.48), metallic=0.05, roughness=0.60)
    mats["Book_Spine_Gold"] = create_pbr_material("Mat_Book_Spine_Gold", (0.78, 0.65, 0.22), metallic=0.70, roughness=0.35)
    mats["Canvas_Bag"] = create_pbr_material("Mat_Canvas_Bag", (0.78, 0.72, 0.60), metallic=0.0, roughness=0.88)
    mats["Sandstone_Sill"] = create_pbr_material("Mat_Sandstone_Sill", (0.82, 0.78, 0.68), metallic=0.02, roughness=0.70)
    mats["Bus_Shelter_Glass"] = create_pbr_material("Mat_Bus_Shelter_Glass", (0.75, 0.85, 0.90), transmission=0.85, roughness=0.08, alpha=0.40)
    mats["Vending_Glass"] = create_pbr_material("Mat_Vending_Glass", (0.80, 0.90, 0.95), transmission=0.80, roughness=0.05, alpha=0.35)

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

    if not is_collider and "Detail" not in name:
        name = f"{name}_Detail"

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
    if not is_collider and "Detail" not in name:
        name = f"{name}_Detail"

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


def add_oriented_cylinder(col, name, p0, p1, radius, material, segments=12, is_collider=True):
    """Create a cylinder between two arbitrary 3D points p0 and p1."""
    if not is_collider and "Detail" not in name:
        name = f"{name}_Detail"

    v0 = Vector(p0)
    v1 = Vector(p1)
    diff = v1 - v0
    dist = diff.length
    if dist < 0.001:
        return None

    mid = (v0 + v1) / 2.0
    direction = diff.normalized()

    up = Vector((0, 0, 1))
    rot_quat = up.rotation_difference(direction)

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = mid
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = rot_quat

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=dist
    )
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = is_collider
    return obj


def add_triangular_prism(col, name, p0, p1, p2, depth, material, is_collider=True):
    """Create an extruded triangular prism (useful for pediments, gables, ramps)."""
    if not is_collider and "Detail" not in name:
        name = f"{name}_Detail"

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    verts = [
        (p0[0], p0[1], p0[2]),
        (p1[0], p1[1], p1[2]),
        (p2[0], p2[1], p2[2]),
        (p0[0], p0[1] + depth, p0[2]),
        (p1[0], p1[1] + depth, p1[2]),
        (p2[0], p2[1] + depth, p2[2]),
    ]
    faces = [
        (0, 1, 2),       # Front
        (5, 4, 3),       # Back
        (0, 3, 4, 1),    # Bottom/Slope
        (1, 4, 5, 2),    # Right/Slope
        (2, 5, 3, 0),    # Left/Slope
    ]

    mesh.from_pydata(verts, [], faces)
    mesh.update()

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = is_collider
    return obj


def add_fluted_column(col, name, base_center, radius, height, material, base_material=None, num_flutes=24):
    """Construct an authentic classical Doric/Ionic monumental column with base, flutes, and capital."""
    bx, by, bz = base_center

    # 1. Square Plinth Base
    plinth_w = radius * 2.5
    plinth_h = height * 0.05
    add_box(col, f"{name}_Plinth", 
            (bx - plinth_w * 0.5, by - plinth_w * 0.5, bz),
            (bx + plinth_w * 0.5, by + plinth_w * 0.5, bz + plinth_h),
            base_material or material, is_collider=True, bevel=True, bevel_width=0.04)

    # 2. Torus & Scotia Base Moldings
    torus_r = radius * 1.25
    add_cylinder(col, f"{name}_Torus_Base", (bx, by, bz + plinth_h + 0.1), torus_r, 0.20, material, segments=24)

    # 3. Main Fluted Shaft
    shaft_h = height * 0.82
    shaft_z = bz + plinth_h + 0.20 + shaft_h / 2.0
    add_cylinder(col, f"{name}_Shaft_Core", (bx, by, shaft_z), radius * 0.95, shaft_h, material, segments=24, is_collider=True)

    # Fluting Ridges (vertical fillets around shaft)
    flute_step = 2.0 * math.pi / float(num_flutes)
    for i in range(num_flutes):
        ang = i * flute_step
        fx = bx + math.cos(ang) * radius
        fy = by + math.sin(ang) * radius
        add_oriented_cylinder(col, f"{name}_Flute_{i}", 
                              (fx, fy, bz + plinth_h + 0.22),
                              (fx, fy, bz + plinth_h + 0.20 + shaft_h - 0.02),
                              radius=0.035, material=material, segments=6, is_collider=False)

    # 4. Classical Capital (Echinus & Abacus)
    cap_z = bz + plinth_h + 0.20 + shaft_h
    add_cylinder(col, f"{name}_Echinus", (bx, by, cap_z + 0.15), radius * 1.28, 0.30, material, segments=24)
    add_box(col, f"{name}_Abacus", 
            (bx - plinth_w * 0.55, by - plinth_w * 0.55, bz + height - 0.3),
            (bx + plinth_w * 0.55, by + plinth_w * 0.55, bz + height),
            base_material or material, is_collider=True, bevel=True, bevel_width=0.03)


def add_gold_ingot(col, name, center, rot_z_deg=0.0, material=None):
    """Create an individual 400 troy-oz Good Delivery trapezoidal gold bullion ingot."""
    if "Detail" not in name:
        name = f"{name}_Detail"
    cx, cy, cz = center
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = (cx, cy, cz)
    obj.rotation_euler = (0, 0, math.radians(rot_z_deg))

    # Standard bar: base 0.26 x 0.09, top 0.24 x 0.075, height 0.045m
    b_l, b_w = 0.13, 0.045
    t_l, t_w = 0.118, 0.038
    h2 = 0.0225

    verts = [
        # Bottom 4
        (-b_l, -b_w, -h2), (b_l, -b_w, -h2), (b_l, b_w, -h2), (-b_l, b_w, -h2),
        # Top 4
        (-t_l, -t_w, h2), (t_l, -t_w, h2), (t_l, t_w, h2), (-t_l, t_w, h2)
    ]
    faces = [
        (0, 1, 2, 3), # Bottom
        (4, 7, 6, 5), # Top
        (0, 4, 5, 1), # Front
        (1, 5, 6, 2), # Right
        (2, 6, 7, 3), # Back
        (3, 7, 4, 0)  # Left
    ]

    mesh.from_pydata(verts, [], faces)
    mesh.update()

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = False
    return obj


def add_catenary_rope(col, name, p0, p1, sag=0.18, radius=0.035, material=None, segments=8):
    """Create a sagging velvet rope in a catenary curve between two stanchion posts."""
    v0 = Vector(p0)
    v1 = Vector(p1)
    diff = v1 - v0
    dist = diff.length
    if dist < 0.1:
        return

    pts = []
    for s in range(segments + 1):
        t = s / float(segments)
        # Linear interpolation
        pt = v0.lerp(v1, t)
        # Parabolic sag approximation of catenary curve
        pt.z -= math.sin(t * math.pi) * sag
        pts.append(pt)

    for i in range(segments):
        add_oriented_cylinder(col, f"{name}_Seg_{i}", pts[i], pts[i+1], radius=radius, material=material, segments=8, is_collider=False)


def build_bank_scene():
    """Assemble the complete realistic, high-fidelity 3D map for The Bank (hd_bank)."""
    clear_scene()
    mats = setup_materials()

    c_arch = get_or_create_collection("Architecture_Core")
    c_facade = get_or_create_collection("Facade_Neoclassical")
    c_lobby = get_or_create_collection("Lobby_Grand_Hall")
    c_vault = get_or_create_collection("Vault_Site_A")
    c_offices = get_or_create_collection("Props_Offices")
    c_street = get_or_create_collection("Props_Street")

    # =========================================================================
    # 1. PERIMETER BOUNDARIES, STREETSCAPE & ARENA CEILING
    # =========================================================================
    # East, West, North Outer Boundary Walls
    add_box(c_arch, "Boundary_Wall_North", (4.0, 60.0, 0.0), (60.0, 60.5, 16.0), mats["Perimeter_Concrete"])
    add_box(c_arch, "Boundary_Wall_West", (3.5, 4.0, 0.0), (4.0, 60.0, 16.0), mats["Perimeter_Concrete"])
    add_box(c_arch, "Boundary_Wall_East", (60.0, 4.0, 0.0), (60.5, 60.0, 16.0), mats["Perimeter_Concrete"])

    # Across the Street (South Side): 3-Story Manhattan Commercial Facade (y: 3.5..4.0)
    # Replaces flat concrete with a detailed downtown urban streetscape!
    add_box(c_arch, "Boundary_Wall_South_Brick", (4.0, 3.5, 0.0), (60.0, 4.0, 16.0), mats["Brick_Commercial_Red"])
    # Ground-Floor Bronze Storefronts & Display Windows
    for bx in range(8, 56, 12):
        # Storefront frame
        add_box(c_street, f"Storefront_Frame_{bx}", (float(bx), 3.85, 0.0), (float(bx) + 8.0, 4.02, 4.0), mats["Storefront_Bronze"], is_collider=False)
        add_box(c_street, f"Storefront_Glass_{bx}", (float(bx) + 0.4, 3.90, 0.5), (float(bx) + 7.6, 4.01, 3.5), mats["Smoked_Glass"], is_collider=False)
        # Commercial fabric awnings projecting over sidewalk
        awn_mat = mats["Awning_Canvas_Green"] if (bx // 12) % 2 == 0 else mats["Awning_Canvas_Burgundy"]
        add_triangular_prism(c_street, f"Storefront_Awning_{bx}", (float(bx) - 0.2, 4.0, 3.8), (float(bx) + 8.2, 4.0, 3.8), ((float(bx) + float(bx) + 8.0)/2.0, 5.2, 3.0), depth=0.02, material=awn_mat, is_collider=False)
        # Upper window sills, lintels, and double-hung sash frames (Floors 2 and 3)
        for fl_z in [5.8, 9.5]:
            for wx in [bx + 1.2, bx + 4.5]:
                add_box(c_street, f"Upper_Win_Sill_{int(wx)}_{int(fl_z)}", (wx - 0.1, 3.88, fl_z), (wx + 2.3, 4.05, fl_z + 0.18), mats["Limestone_Facade"], is_collider=False)
                add_box(c_street, f"Upper_Win_Glass_{int(wx)}_{int(fl_z)}", (wx, 3.92, fl_z + 0.18), (wx + 2.2, 4.01, fl_z + 2.4), mats["Smoked_Glass"], is_collider=False)
                add_box(c_street, f"Upper_Win_Lintel_{int(wx)}_{int(fl_z)}", (wx - 0.15, 3.88, fl_z + 2.4), (wx + 2.35, 4.05, fl_z + 2.7), mats["Limestone_Facade"], is_collider=False)
    # Roofline modillion cornice across the south street
    add_box(c_street, "South_Street_Cornice", (4.0, 3.80, 13.5), (60.0, 4.10, 14.2), mats["Limestone_Facade"], is_collider=False)

    # Heavy Bank Building Ceiling Roof Slab: covers ONLY the building (y: 18.0..60.0)
    # Leaving y: 4.0..18.0 OPEN TO THE BRIGHT BLUE DAYLIGHT SKY!
    add_box(c_arch, "Ceiling_Roof_Slab", (4.0, 18.0, 14.0), (60.0, 60.0, 14.6), mats["Perimeter_Concrete"])

    # =========================================================================
    # 2. FLOOR PAVEMENT & TESSELLATED MARBLE TILES
    # =========================================================================
    # Street Asphalt Floor (y: 4.0..14.0)
    add_box(c_arch, "Floor_Street_Asphalt", (4.0, 4.0, -0.05), (60.0, 14.0, 0.0), mats["Asphalt_Street"])

    # Double Solid Yellow Boulevard Centerline Stripes
    add_box(c_arch, "Stripe_Centerline_Y1", (4.0, 8.88, 0.005), (60.0, 9.02, 0.008), mats["Road_Yellow"], is_collider=False)
    add_box(c_arch, "Stripe_Centerline_Y2", (4.0, 9.18, 0.005), (60.0, 9.32, 0.008), mats["Road_Yellow"], is_collider=False)

    # Crosswalk Continental Stripes
    for cw_x in range(25, 40, 2):
        add_box(c_arch, f"Crosswalk_Stripe_{cw_x}", (float(cw_x), 9.6, 0.005), (float(cw_x) + 1.2, 13.6, 0.008), mats["Road_White"], is_collider=False)

    # Sidewalk Concrete Slab & Granite Curb (y: 14.0..18.0)
    add_box(c_arch, "Sidewalk_Slab", (4.0, 14.0, 0.0), (60.0, 18.0, 0.20), mats["Concrete_Sidewalk"])
    add_box(c_arch, "Sidewalk_Granite_Curb", (4.0, 13.8, 0.0), (60.0, 14.0, 0.22), mats["Granite_Curb"], bevel=True, bevel_width=0.02)

    # Grand Bank Lobby Floor (Italian Carrara Marble Base, y: 18.0..46.0)
    add_box(c_arch, "Floor_Lobby_Marble", (4.0, 18.0, -0.05), (60.0, 46.0, 0.0), mats["Marble_White"])

    # Tessellated Nero Marquina Marble Border Inlays & Tile Seams (4m grid)
    for grid_x in range(8, 58, 4):
        add_box(c_arch, f"Floor_Inlay_X_{grid_x}", (float(grid_x) - 0.08, 18.0, 0.003), (float(grid_x) + 0.08, 46.0, 0.006), mats["Marble_Black"], is_collider=False)
    for grid_y in range(22, 46, 4):
        add_box(c_arch, f"Floor_Inlay_Y_{grid_y}", (4.0, float(grid_y) - 0.08, 0.003), (60.0, float(grid_y) + 0.08, 0.006), mats["Marble_Black"], is_collider=False)

    # Corner Brass Rosettes at Inlay Intersections
    for rx in range(8, 58, 4):
        for ry in range(22, 46, 4):
            add_box(c_arch, f"Floor_Rosette_{rx}_{ry}", (float(rx) - 0.12, float(ry) - 0.12, 0.004), (float(rx) + 0.12, float(ry) + 0.12, 0.007), mats["Brass_Polished"], is_collider=False)

    # Magnificent 8-Pointed Star Compass Rose Centerpiece Mosaic at (32.0, 32.0)
    add_cylinder(c_lobby, "Mosaic_Outer_Ring", (32.0, 32.0, 0.006), radius=4.5, height=0.004, material=mats["Marble_Black"], segments=48, is_collider=False)
    add_cylinder(c_lobby, "Mosaic_Brass_Border", (32.0, 32.0, 0.008), radius=4.42, height=0.004, material=mats["Brass_Polished"], segments=48, is_collider=False)
    add_cylinder(c_lobby, "Mosaic_Inner_Field", (32.0, 32.0, 0.009), radius=4.25, height=0.004, material=mats["Marble_Cream"], segments=48, is_collider=False)
    # 8 Inlaid Brass & Black Marble Diamond Star Points
    for pt in range(8):
        pt_ang = pt * (math.pi / 4.0)
        px_tip = 32.0 + math.cos(pt_ang) * 4.1
        py_tip = 32.0 + math.sin(pt_ang) * 4.1
        add_oriented_cylinder(c_lobby, f"Mosaic_Star_Ray_{pt}", (32.0, 32.0, 0.012), (px_tip, py_tip, 0.012), radius=0.06, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # =========================================================================
    # 3. MONUMENTAL NEOCLASSICAL FACADE & COLONNADE (y: 18.0..22.0)
    # =========================================================================
    # South Exterior Facade Walls
    add_box(c_facade, "Facade_Wall_West", (4.0, 18.0, 0.0), (26.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_facade, "Facade_Wall_East", (38.0, 18.0, 0.0), (60.0, 22.0, 14.0), mats["Limestone_Facade"])
    add_box(c_facade, "Facade_Wall_Upper_Lintel", (26.0, 18.0, 8.0), (38.0, 22.0, 14.0), mats["Limestone_Facade"])

    # Heavy Entrance Stone Canopy / Portico Overhang (Tactical Skill Jump Platform, z=4.80m)
    add_box(c_facade, "Entrance_Canopy_Plinth", (26.0, 15.5, 4.40), (38.0, 18.0, 4.80), mats["Limestone_Dark"], bevel=True, bevel_width=0.05)
    # Decorative Classical Dentil Molding under Canopy
    for dx in range(26, 38):
        add_box(c_facade, f"Canopy_Dentil_{dx}", (float(dx) + 0.15, 15.45, 4.25), (float(dx) + 0.65, 15.55, 4.40), mats["Limestone_Facade"], is_collider=False)

    # 4 Monumental Fluted Columns (Neoclassical Colonnade at y=16.8)
    col_x_coords = [10.0, 22.0, 42.0, 54.0]
    for ci, cx in enumerate(col_x_coords):
        add_fluted_column(c_facade, f"Colonnade_Col_{ci+1}", (cx, 16.8, 0.20), radius=1.05, height=13.6, material=mats["Marble_Column"], base_material=mats["Limestone_Dark"])

    # Classical Entablature: Doric Architrave & Frieze above Columns (z: 11.5..14.0)
    add_box(c_facade, "Entablature_Architrave", (6.0, 16.2, 11.6), (58.0, 17.8, 12.3), mats["Limestone_Facade"], bevel=True, bevel_width=0.03)
    add_box(c_facade, "Entablature_Frieze", (6.0, 16.3, 12.3), (58.0, 17.7, 13.4), mats["Limestone_Dark"])
    # Repeating Triglyphs and Metopes across Frieze
    for fx in range(8, 56, 3):
        add_box(c_facade, f"Triglyph_{fx}", (float(fx), 16.25, 12.35), (float(fx) + 0.8, 16.35, 13.35), mats["Limestone_Facade"], is_collider=False)
        for sub_groove in [0.2, 0.4, 0.6]:
            add_box(c_facade, f"Triglyph_Groove_{fx}_{int(sub_groove*10)}", (float(fx) + sub_groove - 0.03, 16.22, 12.4), (float(fx) + sub_groove + 0.03, 16.36, 13.3), mats["Cast_Iron_Dark"], is_collider=False)

    # Monumental Triangular Pediment over Main Entrance (x: 25.0..39.0, z: 8.0..13.5m)
    p_left = (25.0, 17.85, 8.0)
    p_right = (39.0, 17.85, 8.0)
    p_apex = (32.0, 17.85, 13.2)
    add_triangular_prism(c_facade, "Pediment_Tympanum", p_left, p_right, p_apex, depth=0.45, material=mats["Limestone_Dark"])
    # Projecting Raking Cornices along Pediment Slopes
    add_oriented_cylinder(c_facade, "Pediment_Rake_L", (25.0, 17.80, 8.0), (32.0, 17.80, 13.2), radius=0.18, material=mats["Limestone_Facade"], segments=12, is_collider=False)
    add_oriented_cylinder(c_facade, "Pediment_Rake_R", (39.0, 17.80, 8.0), (32.0, 17.80, 13.2), radius=0.18, material=mats["Limestone_Facade"], segments=12, is_collider=False)

    # Classical Carved Brass Crest Eagle Medallion (Vertically oriented on pediment tympanum)
    add_oriented_cylinder(c_facade, "Pediment_Brass_Medallion", (32.0, 17.80, 10.3), (32.0, 17.70, 10.3), radius=1.15, material=mats["Brass_Polished"], segments=32, is_collider=False)
    add_oriented_cylinder(c_facade, "Pediment_Medallion_Inner_Rim", (32.0, 17.72, 10.3), (32.0, 17.65, 10.3), radius=0.90, material=mats["Bronze_Architectural"], segments=32, is_collider=False)

    # Acroterion Classical Urns on Pediment Corners
    for (ux, uy, uz) in [(25.0, 17.8, 8.2), (39.0, 17.8, 8.2), (32.0, 17.8, 13.4)]:
        add_box(c_facade, f"Acroterion_Pedestal_{int(ux)}", (ux - 0.45, uy - 0.25, uz), (ux + 0.45, uy + 0.25, uz + 0.45), mats["Limestone_Facade"], is_collider=False)
        add_cylinder(c_facade, f"Acroterion_Base_{int(ux)}", (ux, uy, uz + 0.55), radius=0.35, height=0.20, material=mats["Limestone_Facade"], is_collider=False)
        add_cylinder(c_facade, f"Acroterion_Bowl_{int(ux)}", (ux, uy, uz + 0.95), radius=0.50, height=0.60, material=mats["Marble_Column"], segments=16, is_collider=False)
        add_cylinder(c_facade, f"Acroterion_Finial_{int(ux)}", (ux, uy, uz + 1.45), radius=0.18, height=0.40, material=mats["Brass_Polished"], segments=12, is_collider=False)

    # Propped-Open Solid Mahogany Entrance Double Doors
    add_box(c_facade, "Door_Leaf_L_Open", (27.2, 18.2, 0.20), (28.4, 21.0, 4.20), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    add_box(c_facade, "Door_Leaf_R_Open", (35.6, 18.2, 0.20), (36.8, 21.0, 4.20), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    # Heavy Brass Panic Bars & Kickplates
    for d_side, dx in [("L", 28.3), ("R", 35.7)]:
        add_box(c_facade, f"Door_Kickplate_{d_side}", (dx - 0.05, 18.2, 0.20), (dx + 0.05, 21.0, 0.65), mats["Brass_Polished"], is_collider=False)
        add_oriented_cylinder(c_facade, f"Door_PanicBar_{d_side}", (dx, 18.6, 1.6), (dx, 20.6, 1.6), radius=0.03, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # Architectural Bronze Inscription Plaque above Portal ("THE BANK OF COMMERCE")
    add_box(c_facade, "Entrance_Architrave_Plaque", (27.5, 17.82, 4.85), (36.5, 17.88, 5.45), mats["Bronze_Architectural"], is_collider=False)
    add_box(c_facade, "Entrance_Plaque_Lettering", (28.0, 17.80, 5.00), (36.0, 17.84, 5.30), mats["Brass_Polished"], is_collider=False)

    # 24/7 Heavy Bronze Night Depository Drop Box on Facade (x = 24.5, y = 17.9)
    add_box(c_facade, "Night_Deposit_Faceplate", (24.0, 17.85, 1.10), (25.5, 17.95, 2.10), mats["Bronze_Architectural"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(c_facade, "Night_Deposit_Chute_Door", (24.2, 17.82, 1.30), (25.3, 17.88, 1.85), mats["Brass_Polished"], is_collider=False)
    add_oriented_cylinder(c_facade, "Night_Deposit_Handle", (24.4, 17.78, 1.60), (25.1, 17.78, 1.60), radius=0.02, material=mats["Bronze_Architectural"], segments=8, is_collider=False)
    add_cylinder(c_facade, "Night_Deposit_Lock", (24.75, 17.80, 1.95), radius=0.03, height=0.04, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # =========================================================================
    # 4. GRAND BANKING HALL (Site B) - Neoclassical Pillars, Ceiling, Teller Island
    # =========================================================================
    # 4 Monumental Interior Hall Fluted Marble Pillars (Site B tactical cover)
    pillar_coords = [(23.0, 25.0), (41.0, 25.0), (23.0, 41.0), (41.0, 41.0)]
    for pi, (px, py) in enumerate(pillar_coords):
        add_fluted_column(c_lobby, f"Hall_Pillar_{pi+1}", (px, py, 0.0), radius=1.05, height=14.0, material=mats["Marble_Column"], base_material=mats["Marble_Black"])

    # Rich Mahogany Wainscoting & Classical Wall Pilasters (z: 0.0..1.45m)
    add_box(c_arch, "Wainscot_Base_S_L", (4.0, 22.0, 0.0), (26.0, 22.25, 1.45), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    add_box(c_arch, "Wainscot_Base_S_R", (38.0, 22.0, 0.0), (60.0, 22.25, 1.45), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    add_box(c_arch, "Wainscot_Base_N", (4.0, 45.75, 0.0), (60.0, 46.0, 1.45), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    add_box(c_arch, "Wainscot_Chair_Rail_S_L", (4.0, 22.0, 1.45), (26.0, 22.30, 1.55), mats["Mahogany_Dark"], is_collider=False)
    add_box(c_arch, "Wainscot_Chair_Rail_S_R", (38.0, 22.0, 1.45), (60.0, 22.30, 1.55), mats["Mahogany_Dark"], is_collider=False)
    add_box(c_arch, "Wainscot_Chair_Rail_N", (4.0, 45.70, 1.45), (60.0, 46.0, 1.55), mats["Mahogany_Dark"], is_collider=False)

    # Grand Beaux-Arts 2-Story Arched Windows on East Hall Wall (x = 59.85, y: 22.0..46.0)
    # Eliminating flat grey void with monumental bronze-mullioned arched windows
    for win_y in [25.0, 31.0, 37.0, 43.0]:
        # Outer Bronze Window Frame Architrave
        add_box(c_lobby, f"Arch_Win_Frame_{int(win_y)}", (59.80, win_y - 1.80, 2.20), (59.95, win_y + 1.80, 10.40), mats["Storefront_Bronze"], is_collider=False)
        # Glazing Panes (Smoked glass catching directional sunlight)
        add_box(c_lobby, f"Arch_Win_Glass_{int(win_y)}", (59.84, win_y - 1.65, 2.35), (59.90, win_y + 1.65, 10.25), mats["Smoked_Glass"], is_collider=False)
        # Sandstone Window Sill with Drip Edge
        add_box(c_lobby, f"Arch_Win_Sill_{int(win_y)}", (59.65, win_y - 1.95, 2.00), (59.98, win_y + 1.95, 2.25), mats["Sandstone_Sill"], bevel=True, bevel_width=0.03, is_collider=False)
        # Decorative Arch Keystone Block at Peak of Window
        add_box(c_lobby, f"Arch_Win_Keystone_{int(win_y)}", (59.75, win_y - 0.25, 10.30), (59.96, win_y + 0.25, 10.75), mats["Limestone_Facade"], is_collider=False)
        # Horizontal Transom & Vertical Muntin Grids
        for my_off in [-1.1, -0.55, 0.0, 0.55, 1.1]:
            add_oriented_cylinder(c_lobby, f"Arch_Win_Muntin_V_{int(win_y)}_{int((my_off+2)*10)}", (59.86, win_y + my_off, 2.35), (59.86, win_y + my_off, 10.25), radius=0.02, material=mats["Storefront_Bronze"], segments=6, is_collider=False)
        for mz in [4.2, 6.2, 8.2]:
            add_box(c_lobby, f"Arch_Win_Transom_{int(win_y)}_{int(mz*10)}", (59.82, win_y - 1.65, mz - 0.03), (59.92, win_y + 1.65, mz + 0.03), mats["Storefront_Bronze"], is_collider=False)

    # Classical Wall Pilasters on Side Walls (Supporting transverse ceiling beams)
    for pil_y in [25.0, 31.0, 37.0, 43.0]:
        # East Wall Pilasters (x = 59.70)
        add_box(c_lobby, f"Pilaster_East_{int(pil_y)}", (59.60, pil_y - 0.45, 1.55), (59.95, pil_y + 0.45, 13.2), mats["Marble_Column"], is_collider=False)
        add_box(c_lobby, f"Pilaster_Cap_E_{int(pil_y)}", (59.50, pil_y - 0.60, 13.2), (59.95, pil_y + 0.60, 13.8), mats["Limestone_Facade"], is_collider=False)
        # West Wall Pilasters (Under Mezzanine, x = 19.80)
        add_box(c_lobby, f"Pilaster_West_{int(pil_y)}", (19.80, pil_y - 0.40, 1.55), (20.10, pil_y + 0.40, 4.70), mats["Marble_Column"], is_collider=False)

    # Classical Wall Pilasters on North Wall
    for pil_x in [10.0, 24.0, 40.0, 54.0]:
        add_box(c_lobby, f"Pilaster_N_{pil_x}", (pil_x - 0.45, 45.72, 1.55), (pil_x + 0.45, 46.0, 13.8), mats["Marble_Column"], is_collider=False)
        add_box(c_lobby, f"Pilaster_Cap_N_{pil_x}", (pil_x - 0.60, 45.65, 13.4), (pil_x + 0.60, 46.0, 13.8), mats["Limestone_Facade"], is_collider=False)

    # Classical Dentil Frieze Molding running around upper perimeter walls (z = 13.35..13.80m)
    add_box(c_lobby, "Frieze_Molding_North", (4.0, 45.72, 13.45), (60.0, 46.0, 13.80), mats["Limestone_Facade"], is_collider=False)
    add_box(c_lobby, "Frieze_Molding_South", (4.0, 22.0, 13.45), (60.0, 22.28, 13.80), mats["Limestone_Facade"], is_collider=False)
    add_box(c_lobby, "Frieze_Molding_East", (59.72, 22.0, 13.45), (60.0, 46.0, 13.80), mats["Limestone_Facade"], is_collider=False)
    for dx in range(6, 59, 2):
        add_box(c_lobby, f"Dentil_N_{dx}", (float(dx) + 0.3, 45.68, 13.48), (float(dx) + 0.9, 45.74, 13.62), mats["Plaster_Ceiling"], is_collider=False)
        add_box(c_lobby, f"Dentil_S_{dx}", (float(dx) + 0.3, 22.26, 13.48), (float(dx) + 0.9, 22.32, 13.62), mats["Plaster_Ceiling"], is_collider=False)
    for dy in range(23, 45, 2):
        add_box(c_lobby, f"Dentil_E_{dy}", (59.68, float(dy) + 0.3, 13.48), (59.74, float(dy) + 0.9, 13.62), mats["Plaster_Ceiling"], is_collider=False)

    # Large Framed Oil Paintings in Gilded Frames on Hall Walls
    for p_x, p_y, p_rot, p_mat in [
        (12.0, 45.88, 0, mats["Painting_Canvas_1"]),
        (52.0, 45.88, 0, mats["Painting_Canvas_2"]),
        (4.12, 34.0, 90, mats["Painting_Canvas_3"]),
        (59.88, 34.0, 90, mats["Painting_Canvas_1"]),
    ]:
        if p_rot == 0:
            add_box(c_lobby, f"Frame_Gold_{int(p_x)}_{int(p_y)}", (p_x - 1.8, p_y - 0.06, 2.5), (p_x + 1.8, p_y, 4.8), mats["Picture_Frame_Gold"], is_collider=False)
            add_box(c_lobby, f"Canvas_{int(p_x)}_{int(p_y)}", (p_x - 1.6, p_y - 0.08, 2.7), (p_x + 1.6, p_y - 0.02, 4.6), p_mat, is_collider=False)
            # Twin Bronze Wall Torchère Sconces
            for sc_x in [p_x - 2.4, p_x + 2.4]:
                add_cylinder(c_lobby, f"Sconce_Bracket_{int(sc_x*10)}", (sc_x, p_y - 0.12, 3.6), radius=0.08, height=0.30, material=mats["Bronze_Architectural"], segments=8, is_collider=False)
                add_cylinder(c_lobby, f"Sconce_Globe_{int(sc_x*10)}", (sc_x, p_y - 0.22, 3.8), radius=0.15, height=0.25, material=mats["Sconce_Lamp"], segments=12, is_collider=False)

    # Finished Neoclassical Coffered Ceiling & Plaster Infill Panels (closing black void)
    add_box(c_lobby, "Ceiling_Plaster_Lobby_Infill", (4.0, 18.0, 13.8), (60.0, 46.0, 14.0), mats["Plaster_Ceiling"], is_collider=False)

    # Ornate Coffered Ceiling Beams
    for cx in range(8, 60, 8):
        add_box(c_lobby, f"Ceiling_Beam_X_{cx}", (float(cx) - 0.45, 18.0, 13.2), (float(cx) + 0.45, 46.0, 13.8), mats["Mahogany_Dark"], is_collider=False)
    for cy in range(22, 46, 6):
        add_box(c_lobby, f"Ceiling_Beam_Y_{cy}", (4.0, float(cy) - 0.45, 13.2), (60.0, float(cy) + 0.45, 13.8), mats["Mahogany_Dark"], is_collider=False)

    # Central Beaux-Arts Grand Brass Chandelier (Replacing Lego-stud cylinder!)
    ch_x, ch_y, ch_z = 32.0, 32.0, 11.2
    # Ceiling Rosette & Canopy
    add_cylinder(c_lobby, "Chandelier_Ceiling_Rosette", (32.0, 32.0, 13.92), radius=1.8, height=0.12, material=mats["Plaster_Ceiling"], segments=24, is_collider=False)
    add_cylinder(c_lobby, "Chandelier_Ceiling_Brass_Cup", (32.0, 32.0, 13.82), radius=0.45, height=0.16, material=mats["Brass_Polished"], segments=16, is_collider=False)
    # 4 Brass Suspension Chains
    for ch_i, ch_a in enumerate([math.pi*0.25, math.pi*0.75, math.pi*1.25, math.pi*1.75]):
        cx_top = 32.0 + math.cos(ch_a) * 0.35
        cy_top = 32.0 + math.sin(ch_a) * 0.35
        cx_bot = 32.0 + math.cos(ch_a) * 0.75
        cy_bot = 32.0 + math.sin(ch_a) * 0.75
        add_oriented_cylinder(c_lobby, f"Chandelier_Chain_{ch_i}", (cx_top, cy_top, 13.8), (cx_bot, cy_bot, ch_z + 1.2), radius=0.035, material=mats["Brass_Polished"], segments=8, is_collider=False)
    # Central Chandelier Body Hub & Lower Finial
    add_cylinder(c_lobby, "Chandelier_Hub_Upper", (ch_x, ch_y, ch_z + 0.6), radius=0.85, height=0.40, material=mats["Brass_Polished"], segments=20, is_collider=False)
    add_cylinder(c_lobby, "Chandelier_Hub_Main", (ch_x, ch_y, ch_z + 0.2), radius=1.20, height=0.50, material=mats["Brass_Polished"], segments=24, is_collider=False)
    add_cylinder(c_lobby, "Chandelier_Finial", (ch_x, ch_y, ch_z - 0.3), radius=0.25, height=0.50, material=mats["Brass_Polished"], segments=12, is_collider=False)
    # 8 Radiating Cast-Bronze Scrollwork Arms with Frosted Glass Lantern Globes
    for arm_i in range(8):
        arm_ang = arm_i * (2.0 * math.pi / 8.0)
        arm_cos = math.cos(arm_ang)
        arm_sin = math.sin(arm_ang)
        # Arm curve end
        lx = ch_x + arm_cos * 2.8
        ly = ch_y + arm_sin * 2.8
        lz = ch_z + 0.45
        # Curved arm rod
        add_oriented_cylinder(c_lobby, f"Chandelier_Arm_{arm_i}", (ch_x + arm_cos * 1.0, ch_y + arm_sin * 1.0, ch_z + 0.2), (lx, ly, lz), radius=0.045, material=mats["Brass_Polished"], segments=8, is_collider=False)
        # Candle bobeche cup
        add_cylinder(c_lobby, f"Chandelier_Cup_{arm_i}", (lx, ly, lz + 0.08), radius=0.22, height=0.10, material=mats["Brass_Polished"], segments=12, is_collider=False)
        # Frosted glass lantern globe with warm emission
        add_cylinder(c_lobby, f"Chandelier_Globe_{arm_i}", (lx, ly, lz + 0.35), radius=0.28, height=0.45, material=mats["Chandelier_Lamp"], segments=16, is_collider=False)

    # 1.8m Analog Bank Regulator Wall Clock (Facing Banking Hall on Dividing Wall at y=45.88)
    add_oriented_cylinder(c_lobby, "Clock_Bezel_Outer", (32.0, 45.88, 7.5), (32.0, 45.74, 7.5), radius=0.95, material=mats["Mahogany_Dark"], segments=32, is_collider=False)
    add_oriented_cylinder(c_lobby, "Clock_Bezel_Brass", (32.0, 45.75, 7.5), (32.0, 45.68, 7.5), radius=0.82, material=mats["Brass_Polished"], segments=32, is_collider=False)
    add_oriented_cylinder(c_lobby, "Clock_Face_White", (32.0, 45.69, 7.5), (32.0, 45.64, 7.5), radius=0.75, material=mats["Road_White"], segments=32, is_collider=False)
    # Hour and Minute Hands pointing to 10:10
    add_oriented_cylinder(c_lobby, "Clock_Hour_Hand", (32.0, 45.62, 7.5), (32.0 - 0.25, 45.62, 7.5 + 0.32), radius=0.025, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)
    add_oriented_cylinder(c_lobby, "Clock_Minute_Hand", (32.0, 45.62, 7.5), (32.0 + 0.38, 45.62, 7.5 + 0.42), radius=0.018, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)

    # -------------------------------------------------------------------------
    # TELLER COUNTER ISLAND (Site B Centerpiece - x: 26.0..38.0, y: 24.0..27.0)
    # -------------------------------------------------------------------------
    # Heavy Solid Mahogany Counter Base with Plinth Kickplate
    add_box(c_lobby, "Teller_Base_Kickplate", (25.8, 23.8, 0.0), (38.2, 27.2, 0.15), mats["Brass_Polished"], bevel=True, bevel_width=0.02)
    add_box(c_lobby, "Teller_Counter_Base", (26.0, 24.0, 0.15), (38.0, 27.0, 1.15), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)

    # Dual-Tier Bullnose Nero Marquina Marble Countertop Slab (z = 1.15..1.25m)
    add_box(c_lobby, "Teller_Countertop_Marble", (25.6, 23.6, 1.15), (38.4, 27.4, 1.25), mats["Marble_Black"], bevel=True, bevel_width=0.03)

    # BEAUX-ARTS POLISHED BRASS WICKET CAGE (Eliminating the opaque cyan Lego blocks!)
    # 5 Monumental Fluted Brass Station Pillars
    for st_i, st_x in enumerate([26.2, 29.1, 32.0, 34.9, 37.8]):
        add_cylinder(c_lobby, f"Teller_Pillar_Base_{st_i}", (st_x, 25.5, 1.27), radius=0.10, height=0.06, material=mats["Brass_Polished"], segments=12, is_collider=False)
        add_cylinder(c_lobby, f"Teller_Pillar_Shaft_{st_i}", (st_x, 25.5, 1.85), radius=0.06, height=1.10, material=mats["Brass_Polished"], segments=12, is_collider=False)
        add_cylinder(c_lobby, f"Teller_Pillar_Cap_{st_i}", (st_x, 25.5, 2.42), radius=0.10, height=0.08, material=mats["Brass_Polished"], segments=12, is_collider=False)

    # Horizontal Brass Upper Header Beam & Decorative Frieze
    add_box(c_lobby, "Teller_Cage_Upper_Beam", (26.0, 25.42, 2.38), (38.0, 25.58, 2.48), mats["Brass_Polished"], is_collider=False)
    add_box(c_lobby, "Teller_Cage_Lower_Sill", (26.0, 25.44, 1.25), (38.0, 25.56, 1.30), mats["Brass_Polished"], is_collider=False)

    # 4 Service Station Bays (Stations 1..4)
    station_centers = [27.65, 30.55, 33.45, 36.35]
    for b_idx, bay_x in enumerate(station_centers):
        # Station Nameplate ("TELLER 1..4")
        add_box(c_lobby, f"Teller_Nameplate_{b_idx+1}", (bay_x - 0.40, 25.39, 2.22), (bay_x + 0.40, 25.43, 2.34), mats["Bronze_Architectural"], is_collider=False)
        # Cutout Arched Service Wicket Window Header
        add_box(c_lobby, f"Wicket_Arch_Top_{b_idx}", (bay_x - 0.65, 25.43, 2.05), (bay_x + 0.65, 25.57, 2.15), mats["Brass_Polished"], is_collider=False)
        # Stainless-Steel Circular Speaking Rosette (Acoustic grille)
        add_cylinder(c_lobby, f"Wicket_Speech_Port_{b_idx}", (bay_x, 25.50, 1.70), radius=0.12, height=0.05, material=mats["Chrome_Polished"], segments=16, is_collider=False)
        # Recessed Chrome Pass-Through Currency Scoop Tray
        add_box(c_lobby, f"Wicket_Scoop_Tray_{b_idx}", (bay_x - 0.35, 25.30, 1.23), (bay_x + 0.35, 25.70, 1.26), mats["Chrome_Polished"], is_collider=False)
        # Vertical Brass Spindles / Security Bars across the Bay (0.16m spacing, see-through)
        for sp_x in [-1.15, -0.95, -0.75, 0.75, 0.95, 1.15]:
            add_oriented_cylinder(c_lobby, f"Wicket_Bar_{b_idx}_{int((bay_x+sp_x)*10)}", 
                                  (bay_x + sp_x, 25.5, 1.28), (bay_x + sp_x, 25.5, 2.38),
                                  radius=0.015, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # 4 Authentic Teller Workstations behind the Cage (High-Fidelity Banking Equipment)
    for ws_i, ws_x in enumerate(station_centers):
        # PC Monitor on articulated desk arm with glowing CCTV / core banking screen
        add_box(c_lobby, f"Workstation_Monitor_{ws_i}", (ws_x - 0.30, 26.20, 1.28), (ws_x + 0.30, 26.26, 1.68), mats["Monitor_Plastic"], is_collider=False)
        add_box(c_lobby, f"Workstation_Screen_{ws_i}", (ws_x - 0.28, 26.18, 1.30), (ws_x + 0.28, 26.21, 1.66), mats["Monitor_Screen_CCTV"], is_collider=False)
        # Keyboard and optical mouse
        add_box(c_lobby, f"Workstation_KB_{ws_i}", (ws_x - 0.25, 25.80, 1.252), (ws_x + 0.25, 26.02, 1.268), mats["Monitor_Plastic"], is_collider=False)
        add_box(c_lobby, f"Workstation_Mouse_{ws_i}", (ws_x + 0.32, 25.85, 1.252), (ws_x + 0.39, 25.97, 1.268), mats["Monitor_Plastic"], is_collider=False)
        
        # High-Speed Automated Cash-Counting Machine with Feed Hopper & Green LED readout
        add_box(c_lobby, f"Cash_Counter_Body_{ws_i}", (ws_x - 0.70, 25.85, 1.255), (ws_x - 0.38, 26.25, 1.52), mats["Road_White"], bevel=True, bevel_width=0.015, is_collider=False)
        add_box(c_lobby, f"Cash_Counter_Hopper_{ws_i}", (ws_x - 0.66, 26.10, 1.50), (ws_x - 0.42, 26.22, 1.60), mats["Cast_Iron_Dark"], is_collider=False)
        add_box(c_lobby, f"Cash_Counter_LED_{ws_i}", (ws_x - 0.65, 25.84, 1.44), (ws_x - 0.50, 25.86, 1.49), mats["LED_Green"], is_collider=False)
        
        # Thermal POS Receipt Printer with paper roll slot & receipt slip
        add_box(c_lobby, f"Receipt_Printer_{ws_i}", (ws_x - 0.36, 25.85, 1.255), (ws_x - 0.16, 26.05, 1.38), mats["Monitor_Plastic"], bevel=True, bevel_width=0.01, is_collider=False)
        add_box(c_lobby, f"Receipt_Slip_{ws_i}", (ws_x - 0.32, 25.82, 1.36), (ws_x - 0.20, 25.86, 1.42), mats["Paper_White"], is_collider=False)

        # ID & Check Flatbed Scanner with Glass Platen
        add_box(c_lobby, f"Doc_Scanner_{ws_i}", (ws_x + 0.42, 26.02, 1.255), (ws_x + 0.72, 26.30, 1.33), mats["Monitor_Plastic"], is_collider=False)
        add_box(c_lobby, f"Doc_Scanner_Glass_{ws_i}", (ws_x + 0.45, 26.05, 1.332), (ws_x + 0.69, 26.27, 1.335), mats["Frosted_Glass"], is_collider=False)

        # Flexible Gooseneck Intercom Microphone
        add_cylinder(c_lobby, f"Intercom_Base_{ws_i}", (ws_x + 0.15, 25.68, 1.265), radius=0.045, height=0.025, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)
        add_oriented_cylinder(c_lobby, f"Intercom_Neck_{ws_i}", (ws_x + 0.15, 25.68, 1.27), (ws_x + 0.12, 25.62, 1.45), radius=0.008, material=mats["Chrome_Polished"], segments=6, is_collider=False)
        add_cylinder(c_lobby, f"Intercom_Mic_{ws_i}", (ws_x + 0.12, 25.62, 1.46), radius=0.016, height=0.035, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)

        # Frosted Tempered Glass Station Privacy Divider Screen
        if ws_i < 3:
            div_x = (ws_x + station_centers[ws_i + 1]) / 2.0
            add_box(c_lobby, f"Teller_Privacy_Divider_{ws_i}", (div_x - 0.015, 25.55, 1.26), (div_x + 0.015, 26.70, 1.85), mats["Frosted_Glass"], is_collider=False)
            add_cylinder(c_lobby, f"Teller_Divider_Clamp1_{ws_i}", (div_x, 25.75, 1.32), radius=0.025, height=0.04, material=mats["Chrome_Polished"], segments=8, is_collider=False)
            add_cylinder(c_lobby, f"Teller_Divider_Clamp2_{ws_i}", (div_x, 26.45, 1.32), radius=0.025, height=0.04, material=mats["Chrome_Polished"], segments=8, is_collider=False)

        # Folded Brass Station Tent Sign ("NEXT WINDOW PLEASE")
        add_box(c_lobby, f"Teller_Tent_Sign_{ws_i}", (ws_x - 0.20, 25.56, 1.255), (ws_x + 0.05, 25.62, 1.32), mats["Brass_Polished"], is_collider=False)

        # Banded Currency Straps on Desktop
        add_box(c_lobby, f"Desk_Cash_Bundle_{ws_i}", (ws_x + 0.42, 25.75, 1.255), (ws_x + 0.62, 25.95, 1.33), mats["Currency_Green"], is_collider=False)
        add_box(c_lobby, f"Desk_Cash_Band_{ws_i}", (ws_x + 0.49, 25.74, 1.256), (ws_x + 0.55, 25.96, 1.332), mats["Currency_Band"], is_collider=False)

        # Heavy Under-Counter Depository Drop Safe
        add_box(c_lobby, f"Under_Counter_Safe_{ws_i}", (ws_x - 0.32, 25.6, 0.15), (ws_x + 0.32, 26.5, 0.95), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.02, is_collider=False)
        add_cylinder(c_lobby, f"Safe_Dial_{ws_i}", (ws_x, 26.52, 0.70), radius=0.065, height=0.03, material=mats["Chrome_Polished"], segments=16, is_collider=False)
        add_cylinder(c_lobby, f"Safe_Handle_{ws_i}", (ws_x, 26.52, 0.50), radius=0.025, height=0.04, material=mats["Chrome_Polished"], segments=8, is_collider=False)

    # Serpentine VIP Queue Stanchions with Sagging Crimson Velvet Catenary Ropes
    stanchion_pts = [(26.0, 22.0), (30.0, 22.0), (34.0, 22.0), (38.0, 22.0)]
    for si, (stx, sty) in enumerate(stanchion_pts):
        add_cylinder(c_lobby, f"Stanchion_Base_{si}", (stx, sty, 0.03), radius=0.20, height=0.06, material=mats["Brass_Polished"], segments=16, is_collider=False)
        add_oriented_cylinder(c_lobby, f"Stanchion_Post_{si}", (stx, sty, 0.06), (stx, sty, 0.95), radius=0.035, material=mats["Brass_Polished"], segments=12, is_collider=False)
        add_cylinder(c_lobby, f"Stanchion_Urn_{si}", (stx, sty, 0.98), radius=0.06, height=0.08, material=mats["Brass_Polished"], segments=12, is_collider=False)
    for si in range(len(stanchion_pts) - 1):
        p0 = (stanchion_pts[si][0], stanchion_pts[si][1], 0.90)
        p1 = (stanchion_pts[si+1][0], stanchion_pts[si+1][1], 0.90)
        add_catenary_rope(c_lobby, f"Velvet_Rope_{si}", p0, p1, sag=0.16, radius=0.032, material=mats["Velvet_Crimson"])

    # 4 Ergonomic Teller Task Chairs behind Workstations
    for ws_i, ws_x in enumerate(station_centers):
        add_cylinder(c_lobby, f"Teller_Chair_Base_{ws_i}", (ws_x, 27.2, 0.10), radius=0.28, height=0.08, material=mats["Cast_Iron_Dark"], segments=12, is_collider=False)
        add_cylinder(c_lobby, f"Teller_Chair_Stem_{ws_i}", (ws_x, 27.2, 0.30), radius=0.04, height=0.32, material=mats["Chrome_Polished"], segments=8, is_collider=False)
        add_box(c_lobby, f"Teller_Chair_Seat_{ws_i}", (ws_x - 0.24, 27.0, 0.46), (ws_x + 0.24, 27.4, 0.54), mats["Leather_Black"], bevel=True, bevel_width=0.02, is_collider=False)
        add_box(c_lobby, f"Teller_Chair_Back_{ws_i}", (ws_x - 0.22, 27.36, 0.54), (ws_x + 0.22, 27.42, 0.95), mats["Leather_Black"], bevel=True, bevel_width=0.02, is_collider=False)
        # Under-counter lockable cash drawers
        add_box(c_lobby, f"Teller_Cash_Drawer_{ws_i}", (ws_x - 0.70, 25.5, 0.40), (ws_x - 0.35, 26.5, 1.14), mats["Mahogany_Dark"], is_collider=False)
        add_cylinder(c_lobby, f"Teller_Drawer_Lock_{ws_i}", (ws_x - 0.525, 26.52, 1.05), radius=0.015, height=0.03, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # -------------------------------------------------------------------------
    # DUAL CUSTOMER CHECK-WRITING ISLAND PEDESTAL DESKS (x=22.0 and x=42.0)
    # -------------------------------------------------------------------------
    for isl_i, isl_x in enumerate([22.0, 42.0]):
        add_box(c_lobby, f"Check_Island_Base_{isl_i}", (isl_x - 1.2, 19.5, 0.0), (isl_x + 1.2, 20.9, 1.02), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
        add_box(c_lobby, f"Check_Island_Marble_Top_{isl_i}", (isl_x - 1.4, 19.3, 1.02), (isl_x + 1.4, 21.1, 1.10), mats["Marble_Cream"], bevel=True, bevel_width=0.02)
        # Fluted Bronze Pedestal Support Columns
        for cx_off in [-0.8, 0.8]:
            add_cylinder(c_lobby, f"Check_Pedestal_{isl_i}_{int(cx_off*10)}", (isl_x + cx_off, 20.2, 0.51), radius=0.18, height=1.00, material=mats["Bronze_Architectural"], segments=12, is_collider=False)
        # Dual Brass Pen Stands with Chained Ballpoint Pens
        for pi, px in enumerate([isl_x - 0.6, isl_x + 0.6]):
            add_cylinder(c_lobby, f"Check_Pen_Base_{isl_i}_{pi}", (px, 20.2, 1.11), radius=0.04, height=0.03, material=mats["Brass_Polished"], segments=8, is_collider=False)
            add_oriented_cylinder(c_lobby, f"Check_Pen_Stem_{isl_i}_{pi}", (px, 20.2, 1.12), (px + 0.04, 20.2, 1.26), radius=0.008, material=mats["Brass_Polished"], segments=6, is_collider=False)
        # Acrylic Deposit Slip Organizer Rack with White Deposit Slips
        add_box(c_lobby, f"Check_Slip_Organizer_{isl_i}", (isl_x - 0.3, 20.0, 1.105), (isl_x + 0.3, 20.4, 1.22), mats["Security_Glass"], is_collider=False)
        add_box(c_lobby, f"Check_Deposit_Slips_{isl_i}", (isl_x - 0.25, 20.05, 1.11), (isl_x + 0.25, 20.35, 1.20), mats["Paper_White"], is_collider=False)
        # Brass Perpetual Calendar Block ("14 SEP")
        add_box(c_lobby, f"Check_Calendar_{isl_i}", (isl_x - 0.10, 20.55, 1.105), (isl_x + 0.10, 20.75, 1.18), mats["Brass_Polished"], is_collider=False)
        # Waste Depository Flap Slot
        add_box(c_lobby, f"Check_Waste_Flap_{isl_i}", (isl_x + 0.9, 20.0, 1.102), (isl_x + 1.2, 20.4, 1.106), mats["Cast_Iron_Dark"], is_collider=False)

    # DUAL ARCHITECTURAL POTTED FICUS TREES in Polished Brass Urn Planters
    for tree_i, (tx, ty) in enumerate([(10.0, 21.0), (54.0, 21.0)]):
        add_cylinder(c_lobby, f"Lobby_Urn_Base_{tree_i}", (tx, ty, 0.04), radius=0.38, height=0.08, material=mats["Brass_Polished"], segments=16, is_collider=False)
        add_cylinder(c_lobby, f"Lobby_Urn_Body_{tree_i}", (tx, ty, 0.45), radius=0.45, height=0.74, material=mats["Brass_Polished"], segments=16, is_collider=False)
        add_cylinder(c_lobby, f"Lobby_Tree_Trunk_{tree_i}", (tx, ty, 1.20), radius=0.06, height=0.90, material=mats["Mahogany_Dark"], segments=8, is_collider=False)
        add_cylinder(c_lobby, f"Lobby_Tree_Foliage_B_{tree_i}", (tx, ty, 2.0), radius=0.95, height=0.90, material=mats["Foliage_Green"], segments=12, is_collider=False)
        add_cylinder(c_lobby, f"Lobby_Tree_Foliage_T_{tree_i}", (tx, ty, 2.7), radius=0.65, height=0.80, material=mats["Foliage_Green"], segments=12, is_collider=False)

    # -------------------------------------------------------------------------
    # VIP CLIENT WAITING LOUNGE (Site B East Hall - x: 48.0..56.0, y: 25.0..31.0)
    # -------------------------------------------------------------------------
    # Deep-Tufted Burgundy Leather Chesterfield 3-Seater Sofa
    add_box(c_lobby, "Lounge_Chesterfield_Base", (49.5, 26.5, 0.0), (54.5, 28.2, 0.45), mats["Leather_Burgundy"], bevel=True, bevel_width=0.04)
    add_box(c_lobby, "Lounge_Chesterfield_Cushions", (49.8, 26.7, 0.45), (54.2, 27.9, 0.65), mats["Leather_Burgundy"], bevel=True, bevel_width=0.03)
    add_box(c_lobby, "Lounge_Chesterfield_Back", (49.5, 27.8, 0.45), (54.5, 28.3, 1.20), mats["Leather_Burgundy"], bevel=True, bevel_width=0.05)
    add_cylinder(c_lobby, "Lounge_Chesterfield_Arm_L", (49.7, 27.3, 0.68), radius=0.24, height=1.35, material=mats["Leather_Burgundy"], is_collider=False)
    add_cylinder(c_lobby, "Lounge_Chesterfield_Arm_R", (54.3, 27.3, 0.68), radius=0.24, height=1.35, material=mats["Leather_Burgundy"], is_collider=False)
    for fx, fy in [(49.7, 26.7), (54.3, 26.7), (49.7, 28.1), (54.3, 28.1)]:
        add_cylinder(c_lobby, f"Lounge_Sofa_Foot_{int(fx*10)}_{int(fy*10)}", (fx, fy, 0.04), radius=0.05, height=0.08, material=mats["Mahogany_Dark"], segments=8, is_collider=False)

    # Pair of Matching Chesterfield Wingback Armchairs Facing Sofa
    for c_i, cx in enumerate([50.0, 54.0]):
        add_box(c_lobby, f"Lounge_Armchair_Base_{c_i}", (cx - 0.45, 30.0, 0.0), (cx + 0.45, 31.0, 0.45), mats["Leather_Burgundy"], bevel=True, bevel_width=0.03)
        add_box(c_lobby, f"Lounge_Armchair_Back_{c_i}", (cx - 0.45, 30.6, 0.45), (cx + 0.45, 31.0, 1.25), mats["Leather_Burgundy"], bevel=True, bevel_width=0.04)
        for arm_s in [-0.42, 0.42]:
            add_box(c_lobby, f"Lounge_Chair_Arm_{c_i}_{int((arm_s+1)*10)}", (cx + arm_s - 0.06, 30.0, 0.45), (cx + arm_s + 0.06, 30.8, 0.72), mats["Leather_Burgundy"], is_collider=False)

    # Solid Mahogany Coffee Table with Beveled Glass Insert
    add_box(c_lobby, "Lounge_Coffee_Table", (50.5, 28.7, 0.0), (53.5, 29.7, 0.45), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    add_box(c_lobby, "Lounge_Coffee_Table_Glass", (50.6, 28.8, 0.45), (53.4, 29.6, 0.47), mats["Security_Glass"], is_collider=False)
    # Folded Newspapers (Financial Times / Wall Street Journal) & Ceramic Cups
    add_box(c_lobby, "Lounge_Newspaper_1", (51.0, 29.0, 0.472), (51.4, 29.4, 0.485), mats["Paper_White"], is_collider=False)
    add_box(c_lobby, "Lounge_Newspaper_2", (51.2, 28.95, 0.486), (51.6, 29.35, 0.495), mats["Paper_White"], is_collider=False)
    add_cylinder(c_lobby, "Lounge_Cup_1", (52.2, 29.2, 0.50), radius=0.04, height=0.06, material=mats["Road_White"], segments=8, is_collider=False)
    add_cylinder(c_lobby, "Lounge_Cup_2", (52.6, 29.1, 0.50), radius=0.04, height=0.06, material=mats["Road_White"], segments=8, is_collider=False)

    # Monumental 2.65m Grandfather Regulator Clock against East Wall (x = 59.60, y = 28.0)
    add_box(c_lobby, "Grandfather_Clock_Base", (59.35, 27.6, 0.0), (59.85, 28.4, 0.65), mats["Mahogany_Dark"], bevel=True, bevel_width=0.02)
    add_box(c_lobby, "Grandfather_Clock_Waist", (59.40, 27.65, 0.65), (59.80, 28.35, 1.85), mats["Mahogany_Dark"], bevel=True, bevel_width=0.02)
    add_box(c_lobby, "Grandfather_Clock_Glass", (59.38, 27.70, 0.75), (59.42, 28.30, 1.75), mats["Security_Glass"], is_collider=False)
    # Visible Brass Pendulum Bob & Twin Driving Weights
    add_cylinder(c_lobby, "Grandfather_Pendulum", (59.55, 28.0, 1.10), radius=0.08, height=0.03, material=mats["Brass_Polished"], segments=16, is_collider=False)
    add_cylinder(c_lobby, "Grandfather_Weight_L", (59.55, 27.85, 1.40), radius=0.035, height=0.22, material=mats["Brass_Polished"], segments=12, is_collider=False)
    add_cylinder(c_lobby, "Grandfather_Weight_R", (59.55, 28.15, 1.35), radius=0.035, height=0.22, material=mats["Brass_Polished"], segments=12, is_collider=False)
    # Upper Hood & Roman Numeral Dial with Brass Finial
    add_box(c_lobby, "Grandfather_Clock_Hood", (59.32, 27.55, 1.85), (59.88, 28.45, 2.50), mats["Mahogany_Dark"], bevel=True, bevel_width=0.03)
    add_oriented_cylinder(c_lobby, "Grandfather_Clock_Dial", (59.40, 28.0, 2.15), (59.30, 28.0, 2.15), radius=0.28, material=mats["Road_White"], segments=24, is_collider=False)
    add_cylinder(c_lobby, "Grandfather_Clock_Finial", (59.60, 28.0, 2.58), radius=0.05, height=0.16, material=mats["Brass_Polished"], segments=12, is_collider=False)

    # Polished Standing Brass Coat Rack with Umbrellas
    add_cylinder(c_lobby, "Coat_Rack_Base", (56.0, 25.5, 0.04), radius=0.22, height=0.08, material=mats["Brass_Polished"], segments=16, is_collider=False)
    add_oriented_cylinder(c_lobby, "Coat_Rack_Pole", (56.0, 25.5, 0.08), (56.0, 25.5, 1.85), radius=0.03, material=mats["Brass_Polished"], segments=8, is_collider=False)
    add_cylinder(c_lobby, "Coat_Rack_Ring", (56.0, 25.5, 0.65), radius=0.18, height=0.04, material=mats["Brass_Polished"], segments=12, is_collider=False)
    for cri in range(4):
        cr_ang = cri * (math.pi / 2.0)
        cr_hx = 56.0 + math.cos(cr_ang) * 0.22
        cr_hy = 25.5 + math.sin(cr_ang) * 0.22
        add_oriented_cylinder(c_lobby, f"Coat_Hook_{cri}", (56.0, 25.5, 1.75), (cr_hx, cr_hy, 1.82), radius=0.015, material=mats["Brass_Polished"], segments=6, is_collider=False)

    # =========================================================================
    # 5. MEZZANINE & EXECUTIVE BALCONIES (z = 5.00m)
    # =========================================================================
    # West Mezzanine Balcony Floor & Overlook (x: 4.0..20.0, y: 22.0..46.0)
    add_box(c_lobby, "Mezzanine_Floor_West", (4.0, 22.0, 4.70), (20.0, 46.0, 5.00), mats["Marble_Cream"])
    # East Mezzanine Balcony Floor & Overlook (x: 44.0..60.0, y: 22.0..46.0)
    add_box(c_lobby, "Mezzanine_Floor_East", (44.0, 22.0, 4.70), (60.0, 46.0, 5.00), mats["Marble_Cream"])
    # North Connecting Catwalk Mezzanine (x: 20.0..44.0, y: 43.0..46.0)
    add_box(c_lobby, "Mezzanine_Catwalk_North", (20.0, 43.0, 4.70), (44.0, 46.0, 5.00), mats["Marble_Cream"])

    # -------------------------------------------------------------------------
    # AUTHENTIC BEAUX-ARTS OPEN BALUSTRADE (ELIMINATING THE SOLID LEGO WALL!)
    # -------------------------------------------------------------------------
    # Invisible Solid Physics Colliders (Prevent falling / jumping off edge)
    add_box(c_lobby, "Balcony_Rail_West_ColOnly", (19.85, 22.0, 5.00), (20.15, 43.0, 6.18), mats["Plaster_Ceiling"])
    add_box(c_lobby, "Balcony_Rail_East_ColOnly", (43.85, 22.0, 5.00), (44.15, 43.0, 6.18), mats["Plaster_Ceiling"])
    add_box(c_lobby, "Balcony_Rail_North_ColOnly", (20.0, 42.7, 5.00), (44.0, 43.0, 6.18), mats["Plaster_Ceiling"])

    # Continuous Marble Plinth Base Rails along Balcony Edges
    add_box(c_lobby, "Balcony_Plinth_West", (19.82, 22.0, 5.00), (20.18, 43.0, 5.14), mats["Marble_Cream"], bevel=True, bevel_width=0.015, is_collider=False)
    add_box(c_lobby, "Balcony_Plinth_East", (43.82, 22.0, 5.00), (44.18, 43.0, 5.14), mats["Marble_Cream"], bevel=True, bevel_width=0.015, is_collider=False)
    add_box(c_lobby, "Balcony_Plinth_North", (20.0, 42.72, 5.00), (44.0, 42.98, 5.14), mats["Marble_Cream"], bevel=True, bevel_width=0.015, is_collider=False)

    # Molded Bullnose Mahogany Top Handrails (z = 6.04..6.18m)
    add_box(c_lobby, "Balcony_Handrail_West", (19.78, 22.0, 6.04), (20.22, 43.0, 6.18), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(c_lobby, "Balcony_Handrail_East", (43.78, 22.0, 6.04), (44.22, 43.0, 6.18), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(c_lobby, "Balcony_Handrail_North", (20.0, 42.68, 6.04), (44.0, 43.02, 6.18), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02, is_collider=False)

    # Classical Square Newel Pedestals & Polished Bronze Urn Finials
    # 1. West Balcony Newels
    for n_y in [22.0, 25.5, 29.0, 32.5, 36.0, 39.5, 43.0]:
        add_box(c_lobby, f"Newel_West_{int(n_y*10)}", (19.82, n_y - 0.14, 5.00), (20.18, n_y + 0.14, 6.16), mats["Marble_Cream"], bevel=True, bevel_width=0.02, is_collider=False)
        add_cylinder(c_lobby, f"Newel_Finial_W_{int(n_y*10)}", (20.0, n_y, 6.22), radius=0.065, height=0.12, material=mats["Brass_Polished"], segments=12, is_collider=False)
    # 2. East Balcony Newels
    for n_y in [22.0, 25.5, 29.0, 32.5, 36.0, 39.5, 43.0]:
        add_box(c_lobby, f"Newel_East_{int(n_y*10)}", (43.82, n_y - 0.14, 5.00), (44.18, n_y + 0.14, 6.16), mats["Marble_Cream"], bevel=True, bevel_width=0.02, is_collider=False)
        add_cylinder(c_lobby, f"Newel_Finial_E_{int(n_y*10)}", (44.0, n_y, 6.22), radius=0.065, height=0.12, material=mats["Brass_Polished"], segments=12, is_collider=False)
    # 3. North Catwalk Newels
    for n_x in [24.0, 28.0, 32.0, 36.0, 40.0]:
        add_box(c_lobby, f"Newel_North_{int(n_x*10)}", (n_x - 0.14, 42.72, 5.00), (n_x + 0.14, 42.98, 6.16), mats["Marble_Cream"], bevel=True, bevel_width=0.02, is_collider=False)
        add_cylinder(c_lobby, f"Newel_Finial_N_{int(n_x*10)}", (n_x, 42.85, 6.22), radius=0.065, height=0.12, material=mats["Brass_Polished"], segments=12, is_collider=False)

    # Classical Turned Architectural Balusters / Spindles (See-through Beaux-Arts elegance!)
    # West Balcony Spindles
    for sp_y_int in range(224, 428, 4):
        sp_y = float(sp_y_int) / 10.0
        # Skip if close to a newel
        if any(abs(sp_y - ny) < 0.20 for ny in [22.0, 25.5, 29.0, 32.5, 36.0, 39.5, 43.0]):
            continue
        add_cylinder(c_lobby, f"Baluster_W_{sp_y_int}", (20.0, sp_y, 5.58), radius=0.035, height=0.88, material=mats["Marble_Cream"], segments=8, is_collider=False)
        add_cylinder(c_lobby, f"Baluster_W_Ring_{sp_y_int}", (20.0, sp_y, 5.75), radius=0.048, height=0.06, material=mats["Bronze_Architectural"], segments=8, is_collider=False)

    # East Balcony Spindles
    for sp_y_int in range(224, 428, 4):
        sp_y = float(sp_y_int) / 10.0
        if any(abs(sp_y - ny) < 0.20 for ny in [22.0, 25.5, 29.0, 32.5, 36.0, 39.5, 43.0]):
            continue
        add_cylinder(c_lobby, f"Baluster_E_{sp_y_int}", (44.0, sp_y, 5.58), radius=0.035, height=0.88, material=mats["Marble_Cream"], segments=8, is_collider=False)
        add_cylinder(c_lobby, f"Baluster_E_Ring_{sp_y_int}", (44.0, sp_y, 5.75), radius=0.048, height=0.06, material=mats["Bronze_Architectural"], segments=8, is_collider=False)

    # North Catwalk Spindles
    for sp_x_int in range(204, 438, 4):
        sp_x = float(sp_x_int) / 10.0
        if any(abs(sp_x - nx) < 0.20 for nx in [24.0, 28.0, 32.0, 36.0, 40.0]):
            continue
        add_cylinder(c_lobby, f"Baluster_N_{sp_x_int}", (sp_x, 42.85, 5.58), radius=0.035, height=0.88, material=mats["Marble_Cream"], segments=8, is_collider=False)
        add_cylinder(c_lobby, f"Baluster_N_Ring_{sp_x_int}", (sp_x, 42.85, 5.75), radius=0.048, height=0.06, material=mats["Bronze_Architectural"], segments=8, is_collider=False)

    # Executive Burgundy Runners on Mezzanines
    add_box(c_lobby, "Balcony_Carpet_Runner_W", (6.0, 23.0, 5.005), (14.0, 44.0, 5.015), mats["Carpet_Burgundy"], is_collider=False)
    add_box(c_lobby, "Balcony_Carpet_Runner_E", (50.0, 23.0, 5.005), (58.0, 44.0, 5.015), mats["Carpet_Burgundy"], is_collider=False)

    # Executive Chesterfield Tufted Leather Sofa on East Balcony (Replacing block sofa!)
    add_box(c_lobby, "Chesterfield_Base_Frame", (52.0, 43.5, 5.00), (56.0, 45.5, 5.45), mats["Chesterfield_Leather"], bevel=True, bevel_width=0.04)
    add_box(c_lobby, "Chesterfield_Cushions", (52.2, 43.7, 5.45), (55.8, 45.1, 5.65), mats["Chesterfield_Leather"], bevel=True, bevel_width=0.03)
    add_box(c_lobby, "Chesterfield_Backrest", (52.0, 45.1, 5.45), (56.0, 45.6, 6.30), mats["Chesterfield_Leather"], bevel=True, bevel_width=0.05)
    add_cylinder(c_lobby, "Chesterfield_Arm_L", (52.2, 44.4, 5.75), radius=0.28, height=1.60, material=mats["Chesterfield_Leather"], is_collider=False)
    add_cylinder(c_lobby, "Chesterfield_Arm_R", (55.8, 44.4, 5.75), radius=0.28, height=1.60, material=mats["Chesterfield_Leather"], is_collider=False)
    # Turned wooden bun feet
    for fx, fy in [(52.2, 43.7), (55.8, 43.7), (52.2, 45.3), (55.8, 45.3)]:
        add_cylinder(c_lobby, f"Sofa_Foot_{int(fx*10)}_{int(fy*10)}", (fx, fy, 5.05), radius=0.06, height=0.10, material=mats["Mahogany_Dark"], segments=8, is_collider=False)

    # Low Mahogany Coffee Table with Tempered Glass Top
    add_box(c_lobby, "Coffee_Table_Base", (53.0, 41.5, 5.00), (55.0, 42.7, 5.45), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    add_box(c_lobby, "Coffee_Table_Glass", (52.9, 41.4, 5.45), (55.1, 42.8, 5.48), mats["Security_Glass"], is_collider=False)

    # Authentic Grand Staircases (West and East Balconies) with Cast-Iron Balustrades
    for stair_name, sx, sy_start, sy_end in [("Stairs_West", 4.0, 22.0, 36.0), ("Stairs_East", 52.0, 36.0, 22.0)]:
        num_steps = 16
        step_len = abs(sy_end - sy_start) / float(num_steps)
        step_h = 5.0 / float(num_steps)
        step_sign = 1.0 if sy_end > sy_start else -1.0
        for st in range(num_steps):
            y_curr = sy_start + st * step_len * step_sign
            y_next = y_curr + step_len * step_sign
            z_curr = st * step_h
            y_min, y_max = min(y_curr, y_next), max(y_curr, y_next)
            add_box(c_arch, f"{stair_name}_Step_{st}", (sx, y_min, 0.0), (sx + 4.0, y_max, z_curr + step_h), mats["Marble_White"])
            # Cast-iron baluster and mahogany handrail along the inside open edge
            rail_x = sx + 4.0 if sx == 4.0 else sx
            add_oriented_cylinder(c_lobby, f"{stair_name}_Baluster_{st}", (rail_x, (y_min+y_max)/2.0, z_curr + step_h), (rail_x, (y_min+y_max)/2.0, z_curr + step_h + 1.05), radius=0.025, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)
        # Continuous sloping mahogany handrail
        h_y0 = sy_start
        h_y1 = sy_end
        rail_x = sx + 4.0 if sx == 4.0 else sx
        add_oriented_cylinder(c_lobby, f"{stair_name}_Handrail", (rail_x, h_y0, 1.05), (rail_x, h_y1, 6.05), radius=0.045, material=mats["Mahogany_Polished"], segments=8, is_collider=False)

    # =========================================================================
    # 6. REAR OFFICES & CORRIDOR PARTITION WALLS (y: 46.0..50.0)
    # =========================================================================
    add_box(c_arch, "Wall_Dividing_West", (4.0, 46.0, 0.0), (10.0, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Wall_Dividing_MidW", (16.0, 46.0, 0.0), (28.0, 49.0, 14.0), mats["Limestone_Facade"])
    # Cut open portal for Vault Access Corridor (clearing x = 43.5..50.8)
    add_box(c_arch, "Wall_Dividing_MidE", (36.0, 46.0, 0.0), (43.5, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Wall_Dividing_East", (54.0, 46.0, 0.0), (60.0, 49.0, 14.0), mats["Limestone_Facade"])

    add_box(c_arch, "Header_Door_W", (10.0, 46.0, 3.8), (16.0, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Header_Door_C", (28.0, 46.0, 3.8), (36.0, 49.0, 14.0), mats["Limestone_Facade"])
    add_box(c_arch, "Header_Door_E", (43.5, 46.0, 7.8), (54.0, 49.0, 14.0), mats["Limestone_Facade"])

    # Finished Corridor Ceiling over Central Walkway (x: 28.0..44.0, y: 46.0..50.0, z = 4.5m)
    add_box(c_arch, "Corridor_Ceiling_Slab", (28.0, 46.0, 4.4), (44.0, 50.0, 4.6), mats["Plaster_Ceiling"], is_collider=False)
    # Polished mahogany cornice molding along corridor ceiling edges
    add_box(c_arch, "Corridor_Cornice_S", (28.0, 46.0, 4.30), (44.0, 46.16, 4.45), mats["Mahogany_Polished"], is_collider=False)
    add_box(c_arch, "Corridor_Cornice_N", (28.0, 49.84, 4.30), (44.0, 50.0, 4.45), mats["Mahogany_Polished"], is_collider=False)
    # Recessed warm lamps in corridor ceiling
    add_cylinder(c_arch, "Corridor_Lamp_1", (33.0, 48.0, 4.38), radius=0.22, height=0.08, material=mats["Sconce_Lamp"], segments=16, is_collider=False)
    add_cylinder(c_arch, "Corridor_Lamp_2", (39.0, 48.0, 4.38), radius=0.22, height=0.08, material=mats["Sconce_Lamp"], segments=16, is_collider=False)

    # =========================================================================
    # 7. THE VAULT (Site A - x: 40.0..60.0, y: 46.0..60.0)
    # =========================================================================
    add_box(c_arch, "Vault_Reinforced_Wall_W", (39.0, 49.0, 0.0), (41.0, 60.0, 10.0), mats["Vault_Steel"])

    # Reinforced Vault Concrete Ceiling at z=8.0m (Extending from y=46.0 across vault corridor!)
    add_box(c_vault, "Vault_Ceiling_Concrete", (40.0, 46.0, 8.0), (60.0, 60.0, 8.3), mats["Vault_Steel_Dark"], is_collider=False)

    # Heavy Structural Steel Wide-Flange I-Beams across Vault Ceiling (Catching lighting!)
    for ib_y in [48.0, 52.0, 56.0]:
        # Top flange
        add_box(c_vault, f"Vault_IBeam_Top_{int(ib_y)}", (40.5, ib_y - 0.22, 7.92), (59.5, ib_y + 0.22, 8.00), mats["Steel_Structural"], is_collider=False)
        # Center vertical web (receives direct side/fill light!)
        add_box(c_vault, f"Vault_IBeam_Web_{int(ib_y)}", (40.5, ib_y - 0.04, 7.60), (59.5, ib_y + 0.04, 7.92), mats["Steel_Structural"], is_collider=False)
        # Bottom flange
        add_box(c_vault, f"Vault_IBeam_Bot_{int(ib_y)}", (40.5, ib_y - 0.22, 7.52), (59.5, ib_y + 0.22, 7.60), mats["Steel_Structural"], is_collider=False)

    # Heavy Yellow Overhead Crane Hoist Rail running North-South
    add_box(c_vault, "Vault_Crane_Rail", (48.8, 47.0, 7.42), (49.2, 58.0, 7.52), mats["Hazard_Yellow"], is_collider=False)
    # Yellow Hoist Trolley with Steel Lifting Hook
    add_box(c_vault, "Vault_Hoist_Trolley", (48.6, 52.0, 7.18), (49.4, 53.0, 7.44), mats["Hazard_Yellow"], bevel=True, bevel_width=0.02, is_collider=False)
    add_cylinder(c_vault, "Vault_Hoist_Hook_Shank", (49.0, 52.5, 7.06), radius=0.04, height=0.24, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    add_cylinder(c_vault, "Vault_Hoist_Hook_Loop", (49.0, 52.5, 6.90), radius=0.12, height=0.08, material=mats["Chrome_Polished"], segments=12, is_collider=False)

    # Galvanized Electrical Conduit Lines connecting Bulkhead Lamps
    for vl_x in [45.0, 53.0]:
        add_box(c_vault, f"Vault_Conduit_Y_{int(vl_x)}", (vl_x - 0.02, 50.0, 7.94), (vl_x + 0.02, 58.0, 7.98), mats["Deposit_Box_Steel"], is_collider=False)
    add_box(c_vault, "Vault_Conduit_X", (45.0, 54.0 - 0.02, 7.94), (53.0, 54.0 + 0.02, 7.98), mats["Deposit_Box_Steel"], is_collider=False)

    # Industrial Caged Bulkhead Vault Lamps
    for vl_x in [45.0, 53.0]:
        for vl_y in [52.0, 56.0]:
            add_box(c_vault, f"Vault_Bulkhead_Base_{int(vl_x)}_{int(vl_y)}", (vl_x - 0.25, vl_y - 0.25, 7.88), (vl_x + 0.25, vl_y + 0.25, 8.0), mats["Cast_Iron_Dark"], is_collider=False)
            add_cylinder(c_vault, f"Vault_Bulkhead_Globe_{int(vl_x)}_{int(vl_y)}", (vl_x, vl_y, 7.78), radius=0.18, height=0.20, material=mats["Chandelier_Lamp"], segments=12, is_collider=False)

    # Corner Surveillance Camera in Vault
    add_box(c_vault, "Vault_CCTV_Bracket", (58.2, 57.8, 7.5), (58.5, 58.1, 7.7), mats["Deposit_Box_Steel"], is_collider=False)
    add_cylinder(c_vault, "Vault_CCTV_Dome", (58.35, 57.95, 7.42), radius=0.14, height=0.12, material=mats["Smoked_Glass"], segments=12, is_collider=False)
    add_cylinder(c_vault, "Vault_CCTV_LED", (58.35, 57.95, 7.36), radius=0.02, height=0.02, material=mats["Emergency_Red"], segments=8, is_collider=False)

    # 10-Inch Wall-Mounted Red Industrial Alarm Bell Gong
    add_cylinder(c_vault, "Vault_Alarm_Bell", (41.04, 52.0, 4.0), radius=0.22, height=0.08, material=mats["Emergency_Red"], segments=16, is_collider=False)
    add_cylinder(c_vault, "Vault_Alarm_Striker", (41.04, 51.72, 4.0), radius=0.03, height=0.06, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)

    # Heavy Vault Portal Jambs with Yellow/Black Warning Frames
    add_box(c_vault, "Vault_Portal_Jamb_L", (43.5, 48.8, 0.0), (44.8, 50.8, 7.8), mats["Hazard_Yellow"], bevel=True, bevel_width=0.04)
    add_box(c_vault, "Vault_Portal_Jamb_R", (50.8, 48.8, 0.0), (52.2, 50.8, 7.8), mats["Hazard_Yellow"], bevel=True, bevel_width=0.04)
    add_box(c_vault, "Vault_Portal_Header", (43.5, 48.8, 7.2), (52.2, 50.8, 8.0), mats["Hazard_Yellow"], bevel=True, bevel_width=0.04)
    # Hazard Diagonal Stripes on Portal Face
    add_box(c_vault, "Vault_Hazard_Stripe_L_Stripe", (43.45, 48.75, 0.5), (44.85, 48.85, 7.2), mats["Hazard_Black"], is_collider=False)
    add_box(c_vault, "Vault_Hazard_Stripe_R_Stripe", (50.75, 48.75, 0.5), (52.25, 48.85, 7.2), mats["Hazard_Black"], is_collider=False)

    # -------------------------------------------------------------------------
    # MASSIVE CIRCULAR VAULT BLAST DOOR (Swung 70 deg open against portal wall)
    # -------------------------------------------------------------------------
    door_cx = 45.4
    door_cy = 47.0
    door_cz = 3.6
    door_ang = math.radians(70)
    # Normal vector perpendicular to door circular face
    nx = math.cos(door_ang)
    ny = math.sin(door_ang)
    # Vector in door plane (horizontal)
    tx = -ny
    ty = nx

    # Invisible Physics Collider Box for open door (Named with _ColOnly so renderer skips it!)
    add_box(c_vault, "Vault_Door_Bulk_Collider_ColOnly", (43.8, 45.5, 0.0), (46.5, 48.8, 7.2), mats["Vault_Steel"])

    # Multi-Step Circular Vault Door Body (Vertical upright cylinder)
    p_back = (door_cx - nx * 0.25, door_cy - ny * 0.25, door_cz)
    p_front = (door_cx + nx * 0.25, door_cy + ny * 0.25, door_cz)
    add_oriented_cylinder(c_vault, "Vault_Door_Outer_Rim", p_back, p_front, radius=1.85, material=mats["Vault_Steel"], segments=36, is_collider=False)
    p_core_b = (door_cx - nx * 0.15, door_cy - ny * 0.15, door_cz)
    p_core_f = (door_cx + nx * 0.35, door_cy + ny * 0.35, door_cz)
    add_oriented_cylinder(c_vault, "Vault_Door_Stepped_Core", p_core_b, p_core_f, radius=1.55, material=mats["Chrome_Polished"], segments=36, is_collider=False)

    # 16 Hardened Chrome Perimeter Locking Lugs / Bolts (extending radially)
    num_lugs = 16
    for bi in range(num_lugs):
        b_angle = (2.0 * math.pi * bi) / float(num_lugs)
        c_rad = math.cos(b_angle)
        s_rad = math.sin(b_angle)
        lx_in = door_cx + (tx * c_rad) * 1.65
        ly_in = door_cy + (ty * c_rad) * 1.65
        lz_in = door_cz + s_rad * 1.65
        lx_out = door_cx + (tx * c_rad) * 2.05
        ly_out = door_cy + (ty * c_rad) * 2.05
        lz_out = door_cz + s_rad * 2.05
        # Chrome Lug Bolt
        add_oriented_cylinder(c_vault, f"Vault_Bolt_{bi}", (lx_in, ly_in, lz_in), (lx_out, ly_out, lz_out), radius=0.08, material=mats["Chrome_Polished"], segments=12, is_collider=False)
        # Bronze Guide Bushing Collar
        lx_bush = door_cx + (tx * c_rad) * 1.82
        ly_bush = door_cy + (ty * c_rad) * 1.82
        lz_bush = door_cz + s_rad * 1.82
        add_oriented_cylinder(c_vault, f"Vault_Bushing_{bi}", (lx_in, ly_in, lz_in), (lx_bush, ly_bush, lz_bush), radius=0.12, material=mats["Bronze_Bushing"], segments=12, is_collider=False)

    # Heavy Forged Crane-Neck Hinge Arm connecting Wall to Door Center
    add_box(c_vault, "Vault_Hinge_Wall_Bracket", (43.5, 48.4, 1.8), (44.5, 49.8, 5.4), mats["Vault_Steel_Dark"], bevel=True, bevel_width=0.03, is_collider=False)
    add_cylinder(c_vault, "Vault_Hinge_Pin_Upper", (44.0, 48.6, 4.8), radius=0.18, height=0.90, material=mats["Chrome_Polished"], segments=16, is_collider=False)
    add_cylinder(c_vault, "Vault_Hinge_Pin_Lower", (44.0, 48.6, 2.4), radius=0.18, height=0.90, material=mats["Chrome_Polished"], segments=16, is_collider=False)
    add_oriented_cylinder(c_vault, "Vault_Hinge_Arm_Upper", (44.0, 48.6, 4.6), (door_cx, door_cy, 4.2), radius=0.18, material=mats["Vault_Steel_Dark"], segments=16, is_collider=False)
    add_oriented_cylinder(c_vault, "Vault_Hinge_Arm_Lower", (44.0, 48.6, 2.6), (door_cx, door_cy, 3.0), radius=0.18, material=mats["Vault_Steel_Dark"], segments=16, is_collider=False)

    # Center Marine 4-Spoke Chrome Handwheel on Door Front Face
    hw_cx = door_cx + nx * 0.42
    hw_cy = door_cy + ny * 0.42
    hw_cz = door_cz
    add_oriented_cylinder(c_vault, "Vault_Handwheel_Hub", (hw_cx - nx*0.05, hw_cy - ny*0.05, hw_cz), (hw_cx + nx*0.15, hw_cy + ny*0.15, hw_cz), radius=0.28, material=mats["Chrome_Polished"], segments=20, is_collider=False)
    # Handwheel Spokes & Grips
    for sp_i in range(4):
        spa = sp_i * (math.pi / 2.0)
        sx = hw_cx + (tx * math.cos(spa)) * 0.75
        sy = hw_cy + (ty * math.cos(spa)) * 0.75
        sz = hw_cz + math.sin(spa) * 0.75
        add_oriented_cylinder(c_vault, f"Vault_HW_Spoke_{sp_i}", (hw_cx, hw_cy, hw_cz), (sx, sy, sz), radius=0.04, material=mats["Chrome_Polished"], segments=8, is_collider=False)
        add_oriented_cylinder(c_vault, f"Vault_HW_Grip_{sp_i}", (sx, sy, sz), (sx + nx*0.16, sy + ny*0.16, sz), radius=0.05, material=mats["Hazard_Black"], segments=8, is_collider=False)

    # Dual Sargent & Greenleaf Brass Combination Dials
    for dial_i, d_sign in enumerate([-0.65, 0.65]):
        dx_c = hw_cx + tx * d_sign
        dy_c = hw_cy + ty * d_sign
        dz_c = hw_cz + 0.85
        add_oriented_cylinder(c_vault, f"Vault_Dial_{dial_i}", (dx_c, dy_c, dz_c), (dx_c + nx*0.12, dy_c + ny*0.12, dz_c), radius=0.18, material=mats["Brass_Polished"], segments=20, is_collider=False)
        add_oriented_cylinder(c_vault, f"Vault_Dial_Knob_{dial_i}", (dx_c + nx*0.08, dy_c + ny*0.08, dz_c), (dx_c + nx*0.18, dy_c + ny*0.18, dz_c), radius=0.07, material=mats["Chrome_Polished"], segments=12, is_collider=False)

    # Triple-Movement Swiss Mechanical Time Lock Display Case
    tl_cx = hw_cx
    tl_cy = hw_cy
    tl_cz = hw_cz - 0.95
    add_box(c_vault, "Vault_TimeLock_Case", (tl_cx - 0.40, tl_cy - 0.15, tl_cz - 0.25), (tl_cx + 0.40, tl_cy + 0.15, tl_cz + 0.25), mats["Brass_Polished"], is_collider=False)
    for ti in [-0.22, 0.0, 0.22]:
        add_oriented_cylinder(c_vault, f"Vault_Time_Dial_{int(ti*100)}", (tl_cx + ti, tl_cy - 0.16, tl_cz), (tl_cx + ti, tl_cy - 0.12, tl_cz), radius=0.08, material=mats["Road_White"], segments=16, is_collider=False)

    # -------------------------------------------------------------------------
    # VAULT INTERIOR CONTENTS (Site A Wealth & Tactical Cover)
    # -------------------------------------------------------------------------
    # Safety Deposit Locker Walls (East and North interior walls)
    add_box(c_vault, "Deposit_Lockers_East", (56.5, 51.0, 0.0), (58.5, 58.0, 8.0), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.03)
    add_box(c_vault, "Deposit_Lockers_North", (41.0, 56.5, 0.0), (56.5, 58.0, 8.0), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.03)

    # Individual Safety Deposit Box Grid with Bronze Number Plates & Dual Keyholes
    # 1. North Wall Lockers (y = 56.48, x: 41.5..56.0)
    for lz in [0.6, 1.4, 2.2, 3.0, 3.8, 4.6, 5.4, 6.2, 7.0]:
        add_box(c_vault, f"Locker_Seam_N_{int(lz*10)}", (41.5, 56.45, lz), (56.0, 56.52, lz + 0.03), mats["Cast_Iron_Dark"], is_collider=False)
        for kx in range(42, 56, 1):
            add_box(c_vault, f"Locker_Plate_N_{kx}_{int(lz*10)}", (float(kx) + 0.2, 56.43, lz + 0.45), (float(kx) + 0.6, 56.46, lz + 0.55), mats["Bronze_Architectural"], is_collider=False)
            add_cylinder(c_vault, f"Locker_Key1_N_{kx}_{int(lz*10)}", (float(kx) + 0.3, 56.42, lz + 0.25), radius=0.015, height=0.03, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)
            add_cylinder(c_vault, f"Locker_Key2_N_{kx}_{int(lz*10)}", (float(kx) + 0.5, 56.42, lz + 0.25), radius=0.015, height=0.03, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)

    # 2. East Wall Lockers (x = 56.48, y: 51.5..57.5)
    for lz in [0.6, 1.4, 2.2, 3.0, 3.8, 4.6, 5.4, 6.2, 7.0]:
        add_box(c_vault, f"Locker_Seam_E_{int(lz*10)}", (56.45, 51.5, lz), (56.52, 57.5, lz + 0.03), mats["Cast_Iron_Dark"], is_collider=False)
        for ky in range(52, 58, 1):
            add_box(c_vault, f"Locker_Plate_E_{ky}_{int(lz*10)}", (56.43, float(ky) + 0.2, lz + 0.45), (56.46, float(ky) + 0.6, lz + 0.55), mats["Bronze_Architectural"], is_collider=False)
            add_cylinder(c_vault, f"Locker_Key1_E_{ky}_{int(lz*10)}", (56.42, float(ky) + 0.3, lz + 0.25), radius=0.015, height=0.03, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)
            add_cylinder(c_vault, f"Locker_Key2_E_{ky}_{int(lz*10)}", (56.42, float(ky) + 0.5, lz + 0.25), radius=0.015, height=0.03, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)

    # -------------------------------------------------------------------------
    # SAFETY DEPOSIT LOCKER ROLLING LIBRARY LADDER ON TUBULAR BRASS RAIL
    # -------------------------------------------------------------------------
    # Overhead Tubular Brass Guide Rails (z = 7.40m)
    add_oriented_cylinder(c_vault, "Vault_Ladder_Rail_N", (41.5, 56.38, 7.40), (56.0, 56.38, 7.40), radius=0.035, material=mats["Brass_Polished"], segments=8, is_collider=False)
    add_oriented_cylinder(c_vault, "Vault_Ladder_Rail_E", (56.38, 51.5, 7.40), (56.38, 57.5, 7.40), radius=0.035, material=mats["Brass_Polished"], segments=8, is_collider=False)
    # Standoff Wall Brackets
    for bx in [42.0, 46.0, 50.0, 55.0]:
        add_oriented_cylinder(c_vault, f"Ladder_Bracket_N_{int(bx)}", (bx, 56.38, 7.40), (bx, 56.50, 7.40), radius=0.025, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)

    # Wheeled Rolling Library Ladder (Resting along North Wall at x = 49.0)
    lad_x = 49.0
    lad_y_bot, lad_y_top = 55.80, 56.32
    # Dual Upright Mahogany Stiles
    for lx_off in [-0.28, 0.28]:
        add_oriented_cylinder(c_vault, f"Ladder_Stile_{int((lad_x+lx_off)*10)}", (lad_x + lx_off, lad_y_bot, 0.08), (lad_x + lx_off, lad_y_top, 7.55), radius=0.035, material=mats["Mahogany_Polished"], segments=8, is_collider=False)
        # Top Trolley Hook Bracket with Brass Grooved Wheels
        add_cylinder(c_vault, f"Ladder_Top_Wheel_{int((lad_x+lx_off)*10)}", (lad_x + lx_off, 56.38, 7.48), radius=0.055, height=0.04, material=mats["Brass_Polished"], segments=12, is_collider=False)
        # Bottom Rubber Floor Caster
        add_cylinder(c_vault, f"Ladder_Bot_Wheel_{int((lad_x+lx_off)*10)}", (lad_x + lx_off, lad_y_bot, 0.05), radius=0.045, height=0.03, material=mats["Hazard_Black"], segments=8, is_collider=False)
    # 14 Turned Mahogany Rungs
    for r_i in range(14):
        rz = 0.50 + r_i * 0.50
        ry = lad_y_bot + (lad_y_top - lad_y_bot) * (rz / 7.40)
        add_oriented_cylinder(c_vault, f"Ladder_Rung_{r_i}", (lad_x - 0.28, ry, rz), (lad_x + 0.28, ry, rz), radius=0.02, material=mats["Mahogany_Polished"], segments=6, is_collider=False)
    # Curved Upper Safety Grab Handles
    add_oriented_cylinder(c_vault, "Ladder_Handle_L", (lad_x - 0.28, lad_y_top, 7.40), (lad_x - 0.28, lad_y_top - 0.20, 7.80), radius=0.018, material=mats["Brass_Polished"], segments=6, is_collider=False)
    add_oriented_cylinder(c_vault, "Ladder_Handle_R", (lad_x + 0.28, lad_y_top, 7.40), (lad_x + 0.28, lad_y_top - 0.20, 7.80), radius=0.018, material=mats["Brass_Polished"], segments=6, is_collider=False)

    # -------------------------------------------------------------------------
    # INNER HEAVY STEEL SECURITY DAY GATE (GRILLE DOOR)
    # -------------------------------------------------------------------------
    # Hinged open inside the portal threshold (x: 45.0..50.6, y: 50.4, z: 0.0..3.8m)
    add_box(c_vault, "Day_Gate_Outer_Frame", (45.0, 50.35, 0.0), (50.6, 50.45, 3.80), mats["Vault_Steel_Dark"], is_collider=False)
    for gy in range(452, 506, 3):
        gx = float(gy) / 10.0
        add_oriented_cylinder(c_vault, f"Day_Gate_Picket_{gy}", (gx, 50.40, 0.05), (gx, 50.40, 3.75), radius=0.022, material=mats["Vault_Steel"], segments=6, is_collider=False)
        # Spearpoint finials
        add_cylinder(c_vault, f"Day_Gate_Spear_{gy}", (gx, 50.40, 3.78), radius=0.035, height=0.08, material=mats["Bronze_Architectural"], segments=4, is_collider=False)
    # Center Mortise Deadbolt Lockbox & Heavy Bronze Handle
    add_box(c_vault, "Day_Gate_Lockbox", (47.5, 50.30, 1.40), (48.3, 50.50, 1.90), mats["Vault_Steel_Dark"], is_collider=False)
    add_cylinder(c_vault, "Day_Gate_Key_Cylinder", (47.9, 50.28, 1.65), radius=0.025, height=0.05, material=mats["Brass_Polished"], segments=8, is_collider=False)
    add_oriented_cylinder(c_vault, "Day_Gate_Pull_Handle", (48.1, 50.26, 1.50), (48.1, 50.26, 1.80), radius=0.018, material=mats["Brass_Polished"], segments=6, is_collider=False)

    # -------------------------------------------------------------------------
    # CUSTOMER SAFETY DEPOSIT BOX VIEWING / EXAMINATION TABLE
    # -------------------------------------------------------------------------
    # Stainless Steel Examination Table in Vault Center (x: 48.0..50.6, y: 54.5..55.7)
    add_box(c_vault, "Viewing_Table_Top", (48.0, 54.5, 0.88), (50.6, 55.7, 0.95), mats["Stainless_Steel"], bevel=True, bevel_width=0.02)
    for leg_x, leg_y in [(48.15, 54.65), (50.45, 54.65), (48.15, 55.55), (50.45, 55.55)]:
        add_cylinder(c_vault, f"Viewing_Table_Leg_{int(leg_x*10)}_{int(leg_y*10)}", (leg_x, leg_y, 0.44), radius=0.04, height=0.88, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    # Inspection Desk Lamp with Flexible Brass Arm
    add_cylinder(c_vault, "Viewing_Lamp_Base", (48.4, 55.4, 0.96), radius=0.08, height=0.02, material=mats["Brass_Polished"], segments=12, is_collider=False)
    add_oriented_cylinder(c_vault, "Viewing_Lamp_Arm", (48.4, 55.4, 0.97), (48.7, 55.2, 1.25), radius=0.015, material=mats["Brass_Polished"], segments=6, is_collider=False)
    add_cylinder(c_vault, "Viewing_Lamp_Shade", (48.7, 55.2, 1.25), radius=0.10, height=0.14, material=mats["Green_Glass_Banker"], segments=12, is_collider=False)
    # Brass-Rimmed Magnifying Glass on Table
    add_cylinder(c_vault, "Viewing_Magnifier_Rim", (49.5, 55.0, 0.96), radius=0.07, height=0.015, material=mats["Brass_Polished"], segments=16, is_collider=False)
    add_oriented_cylinder(c_vault, "Viewing_Magnifier_Handle", (49.57, 55.0, 0.96), (49.72, 55.0, 0.96), radius=0.01, material=mats["Mahogany_Dark"], segments=6, is_collider=False)
    # Black Velvet-Lined Inspection Trays
    add_box(c_vault, "Viewing_Tray_1", (48.8, 54.7, 0.952), (49.4, 55.3, 0.97), mats["Leather_Black"], is_collider=False)
    add_box(c_vault, "Viewing_Tray_2", (49.8, 54.7, 0.952), (50.4, 55.3, 0.97), mats["Leather_Black"], is_collider=False)

    # -------------------------------------------------------------------------
    # WALL-MOUNTED VAULT SECURITY & ENVIRONMENTAL CONSOLE
    # -------------------------------------------------------------------------
    # Security Console Cabinet on West Vault Wall (x = 41.05, y: 53.0..54.5, z = 1.8..3.0m)
    add_box(c_vault, "Vault_Console_Body", (41.02, 53.0, 1.80), (41.25, 54.5, 3.00), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.02, is_collider=False)
    # Analog Atmospheric Pressure & Humidity Gauges
    for gi, gz in enumerate([2.6, 2.2]):
        add_oriented_cylinder(c_vault, f"Vault_Gauge_Rim_{gi}", (41.26, 53.4, gz), (41.28, 53.4, gz), radius=0.14, material=mats["Brass_Polished"], segments=16, is_collider=False)
        add_oriented_cylinder(c_vault, f"Vault_Gauge_Dial_{gi}", (41.27, 53.4, gz), (41.29, 53.4, gz), radius=0.12, material=mats["Road_White"], segments=16, is_collider=False)
    # Seismograph Vibration Monitor Chart Drum
    add_cylinder(c_vault, "Vault_Seismo_Drum", (41.26, 54.0, 2.6), radius=0.12, height=0.28, material=mats["Paper_White"], segments=16, is_collider=False)
    # Keyed Emergency Lockout Switches & Pilot LEDs
    for li, lz in enumerate([2.1, 2.3, 2.5]):
        l_col = [mats["LED_Green"], mats["LED_Amber"], mats["Emergency_Red"]][li]
        add_cylinder(c_vault, f"Vault_Console_LED_{li}", (41.26, 54.2, lz), radius=0.02, height=0.02, material=l_col, segments=8, is_collider=False)
        add_cylinder(c_vault, f"Vault_Key_Switch_{li}", (41.26, 54.0, lz - 0.2), radius=0.015, height=0.03, material=mats["Chrome_Polished"], segments=6, is_collider=False)

    # -------------------------------------------------------------------------
    # AUTHENTIC FORT KNOX GOLD BULLION PALLET (ELIMINATING THE LEGO BLOCK!)
    # -------------------------------------------------------------------------
    # Invisible Solid Physics Collider (Top at z = 1.80m, Raycast Assertion Verified!)
    add_box(c_vault, "Gold_Bullion_Tier1_ColOnly", (44.0, 53.0, 0.0), (47.0, 56.0, 1.80), mats["Gold_Bullion"], bevel=True, bevel_width=0.02)

    # Heavy Oak Wood Cargo Pallet Underneath
    for sx in [44.05, 45.5, 46.95]:
        add_box(c_vault, f"Pallet_Stringer_{int(sx*10)}", (sx - 0.06, 52.8, 0.0), (sx + 0.06, 56.2, 0.12), mats["Pallet_Wood"], is_collider=False)
    for py in range(53, 57):
        add_box(c_vault, f"Pallet_Slat_{py}_A", (43.85, float(py) - 0.18, 0.12), (47.15, float(py) + 0.18, 0.16), mats["Pallet_Wood"], is_collider=False)

    # Wooden Dunnage Separator Battens between Tiers
    for d_z in [0.45, 0.90, 1.35, 1.80]:
        add_box(c_vault, f"Dunnage_S_{int(d_z*100)}", (43.90, 52.88, d_z - 0.025), (47.10, 53.05, d_z + 0.025), mats["Pallet_Wood"], is_collider=False)
        add_box(c_vault, f"Dunnage_E_{int(d_z*100)}", (46.95, 52.90, d_z - 0.025), (47.12, 56.10, d_z + 0.025), mats["Pallet_Wood"], is_collider=False)
        add_box(c_vault, f"Dunnage_N_{int(d_z*100)}", (43.90, 55.95, d_z - 0.025), (47.10, 56.12, d_z + 0.025), mats["Pallet_Wood"], is_collider=False)
        add_box(c_vault, f"Dunnage_W_{int(d_z*100)}", (43.88, 52.90, d_z - 0.025), (44.05, 56.10, d_z + 0.025), mats["Pallet_Wood"], is_collider=False)

    # Steel Banding Straps with Tension Buckles wrapping the pallet
    for st_z in [0.65, 1.55]:
        add_box(c_vault, f"Pallet_Strap_{int(st_z*100)}", (43.88, 52.88, st_z), (47.12, 56.12, st_z + 0.04), mats["Steel_Banding_Strap"], is_collider=False)
        add_box(c_vault, f"Pallet_Buckle_{int(st_z*100)}", (45.4, 52.82, st_z - 0.02), (45.6, 52.90, st_z + 0.06), mats["Chrome_Polished"], is_collider=False)

    # Densely Packed 24k Gold Bullion Ingots on ALL 4 VERTICAL SIDES
    ingot_z_rows = [0.20, 0.32, 0.55, 0.67, 0.79, 1.00, 1.12, 1.24, 1.45, 1.57, 1.69]
    # 1. South Face (y = 52.94)
    for rz in ingot_z_rows:
        for bx in [44.2, 44.55, 44.9, 45.25, 45.6, 45.95, 46.3, 46.65]:
            add_gold_ingot(c_vault, f"Gold_Ingot_S_{int(bx*100)}_{int(rz*100)}", (bx, 52.94, rz), rot_z_deg=0.0, material=mats["Gold_Bullion"])
    # 2. East Face (x = 47.06 - Facing incoming vault corridor!)
    for rz in ingot_z_rows:
        for by in [53.25, 53.65, 54.05, 54.45, 54.85, 55.25, 55.65]:
            add_gold_ingot(c_vault, f"Gold_Ingot_E_{int(by*100)}_{int(rz*100)}", (47.06, by, rz), rot_z_deg=90.0, material=mats["Gold_Bullion"])
    # 3. North Face (y = 56.06)
    for rz in ingot_z_rows:
        for bx in [44.2, 44.55, 44.9, 45.25, 45.6, 45.95, 46.3, 46.65]:
            add_gold_ingot(c_vault, f"Gold_Ingot_N_{int(bx*100)}_{int(rz*100)}", (bx, 56.06, rz), rot_z_deg=0.0, material=mats["Gold_Bullion_Dark"])
    # 4. West Face (x = 43.94)
    for rz in ingot_z_rows:
        for by in [53.25, 53.65, 54.05, 54.45, 54.85, 55.25, 55.65]:
            add_gold_ingot(c_vault, f"Gold_Ingot_W_{int(by*100)}_{int(rz*100)}", (43.94, by, rz), rot_z_deg=90.0, material=mats["Gold_Bullion_Dark"])

    # Full Solid Top Layer of Gold Ingots at z = 1.83m (covering the collider completely)
    for b_y in [53.2, 53.55, 53.9, 54.25, 54.6, 54.95, 55.3, 55.65, 55.9]:
        for b_x in [44.2, 44.6, 45.0, 45.4, 45.8, 46.2, 46.6, 46.9]:
            add_gold_ingot(c_vault, f"Gold_Top_{int(b_x*100)}_{int(b_y*100)}", (b_x, b_y, 1.83), rot_z_deg=0.0, material=mats["Gold_Bullion"])

    # Stepped Pyramid Upper Tiers of Gold Bars (z: 1.88..2.35m)
    for py_tier, (tz, inset) in enumerate([(1.89, 0.35), (2.03, 0.70), (2.17, 1.05)]):
        for b_x in [44.3 + inset + i * 0.42 for i in range(5 - py_tier)]:
            for b_y in [53.3 + inset + j * 0.42 for j in range(5 - py_tier)]:
                if b_x < 46.8 and b_y < 55.7:
                    add_gold_ingot(c_vault, f"Gold_Pyramid_{py_tier}_{int(b_x*10)}_{int(b_y*10)}", (b_x, b_y, tz), rot_z_deg=90.0 if py_tier % 2 == 1 else 0.0, material=mats["Gold_Bullion"])

    # Loose Angled Gold Bars Resting on Edge
    add_gold_ingot(c_vault, "Gold_Ingot_Loose_1", (46.85, 53.15, 1.85), rot_z_deg=22.0, material=mats["Gold_Bullion"])
    add_gold_ingot(c_vault, "Gold_Ingot_Loose_2", (44.25, 53.30, 1.85), rot_z_deg=-28.0, material=mats["Gold_Bullion"])
    add_gold_ingot(c_vault, "Gold_Ingot_Loose_Floor1", (43.50, 52.40, 0.03), rot_z_deg=35.0, material=mats["Gold_Bullion"])
    add_gold_ingot(c_vault, "Gold_Ingot_Loose_Floor2", (43.80, 52.10, 0.03), rot_z_deg=-15.0, material=mats["Gold_Bullion"])

    # High-Detail Wire-Mesh Cash Transport Cart with Banded Currency
    cart_cx, cart_cy = 50.8, 52.8
    add_box(c_vault, "Cash_Cart_Tubular_Frame", (cart_cx - 1.2, cart_cy - 1.2, 0.0), (cart_cx + 1.2, cart_cy + 1.2, 0.35), mats["Steel_Structural"], bevel=True, bevel_width=0.03)
    for (wx, wy) in [(cart_cx - 1.1, cart_cy - 1.1), (cart_cx + 1.1, cart_cy - 1.1), (cart_cx - 1.1, cart_cy + 1.1), (cart_cx + 1.1, cart_cy + 1.1)]:
        add_cylinder(c_vault, f"Cash_Cart_Wheel_{int(wx*10)}_{int(wy*10)}", (wx, wy, 0.10), radius=0.10, height=0.06, material=mats["Hazard_Black"], segments=12, is_collider=False)
        add_box(c_vault, f"Cash_Cart_Bracket_{int(wx*10)}_{int(wy*10)}", (wx - 0.06, wy - 0.06, 0.12), (wx + 0.06, wy + 0.06, 0.22), mats["Chrome_Polished"], is_collider=False)
    for (px, py) in [(cart_cx - 1.15, cart_cy - 1.15), (cart_cx + 1.15, cart_cy - 1.15), (cart_cx - 1.15, cart_cy + 1.15), (cart_cx + 1.15, cart_cy + 1.15)]:
        add_oriented_cylinder(c_vault, f"Cash_Cart_Post_{int(px*10)}_{int(py*10)}", (px, py, 0.25), (px, py, 1.45), radius=0.035, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    for side_z in [0.45, 0.75, 1.05, 1.35]:
        add_box(c_vault, f"Cash_Cart_Rail_S_{int(side_z*100)}", (cart_cx - 1.15, cart_cy - 1.18, side_z), (cart_cx + 1.15, cart_cy - 1.14, side_z + 0.03), mats["Chrome_Polished"], is_collider=False)
        add_box(c_vault, f"Cash_Cart_Rail_N_{int(side_z*100)}", (cart_cx - 1.15, cart_cy + 1.14, side_z), (cart_cx + 1.15, cart_cy + 1.18, side_z + 0.03), mats["Chrome_Polished"], is_collider=False)
        add_box(c_vault, f"Cash_Cart_Rail_W_{int(side_z*100)}", (cart_cx - 1.18, cart_cy - 1.15, side_z), (cart_cx - 1.14, cart_cy + 1.15, side_z + 0.03), mats["Chrome_Polished"], is_collider=False)
        add_box(c_vault, f"Cash_Cart_Rail_E_{int(side_z*100)}", (cart_cx + 1.14, cart_cy - 1.15, side_z), (cart_cx + 1.18, cart_cy + 1.15, side_z + 0.03), mats["Chrome_Polished"], is_collider=False)

    # Layered Stacks of Banded Currency Bundles ($10,000 Straps)
    for b_z in [0.35, 0.55, 0.75, 0.95, 1.15]:
        for b_x in [-0.8, -0.4, 0.0, 0.4, 0.8]:
            for b_y in [-0.8, -0.4, 0.0, 0.4, 0.8]:
                pkg_x = cart_cx + b_x
                pkg_y = cart_cy + b_y
                add_box(c_vault, f"Cash_Pack_{int(pkg_x*10)}_{int(pkg_y*10)}_{int(b_z*100)}", (pkg_x - 0.18, pkg_y - 0.18, b_z), (pkg_x + 0.18, pkg_y + 0.18, b_z + 0.16), mats["Currency_Green"], is_collider=False)
                add_box(c_vault, f"Cash_Band_{int(pkg_x*10)}_{int(pkg_y*10)}_{int(b_z*100)}", (pkg_x - 0.05, pkg_y - 0.19, b_z + 0.02), (pkg_x + 0.05, pkg_y + 0.19, b_z + 0.14), mats["Currency_Band"], is_collider=False)

    # Tactical Bomb Site A Stencil
    add_box(c_vault, "Stencil_Site_A_Stripe", (44.0, 50.5, 0.005), (48.0, 53.5, 0.008), mats["Tactical_Site_Stencil"], is_collider=False)

    # Industrial HVAC Generator & Overhead Air Duct
    add_box(c_vault, "HVAC_Jump_Generator", (53.0, 44.0, 0.0), (56.0, 46.0, 1.80), mats["Steel_Structural"], bevel=True, bevel_width=0.03)
    add_box(c_vault, "Overhead_Air_Duct", (49.0, 44.0, 3.4), (56.0, 48.0, 4.20), mats["Deposit_Box_Steel"])

    # =========================================================================
    # 8. DEFENDER OFFICES & STAFF BREAKROOM (y: 46.0..60.0, x: 4.0..40.0)
    # =========================================================================
    # Acoustic Drop-Ceiling Tile Grid at z = 5.2m (Covering entire office suite from y=46.0 to 60.0!)
    add_box(c_offices, "Office_Ceiling_Acoustic_Slab", (4.0, 46.0, 5.15), (40.0, 60.0, 5.25), mats["Plaster_Ceiling"], is_collider=False)

    # Suspended 2x4 T-Bar Aluminum Grid (Vertical fins that catch ambient/directional lighting!)
    for x_r in range(6, 40, 2):
        add_box(c_offices, f"TBar_Runner_X_{x_r}", (float(x_r) - 0.02, 46.0, 5.08), (float(x_r) + 0.02, 60.0, 5.16), mats["Aluminum_Brushed"], is_collider=False)
    for y_r in [48.0, 50.0, 52.0, 54.0, 56.0, 58.0]:
        add_box(c_offices, f"TBar_Tee_Y_{int(y_r)}", (4.0, float(y_r) - 0.02, 5.08), (40.0, float(y_r) + 0.02, 5.16), mats["Aluminum_Brushed"], is_collider=False)

    # Recessed 2x4 Fluorescent Troffer Lights with 3D Drop Diffuser Pans
    for fl_x in [8.0, 14.0, 20.0, 26.0, 32.0, 38.0]:
        for fl_y in [49.0, 53.0, 57.0]:
            add_box(c_offices, f"Office_Troffer_Frame_{int(fl_x)}_{int(fl_y)}", (fl_x - 0.85, fl_y - 0.42, 5.08), (fl_x + 0.85, fl_y + 0.42, 5.16), mats["Deposit_Box_Steel"], is_collider=False)
            add_box(c_offices, f"Office_Troffer_Pan_{int(fl_x)}_{int(fl_y)}", (fl_x - 0.78, fl_y - 0.36, 5.04), (fl_x + 0.78, fl_y + 0.36, 5.10), mats["Fluorescent_Troffer_Glow"], is_collider=False)

    # White Louvered HVAC Supply Air Diffusers
    for hvac_x, hvac_y in [(10.0, 51.0), (18.0, 55.0), (28.0, 51.0), (34.0, 55.0)]:
        add_box(c_offices, f"HVAC_Diffuser_Outer_{int(hvac_x)}_{int(hvac_y)}", (hvac_x - 0.4, hvac_y - 0.4, 5.08), (hvac_x + 0.4, hvac_y + 0.4, 5.14), mats["Aluminum_Brushed"], is_collider=False)
        add_box(c_offices, f"HVAC_Diffuser_Inner_{int(hvac_x)}_{int(hvac_y)}", (hvac_x - 0.25, hvac_y - 0.25, 5.06), (hvac_x + 0.25, hvac_y + 0.25, 5.12), mats["Aluminum_Brushed"], is_collider=False)

    # Office Perimeter Wainscoting along Walls
    add_box(c_offices, "Office_Wainscot_W", (4.0, 46.0, 0.0), (4.25, 60.0, 1.20), mats["Mahogany_Polished"], is_collider=False)
    add_box(c_offices, "Office_Wainscot_N", (4.0, 59.75, 0.0), (40.0, 60.0, 1.20), mats["Mahogany_Polished"], is_collider=False)

    # Three Framed Classical Oil Paintings on North Office Wall (In gilded frames!)
    for pi, (px, cmat) in enumerate([(8.0, mats["Painting_Canvas_1"]), (14.0, mats["Painting_Canvas_2"]), (20.0, mats["Painting_Canvas_3"])]):
        add_box(c_offices, f"Office_Painting_Frame_{pi}", (px - 1.2, 59.82, 2.0), (px + 1.2, 59.86, 3.6), mats["Picture_Frame_Gold"], is_collider=False)
        add_box(c_offices, f"Office_Painting_Canvas_{pi}", (px - 1.05, 59.80, 2.15), (px + 1.05, 59.84, 3.45), cmat, is_collider=False)

    # Executive Partner Mahogany Desk (x: 10.0..13.5, y: 51.0..54.0)
    add_box(c_offices, "Executive_Desk_Main", (10.0, 51.0, 0.0), (13.5, 52.2, 0.85), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    add_box(c_offices, "Executive_Desk_Return", (10.0, 52.2, 0.0), (11.2, 54.0, 0.85), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    # Desk drawers with brass pulls
    for dz in [0.25, 0.50, 0.72]:
        add_box(c_offices, f"Desk_Drawer_L_{int(dz*100)}", (10.1, 50.95, dz - 0.08), (11.2, 51.02, dz + 0.08), mats["Mahogany_Dark"], is_collider=False)
        add_oriented_cylinder(c_offices, f"Desk_Pull_L_{int(dz*100)}", (10.4, 50.92, dz), (10.9, 50.92, dz), radius=0.015, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # Leather Desk Blotter Pad
    add_box(c_offices, "Executive_Desk_Blotter", (11.5, 51.2, 0.855), (13.2, 52.0, 0.865), mats["Leather_Black"], is_collider=False)

    # Authentic Classic Emerald Green Glass Banker's Desk Lamp with Brass Pull Chain
    add_cylinder(c_offices, "Bankers_Lamp_Base", (10.5, 51.6, 0.86), radius=0.10, height=0.04, material=mats["Brass_Polished"], segments=16, is_collider=False)
    add_oriented_cylinder(c_offices, "Bankers_Lamp_Stem", (10.5, 51.6, 0.88), (10.5, 51.6, 1.20), radius=0.02, material=mats["Brass_Polished"], segments=8, is_collider=False)
    add_oriented_cylinder(c_offices, "Bankers_Lamp_Arm", (10.5, 51.6, 1.20), (10.5, 51.5, 1.25), radius=0.018, material=mats["Brass_Polished"], segments=8, is_collider=False)
    add_cylinder(c_offices, "Bankers_Lamp_Glass_Shade", (10.5, 51.5, 1.26), radius=0.12, height=0.28, material=mats["Green_Glass_Banker"], segments=16, is_collider=False)
    # Pull chain with gold ball bead
    add_oriented_cylinder(c_offices, "Bankers_Lamp_Chain", (10.5, 51.5, 1.20), (10.5, 51.5, 1.05), radius=0.005, material=mats["Brass_Polished"], segments=6, is_collider=False)
    add_cylinder(c_offices, "Bankers_Lamp_Bead", (10.5, 51.5, 1.04), radius=0.015, height=0.025, material=mats["Brass_Polished"], segments=8, is_collider=False)

    # High-Back Ergonomic Leather Executive Chair
    ch_x, ch_y = 12.2, 52.9
    add_cylinder(c_offices, "Exec_Chair_Stem", (ch_x, ch_y, 0.25), radius=0.045, height=0.35, material=mats["Chrome_Polished"], segments=12, is_collider=False)
    for spoke_i in range(5):
        s_ang = spoke_i * (2.0 * math.pi / 5.0)
        sp_end_x = ch_x + math.cos(s_ang) * 0.32
        sp_end_y = ch_y + math.sin(s_ang) * 0.32
        add_oriented_cylinder(c_offices, f"Chair_Spoke_{spoke_i}", (ch_x, ch_y, 0.12), (sp_end_x, sp_end_y, 0.08), radius=0.025, material=mats["Chrome_Polished"], segments=8, is_collider=False)
        add_cylinder(c_offices, f"Chair_Wheel_{spoke_i}", (sp_end_x, sp_end_y, 0.04), radius=0.04, height=0.03, material=mats["Hazard_Black"], segments=8, is_collider=False)
    add_box(c_offices, "Exec_Chair_Seat", (ch_x - 0.32, ch_y - 0.32, 0.45), (ch_x + 0.32, ch_y + 0.32, 0.58), mats["Leather_Black"], bevel=True, bevel_width=0.04, is_collider=False)
    add_box(c_offices, "Exec_Chair_Back", (ch_x - 0.30, ch_y + 0.28, 0.58), (ch_x + 0.30, ch_y + 0.38, 1.35), mats["Leather_Black"], bevel=True, bevel_width=0.04, is_collider=False)
    for arm_sign, arm_name in [(-1, "L"), (1, "R")]:
        ax = ch_x + arm_sign * 0.35
        add_oriented_cylinder(c_offices, f"Chair_Arm_Post_{arm_name}", (ax, ch_y, 0.50), (ax, ch_y, 0.80), radius=0.02, material=mats["Chrome_Polished"], segments=8, is_collider=False)
        add_box(c_offices, f"Chair_Arm_Pad_{arm_name}", (ax - 0.04, ch_y - 0.20, 0.80), (ax + 0.04, ch_y + 0.20, 0.84), mats["Leather_Black"], bevel=True, bevel_width=0.015, is_collider=False)

    # Dual-Monitor Setup & Keyboard on Desk
    add_box(c_offices, "Office_Monitor_1", (11.8, 51.8, 0.86), (12.4, 52.0, 1.30), mats["Monitor_Plastic"], is_collider=False)
    add_box(c_offices, "Office_Screen_1", (11.84, 51.78, 0.90), (12.36, 51.82, 1.26), mats["Monitor_Screen_CCTV"], is_collider=False)
    add_box(c_offices, "Office_Monitor_2", (12.5, 51.8, 0.86), (13.1, 52.0, 1.30), mats["Monitor_Plastic"], is_collider=False)
    add_box(c_offices, "Office_Screen_2", (12.54, 51.78, 0.90), (13.06, 51.82, 1.26), mats["Monitor_Screen_CCTV"], is_collider=False)
    # Under-desk PC Workstation Tower
    add_box(c_offices, "Office_PC_Tower", (10.2, 53.0, 0.0), (10.5, 53.8, 0.48), mats["Monitor_Plastic"], is_collider=False)
    add_cylinder(c_offices, "Office_PC_LED", (10.35, 52.98, 0.42), radius=0.01, height=0.02, material=mats["LED_Green"], segments=6, is_collider=False)

    # Realistic Office Water Cooler with Translucent Ribbed Bottle & Hot/Cold Spigots
    add_box(c_offices, "Water_Cooler_Cabinet", (6.0, 54.0, 0.0), (6.6, 54.6, 1.05), mats["Road_White"], bevel=True, bevel_width=0.02)
    add_box(c_offices, "Water_Cooler_Drip_Tray", (6.1, 53.90, 0.75), (6.5, 54.05, 0.80), mats["Cast_Iron_Dark"], is_collider=False)
    add_cylinder(c_offices, "Water_Spigot_Cold", (6.25, 53.95, 0.86), radius=0.02, height=0.06, material=mats["Emergency_Blue"], segments=8, is_collider=False)
    add_cylinder(c_offices, "Water_Spigot_Hot", (6.38, 53.95, 0.86), radius=0.02, height=0.06, material=mats["Emergency_Red"], segments=8, is_collider=False)
    add_cylinder(c_offices, "Water_Bottle_Clear", (6.3, 54.3, 1.45), radius=0.22, height=0.75, material=mats["Water_Bottle_Blue"], segments=20, is_collider=False)
    add_cylinder(c_offices, "Water_Bottle_Cap", (6.3, 54.3, 1.84), radius=0.06, height=0.08, material=mats["Emergency_Blue"], segments=12, is_collider=False)
    add_oriented_cylinder(c_offices, "Water_Cup_Dispenser", (6.62, 54.2, 0.70), (6.62, 54.2, 1.40), radius=0.045, material=mats["Chrome_Polished"], segments=12, is_collider=False)

    # 4-Drawer Heavy Steel File Cabinets in Office Corner
    for fc_i, fc_y in enumerate([56.5, 57.8]):
        add_box(c_offices, f"File_Cabinet_{fc_i}", (4.2, fc_y, 0.0), (5.2, fc_y + 1.1, 1.60), mats["Deposit_Box_Steel"], bevel=True, bevel_width=0.02)
        for d_z in [0.25, 0.65, 1.05, 1.45]:
            add_oriented_cylinder(c_offices, f"File_Pull_{fc_i}_{int(d_z*100)}", (5.22, fc_y + 0.35, d_z), (5.22, fc_y + 0.75, d_z), radius=0.015, material=mats["Chrome_Polished"], segments=8, is_collider=False)

    # -------------------------------------------------------------------------
    # SOLID MAHOGANY CREDENZA & LAW BOOKCASE (x = 4.25, y: 50.5..55.5, z: 0.0..2.4m)
    # -------------------------------------------------------------------------
    # Heavy Bookcase Enclosure against West Wall
    add_box(c_offices, "Law_Bookcase_Carcass", (4.20, 50.5, 0.0), (4.75, 55.5, 2.40), mats["Mahogany_Polished"], bevel=True, bevel_width=0.02)
    # 4 Shelves
    for sh_z in [0.55, 1.10, 1.65, 2.20]:
        add_box(c_offices, f"Law_Shelf_{int(sh_z*100)}", (4.22, 50.55, sh_z), (4.73, 55.45, sh_z + 0.04), mats["Mahogany_Dark"], is_collider=False)
    # Shelves Packed with Colorful Leather-Bound Law & Banking Volumes
    book_mats = [mats["Book_Spine_Red"], mats["Book_Spine_Green"], mats["Book_Spine_Blue"], mats["Book_Spine_Gold"]]
    for sh_i, sh_z in enumerate([0.59, 1.14, 1.69]):
        for bk_i, bk_y in enumerate(range(507, 553, 2)):
            by_float = float(bk_y) / 10.0
            # Leave center niche for scales of justice on middle shelf
            if sh_i == 1 and 52.6 < by_float < 53.4:
                continue
            b_col = book_mats[(sh_i * 7 + bk_i) % len(book_mats)]
            b_height = 0.38 + ((bk_i * 3) % 5) * 0.02
            add_box(c_offices, f"Book_{sh_i}_{bk_i}", (4.25, by_float - 0.07, sh_z), (4.70, by_float + 0.07, sh_z + b_height), b_col, is_collider=False)
    # Polished Brass Scales of Justice in Center Shelf Niche
    add_cylinder(c_offices, "Scales_Base", (4.50, 53.0, 1.14), radius=0.10, height=0.04, material=mats["Brass_Polished"], segments=12, is_collider=False)
    add_oriented_cylinder(c_offices, "Scales_Pillar", (4.50, 53.0, 1.16), (4.50, 53.0, 1.62), radius=0.015, material=mats["Brass_Polished"], segments=8, is_collider=False)
    add_oriented_cylinder(c_offices, "Scales_Beam", (4.50, 52.75, 1.60), (4.50, 53.25, 1.60), radius=0.010, material=mats["Brass_Polished"], segments=6, is_collider=False)
    for pan_y in [52.75, 53.25]:
        add_oriented_cylinder(c_offices, f"Scales_String_{int(pan_y*100)}", (4.50, pan_y, 1.60), (4.50, pan_y, 1.42), radius=0.004, material=mats["Brass_Polished"], segments=4, is_collider=False)
        add_cylinder(c_offices, f"Scales_Pan_{int(pan_y*100)}", (4.50, pan_y, 1.41), radius=0.07, height=0.02, material=mats["Brass_Polished"], segments=12, is_collider=False)

    # Antique Terrestrial Floor Globe in Walnut Cradle
    gl_x, gl_y = 7.5, 52.0
    for leg_i in range(3):
        l_ang = leg_i * (2.0 * math.pi / 3.0)
        lx = gl_x + math.cos(l_ang) * 0.28
        ly = gl_y + math.sin(l_ang) * 0.28
        add_oriented_cylinder(c_offices, f"Globe_Leg_{leg_i}", (lx, ly, 0.05), (gl_x, gl_y, 0.65), radius=0.025, material=mats["Mahogany_Dark"], segments=8, is_collider=False)
    add_cylinder(c_offices, "Globe_Meridian_Ring", (gl_x, gl_y, 0.95), radius=0.32, height=0.03, material=mats["Brass_Polished"], segments=20, is_collider=False)
    add_cylinder(c_offices, "Globe_Sphere", (gl_x, gl_y, 0.95), radius=0.28, height=0.52, material=mats["Painting_Canvas_2"], segments=20, is_collider=False)

    # -------------------------------------------------------------------------
    # EXECUTIVE BOARDROOM CONFERENCE TABLE & CHAIRS (x: 18.0..24.0, y: 50.0..54.0)
    # -------------------------------------------------------------------------
    # Oval Polished Mahogany Conference Table
    add_box(c_offices, "Conf_Table_Top", (18.5, 50.5, 0.74), (23.5, 53.5, 0.80), mats["Mahogany_Polished"], bevel=True, bevel_width=0.03)
    for t_leg_x in [19.5, 22.5]:
        add_cylinder(c_offices, f"Conf_Table_Pedestal_{int(t_leg_x)}", (t_leg_x, 52.0, 0.37), radius=0.22, height=0.74, material=mats["Mahogany_Dark"], segments=16, is_collider=False)
    # Triangular Executive Conference Speakerphone (Polycom style)
    add_cylinder(c_offices, "Conf_Speakerphone", (21.0, 52.0, 0.82), radius=0.18, height=0.04, material=mats["Monitor_Plastic"], segments=3, is_collider=False)
    add_cylinder(c_offices, "Conf_Speakerphone_LED", (21.0, 52.0, 0.84), radius=0.02, height=0.015, material=mats["LED_Green"], segments=6, is_collider=False)
    # 6 Black Leather Executive Swivel Conference Chairs
    conf_chair_pts = [(19.5, 49.8), (21.0, 49.8), (22.5, 49.8), (19.5, 54.2), (21.0, 54.2), (22.5, 54.2)]
    for ci, (cx, cy) in enumerate(conf_chair_pts):
        add_cylinder(c_offices, f"Conf_Chair_Stem_{ci}", (cx, cy, 0.22), radius=0.035, height=0.35, material=mats["Chrome_Polished"], segments=8, is_collider=False)
        add_box(c_offices, f"Conf_Chair_Seat_{ci}", (cx - 0.26, cy - 0.26, 0.42), (cx + 0.26, cy + 0.26, 0.48), mats["Leather_Black"], bevel=True, bevel_width=0.02, is_collider=False)
        cy_back = cy - 0.24 if cy < 52.0 else cy + 0.24
        add_box(c_offices, f"Conf_Chair_Back_{ci}", (cx - 0.24, cy_back - 0.04, 0.48), (cx + 0.24, cy_back + 0.04, 1.05), mats["Leather_Black"], bevel=True, bevel_width=0.02, is_collider=False)

    # -------------------------------------------------------------------------
    # STAFF BREAKROOM & KITCHENETTE (x: 28.0..40.0, y: 50.0..60.0)
    # -------------------------------------------------------------------------
    # Full-Size Commercial Snack Vending Machine in Breakroom (x = 28.5, y = 51.0)
    add_box(c_offices, "Vending_Machine_Casing", (28.2, 50.8, 0.0), (29.6, 52.2, 1.95), mats["Cast_Iron_Dark"], bevel=True, bevel_width=0.02)
    add_box(c_offices, "Vending_Glass_Window", (28.4, 50.75, 0.65), (29.4, 50.82, 1.75), mats["Vending_Glass"], is_collider=False)
    # Horizontal Shelves with Snack Coils
    for vs_z in [0.85, 1.15, 1.45]:
        add_box(c_offices, f"Vending_Shelf_{int(vs_z*100)}", (28.42, 50.82, vs_z), (29.38, 51.8, vs_z + 0.02), mats["Stainless_Steel"], is_collider=False)
        for coil_x in [28.6, 28.9, 29.2]:
            add_oriented_cylinder(c_offices, f"Vending_Coil_{int(coil_x*10)}_{int(vs_z*100)}", (coil_x, 50.85, vs_z + 0.08), (coil_x, 51.4, vs_z + 0.08), radius=0.05, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    # Illuminated Dollar Bill Acceptor & Coin Return
    add_box(c_offices, "Vending_Bill_Slot", (28.3, 50.76, 1.25), (28.42, 50.80, 1.30), mats["LED_Green"], is_collider=False)
    add_box(c_offices, "Vending_Keypad", (28.3, 50.76, 1.05), (28.42, 50.80, 1.20), mats["Monitor_Plastic"], is_collider=False)
    add_box(c_offices, "Vending_Drop_Bin", (28.45, 50.76, 0.20), (29.35, 50.82, 0.50), mats["Cast_Iron_Dark"], is_collider=False)

    # Full-Size Stainless Steel French-Door Refrigerator (x = 35.8, y = 51.2, z: 0.0..1.95m)
    add_box(c_offices, "Fridge_Body", (35.6, 50.8, 0.0), (37.2, 52.4, 1.95), mats["Stainless_Steel"], bevel=True, bevel_width=0.03)
    add_box(c_offices, "Fridge_Door_Seam", (36.38, 50.76, 0.70), (36.42, 50.80, 1.90), mats["Cast_Iron_Dark"], is_collider=False)
    add_box(c_offices, "Fridge_Freezer_Seam", (35.65, 50.76, 0.68), (37.15, 50.80, 0.72), mats["Cast_Iron_Dark"], is_collider=False)
    # Dual French Door Handles & Ice Dispenser
    for hx in [36.25, 36.55]:
        add_oriented_cylinder(c_offices, f"Fridge_Handle_{int(hx*100)}", (hx, 50.72, 0.85), (hx, 50.72, 1.70), radius=0.02, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    add_box(c_offices, "Fridge_Dispenser_Recess", (35.85, 50.74, 1.15), (36.25, 50.80, 1.50), mats["Cast_Iron_Dark"], is_collider=False)
    add_cylinder(c_offices, "Fridge_Dispenser_LED", (36.05, 50.74, 1.45), radius=0.015, height=0.02, material=mats["LED_Blue"], segments=6, is_collider=False)

    # Oak Kitchenette Base Cabinet Counter with Nero Marquina Granite Countertop
    add_box(c_offices, "Kitchenette_Base_Cab", (35.5, 53.0, 0.0), (39.5, 59.0, 0.88), mats["Oak_Cabinet"], bevel=True, bevel_width=0.02)
    add_box(c_offices, "Kitchenette_Granite_Top", (35.3, 52.8, 0.88), (39.7, 59.2, 0.94), mats["Marble_Black"], bevel=True, bevel_width=0.02)
    # Stainless Steel Undermount Sink & Chrome Gooseneck Faucet
    add_box(c_offices, "Kitchenette_Sink_Basin", (36.0, 56.5, 0.70), (37.2, 57.8, 0.90), mats["Stainless_Steel"], is_collider=False)
    add_oriented_cylinder(c_offices, "Kitchenette_Faucet_Stem", (35.8, 57.15, 0.94), (35.8, 57.15, 1.25), radius=0.02, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    add_oriented_cylinder(c_offices, "Kitchenette_Faucet_Spout", (35.8, 57.15, 1.25), (36.2, 57.15, 1.20), radius=0.018, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    # Countertop Microwave Oven with Dark Glass Door & Digital Clock
    add_box(c_offices, "Microwave_Body", (36.0, 54.0, 0.94), (37.2, 55.2, 1.30), mats["Stainless_Steel"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(c_offices, "Microwave_Window", (35.95, 54.1, 0.98), (36.02, 54.8, 1.26), mats["Smoked_Glass"], is_collider=False)
    add_box(c_offices, "Microwave_Clock", (35.95, 54.9, 1.18), (36.02, 55.1, 1.26), mats["LED_Green"], is_collider=False)
    # Commercial Drip Coffee Maker with Glass Pot Carafe
    add_box(c_offices, "Coffee_Maker_Body", (37.6, 54.2, 0.94), (38.2, 55.0, 1.35), mats["Monitor_Plastic"], is_collider=False)
    add_cylinder(c_offices, "Coffee_Pot_Glass", (37.9, 54.6, 1.08), radius=0.12, height=0.22, material=mats["Smoked_Glass"], segments=12, is_collider=False)
    # Overhead Oak Wall Cabinets
    add_box(c_offices, "Kitchenette_Wall_Cab", (36.0, 53.0, 1.80), (39.5, 59.0, 2.60), mats["Oak_Cabinet"], bevel=True, bevel_width=0.02, is_collider=False)

    # Cork Bulletin Notice Board on Wall (with pinned memos and emergency fire map!)
    add_box(c_offices, "Breakroom_Bulletin_Frame", (29.5, 59.84, 1.6), (33.5, 59.88, 2.7), mats["Oak_Cabinet"], is_collider=False)
    add_box(c_offices, "Breakroom_Bulletin_Cork", (29.7, 59.82, 1.7), (33.3, 59.86, 2.6), mats["Cork_Bulletin"], is_collider=False)
    for mi, (mx, my_offset, mz) in enumerate([(30.2, 0.0, 2.3), (31.2, 0.0, 2.2), (32.3, 0.0, 2.4), (30.8, 0.0, 1.9)]):
        add_box(c_offices, f"Bulletin_Memo_{mi}", (mx - 0.22, 59.80, mz - 0.16), (mx + 0.22, 59.84, mz + 0.16), mats["Paper_White"], is_collider=False)
    # Red Emergency Evacuation Route on Memo
    add_box(c_offices, "Bulletin_Evac_Red", (32.15, 59.78, 2.32), (32.45, 59.82, 2.48), mats["Emergency_Red"], is_collider=False)

    # Breakroom Dining Table & Modern Cafeteria Chairs
    add_cylinder(c_offices, "Break_Table_Top", (31.0, 54.0, 0.76), radius=1.00, height=0.05, material=mats["Road_White"], segments=24, is_collider=False)
    add_cylinder(c_offices, "Break_Table_Leg", (31.0, 54.0, 0.38), radius=0.08, height=0.72, material=mats["Chrome_Polished"], segments=12, is_collider=False)
    add_cylinder(c_offices, "Break_Table_Base", (31.0, 54.0, 0.03), radius=0.45, height=0.06, material=mats["Chrome_Polished"], segments=16, is_collider=False)
    for c_i, c_ang in enumerate([0.0, math.pi * 0.5, math.pi, math.pi * 1.5]):
        cx = 31.0 + math.cos(c_ang) * 1.35
        cy = 54.0 + math.sin(c_ang) * 1.35
        add_box(c_offices, f"Break_Chair_Seat_{c_i}", (cx - 0.22, cy - 0.22, 0.44), (cx + 0.22, cy + 0.22, 0.48), mats["Emergency_Blue"], is_collider=False)
        add_box(c_offices, f"Break_Chair_Back_{c_i}", (cx - 0.20, cy + 0.18, 0.48), (cx + 0.20, cy + 0.22, 0.85), mats["Emergency_Blue"], is_collider=False)
        add_cylinder(c_offices, f"Break_Chair_Leg_{c_i}", (cx, cy, 0.22), radius=0.03, height=0.44, material=mats["Chrome_Polished"], segments=8, is_collider=False)

    # -------------------------------------------------------------------------
    # IT SERVER CLOSET (x: 20.0..26.0, y: 55.0..60.0)
    # -------------------------------------------------------------------------
    # 42U Server Equipment Racks with Blinking Activity LEDs
    for s_x in [20.0, 22.0, 24.0]:
        add_box(c_offices, f"Server_Rack_{int(s_x)}", (s_x, 56.5, 0.0), (s_x + 1.4, 57.8, 2.4), mats["Monitor_Plastic"], bevel=True, bevel_width=0.02)
        add_box(c_offices, f"Server_Rack_Glass_{int(s_x)}", (s_x + 0.1, 56.45, 0.2), (s_x + 1.3, 56.55, 2.3), mats["Smoked_Glass"], is_collider=False)
        for u_z in [0.5, 0.9, 1.3, 1.7, 2.1]:
            add_cylinder(c_offices, f"Server_LED_G_{int(s_x)}_{int(u_z*10)}", (s_x + 0.3, 56.42, u_z), radius=0.02, height=0.02, material=mats["LED_Green"], segments=8, is_collider=False)
            add_cylinder(c_offices, f"Server_LED_A_{int(s_x)}_{int(u_z*10)}", (s_x + 0.45, 56.42, u_z), radius=0.02, height=0.02, material=mats["LED_Amber"], segments=8, is_collider=False)
            add_box(c_offices, f"Server_Louver_{int(s_x)}_{int(u_z*10)}", (s_x + 0.6, 56.42, u_z - 0.06), (s_x + 1.2, 56.46, u_z + 0.06), mats["Cast_Iron_Dark"], is_collider=False)

    # Overhead Yellow Fiber Cable Management Duct
    add_box(c_offices, "Server_Cable_Duct", (19.5, 57.0, 2.5), (26.0, 57.4, 2.65), mats["Hazard_Yellow"], is_collider=False)

    # =========================================================================
    # 9. STREET & PLAZA INFRASTRUCTURE & VEHICLES (y: 4.0..18.0)
    # =========================================================================
    # Armored SWAT Tactical Assault Truck (Critical Skill-Jump Pathway)
    # Hood = 1.50m, Cab/Roof = 2.80m (Preserving 100% test compatibility!)
    add_box(c_street, "SWAT_Van_Hood", (18.0, 10.0, 0.0), (22.0, 12.5, 1.50), mats["SWAT_Vehicle_Navy"], bevel=True, bevel_width=0.04)
    add_box(c_street, "SWAT_Van_Cab_Roof", (18.0, 12.5, 0.0), (22.0, 16.0, 2.80), mats["SWAT_Vehicle_Navy"], bevel=True, bevel_width=0.04)

    # SWAT Hood Heat Extraction Louvers & Hood Tie-Downs
    for l_y in [10.8, 11.2, 11.6, 12.0]:
        add_box(c_street, f"SWAT_Hood_Louver_L_{int(l_y*10)}", (18.6, l_y - 0.06, 1.502), (19.6, l_y + 0.06, 1.525), mats["Cast_Iron_Dark"], is_collider=False)
        add_box(c_street, f"SWAT_Hood_Louver_R_{int(l_y*10)}", (20.4, l_y - 0.06, 1.502), (21.4, l_y + 0.06, 1.525), mats["Cast_Iron_Dark"], is_collider=False)

    # Sculpted Front Radiator Grille & Headlight Clusters
    add_box(c_street, "SWAT_Radiator_Grille", (18.5, 9.92, 0.5), (21.5, 10.02, 1.2), mats["Cast_Iron_Dark"], is_collider=False)
    for h_x in [18.3, 21.7]:
        add_box(c_street, f"SWAT_Headlight_{int(h_x*10)}", (h_x - 0.25, 9.94, 0.9), (h_x + 0.25, 10.02, 1.15), mats["Chandelier_Lamp"], is_collider=False)
        add_box(c_street, f"SWAT_TurnSignal_{int(h_x*10)}", (h_x - 0.25, 9.94, 0.72), (h_x + 0.25, 10.02, 0.85), mats["Amber_Reflector"], is_collider=False)

    # Heavy Tubular Steel Push-Bumper / Bullbar with Recovery Winch & Dual D-Rings
    add_box(c_street, "SWAT_Bullbar", (17.6, 9.6, 0.3), (22.4, 10.0, 0.95), mats["Chrome_Polished"])
    add_oriented_cylinder(c_street, "SWAT_Bullbar_Tube_T", (17.7, 9.55, 0.90), (22.3, 9.55, 0.90), radius=0.06, material=mats["Chrome_Polished"], segments=12, is_collider=False)
    add_oriented_cylinder(c_street, "SWAT_Bullbar_Tube_B", (17.7, 9.55, 0.45), (22.3, 9.55, 0.45), radius=0.06, material=mats["Chrome_Polished"], segments=12, is_collider=False)
    # Winch Drum with braided steel cable
    add_cylinder(c_street, "SWAT_Winch_Drum", (20.0, 9.75, 0.65), radius=0.18, height=0.35, material=mats["Steel_Structural"], segments=16, is_collider=False)
    # Dual Heavy Tow Shackles (D-Rings)
    for d_x in [18.8, 21.2]:
        add_cylinder(c_street, f"SWAT_Shackle_Pin_{int(d_x*10)}", (d_x, 9.52, 0.40), radius=0.035, height=0.14, material=mats["Chrome_Polished"], segments=8, is_collider=False)
        add_cylinder(c_street, f"SWAT_Shackle_Ring_{int(d_x*10)}", (d_x, 9.48, 0.35), radius=0.08, height=0.04, material=mats["Chrome_Polished"], segments=12, is_collider=False)

    # Sloped Armored Windshield & Dual Heavy Side Mirrors
    add_box(c_street, "SWAT_Windshield_Detail", (18.2, 12.42, 1.50), (21.8, 12.65, 2.35), mats["Smoked_Glass"], is_collider=False)
    for mx in [17.7, 22.3]:
        add_box(c_street, f"SWAT_Mirror_Bracket_{int(mx*10)}", (mx - 0.05, 12.8, 1.8), (mx + 0.05, 13.1, 1.9), mats["Chrome_Polished"], is_collider=False)
        add_box(c_street, f"SWAT_Mirror_Glass_{int(mx*10)}", (mx - 0.08, 12.7, 1.6), (mx + 0.08, 12.9, 2.1), mats["Chrome_Polished"], is_collider=False)

    # Armored Side Vision Slits & Recessed Door Handles
    for s_side, sx in [(-1, 17.96), (1, 22.04)]:
        # Vision slit windows
        for slit_y in [13.2, 14.5]:
            add_box(c_street, f"SWAT_Slit_{int(sx*10)}_{int(slit_y*10)}", (sx - 0.04, slit_y - 0.30, 2.05), (sx + 0.04, slit_y + 0.30, 2.25), mats["Smoked_Glass"], is_collider=False)
        # Recessed Door Handle Pockets
        add_box(c_street, f"SWAT_Handle_{int(sx*10)}", (sx - 0.03, 13.0, 1.50), (sx + 0.03, 13.25, 1.62), mats["Cast_Iron_Dark"], is_collider=False)

    # Rear Twin Armored Barn Doors with Heavy Hinges
    add_box(c_street, "SWAT_Rear_Door_Seam", (19.98, 15.98, 0.4), (20.02, 16.02, 2.65), mats["Cast_Iron_Dark"], is_collider=False)
    for hz in [0.7, 1.5, 2.3]:
        add_box(c_street, f"SWAT_Rear_Hinge_L_{int(hz*10)}", (18.1, 15.96, hz - 0.06), (18.35, 16.02, hz + 0.06), mats["Cast_Iron_Dark"], is_collider=False)
        add_box(c_street, f"SWAT_Rear_Hinge_R_{int(hz*10)}", (21.65, 15.96, hz - 0.06), (21.9, 16.02, hz + 0.06), mats["Cast_Iron_Dark"], is_collider=False)

    # Diamond-Plate Aluminum Running Boards
    add_box(c_street, "SWAT_Running_Board_L", (17.3, 11.5, 0.22), (18.0, 15.5, 0.30), mats["Deposit_Box_Steel"], is_collider=False)
    add_box(c_street, "SWAT_Running_Board_R", (22.0, 11.5, 0.22), (22.7, 15.5, 0.30), mats["Deposit_Box_Steel"], is_collider=False)

    # Armored Roof Turret Cupola & Tactical Whip Antenna
    add_cylinder(c_street, "SWAT_Turret_Hatch", (20.0, 14.5, 2.95), radius=0.65, height=0.30, material=mats["SWAT_Vehicle_Navy"], segments=16, is_collider=False)
    add_oriented_cylinder(c_street, "SWAT_Antenna_Whip", (18.4, 15.5, 2.80), (18.4, 15.5, 4.40), radius=0.015, material=mats["Cast_Iron_Dark"], segments=6, is_collider=False)

    # SLEEK LOW-PROFILE AERODYNAMIC EMERGENCY LED LIGHTBAR
    add_box(c_street, "SWAT_Lightbar_Housing", (18.6, 13.6, 2.82), (21.4, 13.9, 2.96), mats["Smoked_Glass"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(c_street, "SWAT_Lightbar_Reflector", (18.65, 13.65, 2.80), (21.35, 13.85, 2.84), mats["Chrome_Polished"], is_collider=False)
    for li, lx in enumerate([18.8, 19.3, 19.8, 20.2, 20.7, 21.2]):
        l_mat = mats["Emergency_Red"] if li % 2 == 0 else mats["Emergency_Blue"]
        add_box(c_street, f"SWAT_Lightbar_Module_{li}", (lx - 0.16, 13.70, 2.84), (lx + 0.16, 13.82, 2.93), l_mat, is_collider=False)

    # Heavy All-Terrain Truck Wheels with 8 Hex Lug Nuts around Rim
    for wy in [11.0, 14.6]:
        for wx, side_name in [(17.75, "L"), (22.25, "R")]:
            rim_x = wx - 0.20 if side_name == "L" else wx + 0.20
            add_cylinder(c_street, f"SWAT_Tire_{side_name}_{int(wy)}", (wx, wy + 0.6, 0.45), radius=0.45, height=0.40, material=mats["Tire_Rubber"], segments=16)
            add_cylinder(c_street, f"SWAT_Rim_{side_name}_{int(wy)}", (rim_x, wy + 0.6, 0.45), radius=0.28, height=0.08, material=mats["Deposit_Box_Steel"], segments=16, is_collider=False)
            add_cylinder(c_street, f"SWAT_Hub_{side_name}_{int(wy)}", (rim_x, wy + 0.6, 0.45), radius=0.10, height=0.12, material=mats["Cast_Iron_Dark"], segments=12, is_collider=False)
            # 8 Hexagonal Lug Nuts
            for li in range(8):
                lang = li * (math.pi / 4.0)
                lx_lug = wy + 0.6 + math.cos(lang) * 0.18
                lz_lug = 0.45 + math.sin(lang) * 0.18
                add_cylinder(c_street, f"SWAT_Lug_{side_name}_{int(wy)}_{li}", (rim_x, lx_lug, lz_lug), radius=0.02, height=0.14, material=mats["Chrome_Polished"], segments=6, is_collider=False)

    # Police Squad Patrol Cruiser (x: 42.0..46.0, y: 10.0..14.0, z: 0.0..1.60m)
    add_box(c_street, "Police_Cruiser_Body", (42.0, 10.0, 0.0), (46.0, 14.0, 1.60), mats["Police_Cruiser_White"], bevel=True, bevel_width=0.04)
    add_box(c_street, "Police_Cruiser_Windshield", (42.2, 11.2, 0.95), (45.8, 11.6, 1.45), mats["Smoked_Glass"], is_collider=False)
    add_box(c_street, "Police_Cruiser_Rear_Glass", (42.2, 13.2, 0.95), (45.8, 13.6, 1.45), mats["Smoked_Glass"], is_collider=False)
    add_box(c_street, "Police_Push_Bumper", (41.8, 9.7, 0.2), (46.2, 10.1, 0.8), mats["Cast_Iron_Dark"], is_collider=False)
    # Push bumper rubber uprights
    add_box(c_street, "Police_Push_Pad_L", (42.8, 9.62, 0.2), (43.1, 9.72, 0.85), mats["Tire_Rubber"], is_collider=False)
    add_box(c_street, "Police_Push_Pad_R", (44.9, 9.62, 0.2), (45.2, 9.72, 0.85), mats["Tire_Rubber"], is_collider=False)

    # Police Headlights & Amber Turn Signals
    for hx in [42.4, 45.6]:
        add_box(c_street, f"Police_Headlight_{int(hx*10)}", (hx - 0.25, 9.94, 0.65), (hx + 0.25, 10.02, 0.88), mats["Chandelier_Lamp"], is_collider=False)
        add_box(c_street, f"Police_TurnSignal_{int(hx*10)}", (hx - 0.25, 9.94, 0.50), (hx + 0.25, 10.02, 0.62), mats["Amber_Reflector"], is_collider=False)

    # Driver A-Pillar Spotlight with Chrome Shell
    add_oriented_cylinder(c_street, "Police_Spotlight_Shaft", (41.90, 11.2, 1.30), (41.65, 11.0, 1.40), radius=0.02, material=mats["Chrome_Polished"], segments=8, is_collider=False)
    add_cylinder(c_street, "Police_Spotlight_Head", (41.60, 10.95, 1.40), radius=0.08, height=0.12, material=mats["Chrome_Polished"], segments=12, is_collider=False)
    add_cylinder(c_street, "Police_Spotlight_Lens", (41.60, 10.88, 1.40), radius=0.075, height=0.03, material=mats["Chandelier_Lamp"], segments=12, is_collider=False)

    # Sleek Low-Profile Police Lightbar
    add_box(c_street, "Police_Lightbar_Housing", (42.6, 11.9, 1.62), (45.4, 12.2, 1.76), mats["Smoked_Glass"], bevel=True, bevel_width=0.02, is_collider=False)
    for pli, plx in enumerate([42.8, 43.4, 44.0, 44.6, 45.2]):
        pl_mat = mats["Emergency_Red"] if pli < 2 else (mats["Emergency_Blue"] if pli > 2 else mats["Road_White"])
        add_box(c_street, f"Police_Lightbar_Strobe_{pli}", (plx - 0.18, 11.95, 1.64), (plx + 0.18, 12.15, 1.73), pl_mat, is_collider=False)

    # Police Cruiser Wheels with Chrome Center "Dog Dish" Hubcaps
    for wy in [10.8, 13.2]:
        for wx, side_name in [(41.85, "L"), (46.15, "R")]:
            rim_x = wx - 0.10 if side_name == "L" else wx + 0.10
            add_cylinder(c_street, f"Police_Tire_{side_name}_{int(wy)}", (wx, wy, 0.35), radius=0.35, height=0.25, material=mats["Tire_Rubber"], segments=16)
            add_cylinder(c_street, f"Police_Rim_{side_name}_{int(wy)}", (rim_x, wy, 0.35), radius=0.24, height=0.06, material=mats["Cast_Iron_Dark"], segments=16, is_collider=False)
            add_cylinder(c_street, f"Police_Hubcap_{side_name}_{int(wy)}", (rim_x, wy, 0.35), radius=0.12, height=0.10, material=mats["Chrome_Polished"], segments=12, is_collider=False)

    # Concrete Jersey Barriers with Reflective Caution Top
    add_box(c_street, "Jersey_Barrier_Concrete", (30.0, 8.0, 0.0), (34.0, 9.8, 1.20), mats["Limestone_Facade"], bevel=True, bevel_width=0.03)
    add_box(c_street, "Jersey_Barrier_Yellow_Top_Stripe", (30.0, 8.0, 1.15), (34.0, 9.8, 1.25), mats["Hazard_Yellow"], is_collider=False)

    # Sidewalk Concrete Planters with Trimmed Boxwood Hedges
    for px in [14.0, 48.0]:
        add_box(c_street, f"Sidewalk_Planter_{int(px)}", (px, 14.5, 0.20), (px + 2.8, 17.5, 0.70), mats["Granite_Curb"], bevel=True, bevel_width=0.02)
        add_box(c_street, f"Sidewalk_Hedge_{int(px)}_Foliage", (px + 0.2, 14.7, 0.70), (px + 2.6, 17.3, 1.65), mats["Foliage_Green"], is_collider=False)

    # Cast-Iron Victorian Streetlamps with Luminous Globes
    for lx in [12.0, 52.0]:
        add_cylinder(c_street, f"Streetlamp_Base_{int(lx)}", (lx, 6.0, 0.3), radius=0.35, height=0.6, material=mats["Cast_Iron_Dark"], segments=12)
        add_cylinder(c_street, f"Streetlamp_Post_{int(lx)}", (lx, 6.0, 2.75), radius=0.14, height=5.50, material=mats["Cast_Iron_Dark"])
        add_cylinder(c_street, f"Streetlamp_Globe_{int(lx)}_Glow", (lx, 7.2, 5.20), radius=0.35, height=0.50, material=mats["Chandelier_Lamp"], segments=16, is_collider=False)
        add_oriented_cylinder(c_street, f"Streetlamp_Arm_{int(lx)}", (lx, 6.0, 5.1), (lx, 7.2, 5.3), radius=0.06, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)

    # Red Municipal Fire Hydrants with 5-Sided Nut and Chained Caps
    for hx in [8.0, 54.0]:
        add_cylinder(c_street, f"Fire_Hydrant_Base_{int(hx)}", (hx, 13.0, 0.15), radius=0.26, height=0.30, material=mats["Emergency_Red"], segments=16)
        add_cylinder(c_street, f"Fire_Hydrant_Body_{int(hx)}", (hx, 13.0, 0.55), radius=0.20, height=0.60, material=mats["Emergency_Red"], segments=16)
        add_cylinder(c_street, f"Fire_Hydrant_Bonnet_{int(hx)}", (hx, 13.0, 0.88), radius=0.16, height=0.12, material=mats["Emergency_Red"], segments=12, is_collider=False)
        add_cylinder(c_street, f"Fire_Hydrant_Nut_{int(hx)}", (hx, 13.0, 0.96), radius=0.06, height=0.08, material=mats["Bronze_Bushing"], segments=5, is_collider=False)
        # Side Nozzle Caps
        add_cylinder(c_street, f"Fire_Hydrant_Nozzle_L_{int(hx)}", (hx - 0.22, 13.0, 0.55), radius=0.08, height=0.10, material=mats["Bronze_Bushing"], segments=12, is_collider=False)
        add_cylinder(c_street, f"Fire_Hydrant_Nozzle_R_{int(hx)}", (hx + 0.22, 13.0, 0.55), radius=0.08, height=0.10, material=mats["Bronze_Bushing"], segments=12, is_collider=False)

    # Street Details: Cast-Iron Storm Drain & Manhole Cover
    add_box(c_street, "Storm_Drain_Basin", (23.8, 14.2, -0.01), (25.2, 14.8, 0.005), mats["Cast_Iron_Dark"], is_collider=False)
    for gi in range(6):
        add_box(c_street, f"Storm_Grate_Bar_{gi}", (24.0 + gi * 0.20, 14.22, 0.006), (24.08 + gi * 0.20, 14.78, 0.015), mats["Deposit_Box_Steel"], is_collider=False)
    # Cast-Iron Manhole Cover in Street
    add_cylinder(c_street, "Street_Manhole_Cover", (36.0, 11.0, 0.008), radius=0.45, height=0.016, material=mats["Cast_Iron_Dark"], segments=24, is_collider=False)
    add_cylinder(c_street, "Street_Manhole_Ring", (36.0, 11.0, 0.010), radius=0.38, height=0.018, material=mats["Deposit_Box_Steel"], segments=20, is_collider=False)

    # Pedestrian Continental Zebra Crosswalk Stripes across Street
    for cw_x in [26.0, 27.2, 28.4, 29.6, 30.8, 32.0, 33.2, 34.4, 35.6, 36.8]:
        add_box(c_street, f"Crosswalk_Stripe_{int(cw_x*10)}", (cw_x, 7.5, 0.004), (cw_x + 0.6, 14.5, 0.008), mats["Road_White"], is_collider=False)

    # 24/7 Outdoor Bank ATM Wall Vestibule
    add_box(c_street, "Outdoor_ATM_Shell", (9.5, 17.5, 0.4), (12.5, 18.0, 2.4), mats["Deposit_Box_Steel"])
    add_box(c_street, "Outdoor_ATM_Screen_Glow", (9.8, 17.45, 1.2), (12.2, 17.55, 1.8), mats["ATM_Screen"], is_collider=False)
    add_box(c_street, "Outdoor_ATM_Blue_Header", (9.2, 17.35, 2.4), (12.8, 17.55, 2.8), mats["ATM_Blue_Shell"], is_collider=False)
    # Numeric Keypad, Card Slot, Receipt Slot, Cash Dispenser
    add_box(c_street, "Outdoor_ATM_Keypad", (10.6, 17.42, 1.02), (11.4, 17.48, 1.15), mats["Chrome_Polished"], is_collider=False)
    add_box(c_street, "Outdoor_ATM_CardSlot", (11.8, 17.42, 1.05), (12.1, 17.48, 1.12), mats["LED_Green"], is_collider=False)
    add_box(c_street, "Outdoor_ATM_CashSlot", (10.5, 17.42, 0.75), (11.5, 17.48, 0.82), mats["Cast_Iron_Dark"], is_collider=False)

    # Free-standing Indoor ATM Kiosks in Lobby
    for ay in [26.0, 34.0]:
        add_box(c_lobby, f"Indoor_ATM_{int(ay)}", (4.8, ay, 0.0), (6.2, ay + 1.4, 2.1), mats["Deposit_Box_Steel"])
        add_box(c_lobby, f"Indoor_ATM_Screen_{int(ay)}_Glow", (6.0, ay + 0.2, 1.2), (6.25, ay + 1.2, 1.8), mats["ATM_Screen"], is_collider=False)
        add_box(c_lobby, f"Indoor_ATM_Header_{int(ay)}", (4.8, ay, 2.0), (6.2, ay + 1.4, 2.3), mats["ATM_Blue_Shell"], is_collider=False)

    # -------------------------------------------------------------------------
    # MODERN PUBLIC TRANSIT BUS STOP SHELTER ON SOUTH SIDEWALK (x: 35.0..40.0, y: 5.2..7.2)
    # -------------------------------------------------------------------------
    # Dark Gray Powder-Coated Steel Frame & Curved Canopy Roof
    add_box(c_street, "Bus_Shelter_Canopy", (34.8, 5.0, 2.50), (40.2, 7.4, 2.62), mats["Cast_Iron_Dark"], is_collider=False)
    for bx, by in [(35.0, 5.2), (40.0, 5.2), (35.0, 7.2), (40.0, 7.2)]:
        add_oriented_cylinder(c_street, f"Bus_Post_{int(bx)}_{int(by)}", (bx, by, 0.0), (bx, by, 2.50), radius=0.045, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)
    # Rear and Side Tempered Safety Glass Panels
    add_box(c_street, "Bus_Glass_Back", (35.1, 5.15, 0.3), (39.9, 5.25, 2.45), mats["Bus_Shelter_Glass"], is_collider=False)
    add_box(c_street, "Bus_Glass_Side_W", (34.95, 5.2, 0.3), (35.05, 7.1, 2.45), mats["Bus_Shelter_Glass"], is_collider=False)
    add_box(c_street, "Bus_Glass_Side_E", (39.95, 5.2, 0.3), (40.05, 7.1, 2.45), mats["Bus_Shelter_Glass"], is_collider=False)
    # Perforated Metal Passenger Bench
    add_box(c_street, "Bus_Bench_Seat", (35.5, 5.4, 0.48), (38.5, 6.0, 0.52), mats["Stainless_Steel"], is_collider=False)
    add_cylinder(c_street, "Bus_Bench_Leg_L", (35.8, 5.7, 0.24), radius=0.035, height=0.48, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)
    add_cylinder(c_street, "Bus_Bench_Leg_R", (38.2, 5.7, 0.24), radius=0.035, height=0.48, material=mats["Cast_Iron_Dark"], segments=8, is_collider=False)
    # Illuminated Transit Map & Advertising Lightbox
    add_box(c_street, "Bus_Ad_Lightbox_Frame", (38.8, 5.18, 0.4), (39.8, 5.28, 2.3), mats["Cast_Iron_Dark"], is_collider=False)
    add_box(c_street, "Bus_Ad_Poster_Glow", (38.9, 5.20, 0.5), (39.7, 5.24, 2.2), mats["Painting_Canvas_2"], is_collider=False)

    # -------------------------------------------------------------------------
    # SIDEWALK STREET TREES WITH CAST-IRON RADIAL TREE GRATES
    # -------------------------------------------------------------------------
    for tree_i, tree_x in enumerate([16.0, 46.0]):
        # Cast-Iron Slotted Tree Grate Flush with Sidewalk
        add_box(c_street, f"Tree_Grate_{tree_i}", (tree_x - 0.8, 14.7, 0.198), (tree_x + 0.8, 16.3, 0.205), mats["Cast_Iron_Dark"], is_collider=False)
        add_cylinder(c_street, f"Tree_Grate_Inner_{tree_i}", (tree_x, 15.5, 0.206), radius=0.25, height=0.01, material=mats["Granite_Curb"], segments=16, is_collider=False)
        # Young Sidewalk Street Tree with Trunk & Foliage
        add_cylinder(c_street, f"Tree_Trunk_{tree_i}", (tree_x, 15.5, 1.8), radius=0.08, height=3.2, material=mats["Mahogany_Dark"], segments=8, is_collider=False)
        add_cylinder(c_street, f"Tree_Crown_B_{tree_i}", (tree_x, 15.5, 3.8), radius=1.25, height=1.6, material=mats["Foliage_Green"], segments=12, is_collider=False)
        add_cylinder(c_street, f"Tree_Crown_T_{tree_i}", (tree_x, 15.5, 4.8), radius=0.85, height=1.4, material=mats["Foliage_Green"], segments=12, is_collider=False)

    # -------------------------------------------------------------------------
    # SIDEWALK NEWSPAPER HONOR BOXES (USA Today & Daily News)
    # -------------------------------------------------------------------------
    for npi, (npx, np_mat) in enumerate([(27.2, mats["Emergency_Blue"]), (28.4, mats["Emergency_Red"])]):
        add_box(c_street, f"News_Box_Body_{npi}", (npx - 0.25, 14.8, 0.20), (npx + 0.25, 15.4, 1.25), np_mat, bevel=True, bevel_width=0.02, is_collider=False)
        add_box(c_street, f"News_Box_Window_{npi}", (npx - 0.20, 14.76, 0.80), (npx + 0.20, 14.82, 1.15), mats["Smoked_Glass"], is_collider=False)
        add_cylinder(c_street, f"News_Box_Coin_{npi}", (npx + 0.15, 14.76, 1.20), radius=0.02, height=0.02, material=mats["Chrome_Polished"], segments=6, is_collider=False)
        add_oriented_cylinder(c_street, f"News_Box_Handle_{npi}", (npx - 0.12, 14.74, 0.75), (npx + 0.12, 14.74, 0.75), radius=0.015, material=mats["Chrome_Polished"], segments=6, is_collider=False)

    # -------------------------------------------------------------------------
    # MUNICIPAL CAST-IRON DOMED TRASH RECEPTACLES
    # -------------------------------------------------------------------------
    for ti, tx in enumerate([11.5, 53.5]):
        add_cylinder(c_street, f"Trash_Bin_Body_{ti}", (tx, 15.0, 0.65), radius=0.30, height=0.90, material=mats["Cast_Iron_Dark"], segments=16, is_collider=False)
        add_cylinder(c_street, f"Trash_Bin_Dome_{ti}", (tx, 15.0, 1.18), radius=0.32, height=0.18, material=mats["Cast_Iron_Dark"], segments=16, is_collider=False)
        add_box(c_street, f"Trash_Bin_Door_{ti}", (tx - 0.15, 14.68, 0.90), (tx + 0.15, 14.72, 1.08), mats["Chrome_Polished"], is_collider=False)

    print("Bank scene build complete!")


def export_scene_glb():
    """Scale the metre-authored scene to cubes and export it (see maplib)."""
    maplib.export_map_glb("hd_bank")


if __name__ == "__main__":
    print("=== Generating Ultra-Detailed Realistic 'The Bank' (hd_bank) Map in Blender ===")
    build_bank_scene()
    export_scene_glb()
    print("=== Generation and Export Complete! ===")
