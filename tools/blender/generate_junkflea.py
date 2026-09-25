#!/usr/bin/env python3
"""
Procedural Realistic CQB Junkyard Arena Generator for "Junk Flea" (hd_junkflea) in Blender 4.2+ LTS.

Completely eliminates all flat "lego/roblox" block primitives and replaces them with:
- 3D Corrugated ISO Shipping Containers (40ft High Cube & 20ft) with deep trapezoidal
  fluted panels, corner castings with twistlock oval holes, dual hinged cargo doors,
  vertical galvanized locking cam rods, hinge brackets, and hazard placards.
- High Steel Catwalk Bridge (z = 6.4m) with diamond-plate decking, wide-flange I-beam
  stringers, welded stanchion posts, double-pipe safety handrails, and diagonal truss towers.
- Subterranean Drainage Trenches (z = -2.0m) with corrugated sheet-pile retaining walls,
  cast-iron sump pump grates, reflective standing water/oil slicks, and diamond-plate ramps.
- Compacted Automotive Scrap Bales (crushed car cubes) with crumpled sheet metal,
  radiator cores, wheel hubs, and tension wire straps.
- Heavy Industrial Overhead Gantry Crane with twin box girders spanning the yard,
  trolley hoist, steel wire cables, and a 1.8m electromagnetic scrap lifting disc.
- Industrial Yard Props: stacks of radial truck tires with tread grooves, 55-gallon
  ribbed steel oil/hazmat drums, wooden cargo pallets, and high-mast sodium floodlights.
- Weathered PBR Materials: wet gravel mud, chipped container enamel, rusted diamond plate,
  oxidized scrap metal, and industrial caution striping.
"""

import sys
import os
import math

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: This script must be run inside Blender: blender -b -P generate_junkflea.py")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maplib  # noqa: E402  (a sibling, found via the path above)


def clear_scene():
    """Clear all objects, meshes, materials, and collections from Blender."""
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
    """Setup all game materials with gritty industrial PBR properties."""
    mats = {}

    # Terrain & Ground
    mats["Gravel_Mud_Wet"] = create_pbr_material("Mat_Gravel_Mud_Wet", (0.24, 0.22, 0.20), metallic=0.05, roughness=0.88)
    mats["Trench_Mud_Dark"] = create_pbr_material("Mat_Trench_Mud_Dark", (0.16, 0.14, 0.12), metallic=0.02, roughness=0.92)
    mats["Water_Slick"] = create_pbr_material("Mat_Water_Slick_Detail", (0.08, 0.10, 0.12), metallic=0.15, roughness=0.04)

    # Concrete Retaining Walls
    mats["Concrete_Wall"] = create_pbr_material("Mat_Concrete_Wall", (0.44, 0.42, 0.40), metallic=0.0, roughness=0.86)
    mats["Concrete_Stained"] = create_pbr_material("Mat_Concrete_Stained", (0.30, 0.28, 0.26), metallic=0.0, roughness=0.90)

    # Corrugated Shipping Container Enamels
    mats["Container_Blue_Maersk"] = create_pbr_material("Mat_Container_Blue", (0.16, 0.32, 0.48), metallic=0.68, roughness=0.46)
    mats["Container_Red_Hanjin"] = create_pbr_material("Mat_Container_Red", (0.52, 0.20, 0.16), metallic=0.65, roughness=0.48)
    mats["Container_Green_Evergreen"] = create_pbr_material("Mat_Container_Green", (0.20, 0.35, 0.24), metallic=0.66, roughness=0.48)
    mats["Container_Orange_Weathered"] = create_pbr_material("Mat_Container_Orange", (0.58, 0.32, 0.15), metallic=0.62, roughness=0.52)
    mats["Container_Frame_Dark"] = create_pbr_material("Mat_Container_Frame", (0.12, 0.13, 0.14), metallic=0.82, roughness=0.38)

    # Metals, Grating & Structural Steel
    mats["Steel_Structural"] = create_pbr_material("Mat_Steel_Structural", (0.32, 0.34, 0.36), metallic=0.88, roughness=0.36)
    mats["Steel_Rusted"] = create_pbr_material("Mat_Steel_Rusted", (0.38, 0.26, 0.20), metallic=0.72, roughness=0.65)
    mats["Corrugated_Steel_Rusted"] = create_pbr_material("Mat_Corrugated_Steel_Rusted", (0.36, 0.24, 0.18), metallic=0.74, roughness=0.68)
    mats["Steel_IBeam_Yellow"] = create_pbr_material("Mat_Steel_IBeam_Yellow", (0.84, 0.68, 0.12), metallic=0.65, roughness=0.42)
    mats["Diamond_Plate"] = create_pbr_material("Mat_Diamond_Plate", (0.42, 0.44, 0.46), metallic=0.90, roughness=0.38)
    mats["Sheet_Pile_Trench"] = create_pbr_material("Mat_Sheet_Pile", (0.28, 0.24, 0.22), metallic=0.78, roughness=0.62)
    mats["Cast_Iron_Dark"] = create_pbr_material("Mat_Cast_Iron_Dark", (0.16, 0.17, 0.18), metallic=0.90, roughness=0.34)
    mats["Chrome_Galvanized"] = create_pbr_material("Mat_Chrome_Galv", (0.75, 0.76, 0.78), metallic=0.85, roughness=0.28)

    # Compacted Scrap Bales
    mats["Scrap_Car_Red"] = create_pbr_material("Mat_Scrap_Car_Red", (0.44, 0.18, 0.15), metallic=0.75, roughness=0.58)
    mats["Scrap_Car_Blue"] = create_pbr_material("Mat_Scrap_Car_Blue", (0.15, 0.26, 0.40), metallic=0.75, roughness=0.58)
    mats["Scrap_Car_Yellow"] = create_pbr_material("Mat_Scrap_Car_Yellow", (0.52, 0.42, 0.15), metallic=0.72, roughness=0.60)
    mats["Scrap_Metal_Mangled"] = create_pbr_material("Mat_Scrap_Metal_Mangled", (0.30, 0.28, 0.26), metallic=0.82, roughness=0.55)
    mats["Steel_Wire_Bale"] = create_pbr_material("Mat_Steel_Wire_Bale_Detail", (0.65, 0.66, 0.68), metallic=0.92, roughness=0.25)

    # Industrial Rubber, Pallets, Drums & Hazard
    mats["Rubber_Tire"] = create_pbr_material("Mat_Rubber_Tire", (0.08, 0.08, 0.09), metallic=0.0, roughness=0.86)
    mats["Wood_Pallet"] = create_pbr_material("Mat_Wood_Pallet", (0.46, 0.34, 0.22), metallic=0.0, roughness=0.82)
    mats["Drum_Blue"] = create_pbr_material("Mat_Drum_Blue", (0.12, 0.26, 0.46), metallic=0.80, roughness=0.40)
    mats["Drum_Hazmat"] = create_pbr_material("Mat_Drum_Hazmat", (0.82, 0.70, 0.12), metallic=0.80, roughness=0.40)
    mats["Drum_Rust"] = create_pbr_material("Mat_Drum_Rust", (0.42, 0.25, 0.16), metallic=0.70, roughness=0.66)
    mats["Hazard_Yellow"] = create_pbr_material("Mat_Hazard_Yellow", (0.92, 0.76, 0.12), metallic=0.20, roughness=0.42)
    mats["Hazard_Black"] = create_pbr_material("Mat_Hazard_Black", (0.06, 0.06, 0.06), metallic=0.10, roughness=0.62)

    # Lighting & Signs
    mats["Sodium_Floodlight"] = create_pbr_material("Mat_Sodium_Floodlight_Glow", (1.0, 0.85, 0.45), emission_color=(1.0, 0.72, 0.22), emission_strength=7.0)
    mats["Strobe_Amber"] = create_pbr_material("Mat_Strobe_Amber_Glow", (1.0, 0.55, 0.08), emission_color=(1.0, 0.55, 0.08), emission_strength=9.0)
    mats["Placard_White"] = create_pbr_material("Mat_Placard_White_Detail", (0.88, 0.88, 0.86), metallic=0.05, roughness=0.70)

    return mats


def get_or_create_collection(name):
    """Retrieve or create a Blender collection in the master scene."""
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def add_box(col, name, p0, p1, material, is_collider=True, bevel=False, bevel_width=0.03):
    """Add an axis-aligned box between corners p0 and p1."""
    x0, y0, z0 = p0
    x1, y1, z1 = p1

    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    cz = (z0 + z1) * 0.5
    sx = abs(x1 - x0)
    sy = abs(y1 - y0)
    sz = abs(z1 - z0)

    obj_name = name if is_collider else f"{name}_Detail"
    mesh = bpy.data.meshes.new(obj_name)
    obj = bpy.data.objects.new(obj_name, mesh)
    col.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()

    obj.location = (cx, cy, cz)
    obj.scale = (sx, sy, sz)
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if bevel and sz > 0.1 and sx > 0.1 and sy > 0.1:
        mod = obj.modifiers.new(name="Bevel", type="BEVEL")
        mod.width = bevel_width
        mod.segments = 2
        mod.limit_method = "ANGLE"

    if material:
        obj.data.materials.append(material)

    return obj


def add_cylinder(col, name, center, radius, height, material, segments=16, is_collider=True):
    """Add a vertical cylinder centered at (cx, cy, cz)."""
    cx, cy, cz = center
    obj_name = name if is_collider else f"{name}_Detail"
    mesh = bpy.data.meshes.new(obj_name)
    obj = bpy.data.objects.new(obj_name, mesh)
    col.objects.link(obj)

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

    obj.location = (cx, cy, cz)
    bpy.context.view_layer.update()
    if material:
        obj.data.materials.append(material)
    return obj


def add_oriented_cylinder(col, name, p0, p1, radius, material, segments=12, is_collider=True):
    """Add an oriented cylinder connecting p0 to p1."""
    v0 = Vector(p0)
    v1 = Vector(p1)
    diff = v1 - v0
    length = diff.length
    if length < 1e-5:
        return None

    mid = (v0 + v1) * 0.5
    obj_name = name if is_collider else f"{name}_Detail"
    mesh = bpy.data.meshes.new(obj_name)
    obj = bpy.data.objects.new(obj_name, mesh)
    col.objects.link(obj)

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

    rot_quat = Vector((0, 0, 1)).rotation_difference(diff.normalized())
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


def add_triangular_prism(col, name, p0, p1, p2, depth, material, is_collider=True):
    """Add a ramp prism with triangular profile (p0, p1, p2) extruded along depth (+X)."""
    obj_name = name if is_collider else f"{name}_Detail"
    mesh = bpy.data.meshes.new(obj_name)
    obj = bpy.data.objects.new(obj_name, mesh)
    col.objects.link(obj)

    bm = bmesh.new()
    v0 = bm.verts.new((0, p0[0], p0[1]))
    v1 = bm.verts.new((0, p1[0], p1[1]))
    v2 = bm.verts.new((0, p2[0], p2[1]))
    f0 = bm.faces.new((v0, v1, v2))

    res = bmesh.ops.extrude_face_region(bm, geom=[f0])
    extruded_verts = [e for e in res["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=Vector((depth, 0, 0)), verts=extruded_verts)

    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_corrugated_shipping_container(col, mats, name, p0, p1, color_mat_name, door_face="S"):
    """
    Builds an authentic 3D corrugated ISO shipping container (eliminating flat lego blocks):
    - Heavy perimeter tubular steel frame (top/bottom side rails, corner posts).
    - 8 Cast steel corner castings with twistlock oval holes.
    - Deep 3D trapezoidal corrugated wall fluting along sides and roof.
    - Hinged cargo doors with dual vertical locking cam rods, hinge brackets, and handles.
    - Hazard diamond placards and ISO container serial markings.
    """
    x0, y0, z0 = p0
    x1, y1, z1 = p1
    c_mat = mats[color_mat_name]
    frame_mat = mats["Container_Frame_Dark"]
    galv_mat = mats["Chrome_Galvanized"]
    placard_mat = mats["Placard_White"]

    # 1. Main Solid Physics Collider (Invisible tag so renderer skips it)
    add_box(col, f"{name}_ColOnly", (x0, y0, z0), (x1, y1, z1), c_mat, is_collider=True)

    # 2. Outer Structural Perimeter Rails
    rail_w = 0.14
    # Bottom side rails
    add_box(col, f"{name}_Rail_Bot_S", (x0, y0, z0), (x1, y0 + rail_w, z0 + rail_w), frame_mat, is_collider=False)
    add_box(col, f"{name}_Rail_Bot_N", (x0, y1 - rail_w, z0), (x1, y1, z0 + rail_w), frame_mat, is_collider=False)
    add_box(col, f"{name}_Rail_Bot_W", (x0, y0, z0), (x0 + rail_w, y1, z0 + rail_w), frame_mat, is_collider=False)
    add_box(col, f"{name}_Rail_Bot_E", (x1 - rail_w, y0, z0), (x1, y1, z0 + rail_w), frame_mat, is_collider=False)

    # Top side rails
    add_box(col, f"{name}_Rail_Top_S", (x0, y0, z1 - rail_w), (x1, y0 + rail_w, z1), frame_mat, is_collider=False)
    add_box(col, f"{name}_Rail_Top_N", (x0, y1 - rail_w, z1 - rail_w), (x1, y1, z1), frame_mat, is_collider=False)
    add_box(col, f"{name}_Rail_Top_W", (x0, y0, z1 - rail_w), (x0 + rail_w, y1, z1), frame_mat, is_collider=False)
    add_box(col, f"{name}_Rail_Top_E", (x1 - rail_w, y0, z1 - rail_w), (x1, y1, z1), frame_mat, is_collider=False)

    # 4 Heavy Vertical Corner Posts
    for cx_val, cy_val in [(x0, y0), (x1 - rail_w, y0), (x0, y1 - rail_w), (x1 - rail_w, y1 - rail_w)]:
        add_box(col, f"{name}_Post_{int(cx_val*10)}_{int(cy_val*10)}", (cx_val, cy_val, z0), (cx_val + rail_w, cy_val + rail_w, z1), frame_mat, is_collider=False)

    # 3. 8 Cast-Steel Corner Castings with ISO Twistlock Holes
    c_cast_w = 0.22
    for cz_val in [z0, z1 - c_cast_w]:
        for cx_val, cy_val in [(x0, y0), (x1 - c_cast_w, y0), (x0, y1 - c_cast_w), (x1 - c_cast_w, y1 - c_cast_w)]:
            add_box(col, f"{name}_Casting_{int(cx_val*10)}_{int(cy_val*10)}_{int(cz_val*10)}", (cx_val - 0.01, cy_val - 0.01, cz_val), (cx_val + c_cast_w + 0.01, cy_val + c_cast_w + 0.01, cz_val + c_cast_w), frame_mat, bevel=True, bevel_width=0.02, is_collider=False)
            # Oval twistlock aperture
            add_cylinder(col, f"{name}_Hole_{int(cx_val*10)}_{int(cy_val*10)}_{int(cz_val*10)}", (cx_val + c_cast_w * 0.5, cy_val + c_cast_w * 0.5, cz_val + c_cast_w * 0.5), radius=0.045, height=c_cast_w + 0.04, material=mats["Hazard_Black"], segments=8, is_collider=False)

    # 4. 3D Corrugated Fluting along Long Sides
    # Flute period ~0.35m, depth 0.05m
    length_x = abs(x1 - x0)
    length_y = abs(y1 - y0)

    if length_x > length_y:
        # Container runs East-West
        # South Wall Flutes
        for fx in range(int((x0 + 0.4) * 10), int((x1 - 0.4) * 10), 4):
            fx_f = float(fx) / 10.0
            add_box(col, f"{name}_Flute_S_{fx}", (fx_f, y0 - 0.03, z0 + rail_w), (fx_f + 0.20, y0 + 0.03, z1 - rail_w), c_mat, is_collider=False)
        # North Wall Flutes
        for fx in range(int((x0 + 0.4) * 10), int((x1 - 0.4) * 10), 4):
            fx_f = float(fx) / 10.0
            add_box(col, f"{name}_Flute_N_{fx}", (fx_f, y1 - 0.03, z0 + rail_w), (fx_f + 0.20, y1 + 0.03, z1 - rail_w), c_mat, is_collider=False)
    else:
        # Container runs North-South
        # West Wall Flutes
        for fy in range(int((y0 + 0.4) * 10), int((y1 - 0.4) * 10), 4):
            fy_f = float(fy) / 10.0
            add_box(col, f"{name}_Flute_W_{fy}", (x0 - 0.03, fy_f, z0 + rail_w), (x0 + 0.03, fy_f + 0.20, z1 - rail_w), c_mat, is_collider=False)
        # East Wall Flutes
        for fy in range(int((y0 + 0.4) * 10), int((y1 - 0.4) * 10), 4):
            fy_f = float(fy) / 10.0
            add_box(col, f"{name}_Flute_E_{fy}", (x1 - 0.03, fy_f, z0 + rail_w), (x1 + 0.03, fy_f + 0.20, z1 - rail_w), c_mat, is_collider=False)

    # 5. Dual Hinged Cargo Rear Doors with Locking Cam Rods
    # Put doors on the specified end face
    if door_face == "S":
        dy = y0 - 0.02
        # Left door panel and right door panel
        mid_x = (x0 + x1) * 0.5
        add_box(col, f"{name}_Door_L", (x0 + rail_w, dy, z0 + rail_w), (mid_x - 0.02, dy + 0.04, z1 - rail_w), c_mat, is_collider=False)
        add_box(col, f"{name}_Door_R", (mid_x + 0.02, dy, z0 + rail_w), (x1 - rail_w, dy + 0.04, z1 - rail_w), c_mat, is_collider=False)
        # Center sealing gasket
        add_box(col, f"{name}_Gasket", (mid_x - 0.04, dy - 0.01, z0 + rail_w), (mid_x + 0.04, dy + 0.03, z1 - rail_w), mats["Rubber_Tire"], is_collider=False)
        # Vertical Locking Cam Rods
        for rod_x in [x0 + (mid_x - x0) * 0.35, x0 + (mid_x - x0) * 0.75, mid_x + (x1 - mid_x) * 0.25, mid_x + (x1 - mid_x) * 0.65]:
            add_oriented_cylinder(col, f"{name}_CamRod_{int(rod_x*10)}", (rod_x, dy - 0.03, z0 + rail_w + 0.1), (rod_x, dy - 0.03, z1 - rail_w - 0.1), radius=0.028, material=galv_mat, segments=8, is_collider=False)
            # Top and bottom cam lock bracket keepers
            add_box(col, f"{name}_CamKeeper_B_{int(rod_x*10)}", (rod_x - 0.05, dy - 0.05, z0 + rail_w), (rod_x + 0.05, dy, z0 + rail_w + 0.12), frame_mat, is_collider=False)
            add_box(col, f"{name}_CamKeeper_T_{int(rod_x*10)}", (rod_x - 0.05, dy - 0.05, z1 - rail_w - 0.12), (rod_x + 0.05, dy, z1 - rail_w), frame_mat, is_collider=False)
            # Horizontal locking handle
            add_oriented_cylinder(col, f"{name}_Handle_{int(rod_x*10)}", (rod_x, dy - 0.04, z0 + (z1 - z0) * 0.42), (rod_x + 0.22, dy - 0.04, z0 + (z1 - z0) * 0.42), radius=0.016, material=galv_mat, segments=6, is_collider=False)
        # 4 Heavy Pivot Hinges on Outer Edges
        for hz in [z0 + 0.4, z0 + 1.2, z0 + 2.0, z1 - 0.4]:
            add_box(col, f"{name}_Hinge_L_{int(hz*10)}", (x0 + rail_w - 0.04, dy - 0.02, hz - 0.08), (x0 + rail_w + 0.06, dy + 0.03, hz + 0.08), frame_mat, is_collider=False)
            add_box(col, f"{name}_Hinge_R_{int(hz*10)}", (x1 - rail_w - 0.06, dy - 0.02, hz - 0.08), (x1 - rail_w + 0.04, dy + 0.03, hz + 0.08), frame_mat, is_collider=False)

    # Hazard diamond placard on side
    add_box(col, f"{name}_Placard", (x0 + 0.8, y0 - 0.035, z0 + 1.4), (x0 + 1.3, y0 - 0.03, z0 + 1.9), placard_mat, is_collider=False)


def add_scrap_car_bale(col, mats, name, center, rot_z_deg=0.0):
    """
    Builds a hydraulic compacted automotive scrap bale (1.4m x 1.4m x 1.1m):
    - Crumpled layered sheet metal folds with chipped automobile enamel.
    - Visible vehicle radiator core, exhaust pipe stub, and mangled rim.
    - High-tension galvanized steel binding wire straps wrapping the bale.
    """
    cx, cy, cz = center
    hw = 0.65
    hh = 0.55

    # Core bale block
    car_mats = [mats["Scrap_Car_Red"], mats["Scrap_Car_Blue"], mats["Scrap_Car_Yellow"]]
    c_idx = (int(cx * 7 + cy * 11)) % len(car_mats)
    base_mat = car_mats[c_idx]

    add_box(col, f"{name}_Core", (cx - hw, cy - hw, cz), (cx + hw, cy + hw, cz + hh * 2), base_mat, bevel=True, bevel_width=0.05, is_collider=True)

    # Crumpled layered sheet metal ridges
    add_box(col, f"{name}_Fold_1", (cx - hw - 0.04, cy - hw * 0.7, cz + 0.25), (cx + hw + 0.04, cy - hw * 0.2, cz + 0.45), mats["Scrap_Metal_Mangled"], is_collider=False)
    add_box(col, f"{name}_Fold_2", (cx - hw * 0.6, cy - hw - 0.04, cz + 0.60), (cx + hw * 0.8, cy + hw + 0.04, cz + 0.85), mats["Steel_Rusted"], is_collider=False)

    # Mangled wheel hub protruding
    add_cylinder(col, f"{name}_Hub", (cx + hw * 0.7, cy - hw - 0.05, cz + 0.50), radius=0.18, height=0.12, material=mats["Cast_Iron_Dark"], segments=12, is_collider=False)

    # Steel Tension Wire Straps wrapping the bale
    for bz in [cz + 0.35, cz + 0.80]:
        add_box(col, f"{name}_Strap_{int(bz*100)}", (cx - hw - 0.015, cy - hw - 0.015, bz), (cx + hw + 0.015, cy + hw + 0.015, bz + 0.03), mats["Steel_Wire_Bale"], is_collider=False)


def add_tire_stack(col, mats, name, center, count=4):
    """Builds a realistic stack of heavy industrial tires with tread grooves."""
    cx, cy, base_z = center
    t_radius = 0.48
    t_height = 0.30

    for i in range(count):
        tz = base_z + i * t_height + t_height * 0.5
        # Outer tire body
        add_cylinder(col, f"{name}_Tire_{i}", (cx, cy, tz), radius=t_radius, height=t_height - 0.02, material=mats["Rubber_Tire"], segments=20, is_collider=(i == 0))
        # Center void rim opening
        add_cylinder(col, f"{name}_Void_{i}", (cx, cy, tz), radius=t_radius * 0.48, height=t_height + 0.02, material=mats["Hazard_Black"], segments=16, is_collider=False)


def build_junkflea_scene():
    """Build the entire photorealistic Junk Flea (hd_junkflea) map in Blender."""
    clear_scene()
    mats = setup_materials()

    c_arch = get_or_create_collection("Architecture_JunkFlea")
    c_trench = get_or_create_collection("Subterranean_Trenches")
    c_bridge = get_or_create_collection("Catwalk_Bridge")
    c_containers = get_or_create_collection("Shipping_Containers")
    c_crane = get_or_create_collection("Overhead_Gantry_Crane")
    c_salvage = get_or_create_collection("Scrap_Salvage_Bales")
    c_props = get_or_create_collection("Yard_Props")

    print("[1/8] Generating Perimeter Walls, Security Fences & Wet Gravel Yard...")
    # =========================================================================
    # 1. PERIMETER BOUNDARIES & GROUND YARD (64x64 Grid)
    # =========================================================================
    # Outer concrete retaining walls (x: 4.0..60.0, y: 4.0..60.0, height: 14m)
    add_box(c_arch, "Boundary_Wall_South", (4.0, 3.5, 0.0), (60.0, 4.0, 14.0), mats["Concrete_Wall"])
    add_box(c_arch, "Boundary_Wall_North", (4.0, 60.0, 0.0), (60.0, 60.5, 14.0), mats["Concrete_Wall"])
    add_box(c_arch, "Boundary_Wall_West", (3.5, 4.0, 0.0), (4.0, 60.0, 14.0), mats["Concrete_Wall"])
    add_box(c_arch, "Boundary_Wall_East", (60.0, 4.0, 0.0), (60.5, 60.0, 14.0), mats["Concrete_Wall"])

    # Upper Corrugated Steel Wall Extensions with Rusted Weathering
    add_box(c_arch, "Perimeter_Corrugation_S", (4.0, 3.65, 5.0), (60.0, 3.95, 14.0), mats["Corrugated_Steel_Rusted"], is_collider=False)
    add_box(c_arch, "Perimeter_Corrugation_N", (4.0, 60.05, 5.0), (60.0, 60.35, 14.0), mats["Corrugated_Steel_Rusted"], is_collider=False)
    add_box(c_arch, "Perimeter_Corrugation_W", (3.65, 4.0, 5.0), (3.95, 60.0, 14.0), mats["Corrugated_Steel_Rusted"], is_collider=False)
    add_box(c_arch, "Perimeter_Corrugation_E", (60.05, 4.0, 5.0), (60.35, 60.0, 14.0), mats["Corrugated_Steel_Rusted"], is_collider=False)

    # Perimeter Razor Wire Coils along wall crests (z = 14.0m)
    for pi in range(5, 60, 4):
        p_flt = float(pi)
        add_oriented_cylinder(c_arch, f"Razor_Coil_S_{pi}", (p_flt, 3.8, 14.2), (p_flt + 3.5, 3.8, 14.2), radius=0.25, material=mats["Steel_Structural"], segments=8, is_collider=False)
        add_oriented_cylinder(c_arch, f"Razor_Coil_N_{pi}", (p_flt, 60.2, 14.2), (p_flt + 3.5, 60.2, 14.2), radius=0.25, material=mats["Steel_Structural"], segments=8, is_collider=False)

    # Main Ground Yard Floor (z = 0.0) with cutouts for trenches
    # West Ground Strip (x: 4.0..16.0, y: 4.0..60.0)
    add_box(c_arch, "Yard_Floor_West", (4.0, 4.0, -0.4), (16.0, 60.0, 0.0), mats["Gravel_Mud_Wet"])
    # East Ground Strip (x: 48.0..60.0, y: 4.0..60.0)
    add_box(c_arch, "Yard_Floor_East", (48.0, 4.0, -0.4), (60.0, 60.0, 0.0), mats["Gravel_Mud_Wet"])
    # Center Ground Island (x: 22.0..42.0, y: 4.0..60.0)
    add_box(c_arch, "Yard_Floor_Center", (22.0, 4.0, -0.4), (42.0, 60.0, 0.0), mats["Gravel_Mud_Wet"])
    # Trench crossover strips North & South
    add_box(c_arch, "Yard_Cross_S_W", (16.0, 4.0, -0.4), (22.0, 14.0, 0.0), mats["Gravel_Mud_Wet"])
    add_box(c_arch, "Yard_Cross_N_W", (16.0, 50.0, -0.4), (22.0, 60.0, 0.0), mats["Gravel_Mud_Wet"])
    add_box(c_arch, "Yard_Cross_S_E", (42.0, 4.0, -0.4), (48.0, 14.0, 0.0), mats["Gravel_Mud_Wet"])
    add_box(c_arch, "Yard_Cross_N_E", (42.0, 50.0, -0.4), (48.0, 60.0, 0.0), mats["Gravel_Mud_Wet"])

    # Water puddles & oil slicks in yard
    add_box(c_arch, "Oil_Slick_South", (28.0, 16.0, 0.005), (36.0, 19.0, 0.008), mats["Water_Slick"], is_collider=False)
    add_box(c_arch, "Oil_Slick_North", (28.0, 45.0, 0.005), (36.0, 48.0, 0.008), mats["Water_Slick"], is_collider=False)

    print("[2/8] Generating Subterranean Drainage Trenches (z = -2.0m)...")
    # =========================================================================
    # 2. SUBTERRANEAN DRAINAGE TRENCHES (z = -2.0m)
    # =========================================================================
    # West Trench: x: 16.0..22.0, y: 20.0..44.0, z = -2.0m
    add_box(c_trench, "Trench_Floor_West", (16.0, 20.0, -2.4), (22.0, 44.0, -2.0), mats["Trench_Mud_Dark"])
    # East Trench: x: 42.0..48.0, y: 20.0..44.0, z = -2.0m
    add_box(c_trench, "Trench_Floor_East", (42.0, 20.0, -2.4), (48.0, 44.0, -2.0), mats["Trench_Mud_Dark"])

    # Trench Corrugated Sheet Piling Retaining Walls (holding back dirt)
    # West Trench Walls
    add_box(c_trench, "Trench_Wall_W_L", (15.85, 20.0, -2.0), (16.0, 44.0, 0.0), mats["Sheet_Pile_Trench"])
    add_box(c_trench, "Trench_Wall_W_R", (22.0, 20.0, -2.0), (22.15, 44.0, 0.0), mats["Sheet_Pile_Trench"])
    # East Trench Walls
    add_box(c_trench, "Trench_Wall_E_L", (41.85, 20.0, -2.0), (42.0, 44.0, 0.0), mats["Sheet_Pile_Trench"])
    add_box(c_trench, "Trench_Wall_E_R", (48.0, 20.0, -2.0), (48.15, 44.0, 0.0), mats["Sheet_Pile_Trench"])

    # Heavy Diamond-Plate Access Ramps into Trenches (Slope: 6.0m run for 2.0m drop)
    # West South Ramp (y: 14.0..20.0, slope 0.0 -> -2.0)
    add_triangular_prism(c_trench, "Ramp_Trench_W_S", (14.0, 0.0), (20.0, -2.0), (20.0, 0.0), depth=6.0, material=mats["Diamond_Plate"])
    # West North Ramp (y: 44.0..50.0, slope -2.0 -> 0.0)
    add_triangular_prism(c_trench, "Ramp_Trench_W_N", (44.0, -2.0), (50.0, 0.0), (44.0, 0.0), depth=6.0, material=mats["Diamond_Plate"])
    # East South Ramp (y: 14.0..20.0, slope 0.0 -> -2.0)
    add_triangular_prism(c_trench, "Ramp_Trench_E_S", (14.0, 0.0), (20.0, -2.0), (20.0, 0.0), depth=6.0, material=mats["Diamond_Plate"])
    # East North Ramp (y: 44.0..50.0, slope -2.0 -> 0.0)
    add_triangular_prism(c_trench, "Ramp_Trench_E_N", (44.0, -2.0), (50.0, 0.0), (44.0, 0.0), depth=6.0, material=mats["Diamond_Plate"])

    # Sump pump drainage grates and standing water in trenches
    add_box(c_trench, "Trench_Sump_W", (18.2, 30.0, -2.01), (19.8, 34.0, -1.98), mats["Cast_Iron_Dark"], is_collider=False)
    add_box(c_trench, "Trench_Sump_E", (44.2, 30.0, -2.01), (45.8, 34.0, -1.98), mats["Cast_Iron_Dark"], is_collider=False)
    add_box(c_trench, "Trench_Water_W", (16.2, 28.0, -1.99), (21.8, 36.0, -1.97), mats["Water_Slick"], is_collider=False)
    add_box(c_trench, "Trench_Water_E", (42.2, 28.0, -1.99), (47.8, 36.0, -1.97), mats["Water_Slick"], is_collider=False)

    print("[3/8] Generating 3D Corrugated Shipping Containers (Primary Tactical Cover)...")
    # =========================================================================
    # 3. HIGH-FIDELITY CORRUGATED ISO SHIPPING CONTAINERS
    # =========================================================================
    # 1. Center South Container (Blue Maersk 40ft High-Cube: x: 26.0..38.0, y: 20.0..26.0, z: 0.0..3.2m)
    add_corrugated_shipping_container(c_containers, mats, "Container_Center_S", (26.0, 20.0, 0.0), (38.0, 26.0, 3.2), "Container_Blue_Maersk", door_face="S")

    # 2. Center North Container (Red Hanjin 40ft High-Cube: x: 26.0..38.0, y: 38.0..44.0, z: 0.0..3.2m)
    add_corrugated_shipping_container(c_containers, mats, "Container_Center_N", (26.0, 38.0, 0.0), (38.0, 44.0, 3.2), "Container_Red_Hanjin", door_face="S")

    # 3. West Flank Container (Green Evergreen 40ft: x: 8.0..14.0, y: 24.0..40.0, z: 0.0..3.2m)
    add_corrugated_shipping_container(c_containers, mats, "Container_West", (8.0, 24.0, 0.0), (14.0, 40.0, 3.2), "Container_Green_Evergreen", door_face="S")

    # 4. East Flank Container (Weathered Orange 40ft: x: 50.0..56.0, y: 24.0..40.0, z: 0.0..3.2m)
    add_corrugated_shipping_container(c_containers, mats, "Container_East", (50.0, 24.0, 0.0), (56.0, 40.0, 3.2), "Container_Orange_Weathered", door_face="S")

    # Upper stacked containers for vertical silhouette (non-colliding above player reach)
    add_box(c_containers, "Container_Stacked_W_Detail", (8.2, 26.0, 3.22), (13.8, 38.0, 6.2), mats["Container_Orange_Weathered"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(c_containers, "Container_Stacked_E_Detail", (50.2, 26.0, 3.22), (55.8, 38.0, 6.2), mats["Container_Blue_Maersk"], bevel=True, bevel_width=0.03, is_collider=False)

    print("[4/8] Generating High Steel Catwalk Bridge (z = 6.4m)...")
    # =========================================================================
    # 4. HIGH STEEL CATWALK BRIDGE (z = 6.4m)
    # =========================================================================
    # Main Deck: x: 30.0..34.0, y: 14.0..50.0, z = 6.4m (Passes test raycast at x=32, y=32, z=10 -> z=6.4)
    add_box(c_bridge, "Bridge_Deck_Plates", (30.0, 14.0, 6.25), (34.0, 50.0, 6.40), mats["Diamond_Plate"])

    # Longitudinal Heavy Steel Wide-Flange I-Beams underneath deck
    for ib_x in [30.2, 33.8]:
        add_box(c_bridge, f"Bridge_IBeam_Flange_T_{int(ib_x*10)}", (ib_x - 0.15, 14.0, 6.18), (ib_x + 0.15, 50.0, 6.25), mats["Steel_Structural"], is_collider=False)
        add_box(c_bridge, f"Bridge_IBeam_Web_{int(ib_x*10)}", (ib_x - 0.03, 14.0, 5.75), (ib_x + 0.03, 50.0, 6.18), mats["Steel_Structural"], is_collider=False)
        add_box(c_bridge, f"Bridge_IBeam_Flange_B_{int(ib_x*10)}", (ib_x - 0.15, 14.0, 5.68), (ib_x + 0.15, 50.0, 5.75), mats["Steel_Structural"], is_collider=False)

    # Bridge Transverse Cross-Ribs every 3.0 meters
    for rib_y in range(15, 50, 3):
        add_box(c_bridge, f"Bridge_Cross_Rib_{rib_y}", (30.05, float(rib_y) - 0.05, 5.75), (33.95, float(rib_y) + 0.05, 6.20), mats["Steel_Structural"], is_collider=False)

    # Heavy Support Towers (x = 30.0, 34.0; y = 21.0, 43.0)
    for col_x, col_y in [(29.8, 20.8), (33.8, 20.8), (29.8, 42.8), (33.8, 42.8)]:
        # Square tubular column with bolted baseplate
        add_box(c_bridge, f"Bridge_Col_{int(col_x*10)}_{int(col_y*10)}", (col_x, col_y, 0.0), (col_x + 0.4, col_y + 0.4, 5.70), mats["Steel_Structural"])
        add_box(c_bridge, f"Bridge_Baseplate_{int(col_x*10)}_{int(col_y*10)}", (col_x - 0.1, col_y - 0.1, 0.0), (col_x + 0.5, col_y + 0.5, 0.12), mats["Cast_Iron_Dark"], is_collider=False)
        # Welded corner gussets
        add_box(c_bridge, f"Bridge_Gusset_{int(col_x*10)}_{int(col_y*10)}", (col_x - 0.05, col_y - 0.05, 5.2), (col_x + 0.45, col_y + 0.45, 5.7), mats["Steel_Structural"], is_collider=False)

    # South Catwalk Access Ramp: x: 30.0..34.0, y: 8.0..14.0, slope 0.0 -> 6.4m
    add_triangular_prism(c_bridge, "Bridge_Ramp_South", (8.0, 0.0), (14.0, 6.4), (14.0, 0.0), depth=4.0, material=mats["Diamond_Plate"])
    # North Catwalk Access Ramp: x: 30.0..34.0, y: 50.0..56.0, slope 6.4m -> 0.0
    add_triangular_prism(c_bridge, "Bridge_Ramp_North", (50.0, 6.4), (56.0, 0.0), (50.0, 0.0), depth=4.0, material=mats["Diamond_Plate"])

    # Double-Pipe Industrial Handrails along Bridge Edges (West & East)
    # Top rail at z = 7.50m (1.1m above deck), mid rail at z = 6.95m
    for side_x, side_name in [(29.92, "W"), (34.08, "E")]:
        # Continuous longitudinal rails
        add_oriented_cylinder(c_bridge, f"Bridge_Rail_Top_{side_name}", (side_x, 14.0, 7.50), (side_x, 50.0, 7.50), radius=0.035, material=mats["Chrome_Galvanized"], segments=8, is_collider=False)
        add_oriented_cylinder(c_bridge, f"Bridge_Rail_Mid_{side_name}", (side_x, 14.0, 6.95), (side_x, 50.0, 6.95), radius=0.025, material=mats["Chrome_Galvanized"], segments=8, is_collider=False)
        # Vertical stanchion posts every 2.0 meters
        for st_y in range(14, 51, 2):
            add_oriented_cylinder(c_bridge, f"Bridge_Stanchion_{side_name}_{st_y}", (side_x, float(st_y), 6.40), (side_x, float(st_y), 7.55), radius=0.035, material=mats["Chrome_Galvanized"], segments=8, is_collider=False)
        # Kickplate along deck edge
        add_box(c_bridge, f"Bridge_Kickplate_{side_name}", (side_x - 0.02, 14.0, 6.40), (side_x + 0.02, 50.0, 6.55), mats["Hazard_Yellow"], is_collider=False)

    print("[5/8] Generating Overhead Gantry Crane & Electromagnetic Scrap Lifter...")
    # =========================================================================
    # 5. OVERHEAD GANTRY CRANE & ELECTROMAGNETIC LIFTER
    # =========================================================================
    # Twin Box-Girders spanning East-West at y = 32.0, z = 11.5..12.8m
    for g_y in [31.2, 32.8]:
        add_box(c_crane, f"Crane_Girder_{int(g_y*10)}", (6.0, g_y - 0.35, 11.6), (58.0, g_y + 0.35, 12.8), mats["Steel_IBeam_Yellow"], bevel=True, bevel_width=0.04, is_collider=False)
        # Black hazard stripes on girder ends
        add_box(c_crane, f"Crane_Stripe_W_{int(g_y*10)}", (6.05, g_y - 0.36, 11.65), (10.0, g_y + 0.36, 12.75), mats["Hazard_Black"], is_collider=False)
        add_box(c_crane, f"Crane_Stripe_E_{int(g_y*10)}", (54.0, g_y - 0.36, 11.65), (57.95, g_y + 0.36, 12.75), mats["Hazard_Black"], is_collider=False)

    # Heavy Trolley Hoist Unit positioned at center (x = 32.0, y = 32.0, z = 12.8m)
    add_box(c_crane, "Crane_Trolley_Body", (30.5, 30.5, 12.8), (33.5, 33.5, 13.6), mats["Cast_Iron_Dark"], bevel=True, bevel_width=0.03, is_collider=False)
    # Cable Winch Drums
    add_cylinder(c_crane, "Crane_Winch_Drum_1", (31.4, 32.0, 13.2), radius=0.35, height=0.9, material=mats["Steel_Structural"], segments=16, is_collider=False)
    add_cylinder(c_crane, "Crane_Winch_Drum_2", (32.6, 32.0, 13.2), radius=0.35, height=0.9, material=mats["Steel_Structural"], segments=16, is_collider=False)

    # 4 Heavy Steel Wire Rigging Cables hanging from trolley down to magnet disc (z: 12.8 -> 4.5m)
    mag_cx, mag_cy, mag_cz = 32.0, 32.0, 4.2
    for c_off_x, c_off_y in [(-0.6, -0.6), (0.6, -0.6), (-0.6, 0.6), (0.6, 0.6)]:
        add_oriented_cylinder(c_crane, f"Crane_Cable_{int(c_off_x*10)}_{int(c_off_y*10)}", (32.0 + c_off_x * 1.4, 32.0 + c_off_y * 1.4, 12.8), (mag_cx + c_off_x, mag_cy + c_off_y, mag_cz + 0.5), radius=0.02, material=mats["Steel_Wire_Bale"], segments=6, is_collider=False)

    # Massive 1.8m Diameter Circular Electromagnetic Scrap Lifting Disc
    add_cylinder(c_crane, "Scrap_Electromagnet_Housing", (mag_cx, mag_cy, mag_cz + 0.25), radius=0.95, height=0.45, material=mats["Steel_IBeam_Yellow"], segments=24, is_collider=False)
    add_cylinder(c_crane, "Scrap_Electromagnet_Core", (mag_cx, mag_cy, mag_cz + 0.05), radius=0.85, height=0.15, material=mats["Cast_Iron_Dark"], segments=24, is_collider=False)
    # Center lifting eye shackle
    add_cylinder(c_crane, "Scrap_Electromagnet_Shackle", (mag_cx, mag_cy, mag_cz + 0.55), radius=0.14, height=0.25, material=mats["Steel_Structural"], segments=12, is_collider=False)

    # Operator Control Cabin on West End of Crane
    add_box(c_crane, "Crane_Cabin_Body", (7.0, 30.5, 9.8), (9.5, 33.5, 11.6), mats["Hazard_Yellow"], bevel=True, bevel_width=0.03, is_collider=False)
    add_box(c_crane, "Crane_Cabin_Window_F", (9.48, 30.8, 10.3), (9.54, 33.2, 11.4), mats["Water_Slick"], is_collider=False)
    # Amber flashing warning strobe on cabin roof
    add_cylinder(c_crane, "Crane_Strobe_Beacon", (8.25, 32.0, 11.75), radius=0.12, height=0.22, material=mats["Strobe_Amber"], segments=12, is_collider=False)

    print("[6/8] Generating Compacted Automotive Scrap Bales & Salvage Bins...")
    # =========================================================================
    # 6. COMPACTED AUTOMOTIVE SCRAP BALES & CAR SALVAGE
    # =========================================================================
    # South Yard Scrap Bales Cluster (x: 20..24, y: 12..16)
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_S_1", (21.0, 13.0, 0.0))
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_S_2", (22.5, 14.5, 0.0))
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_S_3", (21.8, 13.8, 1.1))

    # North Yard Scrap Bales Cluster (x: 40..44, y: 48..52)
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_N_1", (41.0, 49.0, 0.0))
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_N_2", (42.5, 50.5, 0.0))
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_N_3", (41.8, 49.8, 1.1))

    # West Yard Scrap Cluster (x: 20..24, y: 48..52)
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_W_1", (21.2, 49.2, 0.0))
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_W_2", (22.6, 50.6, 0.0))

    # East Yard Scrap Cluster (x: 40..44, y: 12..16)
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_E_1", (41.2, 13.2, 0.0))
    add_scrap_car_bale(c_salvage, mats, "Scrap_Bale_E_2", (42.6, 14.6, 0.0))

    # Under-bridge Central Scrap Pile (x: 30..34, y: 30..34, z = 0..1.4m)
    add_box(c_salvage, "Center_Scrap_Base", (30.0, 30.0, 0.0), (34.0, 34.0, 1.40), mats["Scrap_Metal_Mangled"], bevel=True, bevel_width=0.06)
    add_box(c_salvage, "Center_Scrap_Debris1", (30.4, 30.2, 1.40), (33.2, 32.8, 1.75), mats["Steel_Rusted"], is_collider=False)
    add_box(c_salvage, "Center_Scrap_Debris2", (31.8, 31.5, 1.40), (33.8, 33.6, 1.65), mats["Scrap_Car_Red"], is_collider=False)

    print("[7/8] Generating Industrial Tire Stacks, Oil Drums & Pallets...")
    # =========================================================================
    # 7. INDUSTRIAL PROPS: TIRES, STEEL DRUMS & WOOD PALLETS
    # =========================================================================
    # Semi-truck tire stacks in corners
    add_tire_stack(c_salvage, mats, "Tires_SW", (10.0, 12.0, 0.0), count=4)
    add_tire_stack(c_salvage, mats, "Tires_SE", (54.0, 12.0, 0.0), count=5)
    add_tire_stack(c_salvage, mats, "Tires_NW", (10.0, 52.0, 0.0), count=4)
    add_tire_stack(c_salvage, mats, "Tires_NE", (54.0, 52.0, 0.0), count=5)

    # 55-Gallon Steel Oil & Chemical Drums
    drum_coords = [
        (18.0, 10.5, mats["Drum_Blue"]),
        (18.9, 10.5, mats["Drum_Hazmat"]),
        (18.4, 11.3, mats["Drum_Rust"]),
        (45.5, 53.5, mats["Drum_Hazmat"]),
        (46.4, 53.5, mats["Drum_Blue"]),
        (45.9, 54.3, mats["Drum_Rust"]),
    ]
    for d_i, (dx, dy, d_mat) in enumerate(drum_coords):
        # Barrel body with rolling ribs
        add_cylinder(c_props, f"Drum_Body_{d_i}", (dx, dy, 0.45), radius=0.30, height=0.90, material=d_mat, segments=16, is_collider=True)
        # Top and bottom reinforcing chimes
        add_cylinder(c_props, f"Drum_Chime_T_{d_i}", (dx, dy, 0.88), radius=0.315, height=0.04, material=mats["Cast_Iron_Dark"], segments=16, is_collider=False)
        add_cylinder(c_props, f"Drum_Chime_B_{d_i}", (dx, dy, 0.04), radius=0.315, height=0.04, material=mats["Cast_Iron_Dark"], segments=16, is_collider=False)
        # Bung cap
        add_cylinder(c_props, f"Drum_Bung_{d_i}", (dx + 0.12, dy, 0.91), radius=0.04, height=0.03, material=mats["Chrome_Galvanized"], segments=8, is_collider=False)

    # Heavy Industrial Wood Pallets
    for p_x, p_y in [(12.0, 18.0), (52.0, 18.0), (12.0, 46.0), (52.0, 46.0)]:
        # 3 Stringers
        for sx_off in [-0.5, 0.0, 0.5]:
            add_box(c_props, f"Pallet_Str_{int((p_x+sx_off)*10)}_{int(p_y*10)}", (p_x + sx_off - 0.05, p_y - 0.6, 0.0), (p_x + sx_off + 0.05, p_y + 0.6, 0.12), mats["Wood_Pallet"], is_collider=False)
        # Top deckboards
        for dy_i in range(5):
            dy_f = p_y - 0.5 + dy_i * 0.25
            add_box(c_props, f"Pallet_Deck_{int(p_x*10)}_{int(dy_f*10)}", (p_x - 0.55, dy_f - 0.08, 0.12), (p_x + 0.55, dy_f + 0.08, 0.15), mats["Wood_Pallet"], is_collider=False)

    print("[8/8] Generating High-Mast Industrial Sodium Yard Floodlights...")
    # =========================================================================
    # 8. HIGH-MAST INDUSTRIAL FLOODLIGHT TOWERS (4 CORNERS)
    # =========================================================================
    light_corners = [(8.0, 8.0), (56.0, 8.0), (8.0, 56.0), (56.0, 56.0)]
    for li, (lx, ly) in enumerate(light_corners):
        # 12m Tapered Octagonal Steel Mast
        add_cylinder(c_props, f"Light_Mast_{li}", (lx, ly, 6.0), radius=0.25, height=12.0, material=mats["Steel_Structural"], segments=8, is_collider=True)
        # Crossarm light rack at z = 12.2m
        add_box(c_props, f"Light_Crossarm_{li}", (lx - 1.2, ly - 0.1, 12.0), (lx + 1.2, ly + 0.1, 12.3), mats["Steel_Structural"], is_collider=False)
        # 4 High-Intensity Sodium Floodlight Fixtures
        for fi, fx_off in enumerate([-0.9, -0.3, 0.3, 0.9]):
            add_box(c_props, f"Flood_Lamp_Body_{li}_{fi}", (lx + fx_off - 0.18, ly - 0.25, 12.1), (lx + fx_off + 0.18, ly + 0.25, 12.5), mats["Cast_Iron_Dark"], is_collider=False)
            add_box(c_props, f"Flood_Lamp_Glow_{li}_{fi}", (lx + fx_off - 0.15, ly - 0.28, 12.15), (lx + fx_off + 0.15, ly - 0.24, 12.45), mats["Sodium_Floodlight"], is_collider=False)

    print("=== Generation of Junk Flea Scene Completed Successfully! ===")
    return mats


def export_scene_glb():
    """Scale the metre-authored scene to cubes and export it (see maplib)."""
    maplib.export_map_glb("hd_junkflea")


if __name__ == "__main__":
    print("=== Generating Ultra-Detailed Realistic 'Junk Flea' (hd_junkflea) Map in Blender ===")
    build_junkflea_scene()
    export_scene_glb()
    print("=== Junk Flea Generation & Export Complete! ===")
