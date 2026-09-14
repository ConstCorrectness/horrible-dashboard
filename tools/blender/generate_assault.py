#!/usr/bin/env python3
"""
CS:GO cs_assault Ultra-Detailed Realistic Map Generator for Blender 4.2+
Natively 1:1 metric scale on dense 56m x 56m CS:GO arena footprint (bounds: 4.0..60.0).

Ultra-Realism Upgrades:
- Structural Open-Web Steel Pratt/Warren Roof Trusses across warehouse ceiling with diagonal web bracing and gusset plates.
- Industrial Roof Purlins and ceiling sway bracing framework.
- Suspended Industrial HVAC Spiral Ductwork System with conical air diffuser registers and trapeze hangers.
- Automatic Wet-Pipe Fire Sprinkler System with OSHA red distribution piping, branch lines, and brass pendant sprinkler heads.
- Suspended High-Bay LED UFO Industrial Luminaires with finned aluminum heat sinks and optical emissive glow.
- Pitched Industrial Roof Ridge Skylights with steel curbs, translucent polycarbonate glazing, and daylight penetration.
- Complete Interior & Exterior Industrial Steel Staircases:
    * West Catwalk Staircase: 18 diamond-plate steps with open risers, C-channel stringers, safety yellow handrails & toeboards.
    * East Wall Exterior Fire Escape: 2-tier structural stair flights with intermediate grated landings from ground to 2nd-floor office.
- Demag / Konecranes 10-Ton Overhead Traveling Bridge Crane:
    * Runway I-beams on column brackets with ASCE crane rails.
    * Double box-girder crane bridge with bold yellow/black chevron safety hazard stripes.
    * Motorized hoist trolley with grooved wire rope drum, finned motor, 4-fall steel wire reeving, and forged 360-degree swivel hook.
    * Suspended yellow pushbutton pendant controller with emergency stop mushroom button.
    * C-rail festoon cable electrification system with looped yellow trailing cables.
- Northwest Maintenance & Workshop Bay:
    * Heavy industrial machinist workbench with 2.5" maple butcher block top and lower tool shelf.
    * 5-inch cast-iron bench vise with knurled jaws and swivel base.
    * Dual-wheel abrasive bench grinder with safety eye shields.
    * Perforated steel tool pegboard with silhouette outlines and hung hand tools.
    * Professional 7-drawer rolling red tool chest on locking casters.
    * 45-gallon OSHA safety yellow flammable liquids storage cabinet with self-closing doors.
    * Compressed air network: anodized blue aluminum airlines, FRL unit with pressure dial gauge, and yellow spring-rewind hose reel.
- Loading Docks & Bay Architecture:
    * Recessed hydraulic pit dock levelers in Bay 1 & Bay 2 with diamond-plate ramp decks, hinged lip plates, and toe guards.
    * Laminated heavy-duty rubber dock bumpers with structural steel face angles.
    * Flush driveway ramp curb aprons at Bay 1 and Bay 2 for seamless vehicle and player entry.
    * Architectural bronze industrial exterior wallpack floodlights with warm amber emissive illumination.
    * Weathered industrial signage: "BAY 01 - RECEIVING", "BAY 02 - SHIPPING", "MAX CLEARANCE 14'-6\"", "FIRE LANE".
- Cargo Diversity & Material Handling:
    * Stacks of 10 empty wooden GMA shipping pallets in warehouse corners.
    * Heavy wooden machinery crates with 2x4 framing, plywood panels, and corner steel brackets.
    * Shrink-wrapped cartons with HazMat diamond placards (Flammable Liquid 3, Corrosive 8) and shipping labels.
    * 55-gallon steel drums on yellow polyethylene chemical spill containment sump platforms.
    * 1.2m industrial wooden cable spools wound with thick insulated black copper feeder cable.
    * Counterbalanced forklift with horizontal LP propane fuel cylinder, 2-stage clear-view mast, hydraulic hoses, and cockpit controls.
    * Yellow polyurethane wrap protectors around all interior column plinths.
    * Manual hydraulic pallet jack with pump tiller and polyurethane wheels.
- Civil, Street & Highway Overpass Infrastructure:
    * Interstate green overhead highway sign bridge with route shields and signs ("I-95 NORTH / EXIT 4B - INDUSTRIAL HARBOR / DOCKS").
    * Galvanized corrugated W-beam guardrails with spacer posts and curved flared terminal end treatments.
    * Yellow highway impact attenuator crash cushions at bridge piers.
    * Segmented concrete jersey barriers with yellow reflective delineator tabs.
    * Curbside storm drain catch basins with slotted cast-iron grates and sewer manhole covers.
    * Concrete utility pole with crossarm, porcelain pin insulators, transformer pot, and aerial service drop cables.
    * Street signs: octagonal red "STOP", "SPEED LIMIT 25", "TRUCK ROUTE ONLY".
- Armored SWAT Tactical Response Van:
    * Heavy tubular wrap-around push bumper / bull bar with rubber push pads.
    * Radiator grille, composite headlights, Whelen emergency strobe lightbar, and dual tactical whip antennas.
- 18-Wheeler Tractor-Trailer:
    * High-roof aerodynamic sleeper cab with wind deflector and vertical dual chrome exhaust stacks.
    * Dual cylindrical polished aluminum diesel fuel tanks with safety step grates.
    * 53ft trailer with ribbed aluminum exterior siding panels, landing gear dolly legs with sand shoes, and full red/white DOT-C2 conspicuity tape.
- 2nd-Floor Hostage Office:
    * Acoustic drop-ceiling suspension grid with fluorescent light troffers and HVAC return grilles.
    * Panoramic observation window with horizontal Venetian blind slats propped half-open.
    * Dual computer workstations with laminate desks, 5-star swivel chairs, dual monitors, computer towers, mousepads.
    * 4-drawer lateral file cabinets, tactical dry-erase whiteboard with schematics.
    * Multi-screen CCTV security console with 4 active camera displays, 42U server equipment rack with blinking LEDs.
    * Coffee maker with glass carafe and 5-gallon bottled water dispenser.
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
    # Concrete and Road
    mats["Asphalt"] = create_pbr_material("Mat_Asphalt", (0.13, 0.13, 0.14), metallic=0.05, roughness=0.90)
    mats["Asphalt_Crack"] = create_pbr_material("Mat_Asphalt_Crack_Detail", (0.04, 0.04, 0.04), metallic=0.0, roughness=0.98)
    mats["Concrete"] = create_pbr_material("Mat_Concrete", (0.55, 0.53, 0.50), metallic=0.0, roughness=0.82)
    mats["Concrete_Dark"] = create_pbr_material("Mat_Concrete_Dark", (0.33, 0.32, 0.31), metallic=0.0, roughness=0.85)
    mats["Concrete_Joint"] = create_pbr_material("Mat_Concrete_Joint_Detail", (0.15, 0.15, 0.16), metallic=0.0, roughness=0.9)
    mats["Curb"] = create_pbr_material("Mat_Curb", (0.62, 0.60, 0.58), metallic=0.0, roughness=0.75)
    mats["Road_Yellow"] = create_pbr_material("Mat_Road_Yellow_Stripe", (0.90, 0.72, 0.12), metallic=0.0, roughness=0.55)
    mats["Road_White"] = create_pbr_material("Mat_Road_White_Stripe", (0.90, 0.90, 0.90), metallic=0.0, roughness=0.55)
    mats["Highway_Green"] = create_pbr_material("Mat_Highway_Green_Sign", (0.04, 0.38, 0.18), metallic=0.1, roughness=0.45)
    mats["Highway_Blue"] = create_pbr_material("Mat_Highway_Blue_Sign", (0.05, 0.28, 0.65), metallic=0.1, roughness=0.45)
    mats["Attenuator_Yellow"] = create_pbr_material("Mat_Attenuator_Yellow", (0.95, 0.78, 0.05), metallic=0.1, roughness=0.5)

    # Architectural Brick & Cladding
    mats["Brick_Base"] = create_pbr_material("Mat_Brick_Base", (0.46, 0.23, 0.17), metallic=0.0, roughness=0.88)
    mats["Warehouse_Wall"] = create_pbr_material("Mat_Warehouse_Wall", (0.38, 0.40, 0.44), metallic=0.35, roughness=0.65)
    mats["Warehouse_Wall_Stripe"] = create_pbr_material("Mat_Warehouse_Wall_Stripe", (0.80, 0.66, 0.14), metallic=0.2, roughness=0.55)
    mats["Warehouse_Floor"] = create_pbr_material("Mat_Warehouse_Floor", (0.43, 0.43, 0.41), metallic=0.1, roughness=0.72)
    mats["Warehouse_Roof"] = create_pbr_material("Mat_Warehouse_Roof", (0.26, 0.27, 0.29), metallic=0.25, roughness=0.9)
    mats["Garage_Door"] = create_pbr_material("Mat_Garage_Door", (0.44, 0.46, 0.48), metallic=0.78, roughness=0.38)
    mats["Hazard_Yellow"] = create_pbr_material("Mat_Hazard_Yellow_Stripe", (0.92, 0.76, 0.08), metallic=0.1, roughness=0.45)
    mats["Hazard_Black"] = create_pbr_material("Mat_Hazard_Black_Stripe", (0.08, 0.08, 0.10), metallic=0.1, roughness=0.45)
    mats["Skylight_Glass"] = create_pbr_material("Mat_Skylight_Glass", (0.75, 0.88, 0.96), transmission=0.85, roughness=0.15, alpha=0.45)
    mats["Oil_Stain"] = create_pbr_material("Mat_Oil_Stain_Detail", (0.03, 0.03, 0.03), metallic=0.1, roughness=0.18, alpha=0.75)

    # Steel & Industrial Utilities
    mats["Steel_Dark"] = create_pbr_material("Mat_Steel_Dark", (0.17, 0.18, 0.20), metallic=0.88, roughness=0.35)
    mats["Steel_Truss"] = create_pbr_material("Mat_Steel_Truss_Detail", (0.22, 0.23, 0.25), metallic=0.90, roughness=0.32)
    mats["Steel_Catwalk"] = create_pbr_material("Mat_Steel_Catwalk", (0.28, 0.30, 0.32), metallic=0.90, roughness=0.3)
    mats["Safety_Rail"] = create_pbr_material("Mat_Safety_Rail", (0.88, 0.72, 0.10), metallic=0.5, roughness=0.38)
    mats["Vent_Duct"] = create_pbr_material("Mat_Vent_Duct", (0.50, 0.52, 0.55), metallic=0.88, roughness=0.25)
    mats["Spiral_Duct"] = create_pbr_material("Mat_Spiral_Duct_Detail", (0.58, 0.60, 0.62), metallic=0.92, roughness=0.22)
    mats["Conduit_Silver"] = create_pbr_material("Mat_Conduit_Silver_Detail", (0.66, 0.68, 0.70), metallic=0.94, roughness=0.22)
    mats["Sprinkler_Red"] = create_pbr_material("Mat_Sprinkler_Red_Pipe", (0.78, 0.10, 0.08), metallic=0.75, roughness=0.32)
    mats["Sprinkler_Brass"] = create_pbr_material("Mat_Sprinkler_Brass_Detail", (0.86, 0.66, 0.26), metallic=0.92, roughness=0.24)
    mats["Brass_Fitting"] = create_pbr_material("Mat_Brass_Fitting_Detail", (0.88, 0.72, 0.28), metallic=0.92, roughness=0.22)
    mats["Diamond_Plate"] = create_pbr_material("Mat_Diamond_Plate", (0.35, 0.37, 0.40), metallic=0.92, roughness=0.25)
    mats["Chainlink_Steel"] = create_pbr_material("Mat_Chainlink_Steel", (0.38, 0.40, 0.42), metallic=0.85, roughness=0.35)
    mats["Guardrail_Galv"] = create_pbr_material("Mat_Guardrail_Galv", (0.60, 0.62, 0.65), metallic=0.88, roughness=0.35)
    mats["Airline_Blue"] = create_pbr_material("Mat_Airline_Blue_Pipe", (0.08, 0.45, 0.85), metallic=0.8, roughness=0.28)
    mats["Hose_Yellow"] = create_pbr_material("Mat_Hose_Yellow_Detail", (0.95, 0.82, 0.08), metallic=0.1, roughness=0.45)
    mats["Wallpack_Bronze"] = create_pbr_material("Mat_Wallpack_Bronze_Detail", (0.22, 0.18, 0.15), metallic=0.7, roughness=0.45)

    # Racks, Storage & Props
    mats["Rack_Upright_Blue"] = create_pbr_material("Mat_Rack_Upright_Blue", (0.10, 0.32, 0.68), metallic=0.35, roughness=0.38)
    mats["Rack_Beam_Orange"] = create_pbr_material("Mat_Rack_Beam_Orange", (0.88, 0.35, 0.06), metallic=0.25, roughness=0.38)
    mats["Cardboard_Box"] = create_pbr_material("Mat_Cardboard_Box", (0.64, 0.49, 0.32), metallic=0.0, roughness=0.85)
    mats["Plastic_Shrinkwrap"] = create_pbr_material("Mat_Plastic_Shrinkwrap", (0.88, 0.90, 0.94), transmission=0.75, roughness=0.15, alpha=0.45)
    mats["Drum_Blue"] = create_pbr_material("Mat_Drum_Blue", (0.08, 0.26, 0.60), metallic=0.55, roughness=0.42)
    mats["Drum_Red"] = create_pbr_material("Mat_Drum_Red", (0.70, 0.12, 0.10), metallic=0.55, roughness=0.42)
    mats["Drum_Black"] = create_pbr_material("Mat_Drum_Black", (0.10, 0.10, 0.12), metallic=0.55, roughness=0.42)
    mats["Wood_Crate"] = create_pbr_material("Mat_Wood_Crate", (0.58, 0.44, 0.28), metallic=0.0, roughness=0.82)
    mats["Wood_Workbench"] = create_pbr_material("Mat_Wood_Workbench", (0.68, 0.50, 0.34), metallic=0.0, roughness=0.65)
    mats["Wood_Pallet"] = create_pbr_material("Mat_Wood_Pallet", (0.55, 0.42, 0.28), metallic=0.0, roughness=0.88)
    mats["Spill_Pallet_Yellow"] = create_pbr_material("Mat_Spill_Pallet_Yellow", (0.86, 0.74, 0.06), metallic=0.1, roughness=0.4)
    mats["Pallet_Jack_Yellow"] = create_pbr_material("Mat_Pallet_Jack_Yellow", (0.92, 0.72, 0.08), metallic=0.4, roughness=0.35)
    mats["Toolbox_Red"] = create_pbr_material("Mat_Toolbox_Red", (0.82, 0.12, 0.10), metallic=0.7, roughness=0.25)
    mats["Propane_White"] = create_pbr_material("Mat_Propane_White_Detail", (0.88, 0.88, 0.86), metallic=0.4, roughness=0.4)
    mats["Cable_Spool_Wood"] = create_pbr_material("Mat_Cable_Spool_Wood", (0.50, 0.38, 0.24), metallic=0.0, roughness=0.85)
    mats["Copper_Cable"] = create_pbr_material("Mat_Copper_Cable_Detail", (0.12, 0.12, 0.13), metallic=0.1, roughness=0.65)

    # Safety & Signs
    mats["Fire_Extinguisher_Red"] = create_pbr_material("Mat_Fire_Extinguisher_Red", (0.86, 0.08, 0.08), metallic=0.45, roughness=0.28)
    mats["Safety_Eyewash_Green"] = create_pbr_material("Mat_Safety_Eyewash_Green", (0.06, 0.58, 0.24), metallic=0.3, roughness=0.4)
    mats["Exit_Sign_Green"] = create_pbr_material("Mat_Exit_Sign_Green", (0.10, 0.85, 0.25), emission_color=(0.15, 0.95, 0.3), emission_strength=5.0)
    mats["Fluorescent_Glow"] = create_pbr_material("Mat_Fluorescent_Glow", (0.95, 0.98, 1.0), emission_color=(0.95, 0.98, 1.0), emission_strength=8.0)
    mats["UFO_LED_Glow"] = create_pbr_material("Mat_UFO_LED_Glow", (0.98, 0.99, 1.0), emission_color=(0.98, 0.99, 1.0), emission_strength=10.0)
    mats["Amber_Glow"] = create_pbr_material("Mat_Amber_Glow_Lamp", (1.0, 0.75, 0.35), emission_color=(1.0, 0.75, 0.35), emission_strength=6.0)
    mats["Traffic_Cone_Orange"] = create_pbr_material("Mat_Traffic_Cone_Orange", (0.96, 0.36, 0.04), metallic=0.0, roughness=0.45)
    mats["Lamp_Glow"] = create_pbr_material("Mat_Lamp_Glow", (1.0, 0.95, 0.8), emission_color=(1.0, 0.95, 0.8), emission_strength=6.0)

    # Containers & Train
    mats["Container_Blue"] = create_pbr_material("Mat_Container_Blue", (0.14, 0.30, 0.56), metallic=0.35, roughness=0.48)
    mats["Container_Red"] = create_pbr_material("Mat_Container_Red", (0.66, 0.20, 0.16), metallic=0.35, roughness=0.48)
    mats["Container_Green"] = create_pbr_material("Mat_Container_Green", (0.16, 0.42, 0.24), metallic=0.35, roughness=0.48)
    mats["Train_Rust"] = create_pbr_material("Mat_Train_Rust", (0.50, 0.20, 0.15), metallic=0.55, roughness=0.68)
    mats["Train_Rail"] = create_pbr_material("Mat_Train_Rail_Detail", (0.22, 0.24, 0.26), metallic=0.96, roughness=0.18)
    mats["Train_Tie"] = create_pbr_material("Mat_Train_Tie_Detail", (0.20, 0.15, 0.11), metallic=0.0, roughness=0.92)

    # Vehicles
    mats["SWAT_Navy"] = create_pbr_material("Mat_SWAT_Navy", (0.10, 0.14, 0.22), metallic=0.55, roughness=0.38)
    mats["SWAT_Tire"] = create_pbr_material("Mat_SWAT_Tire", (0.07, 0.07, 0.08), metallic=0.0, roughness=0.94)
    mats["SWAT_Chrome"] = create_pbr_material("Mat_SWAT_Chrome_Detail", (0.84, 0.86, 0.89), metallic=0.98, roughness=0.10)
    mats["SWAT_Glass"] = create_pbr_material("Mat_SWAT_Glass", (0.68, 0.82, 0.92), transmission=0.8, roughness=0.05, alpha=0.5)
    mats["SWAT_Headlight"] = create_pbr_material("Mat_SWAT_Headlight", (0.95, 0.95, 0.75), emission_color=(1.0, 1.0, 0.8), emission_strength=5.0)
    mats["SWAT_Siren_Red"] = create_pbr_material("Mat_SWAT_Siren_Red", (0.95, 0.08, 0.08), emission_color=(1.0, 0.05, 0.05), emission_strength=5.0)
    mats["SWAT_Siren_Blue"] = create_pbr_material("Mat_SWAT_Siren_Blue", (0.08, 0.35, 0.95), emission_color=(0.1, 0.4, 1.0), emission_strength=5.0)
    mats["Truck_Cab"] = create_pbr_material("Mat_Truck_Cab", (0.90, 0.90, 0.92), metallic=0.35, roughness=0.32)
    mats["Truck_Trailer"] = create_pbr_material("Mat_Truck_Trailer", (0.66, 0.68, 0.72), metallic=0.65, roughness=0.38)
    mats["Forklift_Yellow"] = create_pbr_material("Mat_Forklift_Yellow", (0.88, 0.68, 0.08), metallic=0.25, roughness=0.38)

    # Office & Electronics
    mats["Office_Wall"] = create_pbr_material("Mat_Office_Wall", (0.76, 0.74, 0.70), metallic=0.0, roughness=0.85)
    mats["Office_Floor"] = create_pbr_material("Mat_Office_Floor", (0.35, 0.37, 0.41), metallic=0.0, roughness=0.72)
    mats["Office_Ceiling"] = create_pbr_material("Mat_Office_Ceiling_Detail", (0.85, 0.85, 0.84), metallic=0.0, roughness=0.9)
    mats["Office_Window"] = create_pbr_material("Mat_Office_Window", (0.68, 0.82, 0.92), transmission=0.88, roughness=0.05, alpha=0.35)
    mats["Mini_Blinds"] = create_pbr_material("Mat_Mini_Blinds_Blind", (0.82, 0.82, 0.80), metallic=0.1, roughness=0.6)
    mats["Desk_Wood"] = create_pbr_material("Mat_Desk_Wood", (0.36, 0.30, 0.24), metallic=0.0, roughness=0.7)
    mats["Computer_Chassis"] = create_pbr_material("Mat_Computer_Chassis", (0.72, 0.70, 0.66), metallic=0.1, roughness=0.55)
    mats["Screen_CCTV"] = create_pbr_material("Mat_Screen_CCTV", (0.12, 0.28, 0.22), emission_color=(0.18, 0.55, 0.38), emission_strength=3.0)
    mats["Water_Bottle_Blue"] = create_pbr_material("Mat_Water_Bottle_Blue", (0.2, 0.5, 0.85), transmission=0.9, roughness=0.05, alpha=0.4)
    mats["Whiteboard"] = create_pbr_material("Mat_Whiteboard_Detail", (0.92, 0.92, 0.94), metallic=0.05, roughness=0.2)

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


def add_oriented_box(col, name, p_start, p_end, width, height, material, is_collider=False):
    """Create a box oriented between two arbitrary 3D endpoints (ideal for diagonal truss struts)."""
    p0 = Vector(p_start)
    p1 = Vector(p_end)
    diff = p1 - p0
    length = diff.length
    if length < 0.001:
        return None

    mid = (p0 + p1) / 2.0

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = mid
    obj.scale = (width / 2.0, height / 2.0, length / 2.0)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    # Orient along Z towards target vector
    z_axis = Vector((0, 0, 1))
    target_dir = diff.normalized()
    rot_quat = z_axis.rotation_difference(target_dir)
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = rot_quat
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

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


def add_oriented_cylinder(col, name, p_start, p_end, radius, material, segments=16, is_collider=False):
    """Create a cylinder along an arbitrary 3D line segment (pipes, conduits, ducts)."""
    p0 = Vector(p_start)
    p1 = Vector(p_end)
    diff = p1 - p0
    length = diff.length
    if length < 0.001:
        return None

    mid = (p0 + p1) / 2.0
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)

    obj.location = mid
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

    for p in mesh.polygons:
        p.use_smooth = True

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    z_axis = Vector((0, 0, 1))
    target_dir = diff.normalized()
    rot_quat = z_axis.rotation_difference(target_dir)
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = rot_quat
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

    obj["is_collider"] = is_collider
    return obj


def add_i_beam(col, name, center_x, center_y, z0, z1, depth=0.35, flange_w=0.30, web_t=0.04, flange_t=0.04, material=None, is_collider=True):
    """Create a structural steel I-beam column with web and flanges."""
    # Central web (marked Detail for non-collision)
    add_box(col, f"{name}_Web_Detail", (center_x - web_t/2, center_y - depth/2 + flange_t, z0), (center_x + web_t/2, center_y + depth/2 - flange_t, z1), material, is_collider=False)
    # Front flange
    add_box(col, f"{name}_Flange_Front", (center_x - flange_w/2, center_y - depth/2, z0), (center_x + flange_w/2, center_y - depth/2 + flange_t, z1), material, is_collider=is_collider, bevel=True)
    # Back flange
    add_box(col, f"{name}_Flange_Back", (center_x - flange_w/2, center_y + depth/2 - flange_t, z0), (center_x + flange_w/2, center_y + depth/2, z1), material, is_collider=is_collider, bevel=True)


def add_open_web_truss(col, mats, name, y_coord, x_start=8.2, x_end=45.8, z_bot=8.25, z_top=8.80, panel_w=2.0):
    """Build a realistic Pratt/Warren open-web steel roof truss across warehouse ceiling."""
    # Top chord
    add_box(col, f"{name}_Truss_Chord_Top", (x_start, y_coord - 0.08, z_top - 0.06), (x_end, y_coord + 0.08, z_top), mats["Steel_Truss"], is_collider=False)
    # Bottom chord
    add_box(col, f"{name}_Truss_Chord_Bot", (x_start, y_coord - 0.08, z_bot), (x_end, y_coord + 0.08, z_bot + 0.06), mats["Steel_Truss"], is_collider=False)

    # Vertical and diagonal web struts
    length = x_end - x_start
    num_panels = int(length / panel_w)
    actual_panel_w = length / num_panels

    for p in range(num_panels):
        px0 = x_start + p * actual_panel_w
        px1 = x_start + (p + 1) * actual_panel_w

        # Vertical strut at panel point
        add_box(col, f"{name}_Truss_Vertical_{p}", (px0 - 0.03, y_coord - 0.04, z_bot + 0.05), (px0 + 0.03, y_coord + 0.04, z_top - 0.05), mats["Steel_Truss"], is_collider=False)

        # Diagonal strut (alternating Warren pattern)
        if p % 2 == 0:
            add_oriented_box(col, f"{name}_Truss_Diagonal_{p}", (px0, y_coord, z_bot + 0.05), (px1, y_coord, z_top - 0.05), 0.05, 0.05, mats["Steel_Truss"], is_collider=False)
        else:
            add_oriented_box(col, f"{name}_Truss_Diagonal_{p}", (px0, y_coord, z_top - 0.05), (px1, y_coord, z_bot + 0.05), 0.05, 0.05, mats["Steel_Truss"], is_collider=False)

        # Gusset connection plates at joints
        add_box(col, f"{name}_Gusset_Bot_{p}", (px0 - 0.08, y_coord - 0.05, z_bot + 0.02), (px0 + 0.08, y_coord + 0.05, z_bot + 0.14), mats["Steel_Dark"], is_collider=False)
        add_box(col, f"{name}_Gusset_Top_{p}", (px0 - 0.08, y_coord - 0.05, z_top - 0.14), (px0 + 0.08, y_coord + 0.05, z_top - 0.02), mats["Steel_Dark"], is_collider=False)


def add_roof_purlins_and_sway(col, mats):
    """Add longitudinal steel roof purlins and horizontal sway cross-frames."""
    # Longitudinal Purlins (every 4m across X, running y: 22.0..56.0)
    for px in [12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0, 44.0]:
        add_box(col, f"Roof_Purlin_{int(px)}", (px - 0.05, 22.0, 8.78), (px + 0.05, 56.0, 8.86), mats["Steel_Dark"], is_collider=False)

    # Horizontal sway X-bracing between trusses
    for ty0, ty1 in [(26.0, 34.0), (42.0, 50.0)]:
        add_oriented_box(col, f"Sway_X1_{int(ty0)}", (14.0, ty0, 8.78), (22.0, ty1, 8.78), 0.04, 0.02, mats["Steel_Dark"], is_collider=False)
        add_oriented_box(col, f"Sway_X2_{int(ty0)}", (14.0, ty1, 8.78), (22.0, ty0, 8.78), 0.04, 0.02, mats["Steel_Dark"], is_collider=False)
        add_oriented_box(col, f"Sway_X3_{int(ty0)}", (30.0, ty0, 8.78), (38.0, ty1, 8.78), 0.04, 0.02, mats["Steel_Dark"], is_collider=False)
        add_oriented_box(col, f"Sway_X4_{int(ty0)}", (30.0, ty1, 8.78), (38.0, ty0, 8.78), 0.04, 0.02, mats["Steel_Dark"], is_collider=False)


def add_fire_sprinkler_system(col, mats):
    """Build complete automatic wet-pipe fire sprinkler system with red pipe drops and brass heads."""
    # Main 4-inch distribution header running East-West at y=24.0, z=8.45m
    add_oriented_cylinder(col, "Sprinkler_Main_Header_Pipe", (10.0, 24.0, 8.45), (44.0, 24.0, 8.45), radius=0.045, material=mats["Sprinkler_Red"])

    # Branch lines running North at x = 15.0, 23.0, 31.0, 39.0, z=8.45m
    for bx in [15.0, 23.0, 31.0, 39.0]:
        add_oriented_cylinder(col, f"Sprinkler_Branch_{int(bx)}_Pipe", (bx, 24.0, 8.45), (bx, 54.0, 8.45), radius=0.025, material=mats["Sprinkler_Red"])

        # Pendant sprinkler heads spaced every 6 meters along branch
        for sy in [28.0, 34.0, 40.0, 46.0, 52.0]:
            if bx >= 28.0 and sy >= 40.0:
                continue  # above hostage office
            # Drop pipe nipple
            add_oriented_cylinder(col, f"Sprinkler_Drop_{int(bx)}_{int(sy)}_Pipe", (bx, sy, 8.45), (bx, sy, 8.25), radius=0.015, material=mats["Sprinkler_Red"])
            # Brass sprinkler frame and deflector plate
            add_cylinder(col, f"Sprinkler_Head_{int(bx)}_{int(sy)}_Detail", (bx, sy, 8.22), radius=0.035, height=0.04, material=mats["Sprinkler_Brass"], segments=12, is_collider=False)

    # Main vertical riser pipe going down near west wall
    add_oriented_cylinder(col, "Sprinkler_Riser_Pipe", (9.5, 24.0, 1.0), (9.5, 24.0, 8.45), radius=0.06, material=mats["Sprinkler_Red"])
    # Red OS&Y gate control valve wheel
    add_cylinder(col, "Sprinkler_Valve_Wheel_Detail", (9.5, 24.0, 1.8), radius=0.18, height=0.03, material=mats["Hazard_Yellow"], segments=16, is_collider=False)


def add_spiral_hvac_system(col, mats):
    """Build suspended industrial spiral HVAC ducts with conical air diffuser registers."""
    # Main trunk duct running south-to-north down center aisle (x: 25.5, z: 7.55m, y: 23.0..53.0)
    add_oriented_cylinder(col, "HVAC_Main_Trunk_Duct", (25.5, 23.0, 7.55), (25.5, 53.0, 7.55), radius=0.32, material=mats["Spiral_Duct"], segments=24)

    # Trapeze duct hangers suspended from trusses
    for hy in [26.0, 34.0, 42.0, 50.0]:
        add_box(col, f"HVAC_Trapeze_{int(hy)}_Detail", (25.0, hy - 0.04, 7.15), (26.0, hy + 0.04, 7.22), mats["Steel_Dark"], is_collider=False)
        add_oriented_cylinder(col, f"HVAC_Rod_L_{int(hy)}_Wire", (25.05, hy, 7.22), (25.05, hy, 8.6), radius=0.01, material=mats["Steel_Dark"])
        add_oriented_cylinder(col, f"HVAC_Rod_R_{int(hy)}_Wire", (25.95, hy, 7.22), (25.95, hy, 8.6), radius=0.01, material=mats["Steel_Dark"])

    # Conical ceiling air diffuser registers blowing conditioned air downwards
    for dy in [28.0, 36.0, 44.0]:
        add_cylinder(col, f"HVAC_Diffuser_Neck_{int(dy)}_Vent", (25.5, dy, 7.18), radius=0.15, height=0.15, material=mats["Vent_Duct"], segments=16, is_collider=False)
        add_cylinder(col, f"HVAC_Diffuser_Cone1_{int(dy)}_Vent", (25.5, dy, 7.08), radius=0.28, height=0.03, material=mats["Vent_Duct"], segments=24, is_collider=False)
        add_cylinder(col, f"HVAC_Diffuser_Cone2_{int(dy)}_Vent", (25.5, dy, 7.03), radius=0.38, height=0.02, material=mats["Vent_Duct"], segments=24, is_collider=False)


def add_high_bay_ufo_lighting(col, mats):
    """Add modern high-bay UFO industrial LED luminaires suspended from roof trusses."""
    for lx in [16.0, 26.0, 36.0]:
        for ly in [28.0, 44.0]:
            if lx >= 28.0 and ly >= 40.0:
                continue  # above office
            # Die-cast finned aluminum heatsink housing
            add_cylinder(col, f"UFO_Light_Housing_{int(lx)}_{int(ly)}_Lamp", (lx, ly, 7.75), radius=0.26, height=0.08, material=mats["Steel_Dark"], segments=24, is_collider=False)
            # Glowing optical LED lens face
            add_cylinder(col, f"UFO_Light_Lens_{int(lx)}_{int(ly)}_Glow", (lx, ly, 7.70), radius=0.24, height=0.02, material=mats["UFO_LED_Glow"], segments=24, is_collider=False)
            # Suspension hook and steel safety cable
            add_oriented_cylinder(col, f"UFO_Light_Cable_{int(lx)}_{int(ly)}_Cable", (lx, ly, 7.79), (lx, ly, 8.65), radius=0.008, material=mats["Steel_Dark"])


def add_floor_expansion_joints_and_striping(col, mats):
    """Add realistic concrete saw-cut expansion joints, OSHA safety zones, bay numbers, and oil stains."""
    # 6m Saw-cut floor expansion joints (dark joint sealant lines at z=0.003m)
    for jx in [14.0, 20.0, 26.0, 32.0, 38.0, 44.0]:
        add_box(col, f"Floor_Joint_X_{int(jx)}_Joint", (jx - 0.015, 22.0, 0.001), (jx + 0.015, 56.0, 0.005), mats["Concrete_Joint"], is_collider=False)
    for jy in [28.0, 34.0, 40.0, 46.0, 52.0]:
        add_box(col, f"Floor_Joint_Y_{int(jy)}_Joint", (8.0, jy - 0.015, 0.001), (46.0, jy + 0.015, 0.005), mats["Concrete_Joint"], is_collider=False)

    # Diagonal yellow/black hazard warning stripes around loading Bay 1 & Bay 2 thresholds
    for b_idx, (bx0, bx1) in enumerate([(14.0, 22.0), (30.0, 38.0)]):
        # Perimeter hazard border
        add_box(col, f"Bay_{b_idx+1}_Hazard_Border_S_Stripe", (bx0, 21.85, 0.004), (bx1, 22.15, 0.008), mats["Hazard_Yellow"], is_collider=False)
        add_box(col, f"Bay_{b_idx+1}_Hazard_Border_W_Stripe", (bx0 - 0.15, 21.85, 0.004), (bx0 + 0.15, 23.5, 0.008), mats["Hazard_Yellow"], is_collider=False)
        add_box(col, f"Bay_{b_idx+1}_Hazard_Border_E_Stripe", (bx1 - 0.15, 21.85, 0.004), (bx1 + 0.15, 23.5, 0.008), mats["Hazard_Yellow"], is_collider=False)

        # Diagonal hazard stripes inside apron
        stripe_w = 0.35
        for s_idx in range(int((bx1 - bx0) / stripe_w)):
            sx = bx0 + s_idx * stripe_w
            mat = mats["Hazard_Black"] if s_idx % 2 == 0 else mats["Hazard_Yellow"]
            add_box(col, f"Bay_{b_idx+1}_Stripe_{s_idx}_Stripe", (sx, 22.15, 0.004), (sx + stripe_w * 0.85, 22.6, 0.007), mat, is_collider=False)

    # OSHA 36-inch clearance keep-clear boxes in front of electrical breaker panels
    for px, py in [(8.15, 29.0), (8.15, 45.0), (27.2, 55.85)]:
        if px < 10.0:  # West wall
            add_box(col, f"OSHA_KeepClear_{int(px)}_{int(py)}_Stripe", (8.05, py - 0.75, 0.003), (9.0, py + 0.75, 0.006), mats["Hazard_Yellow"], is_collider=False)
            add_box(col, f"OSHA_KeepClear_Inner_{int(px)}_{int(py)}_Detail", (8.10, py - 0.65, 0.004), (8.95, py + 0.65, 0.007), mats["Hazard_Black"], is_collider=False)

    # White & Yellow Pedestrian Safety Walkway Lane (leading from personnel entrance x=40, y=22 north along east wall)
    add_box(col, "Walkway_Line_L_Stripe", (39.8, 22.0, 0.004), (39.9, 44.0, 0.008), mats["Road_Yellow"], is_collider=False)
    add_box(col, "Walkway_Line_R_Stripe", (41.2, 22.0, 0.004), (41.3, 44.0, 0.008), mats["Road_Yellow"], is_collider=False)

    # Bold Bay Numbers ("01" and "02") painted on concrete floor
    add_box(col, "Bay1_Number_Floor_Decal", (17.5, 23.2, 0.005), (18.5, 24.2, 0.008), mats["Road_White"], is_collider=False)
    add_box(col, "Bay2_Number_Floor_Decal", (33.5, 23.2, 0.005), (34.5, 24.2, 0.008), mats["Road_White"], is_collider=False)

    # Realistic Oil Slicks & Fluid Stains
    oil_spots = [
        ("SWAT_Oil_Stain", (20.0, 16.5, 0.003), 0.9, 0.7),
        ("Truck_Oil_Stain", (34.0, 16.0, 0.003), 1.2, 0.8),
        ("Forklift_Oil_Stain", (23.0, 31.5, 0.003), 0.6, 0.5),
        ("Bay1_Tire_Scrub_L", (16.0, 22.8, 0.003), 0.35, 1.8),
        ("Bay1_Tire_Scrub_R", (20.0, 22.8, 0.003), 0.35, 1.8),
    ]
    for name, (ox, oy, oz), rx, ry in oil_spots:
        add_box(col, f"{name}_Stain", (ox - rx/2, oy - ry/2, oz), (ox + rx/2, oy + ry/2, oz + 0.003), mats["Oil_Stain"], is_collider=False)


def add_asphalt_cracks_and_thermoplastics(col, mats):
    """Add realistic meandering bitumen asphalt crack repairs and thermoplastic road markings."""
    crack_nodes = [
        [(10.0, 13.0), (11.2, 13.5), (12.0, 13.2), (13.5, 14.1), (14.8, 13.9), (16.0, 14.5)],
        [(24.0, 11.0), (25.5, 11.8), (26.2, 11.5), (27.8, 12.2), (29.0, 11.9)],
        [(36.0, 13.0), (37.5, 12.6), (38.8, 13.4), (40.2, 13.1), (41.5, 13.8)],
        [(44.0, 16.0), (45.2, 16.8), (46.8, 16.3), (48.0, 17.2)]
    ]
    for c_id, chain in enumerate(crack_nodes):
        for s_id in range(len(chain) - 1):
            p0 = (*chain[s_id], 0.004)
            p1 = (*chain[s_id + 1], 0.004)
            add_oriented_box(col, f"Asphalt_Crack_{c_id}_{s_id}_Crack", p0, p1, 0.04, 0.005, mats["Asphalt_Crack"], is_collider=False)

    # Thermoplastic Turn Arrow on Asphalt into Bay 1 (x: 18.0, y: 13.5)
    add_box(col, "Arrow_Shaft_Decal", (17.85, 12.5, 0.006), (18.15, 14.2, 0.009), mats["Road_White"], is_collider=False)
    add_oriented_box(col, "Arrow_Head_L_Decal", (18.0, 14.2, 0.006), (17.3, 13.6, 0.006), 0.25, 0.006, mats["Road_White"], is_collider=False)
    add_oriented_box(col, "Arrow_Head_R_Decal", (18.0, 14.2, 0.006), (18.7, 13.6, 0.006), 0.25, 0.006, mats["Road_White"], is_collider=False)

    # White Stop Bar in front of Crosswalk (y = 11.2)
    add_box(col, "Road_Stop_Bar_Stripe", (17.5, 11.1, 0.006), (22.5, 11.4, 0.009), mats["Road_White"], is_collider=False)


def add_commercial_dumpster(col, mats, name, center_x, center_y, z=0.0, color_mat="Container_Green"):
    """Build heavy-duty 6-yard front-load slanted steel dumpster with fork pockets and split plastic lids."""
    dx, dy = center_x, center_y
    mat_body = mats[color_mat]

    add_box(col, f"{name}_Tub", (dx - 1.1, dy - 0.9, z + 0.15), (dx + 1.1, dy + 0.9, z + 1.5), mat_body, bevel=True, bevel_width=0.04)
    add_box(col, f"{name}_Skid_L", (dx - 0.9, dy - 0.95, z), (dx - 0.75, dy + 0.95, z + 0.15), mats["Steel_Dark"])
    add_box(col, f"{name}_Skid_R", (dx + 0.75, dy - 0.95, z), (dx + 0.9, dy + 0.95, z + 0.15), mats["Steel_Dark"])

    for sx in [dx - 1.18, dx + 1.1]:
        add_box(col, f"{name}_Fork_Pocket_{sx}_Detail", (sx, dy - 0.75, z + 0.7), (sx + 0.08, dy + 0.75, z + 0.95), mats["Steel_Dark"], bevel=True, bevel_width=0.02, is_collider=False)

    add_box(col, f"{name}_Lid_L_Detail", (dx - 1.12, dy - 0.92, z + 1.5), (dx - 0.02, dy + 0.92, z + 1.58), mats["Hazard_Black"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(col, f"{name}_Lid_R_Detail", (dx + 0.02, dy - 0.92, z + 1.5), (dx + 1.12, dy + 0.92, z + 1.58), mats["Hazard_Black"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(col, f"{name}_Warning_Sign", (dx - 0.35, dy - 0.91, z + 0.95), (dx + 0.35, dy - 0.89, z + 1.25), mats["Hazard_Yellow"], is_collider=False)


def add_pallet_jack(col, mats, x, y, z=0.0):
    """Build detailed yellow manual industrial hydraulic pallet jack."""
    add_box(col, "PalletJack_Fork_L", (x - 0.28, y - 0.6, z + 0.04), (x - 0.12, y + 0.6, z + 0.12), mats["Pallet_Jack_Yellow"], bevel=True, bevel_width=0.015)
    add_box(col, "PalletJack_Fork_R", (x + 0.12, y - 0.6, z + 0.04), (x + 0.28, y + 0.6, z + 0.12), mats["Pallet_Jack_Yellow"], bevel=True, bevel_width=0.015)
    add_box(col, "PalletJack_Body", (x - 0.32, y - 0.95, z + 0.08), (x + 0.32, y - 0.55, z + 0.45), mats["Pallet_Jack_Yellow"], bevel=True, bevel_width=0.03)

    add_cylinder(col, "PalletJack_Piston_Detail", (x, y - 0.75, z + 0.48), radius=0.06, height=0.25, material=mats["SWAT_Chrome"], segments=16, is_collider=False)
    add_cylinder(col, "PalletJack_Wheel_L_Detail", (x - 0.18, y - 0.75, z + 0.08), radius=0.08, height=0.06, material=mats["Hazard_Black"], segments=16, is_collider=False)
    add_cylinder(col, "PalletJack_Wheel_R_Detail", (x + 0.18, y - 0.75, z + 0.08), radius=0.08, height=0.06, material=mats["Hazard_Black"], segments=16, is_collider=False)

    add_oriented_cylinder(col, "PalletJack_Handle_Rod_Detail", (x, y - 0.75, z + 0.55), (x, y - 1.15, z + 1.1), radius=0.02, material=mats["Pallet_Jack_Yellow"])
    add_box(col, "PalletJack_Handle_Loop_Detail", (x - 0.12, y - 1.18, z + 1.08), (x + 0.12, y - 1.12, z + 1.22), mats["Hazard_Black"], bevel=True, bevel_width=0.02, is_collider=False)


def add_emergency_eyewash_station(col, mats, x, y, z=0.0):
    """Build OSHA safety green & yellow emergency eyewash and drench safety shower station."""
    add_oriented_cylinder(col, "Eyewash_Riser_Pipe", (x, y, z), (x, y, z + 2.3), radius=0.03, material=mats["Safety_Eyewash_Green"])
    add_cylinder(col, "Eyewash_Showerhead_Detail", (x + 0.25, y, z + 2.25), radius=0.15, height=0.06, material=mats["Safety_Eyewash_Green"], segments=24, is_collider=False)
    add_oriented_cylinder(col, "Eyewash_Shower_Arm_Pipe", (x, y, z + 2.25), (x + 0.25, y, z + 2.25), radius=0.025, material=mats["Safety_Eyewash_Green"])
    add_oriented_cylinder(col, "Eyewash_PullRod_Detail", (x + 0.25, y, z + 2.22), (x + 0.25, y, z + 1.45), radius=0.008, material=mats["Hazard_Yellow"])

    add_cylinder(col, "Eyewash_Bowl_Detail", (x + 0.22, y, z + 1.05), radius=0.18, height=0.08, material=mats["SWAT_Chrome"], segments=24, is_collider=False)
    add_cylinder(col, "Eyewash_Nozzle_L_Detail", (x + 0.18, y - 0.05, z + 1.10), radius=0.02, height=0.04, material=mats["Hazard_Yellow"], segments=12, is_collider=False)
    add_cylinder(col, "Eyewash_Nozzle_R_Detail", (x + 0.18, y + 0.05, z + 1.10), radius=0.02, height=0.04, material=mats["Hazard_Yellow"], segments=12, is_collider=False)
    add_box(col, "Eyewash_Sign_Sign", (x + 0.05, y - 0.18, z + 1.6), (x + 0.07, y + 0.18, z + 1.95), mats["Safety_Eyewash_Green"], is_collider=False)


def add_surveillance_camera(col, mats, name, location, is_ptz_dome=False):
    """Build realistic CCTV security cameras."""
    cx, cy, cz = location
    if is_ptz_dome:
        add_cylinder(col, f"{name}_Dome_Base_Camera", (cx, cy, cz), radius=0.14, height=0.06, material=mats["Computer_Chassis"], segments=24, is_collider=False)
        add_cylinder(col, f"{name}_Dome_Glass_Camera", (cx, cy, cz - 0.05), radius=0.11, height=0.08, material=mats["Steel_Dark"], segments=24, is_collider=False)
    else:
        add_cylinder(col, f"{name}_Mount_Plate_Camera", (cx, cy, cz), radius=0.08, height=0.03, material=mats["Computer_Chassis"], segments=16, is_collider=False)
        add_box(col, f"{name}_Arm_Camera", (cx, cy - 0.02, cz - 0.02), (cx + 0.18, cy + 0.02, cz + 0.02), mats["Computer_Chassis"], is_collider=False)
        add_box(col, f"{name}_Body_Camera", (cx + 0.12, cy - 0.06, cz - 0.06), (cx + 0.35, cy + 0.06, cz + 0.06), mats["Computer_Chassis"], bevel=True, bevel_width=0.015, is_collider=False)
        add_box(col, f"{name}_Sunshield_Camera", (cx + 0.10, cy - 0.07, cz + 0.05), (cx + 0.38, cy + 0.07, cz + 0.08), mats["Steel_Dark"], is_collider=False)
        add_cylinder(col, f"{name}_Lens_Camera", (cx + 0.35, cy, cz), radius=0.04, height=0.02, material=mats["Steel_Dark"], segments=16, is_collider=False)


def add_industrial_staircase(col, mats, name, x_min, x_max, y_start, y_end, z_start, z_end, num_steps=18):
    """Build a complete industrial steel staircase with diamond-plate treads, C-channel stringers, and safety handrails."""
    dy = (y_end - y_start) / num_steps
    dz = (z_end - z_start) / num_steps
    tread_w = x_max - x_min
    tread_depth = abs(dy) * 1.05

    # Individual Walkable Stair Treads
    for step_i in range(num_steps):
        sy0 = y_start + step_i * dy
        sy1 = sy0 + dy
        sz0 = z_start + step_i * dz
        sz1 = sz0 + dz
        # Step Tread (Solid Collider)
        add_box(
            col,
            f"{name}_Tread_{step_i}",
            (x_min + 0.04, min(sy0, sy1), sz0),
            (x_max - 0.04, max(sy0, sy1), sz0 + 0.06),
            mats["Diamond_Plate"],
            is_collider=True
        )

    # Slanted C-Channel Stringers on Left & Right
    stringer_t = 0.06
    stringer_h = 0.22
    add_oriented_box(col, f"{name}_Stringer_L", (x_min + stringer_t/2, y_start, z_start), (x_min + stringer_t/2, y_end, z_end), stringer_t, stringer_h, mats["Steel_Dark"], is_collider=False)
    add_oriented_box(col, f"{name}_Stringer_R", (x_max - stringer_t/2, y_start, z_start), (x_max - stringer_t/2, y_end, z_end), stringer_t, stringer_h, mats["Steel_Dark"], is_collider=False)

    # Safety Handrails (Top rail at 1.0m, Mid rail at 0.5m, Kickplate toeboard at 0.1m)
    rail_offset = 0.03
    for side, rx in [("L", x_min - rail_offset), ("R", x_max + rail_offset)]:
        add_oriented_cylinder(col, f"{name}_Handrail_{side}_Top", (rx, y_start, z_start + 1.0), (rx, y_end, z_end + 1.0), radius=0.025, material=mats["Safety_Rail"])
        add_oriented_cylinder(col, f"{name}_Handrail_{side}_Mid", (rx, y_start, z_start + 0.5), (rx, y_end, z_end + 0.5), radius=0.02, material=mats["Safety_Rail"])
        add_oriented_box(col, f"{name}_Toeboard_{side}_Detail", (rx, y_start, z_start + 0.08), (rx, y_end, z_end + 0.08), 0.02, 0.12, mats["Safety_Rail"], is_collider=False)

        # Vertical support posts every 5 steps
        for post_i in range(0, num_steps + 1, 4):
            py = y_start + post_i * dy
            pz = z_start + post_i * dz
            add_oriented_cylinder(col, f"{name}_Post_{side}_{post_i}", (rx, py, pz), (rx, py, pz + 1.0), radius=0.022, material=mats["Safety_Rail"])


def add_ridge_skylight(col, mats, name, center_x, y_start, y_end, width=3.2, curb_height=0.4, peak_height=0.9):
    """Build an industrial pitched roof ridge skylight with steel curbs and translucent glazing."""
    half_w = width / 2.0
    z_roof = 9.0

    # Concrete / Metal Curb around perimeter
    add_box(col, f"{name}_Curb_W", (center_x - half_w - 0.12, y_start - 0.12, z_roof), (center_x - half_w, y_end + 0.12, z_roof + curb_height), mats["Steel_Dark"], is_collider=True)
    add_box(col, f"{name}_Curb_E", (center_x + half_w, y_start - 0.12, z_roof), (center_x + half_w + 0.12, y_end + 0.12, z_roof + curb_height), mats["Steel_Dark"], is_collider=True)
    add_box(col, f"{name}_Curb_S", (center_x - half_w, y_start - 0.12, z_roof), (center_x + half_w, y_start, z_roof + curb_height), mats["Steel_Dark"], is_collider=True)
    add_box(col, f"{name}_Curb_N", (center_x - half_w, y_end, z_roof), (center_x + half_w, y_end + 0.12, z_roof + curb_height), mats["Steel_Dark"], is_collider=True)

    # Central Ridge Beam
    z_curb = z_roof + curb_height
    z_peak = z_curb + peak_height
    add_box(col, f"{name}_Ridge_Beam_Detail", (center_x - 0.05, y_start, z_peak - 0.06), (center_x + 0.05, y_end, z_peak), mats["Steel_Dark"], is_collider=False)

    # Slanted Polycarbonate / Glass Glazing Panels
    num_panels = int((y_end - y_start) / 2.0)
    panel_len = (y_end - y_start) / num_panels
    for p_i in range(num_panels):
        py0 = y_start + p_i * panel_len
        py1 = py0 + panel_len
        # West pitched panel
        add_oriented_box(col, f"{name}_Glass_W_{p_i}_Skylight", (center_x - half_w, (py0 + py1)/2, z_curb), (center_x, (py0 + py1)/2, z_peak), half_w * 1.15, panel_len * 0.95, mats["Skylight_Glass"], is_collider=False)
        # East pitched panel
        add_oriented_box(col, f"{name}_Glass_E_{p_i}_Skylight", (center_x + half_w, (py0 + py1)/2, z_curb), (center_x, (py0 + py1)/2, z_peak), half_w * 1.15, panel_len * 0.95, mats["Skylight_Glass"], is_collider=False)
        # Metal glazing rafter bar
        add_oriented_cylinder(col, f"{name}_Rafter_W_{p_i}_Detail", (center_x - half_w, py0, z_curb), (center_x, py0, z_peak), radius=0.03, material=mats["Steel_Dark"])
        add_oriented_cylinder(col, f"{name}_Rafter_E_{p_i}_Detail", (center_x + half_w, py0, z_curb), (center_x, py0, z_peak), radius=0.03, material=mats["Steel_Dark"])


def add_overhead_crane(col, mats):
    """Build Demag / Konecranes 10-Ton Industrial Overhead Traveling Bridge Crane with festoon loop cables and pendant station."""
    # Runway I-Beams (W14x90) running along East and West interior walls at z=7.5m
    add_box(col, "Crane_Runway_Beam_W", (8.1, 22.0, 7.35), (8.5, 56.0, 7.7), mats["Steel_Dark"])
    add_box(col, "Crane_Runway_Beam_E", (45.5, 22.0, 7.35), (45.9, 56.0, 7.7), mats["Steel_Dark"])
    # Crane Rails on Runway Beams
    add_box(col, "Crane_Rail_W_Detail", (8.22, 22.0, 7.7), (8.38, 56.0, 7.82), mats["Train_Rail"], is_collider=False)
    add_box(col, "Crane_Rail_E_Detail", (45.62, 22.0, 7.7), (45.78, 56.0, 7.82), mats["Train_Rail"], is_collider=False)

    # Double Box-Girder Crane Bridge spanning 37.4m (x: 8.3..45.7 at y=36.0, z: 7.75..8.35m)
    cy = 36.0
    add_box(col, "Crane_Girder_Front_Detail", (8.3, cy - 0.55, 7.75), (45.7, cy - 0.20, 8.35), mats["Hazard_Yellow"], is_collider=False)
    add_box(col, "Crane_Girder_Rear_Detail", (8.3, cy + 0.20, 7.75), (45.7, cy + 0.55, 8.35), mats["Hazard_Yellow"], is_collider=False)

    # 45-Degree Chevron Hazard Warning Stripes on Girder Faces
    for gx in range(9, 45, 2):
        add_box(col, f"Crane_Chevron_F_{gx}_Stripe", (float(gx), cy - 0.57, 7.80), (float(gx) + 0.9, cy - 0.53, 8.30), mats["Hazard_Black"], is_collider=False)
        add_box(col, f"Crane_Chevron_R_{gx}_Stripe", (float(gx), cy + 0.53, 7.80), (float(gx) + 0.9, cy + 0.57, 8.30), mats["Hazard_Black"], is_collider=False)

    # End Trucks with Double-Flanged Steel Wheels
    for side, ex in [("W", 8.3), ("E", 45.7)]:
        add_box(col, f"Crane_EndTruck_{side}_Detail", (ex - 0.35, cy - 1.2, 7.65), (ex + 0.35, cy + 1.2, 8.0), mats["Hazard_Yellow"], bevel=True, bevel_width=0.03, is_collider=False)
        for wy in [cy - 0.9, cy + 0.9]:
            add_cylinder(col, f"Crane_Wheel_{side}_{int(wy*10)}_Detail", (ex, wy, 7.76), radius=0.18, height=0.10, material=mats["Steel_Dark"], segments=20, is_collider=False)

    # Motorized Wire Rope Hoist Trolley
    tx = 26.0
    add_box(col, "Crane_Trolley_Frame_Detail", (tx - 0.95, cy - 0.65, 8.35), (tx + 0.95, cy + 0.65, 8.75), mats["Steel_Dark"], bevel=True, bevel_width=0.03, is_collider=False)
    # Grooved Wire Rope Drum
    add_cylinder(col, "Crane_Wire_Drum_Detail", (tx, cy, 8.55), radius=0.30, height=0.90, material=mats["Steel_Dark"], segments=24, is_collider=False)
    # Electric Motor with finned cooling body
    add_cylinder(col, "Crane_Motor_Detail", (tx - 0.65, cy, 8.55), radius=0.22, height=0.55, material=mats["Airline_Blue"], segments=16, is_collider=False)

    # 4-Fall Steel Wire Rope Reeving hanging down to hook block
    for wx, wy in [(-0.10, -0.08), (0.10, -0.08), (-0.10, 0.08), (0.10, 0.08)]:
        add_oriented_cylinder(col, f"Crane_Wire_{wx}_{wy}_Cable", (tx + wx, cy + wy, 8.35), (tx + wx, cy + wy, 5.25), radius=0.012, material=mats["Steel_Dark"])

    # Bottom Hook Block with 360-Degree Swivel Hook
    add_box(col, "Crane_Hook_Block_Detail", (tx - 0.28, cy - 0.20, 4.85), (tx + 0.28, cy + 0.20, 5.25), mats["Hazard_Yellow"], bevel=True, bevel_width=0.02, is_collider=False)
    # 10T SWL Stencil Label on block
    add_box(col, "Crane_10T_Decal", (tx - 0.18, cy - 0.21, 4.95), (tx + 0.18, cy - 0.19, 5.15), mats["Hazard_Black"], is_collider=False)
    add_cylinder(col, "Crane_Hook_Stem_Detail", (tx, cy, 4.70), radius=0.08, height=0.30, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_cylinder(col, "Crane_Hook_Horn_Detail", (tx + 0.08, cy, 4.50), radius=0.14, height=0.18, material=mats["Steel_Dark"], segments=16, is_collider=False)

    # Suspended Pushbutton Pendant Control Station hanging down to chest height (z=1.5m)
    add_oriented_cylinder(col, "Crane_Pendant_Cable_Wire", (tx + 1.2, cy - 0.4, 8.35), (tx + 1.2, cy - 0.4, 1.8), radius=0.008, material=mats["Steel_Dark"])
    add_box(col, "Crane_Pendant_Station_Detail", (tx + 1.12, cy - 0.46, 1.25), (tx + 1.28, cy - 0.34, 1.80), mats["Hazard_Yellow"], bevel=True, bevel_width=0.02, is_collider=False)
    # Red Mushroom Emergency Stop button
    add_cylinder(col, "Crane_Pendant_Estop_Detail", (tx + 1.20, cy - 0.40, 1.82), radius=0.045, height=0.04, material=mats["Fire_Extinguisher_Red"], segments=12, is_collider=False)

    # C-Rail Festoon Loop Cable Electrification along Bridge
    for fx in range(12, 44, 4):
        # Festoon carrier trolley
        add_box(col, f"Festoon_Carrier_{fx}_Detail", (float(fx) - 0.08, cy + 0.58, 8.30), (float(fx) + 0.08, cy + 0.68, 8.42), mats["Steel_Dark"], is_collider=False)
        # Trailing loop cable
        add_oriented_cylinder(col, f"Festoon_Loop_{fx}_Cable", (float(fx) - 1.8, cy + 0.63, 8.30), (float(fx), cy + 0.63, 7.85), radius=0.015, material=mats["Hazard_Yellow"])
        add_oriented_cylinder(col, f"Festoon_Loop_{fx}_2_Cable", (float(fx), cy + 0.63, 7.85), (float(fx) + 1.8, cy + 0.63, 8.30), radius=0.015, material=mats["Hazard_Yellow"])


def add_workshop_maintenance_bay(col, mats):
    """Build complete industrial maintenance & workshop station in the northwest bay."""
    # Heavy Industrial Machinist Workbench (x: 9.0..13.0, y: 53.5..54.5, height 0.95m)
    add_box(col, "Workbench_Top", (9.0, 53.5, 0.90), (13.0, 54.5, 0.96), mats["Wood_Workbench"], bevel=True, bevel_width=0.02)
    # 4x4 Heavy Tubular Steel Leg Frame and Lower Storage Shelf
    for lx in [9.1, 12.9]:
        for ly in [53.6, 54.4]:
            add_box(col, f"Workbench_Leg_{int(lx*10)}_{int(ly*10)}", (lx - 0.05, ly - 0.05, 0.0), (lx + 0.05, ly + 0.05, 0.90), mats["Steel_Dark"])
    add_box(col, "Workbench_Lower_Shelf", (9.15, 53.6, 0.20), (12.85, 54.4, 0.25), mats["Steel_Dark"])

    # 5-Inch Cast-Iron Machinist Bench Vise on Left Corner
    add_cylinder(col, "Vise_Swivel_Base_Vise", (9.35, 53.7, 0.97), radius=0.10, height=0.04, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_box(col, "Vise_Fixed_Jaw_Vise", (9.22, 53.62, 0.99), (9.48, 53.78, 1.15), mats["Steel_Dark"], bevel=True, bevel_width=0.015, is_collider=False)
    add_box(col, "Vise_Movable_Jaw_Vise", (9.22, 53.82, 0.99), (9.48, 53.92, 1.15), mats["Steel_Dark"], bevel=True, bevel_width=0.015, is_collider=False)
    add_oriented_cylinder(col, "Vise_Screw_Handle_Vise", (9.35, 53.95, 1.05), (9.35, 54.15, 1.05), radius=0.015, material=mats["SWAT_Chrome"])

    # Dual-Wheel Heavy Abrasive Bench Grinder on Right Corner
    add_box(col, "Grinder_Motor_Detail", (12.35, 53.9, 0.96), (12.65, 54.2, 1.15), mats["Steel_Dark"], bevel=True, bevel_width=0.02, is_collider=False)
    add_cylinder(col, "Grinder_Wheel_L_Detail", (12.25, 54.05, 1.05), radius=0.10, height=0.04, material=mats["Concrete_Dark"], segments=20, is_collider=False)
    add_cylinder(col, "Grinder_Wheel_R_Detail", (12.75, 54.05, 1.05), radius=0.10, height=0.04, material=mats["Concrete_Dark"], segments=20, is_collider=False)

    # Perforated Steel Tool Pegboard Panel behind bench
    add_box(col, "Tool_Pegboard_Panel_Detail", (9.0, 54.48, 0.96), (13.0, 54.52, 2.10), mats["Steel_Dark"], is_collider=False)
    # Hung Hand Tools (Wrenches, Hammers, Hacksaw)
    for ti in range(6):
        tx = 9.4 + ti * 0.55
        add_box(col, f"Hung_Tool_{ti}_Detail", (tx - 0.04, 54.45, 1.35), (tx + 0.04, 54.47, 1.65), mats["SWAT_Chrome"], is_collider=False)

    # Professional 7-Drawer Red Rolling Tool Chest on Casters
    add_box(col, "Toolbox_Rollcab_Body", (10.2, 54.6, 0.12), (11.4, 55.4, 1.25), mats["Toolbox_Red"], bevel=True, bevel_width=0.03)
    for di in range(7):
        dz = 0.20 + di * 0.14
        add_box(col, f"Toolbox_Drawer_{di}_Detail", (10.25, 54.58, dz), (11.35, 54.62, dz + 0.11), mats["Toolbox_Red"], is_collider=False)
        add_box(col, f"Toolbox_Trim_{di}_Detail", (10.30, 54.56, dz + 0.08), (11.30, 54.59, dz + 0.10), mats["SWAT_Chrome"], is_collider=False)

    # 45-Gallon OSHA Safety Yellow Flammable Liquids Cabinet
    add_box(col, "Flammable_Cabinet_Body", (11.8, 54.6, 0.0), (13.0, 55.4, 1.65), mats["Hazard_Yellow"], bevel=True, bevel_width=0.03)
    add_box(col, "Flammable_Sign_Sign", (11.95, 54.58, 1.15), (12.85, 54.59, 1.45), mats["Fire_Extinguisher_Red"], is_collider=False)

    # Industrial Compressed Air Network: Blue airline piping, FRL unit, and yellow hose reel
    add_oriented_cylinder(col, "Airline_Main_Header_Pipe", (9.0, 53.0, 4.5), (14.0, 53.0, 4.5), radius=0.025, material=mats["Airline_Blue"])
    add_oriented_cylinder(col, "Airline_Drop_Pipe", (9.0, 53.0, 1.4), (9.0, 53.0, 4.5), radius=0.020, material=mats["Airline_Blue"])
    # Filter-Regulator-Lubricator (FRL) Unit with Pressure Gauge
    add_box(col, "Airline_FRL_Body_Detail", (8.90, 52.88, 1.35), (9.10, 53.12, 1.55), mats["Steel_Dark"], is_collider=False)
    add_cylinder(col, "Airline_Pressure_Gauge_Detail", (9.12, 53.0, 1.50), radius=0.05, height=0.03, material=mats["Brass_Fitting"], segments=16, is_collider=False)
    # Spring-Rewind Coiled Yellow Hose Reel
    add_cylinder(col, "Hose_Reel_Drum_Hose", (9.0, 52.7, 1.85), radius=0.25, height=0.18, material=mats["Steel_Dark"], segments=20, is_collider=False)
    add_cylinder(col, "Hose_Coil_Yellow_Hose", (9.0, 52.7, 1.85), radius=0.22, height=0.15, material=mats["Hose_Yellow"], segments=20, is_collider=False)


def add_loading_dock_upgrades(col, mats):
    """Add recessed hydraulic dock levelers, heavy dock bumpers, bronze wallpacks, and weathered signage."""
    # Recessed Hydraulic Pit Dock Levelers in Bay 1 & Bay 2
    for b_idx, bx_mid in enumerate([18.0, 34.0]):
        # Leveler Ramp Deck (walkable steel diamond plate)
        add_box(col, f"Dock_Leveler_Deck_Bay{b_idx+1}", (bx_mid - 1.2, 21.0, 0.0), (bx_mid + 1.2, 22.8, 0.08), mats["Diamond_Plate"])
        # Hinged Lip Plate
        add_box(col, f"Dock_Leveler_Lip_Bay{b_idx+1}_Detail", (bx_mid - 1.15, 20.85, 0.04), (bx_mid + 1.15, 21.05, 0.08), mats["Steel_Dark"], is_collider=False)
        # Yellow Side Toe Guard Skirts
        add_box(col, f"Dock_ToeGuard_L_Bay{b_idx+1}_Stripe", (bx_mid - 1.24, 21.0, 0.0), (bx_mid - 1.20, 22.8, 0.25), mats["Hazard_Yellow"], is_collider=False)
        add_box(col, f"Dock_ToeGuard_R_Bay{b_idx+1}_Stripe", (bx_mid + 1.20, 21.0, 0.0), (bx_mid + 1.24, 22.8, 0.25), mats["Hazard_Yellow"], is_collider=False)

    # Architectural Bronze Industrial Exterior Wallpack Floodlights
    wallpack_positions = [
        ("Wallpack_Bay1", (18.0, 21.4, 4.5)),
        ("Wallpack_Bay2", (34.0, 21.4, 4.5)),
        ("Wallpack_Door_S", (40.0, 21.4, 3.2)),
        ("Wallpack_Corner_SW", (7.8, 22.3, 4.5)),
        ("Wallpack_Corner_SE", (46.2, 22.3, 4.5)),
        ("Wallpack_North_Door", (25.5, 56.4, 3.2)),
    ]
    for name, (wx, wy, wz) in wallpack_positions:
        # Die-cast bronze housing
        add_box(col, f"{name}_Housing_Lamp", (wx - 0.20, wy - 0.12, wz - 0.15), (wx + 0.20, wy + 0.12, wz + 0.15), mats["Wallpack_Bronze"], is_collider=False)
        # Prismatic glass refractor with warm amber glow
        add_box(col, f"{name}_Lens_Glow", (wx - 0.18, wy - 0.14, wz - 0.13), (wx + 0.18, wy - 0.10, wz + 0.13), mats["Amber_Glow"], is_collider=False)

    # Weathered Industrial Signage above Bay 1 & Bay 2
    add_box(col, "Sign_Bay1_Receiving_Sign", (16.0, 21.42, 4.0), (20.0, 21.44, 4.4), mats["Road_White"], is_collider=False)
    add_box(col, "Sign_Bay2_Shipping_Sign", (32.0, 21.42, 4.2), (36.0, 21.44, 4.6), mats["Road_White"], is_collider=False)
    add_box(col, "Sign_Clearance_Sign", (16.8, 21.42, 3.82), (19.2, 21.44, 3.98), mats["Hazard_Yellow"], is_collider=False)


def add_cargo_and_pallets(col, mats):
    """Add realistic warehouse cargo: empty pallet stacks, wooden machinery crates, chemical drum sump stations, and wooden cable spools."""
    # Stacks of 10 empty wooden GMA pallets (1.2m x 1.0m x 0.14m each)
    pallet_stack_locs = [(14.2, 53.5), (44.0, 24.5), (43.5, 36.0)]
    for s_idx, (sx, sy) in enumerate(pallet_stack_locs):
        for p_i in range(8):
            pz = p_i * 0.14
            add_box(col, f"PalletStack_{s_idx}_{p_i}", (sx - 0.6, sy - 0.5, pz), (sx + 0.6, sy + 0.5, pz + 0.13), mats["Wood_Pallet"])

    # Heavy Wooden Machinery Crates with diagonal framing and stencil markings
    crates = [
        ("Heavy_Machinery_Crate_1", (15.5, 36.0, 0.0), (1.8, 1.4, 1.5)),
        ("Heavy_Machinery_Crate_2", (15.5, 38.0, 0.0), (1.6, 1.2, 1.2)),
        ("Heavy_Machinery_Crate_3", (37.0, 36.0, 0.0), (1.5, 1.5, 1.4)),
    ]
    for c_name, (cx, cy, cz), (sx, sy, sz) in crates:
        add_box(col, c_name, (cx - sx/2, cy - sy/2, cz), (cx + sx/2, cy + sy/2, cz + sz), mats["Wood_Crate"], bevel=True, bevel_width=0.04)
        # Steel corner brackets
        for bx in [cx - sx/2, cx + sx/2]:
            for by in [cy - sy/2, cy + sy/2]:
                add_box(col, f"{c_name}_Bracket_{int(bx*10)}_{int(by*10)}_Detail", (bx - 0.04, by - 0.04, cz), (bx + 0.04, by + 0.04, cz + sz), mats["Steel_Dark"], is_collider=False)

    # Large Industrial Wooden Cable Spool / Wire Reel
    spool_x, spool_y = 20.0, 48.0
    add_cylinder(col, "Cable_Spool_Flange_L_Spool", (spool_x, spool_y - 0.55, 0.65), radius=0.65, height=0.06, material=mats["Cable_Spool_Wood"], segments=24)
    add_cylinder(col, "Cable_Spool_Flange_R_Spool", (spool_x, spool_y + 0.55, 0.65), radius=0.65, height=0.06, material=mats["Cable_Spool_Wood"], segments=24)
    add_cylinder(col, "Cable_Spool_Copper_Spool", (spool_x, spool_y, 0.65), radius=0.52, height=1.00, material=mats["Copper_Cable"], segments=24)

    # Polyethylene Column Wrap Crash Guards around interior columns
    col_coords = [(18.0, 28.0), (18.0, 38.0), (18.0, 48.0), (27.0, 28.0), (27.0, 38.0), (36.0, 28.0), (36.0, 38.0)]
    for col_x, col_y in col_coords:
        add_cylinder(col, f"Column_Protector_{int(col_x)}_{int(col_y)}_Detail", (col_x, col_y, 0.45), radius=0.55, height=0.90, material=mats["Hazard_Yellow"], segments=24, is_collider=False)


def add_civil_and_highway_infrastructure(col, mats):
    """Add highway W-beam guardrails, impact attenuators, overhead gantry sign bridge, and utility poles."""
    # Galvanized Corrugated W-Beam Guardrails along sidewalk (non-collider with crosswalk opening)
    for gx in range(6, 56, 4):
        if 16 <= gx <= 24:
            continue  # Open gap for crosswalk and pedestrian access
        add_box(col, f"Guardrail_Post_{gx}_Detail", (float(gx) - 0.05, 8.05, 0.0), (float(gx) + 0.05, 8.15, 0.85), mats["Guardrail_Galv"], is_collider=False)
        add_box(col, f"Guardrail_WBeam_{gx}_Detail", (float(gx) - 2.0, 7.98, 0.55), (float(gx) + 2.0, 8.08, 0.85), mats["Guardrail_Galv"], is_collider=False)

    # Yellow Highway Impact Attenuator Crash Cushions (sand barrel arrays at overpass pier approaches)
    for att_x in [7.0, 19.0, 31.0, 43.0, 55.0]:
        for att_i, att_y in enumerate([3.8, 3.0]):
            rad = 0.35 - att_i * 0.05
            add_cylinder(col, f"Crash_Attenuator_{int(att_x)}_{att_i}_Cushion", (att_x, att_y, 0.45), radius=rad, height=0.90, material=mats["Attenuator_Yellow"], segments=16, is_collider=False)

    # Segmented Precast Concrete Jersey Barriers (K-Rails) along overpass edge
    for jb_x in range(6, 58, 4):
        add_box(col, f"Jersey_Barrier_{jb_x}", (float(jb_x) - 1.95, 7.82, 7.5), (float(jb_x) + 1.95, 8.12, 8.45), mats["Concrete"], bevel=True, bevel_width=0.04)
        # Amber reflective delineator tab
        add_box(col, f"Barrier_Reflector_{jb_x}_Stripe", (float(jb_x), 7.80, 8.35), (float(jb_x) + 0.08, 7.82, 8.45), mats["Hazard_Yellow"], is_collider=False)

    # Concrete Utility Pole with wooden crossarm, porcelain insulators, and aerial drop cables
    up_x, up_y = 44.0, 9.5
    add_cylinder(col, "Utility_Pole_Shaft", (up_x, up_y, 4.5), radius=0.18, height=9.0, material=mats["Concrete_Dark"], segments=16)
    # Wooden crossarm
    add_box(col, "Utility_Crossarm_Detail", (up_x - 1.4, up_y - 0.08, 8.2), (up_x + 1.4, up_y + 0.08, 8.35), mats["Wood_Crate"], is_collider=False)
    # Porcelain pin insulators
    for ix in [up_x - 1.2, up_x, up_x + 1.2]:
        add_cylinder(col, f"Insulator_{int(ix*10)}_Detail", (ix, up_y, 8.45), radius=0.05, height=0.18, material=mats["Steel_Dark"], segments=12, is_collider=False)
    # Cylindrical Pole-Mounted Utility Transformer Pot
    add_cylinder(col, "Pole_Transformer_Pot_Detail", (up_x + 0.28, up_y, 7.2), radius=0.24, height=0.75, material=mats["Steel_Dark"], segments=16, is_collider=False)

    # Roadside Street Signs: Octagonal "STOP" sign, "SPEED LIMIT 25"
    add_cylinder(col, "Sign_Post_Stop_Detail", (24.0, 11.0, 1.2), radius=0.03, height=2.4, material=mats["Steel_Dark"], segments=12, is_collider=False)
    add_box(col, "Sign_Stop_Octagon_Sign", (23.75, 11.02, 2.0), (24.25, 11.05, 2.5), mats["Fire_Extinguisher_Red"], bevel=True, bevel_width=0.08, is_collider=False)


def add_pallet_rack(col, mats, x0, y0, x1, y1, num_tiers=3, total_height=5.5):
    """Build heavy-duty warehouse storage pallet racking with multiple shelf tiers, wire decks, and loaded cargo."""
    length = abs(x1 - x0)
    num_bays = max(1, int(length / 3.0))
    bay_w = length / num_bays
    depth = abs(y1 - y0)
    cy = (y0 + y1) / 2.0

    # Uprights (Blue)
    for i in range(num_bays + 1):
        rx = x0 + i * bay_w
        add_box(col, f"Rack_Post_F_{i}", (rx - 0.05, cy - depth/2, 0.0), (rx + 0.05, cy - depth/2 + 0.1, total_height), mats["Rack_Upright_Blue"], is_collider=True)
        add_box(col, f"Rack_Post_B_{i}", (rx - 0.05, cy + depth/2 - 0.1, 0.0), (rx + 0.05, cy + depth/2, total_height), mats["Rack_Upright_Blue"], is_collider=True)
        add_box(col, f"Rack_Foot_F_{i}_Detail", (rx - 0.1, cy - depth/2 - 0.05, 0.0), (rx + 0.1, cy - depth/2 + 0.15, 0.02), mats["Steel_Dark"], is_collider=False)
        add_box(col, f"Rack_Foot_B_{i}_Detail", (rx - 0.1, cy + depth/2 - 0.15, 0.0), (rx + 0.1, cy + depth/2 + 0.05, 0.02), mats["Steel_Dark"], is_collider=False)

        if i in [0, num_bays]:
            add_box(col, f"Rack_Guard_F_{i}_Detail", (rx - 0.12, cy - depth/2 - 0.06, 0.0), (rx + 0.12, cy - depth/2 + 0.16, 0.5), mats["Hazard_Yellow"], bevel=True, bevel_width=0.03, is_collider=False)
            add_box(col, f"Rack_Guard_B_{i}_Detail", (rx - 0.12, cy + depth/2 - 0.16, 0.0), (rx + 0.12, cy + depth/2 + 0.06, 0.5), mats["Hazard_Yellow"], bevel=True, bevel_width=0.03, is_collider=False)

        for tz in [1.2, 2.6, 4.0]:
            add_box(col, f"Rack_Lace_{i}_{int(tz)}_Detail", (rx - 0.03, cy - depth/2 + 0.08, tz), (rx + 0.03, cy + depth/2 - 0.08, tz + 0.06), mats["Steel_Dark"], is_collider=False)

    tier_heights = [0.8, 2.4, 4.0]
    drum_colors = [mats["Drum_Blue"], mats["Drum_Red"], mats["Drum_Black"]]
    for b in range(num_bays):
        bx0 = x0 + b * bay_w + 0.06
        bx1 = x0 + (b + 1) * bay_w - 0.06
        for tid, tz in enumerate(tier_heights):
            add_box(col, f"Rack_Beam_F_{b}_{tid}", (bx0, cy - depth/2, tz), (bx1, cy - depth/2 + 0.08, tz + 0.12), mats["Rack_Beam_Orange"], is_collider=True)
            add_box(col, f"Rack_Beam_B_{b}_{tid}", (bx0, cy + depth/2 - 0.08, tz), (bx1, cy + depth/2, tz + 0.12), mats["Rack_Beam_Orange"], is_collider=True)
            add_box(col, f"Rack_Deck_{b}_{tid}", (bx0, cy - depth/2 + 0.06, tz + 0.1), (bx1, cy + depth/2 - 0.06, tz + 0.12), mats["Steel_Dark"], is_collider=True)

            for pid, px in enumerate([bx0 + 0.65, bx1 - 0.65]):
                pz = tz + 0.12
                add_box(col, f"Pallet_{b}_{tid}_{pid}", (px - 0.6, cy - 0.5, pz), (px + 0.6, cy + 0.5, pz + 0.14), mats["Wood_Crate"], is_collider=True)
                pattern = (b + tid + pid) % 3
                if pattern == 0:
                    add_box(col, f"Cargo_Box_{b}_{tid}_{pid}", (px - 0.55, cy - 0.45, pz + 0.14), (px + 0.55, cy + 0.45, pz + 1.1), mats["Cardboard_Box"], is_collider=True, bevel=True, bevel_width=0.03)
                    add_box(col, f"Shrink_{b}_{tid}_{pid}_Detail", (px - 0.56, cy - 0.46, pz + 0.15), (px + 0.56, cy + 0.46, pz + 1.12), mats["Plastic_Shrinkwrap"], is_collider=False)
                elif pattern == 1:
                    add_box(col, f"Cargo_Crate_{b}_{tid}_{pid}", (px - 0.5, cy - 0.4, pz + 0.14), (px + 0.5, cy + 0.4, pz + 0.95), mats["Wood_Crate"], is_collider=True, bevel=True, bevel_width=0.04)
                else:
                    add_box(col, f"Spill_Sump_{b}_{tid}_{pid}_Detail", (px - 0.58, cy - 0.48, pz + 0.14), (px + 0.58, cy + 0.48, pz + 0.28), mats["Spill_Pallet_Yellow"], bevel=True, bevel_width=0.02, is_collider=False)
                    for di, (dx, dy) in enumerate([(-0.25, -0.22), (0.25, -0.22), (-0.25, 0.22), (0.25, 0.22)]):
                        dmat = drum_colors[(b + di) % len(drum_colors)]
                        add_cylinder(col, f"Drum_{b}_{tid}_{pid}_{di}", (px + dx, cy + dy, pz + 0.28 + 0.45), radius=0.22, height=0.9, material=dmat, segments=24, is_collider=True)


def add_forklift(col, mats, x, y, z=0.0):
    """Create a detailed industrial counterbalanced forklift carrying a loaded pallet."""
    add_box(col, "Forklift_Chassis", (x - 0.7, y - 1.2, z + 0.2), (x + 0.7, y + 0.8, z + 0.9), mats["Forklift_Yellow"], bevel=True, bevel_width=0.05)
    add_box(col, "Forklift_Counterweight", (x - 0.68, y - 1.35, z + 0.25), (x + 0.68, y - 1.15, z + 0.85), mats["Steel_Dark"], bevel=True, bevel_width=0.06)

    # LP Propane Tank on Rear Counterweight
    add_cylinder(col, "Forklift_Propane_Tank_Propane", (x, y - 1.25, z + 1.05), radius=0.15, height=0.55, material=mats["Propane_White"], segments=20, is_collider=False)
    add_cylinder(col, "Forklift_Propane_Valve_Detail", (x, y - 1.25, z + 1.35), radius=0.04, height=0.08, material=mats["Brass_Fitting"], segments=12, is_collider=False)

    # Overhead safety cage
    for cx in [-0.65, 0.65]:
        for cy in [-1.0, 0.5]:
            add_box(col, f"Forklift_Cage_Pillar_{cx}_{cy}_Detail", (x + cx - 0.03, y + cy - 0.03, z + 0.9), (x + cx + 0.03, y + cy + 0.03, z + 2.15), mats["Steel_Dark"], is_collider=False)
    add_box(col, "Forklift_Cage_Roof_Detail", (x - 0.68, y - 1.05, z + 2.15), (x + 0.68, y + 0.55, z + 2.22), mats["Steel_Dark"], bevel=True, bevel_width=0.02, is_collider=False)
    add_cylinder(col, "Forklift_Strobe_Beacon_Lamp", (x, y - 0.25, z + 2.28), radius=0.07, height=0.10, material=mats["Traffic_Cone_Orange"], segments=16, is_collider=False)

    add_box(col, "Forklift_Seat", (x - 0.3, y - 0.6, z + 0.9), (x + 0.3, y - 0.2, z + 1.25), mats["Hazard_Black"], bevel=True, bevel_width=0.03)
    add_cylinder(col, "Forklift_Steering_Wheel_Detail", (x, y + 0.1, z + 1.45), radius=0.2, height=0.04, material=mats["Steel_Dark"], segments=16, is_collider=False)

    # Vertical mast channels and hydraulic cylinder
    add_box(col, "Forklift_Mast_L", (x - 0.45, y + 0.8, z), (x - 0.35, y + 0.9, z + 2.6), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    add_box(col, "Forklift_Mast_R", (x + 0.35, y + 0.8, z), (x + 0.45, y + 0.9, z + 2.6), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    add_cylinder(col, "Forklift_Hydraulic_Cylinder_Detail", (x, y + 0.85, z + 1.3), radius=0.06, height=2.4, material=mats["SWAT_Chrome"], segments=16, is_collider=False)
    add_box(col, "Forklift_Fork_Backrest_Detail", (x - 0.5, y + 0.91, z + 0.1), (x + 0.5, y + 0.96, z + 0.8), mats["Steel_Dark"], is_collider=False)

    # Forks
    add_box(col, "Forklift_Fork_L", (x - 0.35, y + 0.95, z + 0.02), (x - 0.25, y + 2.1, z + 0.08), mats["Steel_Dark"], bevel=True, bevel_width=0.02)
    add_box(col, "Forklift_Fork_R", (x + 0.25, y + 0.95, z + 0.02), (x + 0.35, y + 2.1, z + 0.08), mats["Steel_Dark"], bevel=True, bevel_width=0.02)

    # Tires
    for wx, wy in [(-0.75, 0.45), (0.75, 0.45)]:
        add_cylinder(col, f"Forklift_Tire_F_{wx}", (x + wx, y + wy, z + 0.35), radius=0.35, height=0.25, material=mats["SWAT_Tire"], segments=24)
    for wx, wy in [(-0.72, -0.95), (0.72, -0.95)]:
        add_cylinder(col, f"Forklift_Tire_R_{wx}", (x + wx, y + wy, z + 0.28), radius=0.28, height=0.22, material=mats["SWAT_Tire"], segments=24)

    # Cargo on forks
    add_box(col, "Forklift_Pallet", (x - 0.6, y + 1.0, z + 0.08), (x + 0.6, y + 2.0, z + 0.22), mats["Wood_Crate"])
    add_box(col, "Forklift_Crate_1", (x - 0.55, y + 1.05, z + 0.22), (x + 0.05, y + 1.95, z + 0.95), mats["Wood_Crate"], bevel=True, bevel_width=0.03)
    add_box(col, "Forklift_Crate_2", (x + 0.05, y + 1.05, z + 0.22), (x + 0.55, y + 1.95, z + 0.85), mats["Cardboard_Box"], bevel=True, bevel_width=0.03)


def add_hostage_office_props(col, mats):
    """Add detailed office furniture, computer stations, tactical whiteboard, CCTV console, water cooler, and server rack."""
    # Acoustic Drop-Ceiling Grid (z = 8.75m)
    add_box(col, "Office_Acoustic_Ceiling_Detail", (28.1, 40.0, 8.75), (45.4, 55.4, 8.78), mats["Office_Ceiling"], is_collider=False)

    # Horizontal Venetian Mini-Blinds on Panoramic Window
    for bi in range(12):
        bz = 5.2 + bi * 0.16
        add_box(col, f"Office_Blind_{bi}_Blind", (29.2, 39.90, bz), (44.3, 39.92, bz + 0.03), mats["Mini_Blinds"], is_collider=False)

    # Workstations with wood laminate desks
    for dx, dy in [(31.0, 52.0), (37.0, 52.0)]:
        add_box(col, f"Desk_{int(dx)}", (dx - 1.1, dy - 0.5, 4.2), (dx + 1.1, dy + 0.5, 4.95), mats["Desk_Wood"], bevel=True, bevel_width=0.02)
        add_box(col, f"PC_Tower_{int(dx)}_Detail", (dx + 0.7, dy - 0.3, 4.2), (dx + 0.95, dy + 0.3, 4.75), mats["Computer_Chassis"], bevel=True, bevel_width=0.02, is_collider=False)
        add_box(col, f"Monitor_L_{int(dx)}_Detail", (dx - 0.45, dy + 0.2, 4.95), (dx - 0.05, dy + 0.28, 5.45), mats["Steel_Dark"], is_collider=False)
        add_box(col, f"Monitor_R_{int(dx)}_Detail", (dx + 0.05, dy + 0.2, 4.95), (dx + 0.45, dy + 0.28, 5.45), mats["Steel_Dark"], is_collider=False)
        add_box(col, f"Keyboard_{int(dx)}_Detail", (dx - 0.25, dy - 0.2, 4.95), (dx + 0.25, dy, 4.98), mats["Steel_Dark"], is_collider=False)

        # Swivel office chair
        add_cylinder(col, f"Chair_Base_{int(dx)}_Detail", (dx, dy - 0.9, 4.25), radius=0.3, height=0.1, material=mats["Steel_Dark"], segments=12, is_collider=False)
        add_cylinder(col, f"Chair_Post_{int(dx)}_Detail", (dx, dy - 0.9, 4.45), radius=0.04, height=0.3, material=mats["Steel_Dark"], segments=8, is_collider=False)
        add_box(col, f"Chair_Seat_{int(dx)}_Detail", (dx - 0.25, dy - 1.15, 4.58), (dx + 0.25, dy - 0.75, 4.68), mats["Hazard_Black"], bevel=True, bevel_width=0.03, is_collider=False)
        add_box(col, f"Chair_Back_{int(dx)}_Detail", (dx - 0.25, dy - 1.18, 4.68), (dx + 0.25, dy - 1.12, 5.15), mats["Hazard_Black"], bevel=True, bevel_width=0.03, is_collider=False)

    # 4-Drawer steel filing cabinets
    for fx in [43.0, 44.2]:
        add_box(col, f"Filing_Cabinet_{int(fx*10)}", (fx - 0.45, 53.5, 4.2), (fx + 0.45, 54.5, 5.6), mats["Steel_Dark"], bevel=True, bevel_width=0.02)

    # CCTV 4-Monitor Security Console on south counter
    add_box(col, "CCTV_Console_Desk", (33.0, 45.0, 4.2), (37.0, 45.8, 4.95), mats["Desk_Wood"])
    for mi, (mx, mz) in enumerate([(-0.6, 5.0), (0.6, 5.0), (-0.6, 5.55), (0.6, 5.55)]):
        add_box(col, f"CCTV_Screen_{mi}_Detail", (35.0 + mx - 0.45, 45.5, mz), (35.0 + mx + 0.45, 45.6, mz + 0.45), mats["Screen_CCTV"], is_collider=False)

    # Commercial Server Rack Cabinet
    add_box(col, "Office_Server_Rack_Body", (41.5, 46.5, 4.2), (42.5, 47.5, 6.4), mats["Steel_Dark"], bevel=True, bevel_width=0.03)
    add_box(col, "Office_Server_Door_Glass", (41.52, 46.48, 4.3), (42.48, 46.52, 6.3), mats["SWAT_Glass"], is_collider=False)
    for s_led in range(6):
        add_box(col, f"Server_LED_{s_led}_Glow", (41.6 + s_led * 0.14, 46.51, 5.8), (41.68 + s_led * 0.14, 46.53, 5.85), mats["Exit_Sign_Green"], is_collider=False)

    # Tactical Dry-Erase Whiteboard on north wall
    add_box(col, "Office_Whiteboard_Board_Detail", (32.0, 55.35, 5.6), (36.0, 55.38, 7.2), mats["Whiteboard"], is_collider=False)
    add_box(col, "Office_Whiteboard_Frame_Detail", (31.95, 55.33, 5.55), (36.05, 55.37, 7.25), mats["Steel_Dark"], is_collider=False)
    add_box(col, "Office_Whiteboard_Tray_Detail", (32.2, 55.25, 5.52), (35.8, 55.36, 5.56), mats["SWAT_Chrome"], is_collider=False)

    # Water cooler station
    add_box(col, "Water_Cooler_Base", (43.5, 47.0, 4.2), (44.3, 47.8, 5.2), mats["Computer_Chassis"], bevel=True, bevel_width=0.03)
    add_cylinder(col, "Water_Bottle_Detail", (43.9, 47.4, 5.55), radius=0.25, height=0.6, material=mats["Water_Bottle_Blue"], segments=16, is_collider=False)

    # Hostage chairs
    for hx in [33.5, 36.5]:
        add_box(col, f"Hostage_Chair_{int(hx*10)}", (hx - 0.25, 48.0, 4.2), (hx + 0.25, 48.5, 4.8), mats["Wood_Crate"], bevel=True, bevel_width=0.02)
        add_box(col, f"Hostage_Chair_Back_{int(hx*10)}", (hx - 0.25, 48.4, 4.8), (hx + 0.25, 48.5, 5.3), mats["Wood_Crate"], bevel=True, bevel_width=0.02)


def add_street_and_civil_details(col, mats):
    """Add stormwater catch basins, manholes, chain-link security fencing, traffic cones, and highway signs."""
    # Catch basins with slotted cast-iron grates
    for sx, sy in [(12.0, 7.85), (28.0, 7.85), (42.0, 7.85), (22.0, 20.65)]:
        add_box(col, f"Storm_Drain_{int(sx)}_{int(sy)}_Drain", (sx - 0.45, sy - 0.25, 0.0), (sx + 0.45, sy + 0.25, 0.02), mats["Steel_Dark"], is_collider=False)
        for g in range(5):
            add_box(col, f"Drain_Grate_{int(sx)}_{g}_Drain", (sx - 0.35 + g * 0.15, sy - 0.22, 0.015), (sx - 0.30 + g * 0.15, sy + 0.22, 0.025), mats["Hazard_Black"], is_collider=False)

    # Sewer Manhole Covers with relief rings
    for mx, my in [(19.0, 12.0), (33.0, 16.0), (45.0, 12.0)]:
        add_cylinder(col, f"Manhole_{int(mx)}_{int(my)}_Manhole", (mx, my, 0.01), radius=0.45, height=0.02, material=mats["Steel_Dark"], segments=24, is_collider=False)
        add_cylinder(col, f"Manhole_Ring_{int(mx)}_{int(my)}_Detail", (mx, my, 0.015), radius=0.38, height=0.025, material=mats["Concrete_Dark"], segments=24, is_collider=False)

    # Traffic Safety Cones near SWAT van
    for tx, ty in [(17.5, 12.5), (22.5, 12.5), (23.5, 15.5)]:
        add_box(col, f"Cone_Base_{int(tx*10)}_{int(ty*10)}_Detail", (tx - 0.2, ty - 0.2, 0.0), (tx + 0.2, ty + 0.2, 0.03), mats["Traffic_Cone_Orange"], is_collider=False)
        add_cylinder(col, f"Cone_Body_{int(tx*10)}_{int(ty*10)}_Detail", (tx, ty, 0.38), radius=0.12, height=0.7, material=mats["Traffic_Cone_Orange"], segments=16, is_collider=False)
        add_cylinder(col, f"Cone_Reflector_{int(tx*10)}_{int(ty*10)}_Stripe", (tx, ty, 0.45), radius=0.125, height=0.15, material=mats["Road_White"], segments=16, is_collider=False)

    # Precast Box Girders under highway overpass deck (z: 6.3..6.9)
    for gx in [10.0, 20.0, 30.0, 40.0]:
        add_box(col, f"Bridge_Girder_{int(gx)}", (gx - 1.2, 4.0, 6.3), (gx + 1.2, 8.0, 6.9), mats["Concrete_Dark"], bevel=True, bevel_width=0.08)

    # Highway Directional Sign Gantry with truss cross-bracing
    add_cylinder(col, "Gantry_Post_S_Detail", (12.0, 4.3, 9.8), radius=0.15, height=4.6, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_cylinder(col, "Gantry_Post_N_Detail", (12.0, 7.7, 9.8), radius=0.15, height=4.6, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_box(col, "Gantry_Beam_Detail", (11.85, 4.15, 11.9), (12.15, 7.85, 12.2), mats["Steel_Dark"], is_collider=False)
    # Primary Highway Sign ("EXIT 4B - INDUSTRIAL TERMINAL")
    add_box(col, "Gantry_Sign_1_Sign", (11.92, 4.5, 10.4), (12.08, 6.2, 11.8), mats["Highway_Green"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(col, "Gantry_Sign_1_Border_Stripe", (11.9, 4.45, 10.35), (12.1, 6.25, 11.85), mats["Road_White"], is_collider=False)
    # Secondary Highway Sign ("KEEP RIGHT")
    add_box(col, "Gantry_Sign_2_Sign", (11.92, 6.4, 10.7), (12.08, 7.6, 11.8), mats["Highway_Blue"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(col, "Gantry_Sign_2_Border_Stripe", (11.9, 6.35, 10.65), (12.1, 7.65, 11.85), mats["Road_White"], is_collider=False)

    # Cobra-Head Highway Luminaire Pole on Overpass
    add_cylinder(col, "Highway_Light_Pole_Detail", (30.0, 4.1, 10.5), radius=0.10, height=4.0, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_oriented_cylinder(col, "Highway_Light_Davit_Arm", (30.0, 4.1, 12.5), (30.0, 5.5, 13.0), radius=0.06, material=mats["Steel_Dark"])
    add_box(col, "Highway_Light_Head_Lamp", (29.85, 5.4, 12.85), (30.15, 5.9, 13.05), mats["Steel_Dark"], bevel=True, bevel_width=0.02, is_collider=False)
    add_box(col, "Highway_Light_Lens_Glow", (29.88, 5.45, 12.82), (30.12, 5.85, 12.87), mats["Lamp_Glow"], is_collider=False)

    # Chain-Link Perimeter Security Fence enclosing rail yard
    for fx in range(48, 60, 3):
        add_cylinder(col, f"Fence_Post_{fx}", (float(fx), 21.0, 1.25), radius=0.04, height=2.5, material=mats["Chainlink_Steel"], segments=12, is_collider=True)
    add_box(col, "Fence_Top_Rail_Detail", (48.0, 20.96, 2.45), (59.0, 21.04, 2.5), mats["Chainlink_Steel"], is_collider=False)
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

    print("[1/12] Building Ground Terrain, Streets, and Sidewalks...")
    # Map footprint: (4.0..60.0, 4.0..60.0) -> Exactly 56m x 56m
    add_box(c_arch, "Street_Asphalt", (4.0, 8.0, -0.2), (60.0, 22.0, 0.0), mats["Asphalt"])

    # Double Yellow Centerline on Street (y=15.0)
    add_box(c_arch, "Road_Stripe_Yellow_1_Stripe", (4.0, 14.85, 0.005), (60.0, 14.95, 0.01), mats["Road_Yellow"], is_collider=False)
    add_box(c_arch, "Road_Stripe_Yellow_2_Stripe", (4.0, 15.05, 0.005), (60.0, 15.15, 0.01), mats["Road_Yellow"], is_collider=False)

    # White Crosswalk Markings in front of Warehouse Garage Bay 1 (x: 18..22, y: 11..21)
    for cx in [18.2, 19.0, 19.8, 20.6, 21.4]:
        add_box(c_arch, f"Crosswalk_Stripe_{cx}_Stripe", (cx - 0.25, 11.5, 0.005), (cx + 0.25, 20.5, 0.01), mats["Road_White"], is_collider=False)

    # South Sidewalk (CT Spawn zone under highway bridge: y: 4.0..8.0, z: 0.18m)
    add_box(c_arch, "Sidewalk_CT_Spawn", (4.0, 4.0, 0.0), (60.0, 8.0, 0.18), mats["Concrete"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Sidewalk_CT_Curb", (4.0, 7.85, 0.0), (60.0, 8.0, 0.2), mats["Curb"])

    # North Sidewalk with flush driveway cuts at Bay 1 and Bay 2 (ensures completely level entry)
    add_box(c_arch, "Sidewalk_Warehouse_West", (4.0, 20.8, 0.0), (13.5, 22.0, 0.18), mats["Concrete"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Sidewalk_Warehouse_Curb_West", (4.0, 20.8, 0.0), (13.5, 20.95, 0.2), mats["Curb"])
    # Bay 1 Apron: completely flush with asphalt (z=0.0)
    add_box(c_arch, "Bay1_Apron_Slab", (13.5, 20.8, -0.05), (22.5, 22.0, 0.0), mats["Concrete"])
    add_box(c_arch, "Sidewalk_Warehouse_Mid", (22.5, 20.8, 0.0), (29.5, 22.0, 0.18), mats["Concrete"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Sidewalk_Warehouse_Curb_Mid", (22.5, 20.8, 0.0), (29.5, 20.95, 0.2), mats["Curb"])
    # Bay 2 Apron: completely flush with asphalt (z=0.0)
    add_box(c_arch, "Bay2_Apron_Slab", (29.5, 20.8, -0.05), (38.5, 22.0, 0.0), mats["Concrete"])
    add_box(c_arch, "Sidewalk_Warehouse_East", (38.5, 20.8, 0.0), (48.0, 22.0, 0.18), mats["Concrete"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Sidewalk_Warehouse_Curb_East", (38.5, 20.8, 0.0), (48.0, 20.95, 0.2), mats["Curb"])

    # Asphalt Cracks and Thermoplastics
    add_asphalt_cracks_and_thermoplastics(c_arch, mats)

    print("[2/12] Building Highway Overpass Structure...")
    # Elevated Highway Bridge Deck (y: 4.0..8.0, walking surface z=7.5m, thickness 0.6m)
    add_box(c_overpass, "Overpass_Deck", (4.0, 4.0, 6.9), (60.0, 8.0, 7.5), mats["Concrete_Dark"])
    add_box(c_overpass, "Overpass_Asphalt", (4.0, 4.2, 7.48), (60.0, 7.8, 7.5), mats["Asphalt"], is_collider=False)
    add_box(c_overpass, "Retaining_Wall_South", (4.0, 3.6, 0.0), (60.0, 4.0, 11.0), mats["Concrete"])
    add_box(c_overpass, "Overpass_Barrier_N", (4.0, 7.8, 7.5), (60.0, 8.1, 8.5), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_overpass, "Overpass_Barrier_S", (4.0, 3.9, 7.5), (60.0, 4.2, 8.5), mats["Concrete"], bevel=True, bevel_width=0.04)

    # Cylindrical Heavy Concrete Overpass Support Pillars
    pillar_x_coords = [8.0, 20.0, 32.0, 44.0, 56.0]
    for i, px in enumerate(pillar_x_coords):
        add_cylinder(c_overpass, f"Overpass_Pillar_{i}", (px, 6.0, 3.15), radius=0.65, height=6.3, material=mats["Concrete"], segments=32)
        add_box(c_overpass, f"Pillar_Pier_Cap_{i}", (px - 0.8, 4.8, 6.3), (px + 0.8, 7.2, 6.9), mats["Concrete_Dark"], bevel=True, bevel_width=0.05)
        add_box(c_overpass, f"Bearing_Pad_{i}_L_Detail", (px - 0.6, 5.2, 6.85), (px - 0.3, 5.8, 6.9), mats["Steel_Dark"], is_collider=False)
        add_box(c_overpass, f"Bearing_Pad_{i}_R_Detail", (px + 0.3, 5.2, 6.85), (px + 0.6, 5.8, 6.9), mats["Steel_Dark"], is_collider=False)

    print("[3/12] Building Rail Yard and Train Tracks...")
    add_box(c_rail, "Rail_Yard_Ballast", (48.0, 22.0, 0.0), (60.0, 56.0, 0.15), mats["Concrete_Dark"])
    for tx in [51.5, 54.5]:
        for ty in range(23, 56, 1):
            add_box(c_rail, f"Tie_{tx}_{ty}", (tx - 0.7, float(ty) - 0.12, 0.15), (tx + 0.7, float(ty) + 0.12, 0.23), mats["Train_Tie"])
            add_box(c_rail, f"TiePlate_L_{tx}_{ty}_Detail", (tx - 0.55, float(ty) - 0.10, 0.22), (tx - 0.41, float(ty) + 0.10, 0.24), mats["Steel_Dark"], is_collider=False)
            add_box(c_rail, f"TiePlate_R_{tx}_{ty}_Detail", (tx + 0.41, float(ty) - 0.10, 0.22), (tx + 0.55, float(ty) + 0.10, 0.24), mats["Steel_Dark"], is_collider=False)

        add_box(c_rail, f"Rail_L_{tx}", (tx - 0.52, 22.0, 0.23), (tx - 0.44, 56.0, 0.35), mats["Train_Rail"])
        add_box(c_rail, f"Rail_R_{tx}", (tx + 0.44, 22.0, 0.23), (tx + 0.52, 56.0, 0.35), mats["Train_Rail"])

    # Freight Boxcar
    add_box(c_rail, "Boxcar_Body", (53.6, 32.2, 0.9), (56.4, 45.8, 3.8), mats["Train_Rust"], bevel=True, bevel_width=0.05)
    add_box(c_rail, "Boxcar_Roof", (53.4, 31.9, 3.8), (56.6, 46.1, 4.0), mats["Steel_Dark"])
    add_box(c_rail, "Boxcar_Underframe", (53.8, 32.5, 0.4), (56.2, 45.5, 0.9), mats["Steel_Dark"])
    add_box(c_rail, "Boxcar_Roof_Walk_Detail", (54.6, 32.0, 4.0), (55.4, 46.0, 4.05), mats["Steel_Catwalk"], is_collider=False)
    add_cylinder(c_rail, "Boxcar_Brake_Wheel_Detail", (55.0, 32.1, 3.2), radius=0.22, height=0.03, material=mats["Hazard_Black"], segments=16, is_collider=False)
    add_box(c_rail, "Boxcar_Coupler_S_Detail", (54.7, 31.6, 0.5), (55.3, 32.2, 0.8), mats["Steel_Dark"], is_collider=False)
    add_box(c_rail, "Boxcar_Coupler_N_Detail", (54.7, 45.8, 0.5), (55.3, 46.4, 0.8), mats["Steel_Dark"], is_collider=False)

    for bz, by_coords in [(0.45, [33.5, 35.0, 43.0, 44.5])]:
        for wy in by_coords:
            for wx in [53.5, 56.5]:
                add_cylinder(c_rail, f"Boxcar_Wheel_{wx}_{wy}", (wx, wy, bz), radius=0.35, height=0.12, material=mats["Steel_Dark"], segments=32)

    for lz in [1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6]:
        add_box(c_rail, f"Boxcar_Rung_{lz}", (53.38, 32.4, lz), (53.48, 32.9, lz + 0.04), mats["Safety_Rail"], is_collider=False)

    add_box(c_rail, "Container_Blue_1", (48.2, 24.0, 0.15), (50.6, 30.0, 2.75), mats["Container_Blue"], bevel=True, bevel_width=0.04)
    add_box(c_rail, "Container_Red_1", (48.2, 31.0, 0.15), (50.6, 37.0, 2.75), mats["Container_Red"], bevel=True, bevel_width=0.04)
    add_box(c_rail, "Container_Green_1", (48.2, 31.0, 2.75), (50.6, 37.0, 5.35), mats["Container_Green"], bevel=True, bevel_width=0.04)
    add_commercial_dumpster(c_rail, mats, "Rail_Dumpster", 56.0, 26.0, z=0.15, color_mat="Container_Blue")

    print("[4/12] Building SWAT Tactical Assault Van...")
    vx, vy = 20.0, 15.5
    add_box(c_vehicles, "SWAT_Body_Main", (vx - 1.2, vy - 2.5, 0.5), (vx + 1.2, vy + 1.2, 2.6), mats["SWAT_Navy"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "SWAT_Hood", (vx - 1.15, vy + 1.2, 0.5), (vx + 1.15, vy + 2.4, 1.6), mats["SWAT_Navy"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "SWAT_Front_Bumper", (vx - 1.2, vy + 2.38, 0.3), (vx + 1.2, vy + 2.55, 0.8), mats["Steel_Dark"], bevel=True, bevel_width=0.04)

    add_cylinder(c_vehicles, "SWAT_Bullbar_Top", (vx, vy + 2.6, 1.2), radius=0.04, height=1.8, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_box(c_vehicles, "SWAT_Bullbar_Post_L", (vx - 0.7, vy + 2.5, 0.5), (vx - 0.62, vy + 2.62, 1.25), mats["Steel_Dark"], is_collider=False)
    add_box(c_vehicles, "SWAT_Bullbar_Post_R", (vx + 0.62, vy + 2.5, 0.5), (vx + 0.7, vy + 2.62, 1.25), mats["Steel_Dark"], is_collider=False)

    add_box(c_vehicles, "SWAT_Grille_Detail", (vx - 0.8, vy + 2.41, 0.9), (vx + 0.8, vy + 2.43, 1.4), mats["Steel_Dark"], is_collider=False)
    add_box(c_vehicles, "SWAT_Headlight_L_Headlight", (vx - 1.05, vy + 2.41, 1.0), (vx - 0.85, vy + 2.43, 1.35), mats["SWAT_Headlight"], is_collider=False)
    add_box(c_vehicles, "SWAT_Headlight_R_Headlight", (vx + 0.85, vy + 2.41, 1.0), (vx + 1.05, vy + 2.43, 1.35), mats["SWAT_Headlight"], is_collider=False)

    add_box(c_vehicles, "SWAT_Windshield", (vx - 1.05, vy + 1.1, 1.65), (vx + 1.05, vy + 1.25, 2.45), mats["SWAT_Glass"], is_collider=False)
    add_box(c_vehicles, "SWAT_Side_Glass_L", (vx - 1.22, vy - 0.5, 1.7), (vx - 1.18, vy + 1.0, 2.4), mats["SWAT_Glass"], is_collider=False)
    add_box(c_vehicles, "SWAT_Side_Glass_R", (vx + 1.18, vy - 0.5, 1.7), (vx + 1.22, vy + 1.0, 2.4), mats["SWAT_Glass"], is_collider=False)

    for mx in [vx - 1.35, vx + 1.35]:
        add_box(c_vehicles, f"SWAT_Mirror_{mx}_Detail", (mx - 0.08, vy + 1.0, 1.8), (mx + 0.08, vy + 1.25, 2.2), mats["Steel_Dark"], bevel=True, bevel_width=0.02, is_collider=False)

    wheel_coords = [(-1.25, -1.4), (1.25, -1.4), (-1.25, 1.5), (1.25, 1.5)]
    for i, (wx, wy) in enumerate(wheel_coords):
        add_cylinder(c_vehicles, f"SWAT_Tire_{i}", (vx + wx, vy + wy, 0.42), radius=0.42, height=0.3, material=mats["SWAT_Tire"], segments=32)
        add_cylinder(c_vehicles, f"SWAT_Rim_{i}", (vx + wx * 1.05, vy + wy, 0.42), radius=0.24, height=0.31, material=mats["SWAT_Chrome"], segments=32, is_collider=False)

    add_box(c_vehicles, "SWAT_Lightbar_Mount", (vx - 0.8, vy + 0.2, 2.6), (vx + 0.8, vy + 0.4, 2.65), mats["Steel_Dark"], is_collider=False)
    add_box(c_vehicles, "SWAT_Siren_Red", (vx - 0.75, vy + 0.22, 2.65), (vx - 0.15, vy + 0.38, 2.78), mats["SWAT_Siren_Red"], is_collider=False)
    add_box(c_vehicles, "SWAT_Speaker_Horn_Detail", (vx - 0.12, vy + 0.20, 2.65), (vx + 0.12, vy + 0.42, 2.78), mats["Steel_Dark"], is_collider=False)
    add_box(c_vehicles, "SWAT_Siren_Blue", (vx + 0.15, vy + 0.22, 2.65), (vx + 0.75, vy + 0.38, 2.78), mats["SWAT_Siren_Blue"], is_collider=False)

    print("[5/12] Building Semi-Truck & Trailer at Bay 2...")
    tx, ty = 34.0, 19.0
    add_box(c_vehicles, "Truck_Trailer_Body", (tx - 1.3, ty - 1.0, 0.6), (tx + 1.3, ty + 5.0, 3.8), mats["Truck_Trailer"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "Truck_Cab_Body", (tx - 1.25, ty - 4.5, 0.5), (tx + 1.25, ty - 1.0, 3.2), mats["Truck_Cab"], bevel=True, bevel_width=0.06)
    add_box(c_vehicles, "Truck_Windshield", (tx - 1.1, ty - 4.52, 1.8), (tx + 1.1, ty - 4.48, 2.8), mats["SWAT_Glass"], is_collider=False)

    add_cylinder(c_vehicles, "Truck_Exhaust_Pipe_Detail", (tx + 1.2, ty - 1.2, 2.8), radius=0.08, height=2.2, material=mats["SWAT_Chrome"], segments=16, is_collider=False)
    add_cylinder(c_vehicles, "Truck_Heat_Shield_Detail", (tx + 1.2, ty - 1.2, 2.4), radius=0.12, height=1.4, material=mats["Steel_Dark"], segments=16, is_collider=False)
    add_box(c_vehicles, "Truck_Roof_Deflector_Detail", (tx - 1.15, ty - 3.5, 3.2), (tx + 1.15, ty - 1.2, 3.75), mats["Truck_Cab"], bevel=True, bevel_width=0.04, is_collider=False)

    add_box(c_vehicles, "Trailer_DOT_Bumper_Detail", (tx - 1.2, ty + 4.95, 0.35), (tx + 1.2, ty + 5.05, 0.55), mats["Steel_Dark"], is_collider=False)
    for ref_i in range(8):
        ref_x = tx - 1.1 + ref_i * 0.28
        ref_mat = mats["Fire_Extinguisher_Red"] if ref_i % 2 == 0 else mats["Road_White"]
        add_box(c_vehicles, f"Trailer_Reflector_{ref_i}_Stripe", (ref_x, ty + 5.06, 0.4), (ref_x + 0.24, ty + 5.08, 0.5), ref_mat, is_collider=False)

    truck_wheels = [(-1.3, -3.8), (1.3, -3.8), (-1.3, -2.0), (1.3, -2.0), (-1.3, 3.0), (1.3, 3.0), (-1.3, 4.2), (1.3, 4.2)]
    for i, (wx, wy) in enumerate(truck_wheels):
        add_cylinder(c_vehicles, f"Truck_Tire_{i}", (tx + wx, ty + wy, 0.5), radius=0.5, height=0.32, material=mats["SWAT_Tire"], segments=32)

    print("[6/12] Building Main Warehouse Shell & Architecture...")
    # Warehouse footprint: (8.0..46.0, 22.0..56.0, z: 0.0..9.0m)
    add_box(c_arch, "Warehouse_Ground_Slab", (8.0, 22.0, -0.2), (46.0, 56.0, 0.0), mats["Warehouse_Floor"])

    # Brick Masonry Foundation Skirt under solid walls only (completely open at Bay 1 and Bay 2)
    add_box(c_arch, "Warehouse_Brick_Skirt_S1", (7.5, 21.5, 0.0), (14.0, 22.0, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_S2", (22.0, 21.5, 0.0), (30.0, 22.0, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_S3", (42.0, 21.5, 0.0), (46.5, 22.0, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_W", (7.5, 22.0, 0.0), (8.0, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_N1", (7.5, 56.0, 0.0), (24.0, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_N2", (27.0, 56.0, 0.0), (46.5, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)
    add_box(c_arch, "Warehouse_Brick_Skirt_E", (46.0, 22.0, 0.0), (46.5, 56.5, 0.8), mats["Brick_Base"], bevel=True, bevel_width=0.03)

    # South Facade Walls
    add_box(c_arch, "Wall_South_1", (8.0, 21.6, 0.8), (14.0, 22.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_South_Bay1_Header", (14.0, 21.6, 3.8), (22.0, 22.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_South_2", (22.0, 21.6, 0.8), (30.0, 22.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_South_Bay2_Header", (30.0, 21.6, 4.0), (38.0, 22.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_South_Door_Header", (38.0, 21.6, 2.6), (42.0, 22.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_South_3", (42.0, 21.6, 0.8), (46.0, 22.0, 9.0), mats["Warehouse_Wall"])

    for px in [14.0, 22.0, 30.0, 38.0, 42.0]:
        add_i_beam(c_arch, f"Pilaster_{int(px)}", px, 21.45, 0.8, 9.0, depth=0.35, flange_w=0.30, material=mats["Steel_Dark"])
        add_box(c_arch, f"Pilaster_Base_{int(px)}", (px - 0.25, 21.25, 0.0), (px + 0.25, 21.6, 0.8), mats["Concrete"], bevel=True, bevel_width=0.03)

    for rz in [4.2, 4.6, 5.0, 5.4, 7.2, 7.6, 8.0]:
        add_box(c_arch, f"Facade_Relief_Band_{int(rz*10)}_Stripe", (8.1, 21.38, rz), (45.9, 21.62, rz + 0.12), mats["Warehouse_Wall_Stripe"], is_collider=False)

    for sz in [3.25, 3.40, 3.55, 3.70]:
        add_box(c_arch, f"Bay1_Slat_{int(sz*100)}", (14.05, 21.55, sz), (21.95, 21.65, sz + 0.12), mats["Garage_Door"], bevel=True, bevel_width=0.02)
    add_oriented_cylinder(c_arch, "Bay1_Shutter_Coil_Detail", (14.05, 21.6, 3.85), (21.95, 21.6, 3.85), radius=0.22, material=mats["Garage_Door"], segments=24)
    add_box(c_arch, "Bay1_Track_L_Detail", (13.92, 21.50, 0.0), (14.08, 21.65, 3.85), mats["Steel_Dark"], is_collider=False)
    add_box(c_arch, "Bay1_Track_R_Detail", (21.92, 21.50, 0.0), (22.08, 21.65, 3.85), mats["Steel_Dark"], is_collider=False)

    # Heavy Rubber Dock Bumpers
    for bx in [14.2, 21.8, 30.2, 37.8]:
        add_box(c_arch, f"Dock_Bumper_{int(bx*10)}", (bx - 0.15, 21.35, 0.2), (bx + 0.15, 21.55, 1.0), mats["Hazard_Black"], bevel=True, bevel_width=0.03)

    # Concrete Safety Bollards with Hazard Stripes
    add_cylinder(c_arch, "Bay1_Bollard_L", (13.5, 20.8, 0.55), radius=0.14, height=1.1, material=mats["Hazard_Yellow"], segments=24)
    add_cylinder(c_arch, "Bay1_Bollard_L_Cap_Cap", (13.5, 20.8, 1.035), radius=0.15, height=0.17, material=mats["Hazard_Black"], segments=24, is_collider=False)
    add_cylinder(c_arch, "Bay1_Bollard_R", (22.5, 20.8, 0.55), radius=0.14, height=1.1, material=mats["Hazard_Yellow"], segments=24)
    add_cylinder(c_arch, "Bay1_Bollard_R_Cap_Cap", (22.5, 20.8, 1.035), radius=0.15, height=0.17, material=mats["Hazard_Black"], segments=24, is_collider=False)

    # Exterior warehouse walls
    add_box(c_arch, "Wall_West", (7.6, 22.0, 0.0), (8.0, 56.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_North_L", (8.0, 56.0, 0.0), (24.0, 56.4, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_North_Door_Header", (24.0, 56.0, 2.6), (27.0, 56.4, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_North_R", (27.0, 56.0, 0.0), (46.0, 56.4, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_East_1", (46.0, 22.0, 0.0), (46.4, 44.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_East_Door_Header", (46.0, 44.0, 6.8), (46.4, 47.0, 9.0), mats["Warehouse_Wall"])
    add_box(c_arch, "Wall_East_2", (46.0, 47.0, 0.0), (46.4, 56.0, 9.0), mats["Warehouse_Wall"])

    # Warehouse Roof Slab with Parapets and Coping
    add_box(c_arch, "Roof_Slab", (8.0, 22.0, 8.8), (46.0, 56.0, 9.0), mats["Warehouse_Roof"])
    add_box(c_arch, "Roof_Parapet_S", (7.8, 21.8, 9.0), (46.2, 22.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Parapet_N", (7.8, 55.8, 9.0), (46.2, 56.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Parapet_W", (7.8, 21.8, 9.0), (8.2, 56.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Parapet_E", (45.8, 21.8, 9.0), (46.2, 56.2, 9.8), mats["Concrete"], bevel=True, bevel_width=0.04)
    add_box(c_arch, "Roof_Coping_S_Detail", (7.75, 21.75, 9.8), (46.25, 22.25, 9.85), mats["Steel_Dark"], is_collider=False)
    add_box(c_arch, "Roof_Coping_N_Detail", (7.75, 55.75, 9.8), (46.25, 56.25, 9.85), mats["Steel_Dark"], is_collider=False)

    # Rooftop Pitched Ridge Skylights
    add_ridge_skylight(c_arch, mats, "Skylight_West", 20.0, 26.0, 52.0, width=3.2, curb_height=0.4, peak_height=0.9)
    add_ridge_skylight(c_arch, mats, "Skylight_East", 34.0, 26.0, 38.0, width=3.2, curb_height=0.4, peak_height=0.9)

    # Rooftop Vents, Chillers & Exhaust Fans
    add_box(c_vents, "Roof_Vent_1", (14.0, 28.0, 9.0), (18.0, 32.0, 10.2), mats["Vent_Duct"], bevel=True, bevel_width=0.05)
    add_box(c_vents, "Roof_Vent_2", (28.0, 44.0, 9.0), (32.0, 48.0, 10.2), mats["Vent_Duct"], bevel=True, bevel_width=0.05)
    add_box(c_props, "Roof_Chiller", (36.0, 28.0, 9.0), (40.0, 34.0, 10.4), mats["Steel_Dark"], bevel=True, bevel_width=0.05)
    add_cylinder(c_vents, "Roof_Exhaust_Fan_Detail", (22.0, 48.0, 9.6), radius=0.6, height=1.2, material=mats["Vent_Duct"], segments=20, is_collider=False)

    print("[7/12] Building Interior Structural Columns, Roof Trusses & Purlins...")
    for cx in [18.0, 27.0, 36.0]:
        for cy in [28.0, 38.0, 48.0]:
            if cx >= 28.0 and cy >= 40.0:
                continue
            add_i_beam(c_warehouse, f"Int_Col_{int(cx)}_{int(cy)}", cx, cy, 0.0, 9.0, depth=0.4, flange_w=0.35, material=mats["Steel_Dark"])
            add_box(c_warehouse, f"Int_Col_Plinth_{int(cx)}_{int(cy)}", (cx - 0.35, cy - 0.35, 0.0), (cx + 0.35, cy + 0.35, 0.4), mats["Concrete"], bevel=True, bevel_width=0.04)

    for ty in [26.0, 34.0, 42.0, 50.0]:
        add_open_web_truss(c_warehouse, mats, f"Truss_{int(ty)}", ty, x_start=8.2, x_end=45.8, z_bot=8.25, z_top=8.80, panel_w=2.0)

    add_roof_purlins_and_sway(c_warehouse, mats)
    add_fire_sprinkler_system(c_warehouse, mats)
    add_spiral_hvac_system(c_warehouse, mats)
    add_high_bay_ufo_lighting(c_warehouse, mats)

    print("[8/12] Building Elevated Catwalks, Interior Stairs & Fire Escape...")
    # West Catwalk Deck (z = 4.2m)
    add_box(c_catwalk, "Catwalk_West_Deck", (8.0, 22.0, 4.15), (13.0, 56.0, 4.2), mats["Diamond_Plate"])
    # East Catwalk Runway to Hostage Office
    add_box(c_catwalk, "Catwalk_Office_Runway", (13.0, 38.0, 4.15), (28.0, 42.0, 4.2), mats["Diamond_Plate"])

    # Handrails on Catwalks
    add_box(c_catwalk, "Rail_West_Top", (12.92, 29.4, 5.15), (13.08, 38.0, 5.25), mats["Safety_Rail"], is_collider=False)
    add_box(c_catwalk, "Rail_West_Mid", (12.94, 29.4, 4.65), (13.06, 38.0, 4.75), mats["Safety_Rail"], is_collider=False)
    add_box(c_catwalk, "Rail_Runway_Top", (13.0, 37.92, 5.15), (28.0, 38.08, 5.25), mats["Safety_Rail"], is_collider=False)
    add_box(c_catwalk, "Rail_Runway_Mid", (13.0, 37.94, 4.65), (28.0, 38.06, 4.75), mats["Safety_Rail"], is_collider=False)

    # Interior Industrial Staircase: Ground Floor to West Catwalk (y: 24.0..29.4, z: 0.0..4.2m)
    add_industrial_staircase(c_catwalk, mats, "Stair_West_Catwalk", 11.5, 12.95, 24.0, 29.4, 0.0, 4.2, num_steps=18)

    # Exterior Fire Escape Staircase on East Wall (connecting rail yard ground to 2nd-floor hostage office door)
    add_box(c_catwalk, "Fire_Landing_Low", (46.4, 36.0, 2.05), (48.8, 38.5, 2.15), mats["Steel_Catwalk"], bevel=True, bevel_width=0.03)
    add_box(c_catwalk, "Fire_Landing_Mid", (46.4, 44.0, 4.15), (48.8, 47.0, 4.25), mats["Steel_Catwalk"], bevel=True, bevel_width=0.03)
    # Lower flight: Ground (y=31.0, z=0.0) up to Low Landing (y=36.0, z=2.1m)
    add_industrial_staircase(c_catwalk, mats, "Stair_Fire_Escape_Lower", 46.5, 48.5, 31.0, 36.0, 0.0, 2.1, num_steps=9)
    # Upper flight: Low Landing (y=38.5, z=2.1m) up to High Landing (y=44.0, z=4.2m)
    add_industrial_staircase(c_catwalk, mats, "Stair_Fire_Escape_Upper", 46.5, 48.5, 38.5, 44.0, 2.1, 4.2, num_steps=9)

    print("[9/12] Building 2nd-Floor Hostage Office...")
    add_box(c_office, "Office_Floor", (28.0, 40.0, 4.15), (45.5, 55.5, 4.2), mats["Office_Floor"])
    add_box(c_office, "Office_Front_Wall_Low", (28.0, 39.8, 4.2), (45.5, 40.0, 5.0), mats["Office_Wall"])
    add_box(c_office, "Office_Panoramic_Window", (29.0, 39.85, 5.0), (44.5, 39.95, 7.2), mats["Office_Window"], is_collider=False)
    for i, mx in enumerate([32.0, 35.0, 38.0, 41.0]):
        add_box(c_office, f"Office_Mullion_V_{i}", (mx - 0.08, 39.82, 5.0), (mx + 0.08, 39.98, 7.2), mats["Steel_Dark"], is_collider=False)
    add_box(c_office, "Office_Mullion_H", (29.0, 39.82, 6.05), (44.5, 39.98, 6.15), mats["Steel_Dark"], is_collider=False)
    add_box(c_office, "Office_Front_Wall_High", (28.0, 39.8, 7.2), (45.5, 40.0, 8.8), mats["Office_Wall"])
    add_box(c_office, "Office_West_Wall_1", (28.0, 40.0, 4.2), (28.4, 48.0, 8.8), mats["Office_Wall"])
    add_box(c_office, "Office_West_Door_Header", (28.0, 48.0, 6.6), (28.4, 51.0, 8.8), mats["Office_Wall"])
    add_box(c_office, "Office_West_Wall_2", (28.0, 51.0, 4.2), (28.4, 55.5, 8.8), mats["Office_Wall"])
    add_hostage_office_props(c_office, mats)

    print("[10/12] Building Traveling Bridge Crane, Workshop Bay & Loading Docks...")
    add_overhead_crane(c_warehouse, mats)
    add_workshop_maintenance_bay(c_props, mats)
    add_loading_dock_upgrades(c_arch, mats)

    print("[11/12] Building Pallet Racks, Forklift, Cargo & Civil Details...")
    add_pallet_rack(c_props, mats, 34.0, 30.0, 44.0, 33.0, num_tiers=3, total_height=5.5)
    add_forklift(c_props, mats, 23.0, 32.0, z=0.0)
    add_pallet_jack(c_props, mats, 31.0, 32.0, z=0.0)
    add_emergency_eyewash_station(c_props, mats, 8.25, 34.0, z=0.0)
    add_cargo_and_pallets(c_props, mats)
    add_floor_expansion_joints_and_striping(c_warehouse, mats)
    add_civil_and_highway_infrastructure(c_arch, mats)
    add_street_and_civil_details(c_arch, mats)
    add_commercial_dumpster(c_props, mats, "Street_Dumpster", 13.0, 9.0, z=0.0, color_mat="Container_Green")

    # Fire Hydrant on sidewalk
    add_cylinder(c_props, "Fire_Hydrant_Base", (26.0, 7.2, 0.4), radius=0.22, height=0.4, material=mats["Fire_Extinguisher_Red"], segments=32)
    add_cylinder(c_props, "Fire_Hydrant_Body", (26.0, 7.2, 0.8), radius=0.18, height=0.6, material=mats["Fire_Extinguisher_Red"], segments=32)
    add_cylinder(c_props, "Fire_Hydrant_Cap", (26.0, 7.2, 1.15), radius=0.2, height=0.15, material=mats["Fire_Extinguisher_Red"], segments=32)
    add_cylinder(c_props, "Fire_Hydrant_Nozzle_L", (25.75, 7.2, 0.75), radius=0.08, height=0.35, material=mats["Steel_Dark"], segments=24)
    add_cylinder(c_props, "Fire_Hydrant_Nozzle_R", (26.25, 7.2, 0.75), radius=0.08, height=0.35, material=mats["Steel_Dark"], segments=24)

    # Surveillance CCTV Cameras
    add_surveillance_camera(c_props, mats, "Camera_Exterior_SW", (7.6, 21.2, 6.5), is_ptz_dome=True)
    add_surveillance_camera(c_props, mats, "Camera_Exterior_SE", (46.4, 21.2, 6.5), is_ptz_dome=True)
    add_surveillance_camera(c_props, mats, "Camera_Interior_Catwalk", (13.0, 24.0, 5.3), is_ptz_dome=False)

    print("[12/12] Finalizing Scene Graph and Hierarchy...")
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

    import shutil
    shutil.copyfile(glb_backend_path, glb_web_path)
    print(f"Copied GLB to Web: {glb_web_path}")


if __name__ == "__main__":
    print("=== Generating Ultra-Detailed Realistic CS:GO cs_assault Map in Blender (56m Scale) ===")
    build_assault_scene()
    export_scene_glb()
    print("=== Ultra-Detailed Generation Complete! ===")
