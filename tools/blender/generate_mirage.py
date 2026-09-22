#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Desert Courtyard (hd_mirage).
Authentic competitive tournament arena on a 70m x 70m footprint:
- Bomb Site A: Palace interior colonnade (z=2.8m), Tetris boxes, Triple boxes, Ticket booth, and CT ramp.
- Middle & Underpass: Central street, Sniper's Nest (Window Room z=2.4m), Connector steps, Catwalk, and Underpass.
- Bomb Site B: B Apartments (z=2.8m) with arched jump window, Market/Kitchen room with service window, delivery Van barricade, and Site B pillar.
- T & CT Spawns: Moorish courtyards with date palm trees, fabric sun canopies, terracotta urns, and blue mosaic tile trims.

Outputs:
  - backend/modules/hassault/maps/hd_mirage.glb
  - apps/web/public/hd_mirage.glb
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
    print("Error: generate_mirage.py must be run from within Blender (e.g. `blender --background --python ...`)")
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
    # Sandstone & Moorish Plaster with rich surface roughness
    mats["sandstone_paving"] = create_pbr_material("mat_sandstone_paving", (0.76, 0.70, 0.58), metallic=0.01, roughness=0.85, bump_strength=0.18, bump_scale=20.0)
    mats["sandstone_light"] = create_pbr_material("mat_sandstone_light", (0.84, 0.76, 0.62), metallic=0.01, roughness=0.82, bump_strength=0.16, bump_scale=18.0)
    mats["sandstone_ochre"] = create_pbr_material("mat_sandstone_ochre", (0.78, 0.58, 0.38), metallic=0.01, roughness=0.88, bump_strength=0.20, bump_scale=18.0)
    mats["moorish_plaster_warm"] = create_pbr_material("mat_moorish_plaster_warm", (0.88, 0.82, 0.72), metallic=0.01, roughness=0.90, bump_strength=0.15, bump_scale=25.0)
    mats["mosaic_tile_blue"] = create_pbr_material("mat_mosaic_tile_blue", (0.10, 0.38, 0.68), metallic=0.08, roughness=0.35, bump_strength=0.06, bump_scale=35.0)

    # Woods & Metals
    mats["cedar_wood"] = create_pbr_material("mat_cedar_wood", (0.36, 0.24, 0.16), metallic=0.0, roughness=0.75, bump_strength=0.28, bump_scale=12.0)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.54, 0.40, 0.26), metallic=0.0, roughness=0.65, bump_strength=0.25, bump_scale=14.0)
    mats["wrought_iron"] = create_pbr_material("mat_wrought_iron", (0.18, 0.18, 0.20), metallic=0.85, roughness=0.45, bump_strength=0.10, bump_scale=32.0)
    mats["van_metal_olive"] = create_pbr_material("mat_van_metal_olive", (0.32, 0.38, 0.28), metallic=0.45, roughness=0.55, bump_strength=0.10, bump_scale=25.0)

    # Fabrics & foliage
    mats["canopy_crimson"] = create_pbr_material("mat_canopy_crimson", (0.65, 0.15, 0.18), metallic=0.0, roughness=0.92, bump_strength=0.14, bump_scale=30.0)
    mats["canopy_indigo"] = create_pbr_material("mat_canopy_indigo", (0.15, 0.25, 0.60), metallic=0.0, roughness=0.92, bump_strength=0.14, bump_scale=30.0)
    mats["palm_bark"] = create_pbr_material("mat_palm_bark", (0.32, 0.24, 0.18), metallic=0.0, roughness=0.95, bump_strength=0.32, bump_scale=10.0)
    mats["palm_fronds"] = create_pbr_material("mat_palm_fronds", (0.16, 0.38, 0.12), metallic=0.0, roughness=0.80)
    mats["terracotta_urn"] = create_pbr_material("mat_terracotta_urn", (0.72, 0.34, 0.20), metallic=0.0, roughness=0.78, bump_strength=0.22, bump_scale=16.0)

    # Breakable glass windows
    mats["glass_window"] = create_pbr_material("mat_glass_window", (0.82, 0.92, 0.98), metallic=0.05, roughness=0.06, alpha=0.35)
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


def add_arch(collection, name, center, span, height, depth, material, segments=8):
    """Moorish horseshoe arch with column plinths and carved capitals."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    pillar_w = 0.8
    pillar_h = height - span * 0.45
    # Left pillar
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((pillar_w, depth, pillar_h)), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((-span * 0.5 - pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=bm.verts)

    # Right pillar
    r_bm = bmesh.new()
    bmesh.ops.create_cube(r_bm, size=1.0)
    bmesh.ops.scale(r_bm, vec=Vector((pillar_w, depth, pillar_h)), verts=r_bm.verts)
    bmesh.ops.translate(r_bm, vec=Vector((span * 0.5 + pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=r_bm.verts)
    for v in r_bm.verts:
        bm.verts.new(v.co)
    r_bm.free()

    # Curved arch
    radius = span * 0.5
    for i in range(segments):
        a0 = math.pi * i / segments
        a1 = math.pi * (i + 1) / segments
        x0 = math.cos(a0) * radius
        z0 = math.sin(a0) * radius + pillar_h
        x1 = math.cos(a1) * radius
        z1 = math.sin(a1) * radius + pillar_h

        seg_bm = bmesh.new()
        bmesh.ops.create_cube(seg_bm, size=1.0)
        seg_len = math.hypot(x1 - x0, z1 - z0)
        bmesh.ops.scale(seg_bm, vec=Vector((seg_len * 1.05, depth * 1.05, 0.55)), verts=seg_bm.verts)
        ang = math.atan2(z1 - z0, x1 - x0)
        bmesh.ops.rotate(seg_bm, matrix=Euler((0.0, -ang, 0.0)).to_matrix(), verts=seg_bm.verts)
        bmesh.ops.translate(seg_bm, vec=Vector(((x0 + x1) * 0.5, 0.0, (z0 + z1) * 0.5)), verts=seg_bm.verts)
        for v in seg_bm.verts:
            bm.verts.new(v.co)
        seg_bm.free()

    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_detailed_palm(collection, name_prefix, base_pos, mats, trunk_height=7.0):
    """Palm tree with segmented trunk rings and curved fronds (NonCol)."""
    bx, by, bz = base_pos
    num_segs = 6
    seg_h = trunk_height / num_segs
    for i in range(num_segs):
        sz = bz + i * seg_h + seg_h * 0.5
        rad = 0.36 - (i / num_segs) * 0.10
        add_cylinder(collection, f"{name_prefix}_trunk_{i}", (bx, by, sz), radius=rad, height=seg_h * 1.02, material=mats["palm_bark"], segments=10)
        add_cylinder(collection, f"{name_prefix}_ring_{i}_NonCol", (bx, by, sz + seg_h * 0.5), radius=rad * 1.15, height=0.08, material=mats["palm_bark"], segments=10)

    # Fronds
    top_z = bz + trunk_height
    for tier, (num_f, f_len, droop_deg) in enumerate([(10, 3.4, 25), (10, 2.6, 50)]):
        for f in range(num_f):
            ang = (2.0 * math.pi * f) / num_f + (tier * 0.3)
            fx = bx + math.cos(ang) * (f_len * 0.45)
            fy = by + math.sin(ang) * (f_len * 0.45)
            fz = top_z - math.sin(math.radians(droop_deg)) * (f_len * 0.3)
            mesh = bpy.data.meshes.new(f"{name_prefix}_frond_{tier}_{f}_NonCol")
            obj = bpy.data.objects.new(f"{name_prefix}_frond_{tier}_{f}_NonCol", mesh)
            collection.objects.link(obj)
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=Vector((0.55, f_len, 0.05)), verts=bm.verts)
            bmesh.ops.rotate(bm, matrix=Euler((math.radians(droop_deg), 0.0, -ang + math.pi*0.5)).to_matrix(), verts=bm.verts)
            bmesh.ops.translate(bm, vec=Vector((fx, fy, fz)), verts=bm.verts)
            bm.to_mesh(mesh)
            bm.free()
            obj.data.materials.append(mats["palm_fronds"])


def add_delivery_van(collection, mats):
    """Detailed market delivery van at Site B (X: 18, Y: 46)."""
    cx, cy, cz = 18.0, 46.0, 0.0
    L, W, H = 4.8, 2.0, 2.2
    # Chassis frame
    add_box(collection, "Van_Chassis", (cx, cy, cz + 0.4), (L, W, 0.4), mats["wrought_iron"])
    # Cabin
    add_box(collection, "Van_Cabin", (cx - 1.2, cy, cz + 1.2), (1.6, W * 0.95, 1.2), mats["van_metal_olive"])
    # Rear Cargo Box
    add_box(collection, "Van_Cargo_Box", (cx + 0.8, cy, cz + 1.4), (3.0, W, 1.6), mats["moorish_plaster_warm"])
    # Wheels (NonCol)
    for wx in [cx - 1.4, cx + 1.4]:
        for wy in [cy - W * 0.5, cy + W * 0.5]:
            add_cylinder(collection, f"Van_Wheel_{int(wx*10)}_{int(wy*10)}_NonCol", (wx, wy, cz + 0.4), radius=0.4, height=0.3, material=mats["wrought_iron"], segments=12)
    # Headlights (NonCol)
    for hy in [cy - 0.6, cy + 0.6]:
        add_cylinder(collection, f"Van_Headlight_{int(hy*10)}_NonCol", (cx - 2.05, hy, cz + 0.9), radius=0.15, height=0.1, material=mats["mosaic_tile_blue"], segments=8)


def build_mirage_perimeter_and_terrain(col, mats):
    """Paving slabs, perimeter desert masonry, and mosaic trims."""
    add_box(col, "Terrain_Paving", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["sandstone_paving"])

    # High Perimeter Desert Stucco Walls (10.0m)
    add_box(col, "Wall_Perimeter_S", (35.0, 4.0, 5.0), (62.0, 1.2, 10.0), mats["sandstone_light"])
    add_box(col, "Wall_Perimeter_N", (35.0, 66.0, 5.0), (62.0, 1.2, 10.0), mats["sandstone_light"])
    add_box(col, "Wall_Perimeter_E", (66.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_W", (4.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["sandstone_ochre"])

    # Blue mosaic tile decorative trim band
    add_box(col, "Mosaic_Trim_S", (35.0, 4.6, 9.8), (62.0, 0.4, 0.4), mats["mosaic_tile_blue"])
    add_box(col, "Mosaic_Trim_N", (35.0, 65.4, 9.8), (62.0, 0.4, 0.4), mats["mosaic_tile_blue"])


def build_site_a(col, mats):
    """Site A Palace interior colonnade, Tetris, Triple, Ticket booth."""
    # Palace interior raised floor at Z = 2.8m (X: 48..62, Y: 18..36)
    add_box(col, "Palace_Floor_Raised", (55.0, 27.0, 1.4), (14.0, 18.0, 2.8), mats["sandstone_paving"])
    # Palace interior outer wall
    add_box(col, "Palace_Outer_Wall_E", (62.0, 27.0, 6.0), (1.2, 18.0, 6.4), mats["moorish_plaster_warm"])

    # Palace Colonnade Columns along Palace entrance (X: 48.0)
    for col_y in [20.0, 24.0, 28.0, 32.0]:
        add_cylinder(col, f"Palace_Column_{int(col_y)}", (48.0, col_y, 4.2), radius=0.45, height=2.8, material=mats["sandstone_light"], segments=16)

    # Tetris Box Stack (48.0, 40.0)
    add_box(col, "Tetris_Box_1", (48.0, 40.0, 0.6), (1.4, 1.4, 1.2), mats["wood_crate"])
    add_box(col, "Tetris_Box_2", (48.0, 41.5, 0.6), (1.4, 1.4, 1.2), mats["wood_crate"])
    add_box(col, "Tetris_Box_Top", (48.0, 40.0, 1.7), (1.2, 1.2, 1.0), mats["wood_crate"])

    # Triple Box Stack (52.0, 48.0)
    add_box(col, "Triple_Box_1", (52.0, 48.0, 0.6), (1.3, 1.3, 1.2), mats["wood_crate"])
    add_box(col, "Triple_Box_2", (52.0, 49.4, 0.6), (1.3, 1.3, 1.2), mats["wood_crate"])
    add_box(col, "Triple_Box_3", (53.4, 48.0, 0.6), (1.3, 1.3, 1.2), mats["wood_crate"])

    # Ticket Booth at CT ramp entrance (58.0, 56.0)
    add_box(col, "Ticket_Booth_Body", (58.0, 56.0, 1.2), (3.0, 3.0, 2.4), mats["sandstone_ochre"])
    add_box(col, "Ticket_Booth_Window", (56.4, 56.0, 1.4), (0.2, 1.6, 0.8), mats["cedar_wood"])

    # CT Ramp ascending to Ticket booth
    add_box(col, "CT_Ramp_Slope", (58.0, 50.0, 0.5), (3.0, 6.0, 1.0), mats["sandstone_paving"])


def build_middle_and_underpass(col, mats):
    """Mid street, Sniper's Nest (Window Room z=2.4m), Catwalk, Connector, Underpass."""
    # Sniper's Nest / Window Room (X: 34..44, Y: 46..54, Z: 0..4.8)
    add_box(col, "Window_Room_Floor", (39.0, 50.0, 1.2), (10.0, 8.0, 2.4), mats["sandstone_paving"])
    add_box(col, "Window_Room_Wall_S", (39.0, 46.0, 3.6), (10.0, 0.8, 2.4), mats["sandstone_light"])
    # Sniper Window cutout at (39.0, 46.0, Z=2.4m)
    add_box(col, "Sniper_Window_Sill", (39.0, 46.0, 2.8), (2.4, 0.8, 0.2), mats["cedar_wood"])

    # Connector stairs connecting Mid to Site A
    add_box(col, "Connector_Ramp", (44.0, 40.0, 0.8), (4.0, 6.0, 1.6), mats["sandstone_paving"])

    # Underpass tunnel beneath Mid (X: 30..36, Y: 28..38, Z: -1.5m)
    add_box(col, "Underpass_Trench_Floor", (33.0, 33.0, -0.75), (5.0, 10.0, 1.5), mats["sandstone_ochre"])

    # Mid Catwalk wooden planks (Z = 1.6m)
    add_box(col, "Catwalk_Deck", (26.0, 36.0, 1.6), (3.0, 12.0, 0.3), mats["cedar_wood"])
    add_box(col, "Catwalk_Railing_NonCol", (27.4, 36.0, 2.2), (0.1, 12.0, 0.9), mats["wrought_iron"])


def build_site_b(col, mats):
    """Site B Apartments, Kitchen/Market, delivery Van barricade, Site B Pillar."""
    # B Apartments (X: 12..24, Y: 18..32, Z: 0..5.6)
    add_box(col, "B_Apts_Floor_2nd", (18.0, 25.0, 2.8), (12.0, 14.0, 0.3), mats["cedar_wood"])
    add_box(col, "B_Apts_Wall_E", (24.0, 25.0, 4.2), (0.8, 14.0, 2.8), mats["moorish_plaster_warm"])

    # B jump window overlooking Site B at (24.0, 28.0, Z=2.8m)
    add_arch(col, "B_Apts_Jump_Window", (24.0, 28.0, 2.8), span=2.2, height=2.4, depth=0.8, material=mats["sandstone_light"])

    # Central massive Site B stone pillar at (22.0, 52.0)
    add_box(col, "SiteB_Central_Pillar", (22.0, 52.0, 2.5), (2.8, 2.8, 5.0), mats["sandstone_ochre"])

    # Delivery Van barricade
    add_delivery_van(col, mats)

    # Market / Kitchen room at North side of B (X: 28..36, Y: 52..62)
    add_box(col, "Market_Wall_W", (28.0, 57.0, 2.5), (0.8, 10.0, 5.0), mats["moorish_plaster_warm"])
    add_box(col, "Market_Service_Counter", (30.0, 54.0, 0.55), (3.6, 1.0, 1.1), mats["cedar_wood"])


def build_props_and_foliage(col, mats):
    """Palms, sun canopies, and urns."""
    # Date Palm in T Courtyard
    add_detailed_palm(col, "T_Palm_1", (20.0, 10.0, 0.0), mats, trunk_height=7.5)
    add_detailed_palm(col, "T_Palm_2", (36.0, 10.0, 0.0), mats, trunk_height=6.8)

    # Date Palm in CT Courtyard
    add_detailed_palm(col, "CT_Palm_1", (48.0, 60.0, 0.0), mats, trunk_height=7.2)

    # Sun Canopies over market alleys (NonCol)
    add_box(col, "Canopy_Market_Crimson_NonCol", (28.0, 46.0, 4.2), (6.0, 4.0, 0.1), mats["canopy_crimson"])
    add_box(col, "Canopy_Palace_Indigo_NonCol", (52.0, 36.0, 4.5), (5.0, 6.0, 0.1), mats["canopy_indigo"])

    # Terracotta urns
    for ux, uy in [(22.0, 12.0), (34.0, 12.0), (54.0, 54.0), (20.0, 56.0)]:
        add_cylinder(col, f"Urn_{int(ux)}_{int(uy)}_NonCol", (ux, uy, 0.45), radius=0.35, height=0.9, material=mats["terracotta_urn"], segments=12)

    # Breakable windows overlooking key sniper corridors (tactical glass penetration)
    add_box(col, "Window_Glass_SniperNest_1", (36.0, 48.0, 3.8), (2.2, 0.12, 1.4), mats["glass_window"])
    add_box(col, "Window_Glass_SniperNest_2", (42.0, 48.0, 3.8), (2.2, 0.12, 1.4), mats["glass_window"])
    add_box(col, "Window_Glass_Palace_Balcony", (54.0, 24.0, 3.8), (0.12, 2.4, 1.4), mats["glass_window"])
    add_box(col, "Window_Glass_B_Apts", (24.0, 28.0, 3.6), (0.12, 2.0, 1.4), mats["glass_window"])


def build_mirage_scene():
    clear_scene()
    mats = setup_materials()

    c_terrain = get_or_create_collection("Terrain")
    c_site_a = get_or_create_collection("SiteA")
    c_mid = get_or_create_collection("Middle")
    c_site_b = get_or_create_collection("SiteB")
    c_props = get_or_create_collection("Props")

    build_mirage_perimeter_and_terrain(c_terrain, mats)
    build_site_a(c_site_a, mats)
    build_middle_and_underpass(c_mid, mats)
    build_site_b(c_site_b, mats)
    build_props_and_foliage(c_props, mats)

    print("=== Desert Courtyard (hd_mirage) Built Successfully! ===")


def export_glb():
    backend_map_dir = "/home/horrible/horrible-dashboard/backend/modules/hassault/maps"
    web_public_dir = "/home/horrible/horrible-dashboard/apps/web/public"

    os.makedirs(backend_map_dir, exist_ok=True)
    os.makedirs(web_public_dir, exist_ok=True)

    backend_glb_path = os.path.join(backend_map_dir, "hd_mirage.glb")
    web_glb_path = os.path.join(web_public_dir, "hd_mirage.glb")

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
    print("=== Mirage Generation & Export Complete! ===")


if __name__ == "__main__":
    build_mirage_scene()
    export_glb()
