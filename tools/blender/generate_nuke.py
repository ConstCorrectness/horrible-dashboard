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
import cutlayout  # noqa: E402
import maplib  # noqa: E402  (a sibling, found via the path above)
import nuke_layout  # noqa: E402
import props  # noqa: E402


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
    # Every name carries the map's own `nuke_` infix, so the photographed-surface
    # rules in glb-surfaces.json can pick out Nuke's concrete and steel without
    # touching another map's `mat_concrete_wall` or `mat_container_red`.
    mats = {}
    mats["concrete_floor"] = create_pbr_material("mat_nuke_concrete_floor", (0.58, 0.60, 0.62), metallic=0.02, roughness=0.78, bump_strength=0.15, bump_scale=20.0)
    mats["concrete_dark"] = create_pbr_material("mat_nuke_concrete_dark", (0.38, 0.40, 0.42), metallic=0.04, roughness=0.82, bump_strength=0.18, bump_scale=18.0)
    mats["concrete_wall"] = create_pbr_material("mat_nuke_concrete_wall", (0.72, 0.74, 0.76), metallic=0.01, roughness=0.82, bump_strength=0.14, bump_scale=22.0)
    mats["hazard_yellow"] = create_pbr_material("mat_nuke_hazard_yellow", (0.88, 0.76, 0.12), metallic=0.45, roughness=0.35, bump_strength=0.08, bump_scale=30.0)
    mats["hazard_black"] = create_pbr_material("mat_nuke_hazard_black", (0.16, 0.16, 0.18), metallic=0.2, roughness=0.6, bump_strength=0.08, bump_scale=30.0)
    mats["steel_industrial"] = create_pbr_material("mat_nuke_steel_industrial", (0.42, 0.45, 0.48), metallic=0.85, roughness=0.35, bump_strength=0.12, bump_scale=32.0)
    mats["steel_dark"] = create_pbr_material("mat_nuke_steel_dark", (0.24, 0.26, 0.28), metallic=0.90, roughness=0.30, bump_strength=0.12, bump_scale=32.0)
    mats["steel_grate"] = create_pbr_material("mat_nuke_steel_grate", (0.46, 0.48, 0.50), metallic=0.80, roughness=0.40, bump_strength=0.20, bump_scale=40.0)
    mats["reactor_glow_cyan"] = create_pbr_material("mat_nuke_reactor_glow_cyan", (0.10, 0.88, 0.98), emission_color=(0.10, 0.88, 0.98), emission_strength=6.0)
    mats["reactor_hull"] = create_pbr_material("mat_nuke_reactor_hull", (0.30, 0.34, 0.38), metallic=0.82, roughness=0.28, bump_strength=0.15, bump_scale=24.0)
    mats["coolant_blue"] = create_pbr_material("mat_nuke_coolant_blue", (0.18, 0.40, 0.75), metallic=0.6, roughness=0.35)
    mats["pipe_yellow"] = create_pbr_material("mat_nuke_pipe_yellow", (0.85, 0.68, 0.10), metallic=0.5, roughness=0.4, bump_strength=0.08, bump_scale=28.0)
    mats["container_red"] = create_pbr_material("mat_nuke_container_red", (0.68, 0.18, 0.14), metallic=0.35, roughness=0.55, bump_strength=0.15, bump_scale=25.0)
    mats["container_blue"] = create_pbr_material("mat_nuke_container_blue", (0.16, 0.30, 0.60), metallic=0.35, roughness=0.55, bump_strength=0.15, bump_scale=25.0)
    mats["container_green"] = create_pbr_material("mat_nuke_container_green", (0.18, 0.45, 0.26), metallic=0.35, roughness=0.55, bump_strength=0.15, bump_scale=25.0)
    mats["wood_crate"] = create_pbr_material("mat_nuke_wood_crate", (0.54, 0.40, 0.26), metallic=0.0, roughness=0.65, bump_strength=0.25, bump_scale=14.0)
    mats["light_fluor"] = create_pbr_material("mat_nuke_light_fluor", (0.95, 0.98, 1.0), emission_color=(0.95, 0.98, 1.0), emission_strength=4.0)
    mats["insulator_ceramic"] = create_pbr_material("mat_nuke_insulator_ceramic", (0.65, 0.38, 0.25), metallic=0.1, roughness=0.3)
    mats["glass_window"] = create_pbr_material("mat_nuke_glass_window", (0.80, 0.92, 0.98), metallic=0.05, roughness=0.05, alpha=0.32)
    # The rule-placed clutter's own: steel drums and sandbags.
    mats["drum_blue"] = create_pbr_material("mat_nuke_drum_blue", (0.20, 0.32, 0.52), metallic=0.55, roughness=0.5)
    mats["sandbag"] = create_pbr_material("mat_nuke_sandbag", (0.56, 0.50, 0.38), metallic=0.0, roughness=0.9)
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


def add_shipping_container(collection, name_prefix, center, material, mats):
    """Detailed intermodal ISO shipping container with corrugated ribs, corner castings, and latch doors.

    Always long along X. This once took a yaw and turned only the trim, never the
    body: the ribs, castings and lock rods of a 90° container stood in a line across
    open yard with the box lying the other way, and a body walked through them.
    """
    cx, cy, cz = center
    # Main container body (6.0m L x 2.44m W x 2.59m H)
    L, W, H = 6.0, 2.44, 2.59

    # Base box
    add_box(collection, f"{name_prefix}_Body", (cx, cy, cz + H * 0.5), (L, W, H), material)

    # Corrugated side ribs (decorative NonCol)
    num_ribs = 9
    for i in range(num_ribs):
        rx = cx - L * 0.45 + (L * 0.9 / (num_ribs - 1)) * i
        add_box(collection, f"{name_prefix}_RibL_{i}_NonCol", (rx, cy - W * 0.51, cz + H * 0.5), (0.1, 0.08, H * 0.94), material)
        add_box(collection, f"{name_prefix}_RibR_{i}_NonCol", (rx, cy + W * 0.51, cz + H * 0.5), (0.1, 0.08, H * 0.94), material)

    # Corner casting blocks
    for dx in [-L * 0.48, L * 0.48]:
        for dy in [-W * 0.48, W * 0.48]:
            for dz in [0.15, H - 0.15]:
                add_box(collection, f"{name_prefix}_Cast_NonCol", (cx + dx, cy + dy, cz + dz), (0.24, 0.24, 0.28), mats["steel_dark"])

    # Rear door locking cam rods
    for rod_dy in [-W * 0.2, W * 0.2]:
        add_cylinder(collection, f"{name_prefix}_LockRod_NonCol", (cx - L * 0.51, cy + rod_dy, cz + H * 0.5), 0.04, H * 0.88, mats["steel_industrial"], segments=8)


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


# ---------------------------------------------------------------------------
# Layout: the map is cut along a few empty lines and the halves spread apart (see
# `cutlayout.py`, and `nuke_layout.py` for the cuts). Every primitive goes through
# the warper; the composites move as one by their anchor point.
# ---------------------------------------------------------------------------
W = cutlayout.Warper(nuke_layout.CUTS)

add_box = W.box(add_box)
add_cylinder = W.cylinder(add_cylinder)
add_boxes = W.boxes(maplib.add_boxes)
add_plinths = W.plinths(maplib.add_plinths)
add_wall_with_door = W.wall_with_door(maplib.add_wall_with_door)
add_wedge = W.wedge(maplib.add_wedge)
add_stairs = W.stairs(maplib.add_stairs)
add_light = W.light(maplib.add_light)
add_pipe_run = W.line(add_pipe_run, 2, 3)
add_shipping_container = W.composite(add_shipping_container, 2)


def build_nuke_perimeter_and_ground(col, mats):
    # The ground (70m x 70m), less the holes B, the ramp and the vent's drop go
    # down through: the server keeps one floor per column, so anything below ground
    # level is open to the sky above it or roofed by a building, never under a
    # walkable surface.
    ground = [
        (0.0, 70.0, 0.0, 8.0),
        (0.0, 70.0, 39.4, 70.0),
        (0.0, 8.6, 8.0, 39.4),
        (8.6, 16.6, 8.0, 20.6),
        (22.6, 70.0, 8.0, 39.4),
        (21.4, 22.6, 8.0, 30.2),
        (21.4, 22.6, 31.8, 39.4),
    ]
    for i, (x0, x1, y0, y1) in enumerate(ground):
        add_box(
            col,
            f"Terrain_Concrete_Ground_{i}",
            ((x0 + x1) / 2, (y0 + y1) / 2, -0.5),
            (x1 - x0, y1 - y0, 1.0),
            mats["concrete_floor"],
        )

    # High Perimeter Concrete Security Walls (Height 12m)
    add_box(
        col,
        "Wall_Perimeter_S",
        (35.0, 4.0, 6.0),
        (62.0, 1.2, 12.0),
        mats["concrete_wall"],
    )
    add_box(
        col,
        "Wall_Perimeter_N",
        (35.0, 66.0, 6.0),
        (62.0, 1.2, 12.0),
        mats["concrete_wall"],
    )
    add_box(
        col,
        "Wall_Perimeter_E",
        (66.0, 35.0, 6.0),
        (1.2, 62.0, 12.0),
        mats["concrete_wall"],
    )
    add_box(
        col,
        "Wall_Perimeter_W",
        (4.0, 35.0, 6.0),
        (1.2, 62.0, 12.0),
        mats["concrete_wall"],
    )

    # Yellow Hazard Security Trim along top
    add_box(
        col, "Hazard_Trim_S", (35.0, 4.6, 11.6), (62.0, 0.4, 0.6), mats["hazard_yellow"]
    )
    add_box(
        col,
        "Hazard_Trim_N",
        (35.0, 65.4, 11.6),
        (62.0, 0.4, 0.6),
        mats["hazard_yellow"],
    )
    add_box(
        col,
        "Hazard_Trim_E",
        (65.4, 35.0, 11.6),
        (0.4, 62.0, 0.6),
        mats["hazard_yellow"],
    )
    add_box(
        col, "Hazard_Trim_W", (4.6, 35.0, 11.6), (0.4, 62.0, 0.6), mats["hazard_yellow"]
    )


def build_site_a_upper_hall(col, mats):
    """Bomb Site A Upper Reactor Hall at Z=0.0m with Hut, Rafters, and Crane."""
    # Outer containment building walls (X: 22..50, Y: 28..52, Z: 0..8.0)
    # Doorways on the T (south), CT (north), west and east sides. The hall was once
    # four unbroken 8 m walls: Site A was sealed, reachable by nobody. The east door
    # ("Squeaky") is T's second way in, from the yard by the garage.
    add_wall_with_door(
        col,
        "SiteA_Wall_N",
        (36.0, 52.0, 4.0),
        (28.0, 1.2, 8.0),
        mats["concrete_wall"],
        30.0,
        3.5,
        3.2,
    )
    add_wall_with_door(
        col,
        "SiteA_Wall_E",
        (50.0, 40.0, 4.0),
        (1.2, 24.0, 8.0),
        mats["concrete_wall"],
        34.0,
        3.0,
        3.2,
    )
    # West: the door to the yard, and at the south end, at the wall's foot, the
    # vent (see below).
    add_box(col, "SiteA_Wall_W_S", (22.0, 29.1, 4.0), (1.2, 2.2, 8.0), mats["concrete_wall"])
    add_box(col, "SiteA_Wall_W_Vent_Lintel", (22.0, 31.0, 5.2), (1.2, 1.6, 5.6), mats["concrete_wall"])
    add_wall_with_door(
        col,
        "SiteA_Wall_W",
        (22.0, 41.9, 4.0),
        (1.2, 20.2, 8.0),
        mats["concrete_wall"],
        44.0,
        3.5,
        3.2,
    )
    add_wall_with_door(
        col,
        "SiteA_Wall_S",
        (36.0, 28.0, 4.0),
        (28.0, 1.2, 8.0),
        mats["concrete_wall"],
        40.0,
        3.5,
        3.2,
    )

    # Hall ceiling at Z=8.0
    add_box(
        col,
        "SiteA_Roof_Deck",
        (36.0, 40.0, 8.2),
        (28.0, 24.0, 0.4),
        mats["concrete_dark"],
    )

    # Overhead high-bay fluorescent light troffers (NonCol)
    for lx in [28.0, 36.0, 44.0]:
        for ly in [34.0, 46.0]:
            add_box(
                col,
                f"SiteA_Light_Fluor_{int(lx)}_{int(ly)}_NonCol",
                (lx, ly, 7.85),
                (2.4, 0.6, 0.2),
                mats["light_fluor"],
            )

    # Hut Structure inside Site A (X: 24..28, Y: 34..40, Z: 0..2.8)
    add_box(
        col, "SiteA_Hut_Roof", (26.0, 37.0, 2.7), (4.4, 6.4, 0.2), mats["concrete_wall"]
    )
    add_box(
        col,
        "SiteA_Hut_Wall_W",
        (24.0, 37.0, 1.3),
        (0.4, 6.0, 2.6),
        mats["concrete_wall"],
    )
    add_box(
        col,
        "SiteA_Hut_Wall_S",
        (26.0, 34.0, 1.3),
        (4.0, 0.4, 2.6),
        mats["concrete_wall"],
    )
    add_box(
        col,
        "SiteA_Hut_Doorframe",
        (28.0, 37.0, 2.3),
        (0.4, 6.0, 0.6),
        mats["hazard_yellow"],
    )

    # Hut window opening on East wall. A window needs wall under it: without this
    # the pane and sill hung in the hut's open side and a body stood in the glass.
    add_box(
        col,
        "SiteA_Hut_Wall_E_Low",
        (28.0, 38.5, 0.525),
        (0.4, 1.8, 1.05),
        mats["concrete_wall"],
    )
    add_box(
        col,
        "SiteA_Hut_Window_Sill",
        (28.0, 38.5, 1.1),
        (0.4, 1.8, 0.1),
        mats["steel_industrial"],
    )
    add_box(
        col,
        "Window_Glass_SiteA_Hut",
        (28.0, 38.5, 1.6),
        (0.1, 1.8, 0.9),
        mats["glass_window"],
    )

    # No control-room windows. The two this hall had stood in the open yard with no
    # wall around them, one of them inside Container_Green_1. A window needs a wall
    # with an opening; add both together.

    # Overhead Rafters / Catwalks at Z = 3.8m
    add_box(
        col,
        "SiteA_Catwalk_East",
        (48.0, 40.0, 3.8),
        (2.0, 22.0, 0.2),
        mats["steel_grate"],
    )
    add_box(
        col,
        "SiteA_Catwalk_North",
        (36.0, 50.0, 3.8),
        (22.0, 2.0, 0.2),
        mats["steel_grate"],
    )
    add_box(
        col,
        "SiteA_Catwalk_Railing_E",
        (47.0, 40.0, 4.4),
        (0.1, 22.0, 1.0),
        mats["hazard_yellow"],
    )
    add_box(
        col,
        "SiteA_Catwalk_Railing_N",
        (36.0, 49.0, 4.4),
        (22.0, 0.1, 1.0),
        mats["hazard_yellow"],
    )

    # Yellow Gantry Crane Beam across ceiling at Z = 6.8m
    add_gantry_crane(col, mats)

    # Plant Marker A Disc at (36.0, 40.0, 0.0) with hazard chevron ring
    add_cylinder(
        col,
        "SiteA_Plant_Marker",
        (36.0, 40.0, 0.05),
        radius=2.5,
        height=0.1,
        material=mats["hazard_yellow"],
        segments=20,
    )
    add_cylinder(
        col,
        "SiteA_Plant_Inner_Marker_NonCol",
        (36.0, 40.0, 0.07),
        radius=1.8,
        height=0.06,
        material=mats["hazard_black"],
        segments=20,
    )

    # The vent: an opening 2.4 m tall at the foot of the west wall, straight into
    # B's air. Step through and drop 4.5 m; nothing climbs back. It was a hatch
    # over a shaft and a crawl once, and a bot that fell in never left: a pocket
    # whose only way out points away from where the bot wants to go.
    add_boxes(
        col,
        "SiteA_Vent_Frame_NonCol",
        [
            ((22.63, 30.1, 1.2), (0.06, 0.2, 2.4)),
            ((22.63, 31.9, 1.2), (0.06, 0.2, 2.4)),
            ((22.63, 31.0, 2.5), (0.06, 1.8, 0.2)),
        ],
        mats["hazard_yellow"],
    )


def build_site_b_lower_silo(col, mats):
    """Bomb Site B: the reactor room, sunk 4.5 m, against A's west wall.

    It sat directly under A once, sealed under the ground: the server's heightfield
    keeps one floor per column, so A's floor would have been B's ceiling. Here it
    is a building of its own whose floor is the pit: T come down the ramp from the
    south yard, CT down the stairs from the north door, and anyone can drop in from
    A through the vent, which comes out of the shared wall.
    """
    concrete, dark = mats["concrete_wall"], mats["concrete_dark"]
    # The pit floor runs under the ramp and the vent crawl too.
    add_box(
        col,
        "Pit_SiteB_Floor",
        (15.0, 24.3, -4.7),
        (15.2, 32.6, 0.4),
        mats["concrete_floor"],
    )

    # Walls from the pit floor to the roof at 5 m.
    add_box(col, "SiteB_Wall_W", (8.0, 30.0, 0.05), (1.2, 21.2, 9.9), concrete)
    add_box(col, "SiteB_Wall_S", (12.0, 20.0, 0.05), (9.2, 1.2, 9.9), concrete)
    add_box(col, "SiteB_Wall_S_Lintel", (19.0, 20.0, 2.5), (4.8, 1.2, 5.0), concrete)
    add_box(col, "SiteB_Wall_N_Low", (14.4, 40.0, -2.45), (14.0, 1.2, 4.9), concrete)
    add_wall_with_door(
        col,
        "SiteB_Wall_N_Door",
        (14.4, 40.0, 2.5),
        (14.0, 1.2, 5.0),
        concrete,
        19.8,
        2.8,
        3.2,
    )
    # East: the ramp's wall carried on to the hall's corner, then (under the hall's
    # west wall) only below ground, open where the vent comes through.
    add_box(col, "SiteB_Wall_E", (22.0, 18.0, 0.05), (1.2, 20.0, 9.9), concrete)
    add_box(col, "SiteB_Wall_E_Below_S", (22.0, 29.1, -2.95), (1.2, 2.2, 3.9), dark)
    add_box(col, "SiteB_Wall_E_Below_N", (22.0, 36.2, -2.95), (1.2, 8.8, 3.9), dark)
    add_box(col, "SiteB_Vent_Backplate", (22.8, 31.0, -2.95), (0.4, 1.6, 3.9), dark)
    add_box(col, "SiteB_Roof", (14.4, 30.0, 5.2), (14.0, 21.2, 0.4), dark)

    # The stairs climb north up the east wall, straight to the north door, so a
    # body leaving B toward A (or a bot steering at A) walks onto their foot and
    # not into their side.
    add_stairs(col, "SiteB_Stairs", (19.9, 32.4, -4.5), 3.0, 4.5, 7.0, 15, "+y", mats["concrete_floor"])

    # The reactor: a solid pillar in the room, with its glow band and pipes.
    rx, ry = 13.5, 28.5
    add_cylinder(
        col,
        "Reactor_Base_Pedestal",
        (rx, ry, -4.1),
        radius=2.4,
        height=0.8,
        material=mats["concrete_floor"],
        segments=24,
    )
    add_cylinder(
        col,
        "Reactor_Pressure_Vessel",
        (rx, ry, -1.75),
        radius=2.0,
        height=5.5,
        material=mats["reactor_hull"],
        segments=24,
    )
    add_cylinder(
        col,
        "Reactor_Core_Glow_Ring_NonCol",
        (rx, ry, -2.0),
        radius=2.06,
        height=0.8,
        material=mats["reactor_glow_cyan"],
        segments=24,
    )
    add_cylinder(
        col,
        "Reactor_Top_Dome",
        (rx, ry, 1.25),
        radius=1.8,
        height=0.5,
        material=mats["hazard_yellow"],
        segments=20,
    )
    for ang_deg in [0, 60, 120, 180, 240, 300]:
        rad = math.radians(ang_deg)
        add_cylinder(
            col,
            f"Reactor_Cooling_Pipe_{ang_deg}",
            (rx + math.cos(rad) * 2.2, ry + math.sin(rad) * 2.2, -1.75),
            radius=0.15,
            height=5.2,
            material=mats["pipe_yellow"],
            segments=10,
        )

    # Coolant tanks, the room's other cover.
    # The west tank stands against the wall (a slot behind it is a place to stick);
    # the north one clear of the stairs' foot, so CT can walk off them.
    for name, (tx, ty, tr) in (("W", (10.0, 23.0, 1.4)), ("N", (15.5, 33.9, 1.2))):
        add_cylinder(
            col,
            f"Coolant_Tank_{name}",
            (tx, ty, -3.0),
            radius=tr,
            height=3.0,
            material=mats["coolant_blue"],
            segments=16,
        )
        add_cylinder(
            col,
            f"Coolant_Tank_{name}_Dome_NonCol",
            (tx, ty, -1.3),
            radius=tr - 0.1,
            height=0.4,
            material=mats["steel_industrial"],
            segments=16,
        )
        add_pipe_run(
            col,
            f"Coolant_Run_{name}",
            (tx, ty, -1.0),
            (rx, ry, -1.0),
            0.14,
            mats["coolant_blue"],
            False,
        )

    # Crates against the east wall, by the plant.
    for i, (x, y, z, s) in enumerate(
        (
            (20.8, 25.0, -4.5, 1.2),
            (20.8, 26.3, -4.5, 1.2),
            (20.8, 25.6, -3.3, 1.1),
            (16.0, 22.4, -4.5, 1.1),
        )
    ):
        add_box(
            col, f"SiteB_Crate_{i}", (x, y, z + s / 2), (s, s, s), mats["wood_crate"]
        )

    # Decon portal on the west wall (a frame, nothing behind it).
    add_box(
        col,
        "SiteB_Decon_Frame_NonCol",
        (8.75, 29.0, -3.0),
        (0.1, 4.0, 3.0),
        mats["hazard_yellow"],
    )
    add_box(
        col,
        "SiteB_Decon_Doors_NonCol",
        (8.7, 29.0, -3.0),
        (0.1, 3.2, 2.8),
        mats["steel_grate"],
    )

    # Ceiling troffers.
    for lx in (11.5, 17.5):
        for ly in (23.5, 31.5):
            add_box(
                col,
                f"SiteB_Light_Fluor_{int(lx)}_{int(ly)}_NonCol",
                (lx, ly, 4.85),
                (2.4, 0.6, 0.2),
                mats["light_fluor"],
            )

    # Plant Marker B, beside the reactor.
    add_cylinder(
        col,
        "SiteB_Plant_Marker",
        (17.5, 27.0, -4.45),
        radius=3.0,
        height=0.08,
        material=mats["hazard_yellow"],
        segments=24,
    )


def build_connecting_ramps_and_vents(col, mats):
    """The ramp down into B from the south yard."""
    # Ramp room: a roofed slope from the yard (z=0, y=8) to B's floor (y=20.6).
    add_wedge(
        col,
        "Ramp_Floor",
        (19.0, 14.3, -2.25),
        (4.8, 12.6, 4.5),
        mats["concrete_floor"],
        "-y",
    )
    # The west wall: solid below the yard, and above it a breach (the "secret")
    # where the west yard drops 2 m onto the ramp. One way: too high to climb
    # back. Without it the ramp room was 20 m of wall between the yard and B.
    add_box(
        col, "Ramp_Wall_W_Low", (16.0, 13.7, -2.45), (1.2, 11.4, 4.9), mats["concrete_wall"]
    )
    add_wall_with_door(
        col, "Ramp_Wall_W", (16.0, 13.7, 1.95), (1.2, 11.4, 3.9), mats["concrete_wall"], 17.5, 2.6, 3.2
    )
    add_box(
        col, "Ramp_Roof", (19.0, 13.7, 3.7), (7.2, 11.4, 0.4), mats["concrete_dark"]
    )
    for ly in (11.0, 17.0):
        add_box(
            col,
            f"Ramp_Light_Fluor_{int(ly)}_NonCol",
            (19.0, ly, 3.4),
            (0.6, 2.4, 0.2),
            mats["light_fluor"],
        )


def build_outside_yard(col, mats):
    """Outside Yard with Silo tower, shipping containers, electrical substation, and Garage."""
    # Silo Tower at (16.0, 20.0, 0.0)
    add_cylinder(col, "Outside_Silo_Base", (9.5, 15.0, 3.0), radius=2.8, height=6.0, material=mats["concrete_wall"], segments=24)
    add_cylinder(col, "Outside_Silo_Dome", (9.5, 15.0, 6.3), radius=2.6, height=0.8, material=mats["hazard_yellow"], segments=20)
    # Silo external safety cage ladder (NonCol)
    for step in range(12):
        sz = 0.5 + step * 0.45
        add_box(col, f"Silo_Ladder_Rung_{step}_NonCol", (12.4, 15.0, sz), (0.05, 0.6, 0.04), mats["hazard_yellow"])

    # Red, Blue, and Green Shipping Containers in Yard
    add_shipping_container(col, "Container_Red_1", (28.0, 9.0, 0.0), mats["container_red"], mats)
    add_shipping_container(col, "Container_Blue_1", (11.0, 44.5, 0.0), mats["container_blue"], mats)
    add_shipping_container(col, "Container_Green_1", (10.5, 54.0, 0.0), mats["container_green"], mats)

    # Electrical Substation in North Yard
    add_substation(col, mats)

    # Garage Structure at East Yard (X: 52..60, Y: 40..52, Z: 0..5.0)
    add_box(col, "Garage_Building", (56.0, 46.0, 2.5), (8.0, 12.0, 5.0), mats["concrete_wall"])
    add_box(col, "Garage_Door_Frame", (51.8, 46.0, 2.0), (0.4, 4.0, 4.0), mats["hazard_yellow"])
    add_box(col, "Garage_Rollup_Door", (52.0, 46.0, 2.0), (0.1, 3.8, 3.8), mats["steel_grate"])

    # Forklift / wooden pallets near Garage
    add_box(col, "Yard_Pallet_Stack_1", (47.5, 30.0, 0.4), (2.0, 2.0, 0.8), mats["wood_crate"])
    add_box(col, "Yard_Pallet_Stack_2", (53.5, 30.0, 0.6), (1.8, 1.8, 1.2), mats["wood_crate"])


def build_layout_revision(col, mats):
    """Cover in the open yard and inside A.

    The yard between T spawn and the hall's south door was 15 m of bare
    concrete, and A's floor had nothing on it but the hut. Jersey barriers
    and a container stack break the approach; crates give A a fight inside.
    (B is a building of its own beside the hall, not under it: the server's
    heightfield keeps one floor per column. See `build_site_b_lower_silo`.)

    The Lobby: a low building against the hall's south wall, so the south door
    is reached through the Lobby's west door, by the ramp, or its open east end,
    and not straight across the yard from T spawn. T's other way into A is the
    east door, from outside.
    """
    add_wall_with_door(col, "Lobby_Wall_S", (35.3, 21.9, 2.0), (25.4, 0.6, 4.0), mats["concrete_wall"], 25.5, 3.0, 3.2)
    add_box(col, "Lobby_Roof", (35.45, 24.5, 4.2), (25.7, 5.8, 0.4), mats["concrete_dark"])
    for lx in (28.0, 36.0, 44.0):
        add_box(col, f"Lobby_Light_Fluor_{int(lx)}_NonCol", (lx, 24.5, 3.85), (2.4, 0.6, 0.2), mats["light_fluor"])

    barriers = [(44.0, 20.0, 0.0), (54.0, 26.0, 0.0), (12.0, 48.0, 90.0)]
    for i, (x, y, yaw) in enumerate(barriers):
        sx, sy = (3.0, 0.6) if yaw == 0.0 else (0.6, 3.0)
        add_box(col, f"Jersey_Barrier_{i}", (x, y, 0.45), (sx, sy, 0.9), mats["concrete_floor"])
        add_box(col, f"Jersey_Barrier_{i}_Stripe_NonCol", (x, y, 0.72), (sx + 0.02, sy + 0.02, 0.14), mats["hazard_yellow"])
    add_shipping_container(col, "Container_Red_2", (58.0, 16.0, 0.0), mats["container_red"], mats)
    add_shipping_container(col, "Container_Blue_2", (58.0, 16.0, 2.59), mats["container_blue"], mats)

    for i, (x, y) in enumerate(((42.0, 34.0), (43.3, 34.0), (42.6, 35.3), (30.0, 46.0), (31.3, 46.0))):
        add_box(col, f"SiteA_Crate_{i}", (x, y, 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "SiteA_Crate_Top", (42.6, 34.6, 1.7), (1.1, 1.1, 1.0), mats["wood_crate"])


def build_dressing_and_lights(col, mats):
    """Pipes, striping and lamps; then the sky."""
    # Hazard striping on the hall's door reveals and the perimeter's foot.
    add_boxes(col, "Hazard_Door_Stripes_NonCol", [
        ((30.0 - 1.85, 52.0, 1.6), (0.2, 1.3, 3.2)), ((30.0 + 1.85, 52.0, 1.6), (0.2, 1.3, 3.2)),
        ((40.0 - 1.85, 28.0, 1.6), (0.2, 1.3, 3.2)), ((40.0 + 1.85, 28.0, 1.6), (0.2, 1.3, 3.2)),
        ((22.0, 44.0 - 1.85, 1.6), (1.3, 0.2, 3.2)), ((22.0, 44.0 + 1.85, 1.6), (1.3, 0.2, 3.2)),
        ((50.0, 34.0 - 1.6, 1.6), (1.3, 0.2, 3.2)), ((50.0, 34.0 + 1.6, 1.6), (1.3, 0.2, 3.2)),
        ((25.5 - 1.6, 21.9, 1.6), (0.2, 0.7, 3.2)), ((25.5 + 1.6, 21.9, 1.6), (0.2, 0.7, 3.2)),
        ((19.8 - 1.5, 40.0, 1.6), (0.2, 1.3, 3.2)), ((19.8 + 1.5, 40.0, 1.6), (0.2, 1.3, 3.2)),
    ], mats["hazard_yellow"])
    walls = [
        ((35.0, 4.0, 6.0), (62.0, 1.2, 12.0)),
        ((35.0, 66.0, 6.0), (62.0, 1.2, 12.0)),
        ((66.0, 35.0, 6.0), (1.2, 62.0, 12.0)),
        ((4.0, 35.0, 6.0), (1.2, 62.0, 12.0)),
    ]
    add_plinths(col, "Trim_Kerb_NonCol", walls, mats["concrete_dark"], height=0.35, proud=0.12)

    # Pipe runs along the outside of the hall, on brackets.
    pipes, brackets = [], []
    for y, side in ((27.2, -1), (52.8, 1)):
        pipes.append(((36.0, y, 5.6), (27.0, 0.3, 0.3)))
        pipes.append(((36.0, y, 6.2), (27.0, 0.2, 0.2)))
        for x in range(24, 50, 4):
            brackets.append(((x, y + side * 0.1, 5.9), (0.12, 0.5, 1.0)))
    add_boxes(col, "Pipes_Hall_NonCol", pipes, mats["pipe_yellow"])
    add_boxes(col, "Pipe_Brackets_NonCol", brackets, mats["steel_dark"])

    # Yard floodlights: a pole, a head, a light each.
    sodium = maplib.make_material("mat_nuke_light_sodium_glow", (1.0, 0.78, 0.42), emission=(1.0, 0.72, 0.35), strength=6.0)
    poles, heads = [], []
    for x, y in ((12.0, 12.0), (58.0, 12.0), (10.0, 60.0), (60.0, 58.0), (28.0, 18.0), (48.0, 58.0)):
        poles.append(((x, y, 3.5), (0.22, 0.22, 7.0)))
        heads.append(((x, y, 7.05), (0.9, 0.5, 0.18)))
        add_light((x, y, 6.6), color=(255, 200, 140), radius=11.0, intensity=1.6)
    # Solid: a NonCol pole is one a body stands inside, and it vanishes from there.
    add_boxes(col, "Yard_Poles", poles, mats["steel_dark"])
    add_boxes(col, "Floodlight_Heads_NonCol", heads, sodium)

    # Wall packs over the hall's four doors, B's north door and the Lobby's.
    packs = [((30.0, 52.8, 3.9), (0.6, 0.3, 0.3)), ((40.0, 27.2, 3.9), (0.6, 0.3, 0.3)), ((21.2, 44.0, 3.9), (0.3, 0.6, 0.3)),
             ((50.8, 34.0, 3.9), (0.3, 0.6, 0.3)), ((19.8, 40.8, 3.9), (0.6, 0.3, 0.3)), ((25.5, 21.45, 3.6), (0.6, 0.3, 0.3))]
    add_boxes(col, "Wallpack_NonCol", packs, mats["light_fluor"])
    for (x, y, z), _ in packs:
        add_light((x, y, z - 0.4), color=(235, 242, 255), radius=7.0, intensity=1.6)

    # The hall's own troffers.
    maplib.lights_from_emitters("mat_nuke_light_fluor", color=(235, 242, 255), radius=10.0, intensity=1.8, spacing=5.0, drop=0.6)
    # And the reactor's glow, down in B.
    add_light((13.5, 28.5, -0.5), color=(80, 220, 255), radius=7.0, intensity=1.4)

    maplib.set_atmosphere("overcast")


# Where the clutter gathers, in the authored (pre-cut) frame so each zone stretches
# with the map: (x0, y0, x1, y1), density, vignette weights, lane direction or None.
_YARD = {"crates": 3, "barrels": 3, "sacks": 2}
_ZONES = [
    # The Lobby and the yard along its south wall: a passage, kept clear. A crate
    # row against that wall turned a bot sliding along it into a pile-up.
    ((22, 18, 49, 28), 0.0, _YARD, None),
    ((5, 5, 65, 21.5), 1.0, _YARD, (0.0, 1.0)),  # the T yard
    ((22, 28, 50, 52), 0.7, {"crates": 4, "barrels": 3}, None),  # hall A
    ((50, 21.5, 65, 65), 0.9, _YARD, (0.0, 1.0)),  # outside: the garage lane
    ((5, 21.5, 22, 65), 0.8, {"crates": 3, "barrels": 3, "sacks": 3}, None),  # the west yard
    ((22, 52, 50, 65), 0.6, _YARD, None),  # CT spawn
]
_DRESS_SEED = 20261018
# Anything with one of these in its name is a way through: no clutter within 2 m.
# Every doorway here has hazard stripes on its jambs, so `Door` keeps them all clear.
_WAYS = ("Door", "Portal", "Window", "Stairs", "Ramp", "Vent")
# What each kind of prop is made of, by this map's own material keys. No jars here.
_PROP_MATERIALS = {
    "crate": "wood_crate",
    "barrel_wood": "drum_blue",
    "barrel_iron": "steel_dark",
    "sack": "sandbag",
    "jar": "concrete_dark",
    "rubble": "concrete_dark",
}


def build_natural_dressing(col, mats):
    """Clutter laid out against the finished map rather than typed in.

    Plans on everything already built (so it must run last among the builders),
    keeps clear of spawns, bomb sites, doorways, the ramp and stairs, and places
    vignettes where a place gets cluttered: against walls, in corners, and a few
    pieces of cover in the open yards. B, the ramp and the vent are `Pit_` floors,
    banned: props stand at ground level, and B's floor is 4.5 m below it. See
    `props.py`.
    """
    import json
    import random

    with open(
        os.path.join(maplib.REPO_ROOT, "backend", "modules", "hassault", "maps", "hd_nuke.json"),
        encoding="utf-8",
    ) as f:
        placed = json.load(f)
    plan = W.plan(
        props,
        extent=(4.0, 4.0, 78.0, 80.0),
        domain=(5.0, 77.0, 5.0, 79.0),
        placed=placed,
        ways=_WAYS,
    )
    zone_of = W.zone_of(_ZONES, (0.5, _YARD, None))
    rng = random.Random(_DRESS_SEED)
    vignettes = props.dress(plan, rng, zone_of, lane_budget=8)
    counts = props.emit(vignettes, col, mats, _PROP_MATERIALS)
    tally = {}
    for name, _ in vignettes:
        tally[name] = tally.get(name, 0) + 1
    print("dressing:", tally, counts)


def build_architectural_detail(col, mats):
    """Nuclear facility architectural detailing: Site A & Site B overhead roof
    trusses and HVAC ductwork, catwalk & reactor-pit telemetry control consoles,
    containment building exterior buttresses & clerestory windows, Garage rooftop
    chiller units, and nuclear plant auxiliary buildings on the horizon (`_NonCol`).
    """
    mep_mats = {
        "steel": mats["steel_dark"],
        "duct": mats["steel_industrial"],
        "pipe": mats["coolant_blue"],
    }
    # 1. Site A Upper Reactor Hall roof trusses & HVAC (z = 7.95m)
    maplib.add_ceiling_mep(
        col,
        "Nuke_SiteA_MEP",
        (23.2, 29.2, 48.8, 50.8),
        z_ceil=7.95,
        mats=mep_mats,
        axis="x",
        spacing=5.5,
        boxes_fn=add_boxes,
    )
    # 2. Site B Lower Silo ceiling trusses & coolant pipes (z = 4.95m)
    maplib.add_ceiling_mep(
        col,
        "Nuke_SiteB_MEP",
        (9.2, 21.2, 20.8, 38.8),
        z_ceil=4.95,
        mats=mep_mats,
        axis="y",
        spacing=4.5,
        boxes_fn=add_boxes,
    )

    # 3. Telemetry Command Consoles on Site A North Rafters & in Site B Pit
    console_mats = {
        "cabinet": mats["steel_industrial"],
        "bezel": mats["steel_dark"],
        "screen": mats["reactor_glow_cyan"],
    }
    maplib.add_control_station(
        col,
        "SiteA_Rafter_Console",
        (36.0, 50.5, 3.9),
        yaw_deg=180.0,
        length=3.6,
        mats=console_mats,
        boxes_fn=add_boxes,
    )
    maplib.add_control_station(
        col,
        "SiteB_Pit_Console",
        (9.35, 34.8, -4.5),
        yaw_deg=90.0,
        length=2.8,
        mats=console_mats,
        boxes_fn=add_boxes,
    )

    # 4. Containment Hall Clerestory Windows, Exterior Buttresses & Rooftop Chillers
    win_mats = {
        "trim": mats["steel_dark"],
        "pane": mats["hazard_black"],
    }
    maplib.add_window_row(
        col,
        "SiteA_North_Clerestory",
        (36.0, 52.0, 4.0),
        (28.0, 1.2, 8.0),
        "+y",
        z_base=5.6,
        count=5,
        win_size=(1.6, 1.2),
        mats=win_mats,
        style="industrial",
        boxes_fn=add_boxes,
    )
    maplib.add_window_row(
        col,
        "SiteA_East_Clerestory",
        (50.0, 40.0, 4.0),
        (1.2, 24.0, 8.0),
        "+x",
        z_base=5.6,
        count=4,
        win_size=(1.6, 1.2),
        mats=win_mats,
        style="industrial",
        boxes_fn=add_boxes,
    )

    # Rooftop HVAC chillers on Garage, Lobby, and Site A Hall Roof
    hvac_boxes = [
        ((56.0, 44.0, 5.65), (3.2, 2.4, 1.2)),
        ((56.0, 48.2, 5.55), (2.6, 2.0, 1.0)),
        ((35.5, 24.5, 4.95), (3.8, 2.2, 1.0)),
        ((32.0, 36.0, 9.05), (4.5, 3.2, 1.2)),
        ((41.0, 44.0, 9.05), (4.5, 3.2, 1.2)),
    ]
    buttress_boxes = []
    for bx in (25.0, 35.0, 45.0):
        buttress_boxes.append(((bx, 52.75, 4.0), (0.65, 0.35, 7.8)))
    for by in (30.5, 39.0, 47.5):
        buttress_boxes.append(((50.75, by, 4.0), (0.35, 0.65, 7.8)))
    add_boxes(col, "Nuke_Rooftop_HVAC_NonCol", hvac_boxes, mats["steel_industrial"])
    add_boxes(col, "Nuke_Hall_Buttresses_NonCol", buttress_boxes, mats["concrete_dark"])

    # 5. Nuclear Plant Horizon Buildings & Cooling Tower Silhouettes
    horizon_specs = [
        {"center": (20.0, -3.5, 0.0), "size": (18.0, 9.0, 16.5), "wall": "concrete_wall", "trim": "concrete_dark", "pane": "hazard_black", "roof": "steel_industrial", "stories": 4},
        {"center": (50.0, -4.0, 0.0), "size": (18.0, 9.5, 18.0), "wall": "concrete_dark", "trim": "hazard_yellow", "pane": "hazard_black", "roof": "steel_dark", "stories": 5},
        {"center": (20.0, 73.5, 0.0), "size": (18.0, 9.0, 17.5), "wall": "concrete_wall", "trim": "concrete_dark", "pane": "hazard_black", "roof": "steel_industrial", "stories": 5},
        {"center": (50.0, 74.0, 0.0), "size": (16.0, 9.0, 16.5), "wall": "concrete_dark", "trim": "hazard_yellow", "pane": "hazard_black", "roof": "steel_dark", "stories": 4},
        {"center": (-3.5, 22.0, 0.0), "size": (9.0, 16.0, 17.0), "wall": "concrete_wall", "trim": "concrete_dark", "pane": "hazard_black", "roof": "steel_industrial", "stories": 4},
        {"center": (-4.0, 50.0, 0.0), "size": (11.0, 11.0, 24.0), "wall": "concrete_wall", "trim": "hazard_yellow", "pane": "hazard_black", "roof": "concrete_dark", "stories": 6},
        {"center": (73.5, 24.0, 0.0), "size": (9.0, 18.0, 17.5), "wall": "concrete_dark", "trim": "steel_industrial", "pane": "hazard_black", "roof": "steel_dark", "stories": 5},
        {"center": (74.0, 52.0, 0.0), "size": (11.0, 11.0, 24.0), "wall": "concrete_wall", "trim": "hazard_yellow", "pane": "hazard_black", "roof": "concrete_dark", "stories": 6},
    ]
    maplib.add_horizon_buildings(col, "Nuke_Horizon", horizon_specs, mats, boxes_fn=add_boxes)


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
    build_layout_revision(get_or_create_collection("Revision"), mats)
    build_dressing_and_lights(get_or_create_collection("Dressing"), mats)
    build_natural_dressing(get_or_create_collection("Clutter"), mats)  # last: it plans on all of the above
    build_architectural_detail(get_or_create_collection("Arch_Detail"), mats)
    if os.environ.get("HASSAULT_NUKE_SCAN"):
        W.dump_scan(os.environ["HASSAULT_NUKE_SCAN"])


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
