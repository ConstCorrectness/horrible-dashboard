#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Desert Courtyard (hd_mirage).
Authentic competitive tournament arena on a 70m x 70m footprint:
- Bomb Site A: Palace interior colonnade (z=2.8m), Tetris boxes, Triple boxes, Ticket booth, and CT ramp.
- Middle & Underpass: Central street, Sniper's Nest (Window Room z=2.4m), Connector steps, Catwalk, and Underpass.
- Bomb Site B: B Apartments (z=2.8m) with arched jump window, Market/Kitchen room with service window, Van/truck barricade, and Site B pillar.
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


def create_pbr_material(name, base_color, metallic=0.0, roughness=0.7, emission_color=None, emission_strength=0.0, alpha=1.0):
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

    out = nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def setup_materials():
    mats = {}
    # Sandstone & Moorish Plaster
    mats["sandstone_paving"] = create_pbr_material("mat_sandstone_paving", (0.76, 0.70, 0.58), metallic=0.01, roughness=0.85)
    mats["sandstone_light"] = create_pbr_material("mat_sandstone_light", (0.84, 0.76, 0.62), metallic=0.01, roughness=0.82)
    mats["sandstone_ochre"] = create_pbr_material("mat_sandstone_ochre", (0.78, 0.58, 0.38), metallic=0.01, roughness=0.88)
    mats["moorish_plaster_warm"] = create_pbr_material("mat_moorish_plaster_warm", (0.88, 0.82, 0.72), metallic=0.01, roughness=0.90)
    mats["mosaic_tile_blue"] = create_pbr_material("mat_mosaic_tile_blue", (0.10, 0.38, 0.68), metallic=0.08, roughness=0.35)

    # Woods & Metals
    mats["cedar_wood"] = create_pbr_material("mat_cedar_wood", (0.36, 0.24, 0.16), metallic=0.0, roughness=0.75)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.54, 0.40, 0.26), metallic=0.0, roughness=0.65)
    mats["wrought_iron"] = create_pbr_material("mat_wrought_iron", (0.18, 0.18, 0.20), metallic=0.85, roughness=0.45)
    mats["van_metal_olive"] = create_pbr_material("mat_van_metal_olive", (0.32, 0.38, 0.28), metallic=0.45, roughness=0.55)

    # Fabrics & foliage
    mats["canopy_crimson"] = create_pbr_material("mat_canopy_crimson", (0.65, 0.15, 0.18), metallic=0.0, roughness=0.92)
    mats["palm_bark"] = create_pbr_material("mat_palm_bark", (0.32, 0.24, 0.18), metallic=0.0, roughness=0.95)
    mats["palm_fronds"] = create_pbr_material("mat_palm_fronds", (0.16, 0.38, 0.12), metallic=0.0, roughness=0.80)
    mats["terracotta_urn"] = create_pbr_material("mat_terracotta_urn", (0.72, 0.34, 0.20), metallic=0.0, roughness=0.78)
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


def add_arch(collection, name, center, span, height, depth, material):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    bm = bmesh.new()
    pillar_w = 0.8
    pillar_h = height - span * 0.4
    # Left pillar
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((pillar_w, depth, pillar_h)), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((-span * 0.5 + pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=bm.verts)

    # Right pillar
    r_bm = bmesh.new()
    bmesh.ops.create_cube(r_bm, size=1.0)
    bmesh.ops.scale(r_bm, vec=Vector((pillar_w, depth, pillar_h)), verts=r_bm.verts)
    bmesh.ops.translate(r_bm, vec=Vector((span * 0.5 - pillar_w * 0.5, 0.0, pillar_h * 0.5)), verts=r_bm.verts)
    for v in r_bm.verts:
        bm.verts.new(v.co)
    r_bm.free()

    # Top lintel arch
    t_bm = bmesh.new()
    bmesh.ops.create_cube(t_bm, size=1.0)
    bmesh.ops.scale(t_bm, vec=Vector((span + pillar_w * 2.0, depth, 0.9)), verts=t_bm.verts)
    bmesh.ops.translate(t_bm, vec=Vector((0.0, 0.0, height + 0.45)), verts=t_bm.verts)
    for v in t_bm.verts:
        bm.verts.new(v.co)
    t_bm.free()

    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


def add_palm_tree(collection, name, pos, mats):
    px, py, pz = pos
    # Trunk
    add_cylinder(collection, f"{name}_trunk", (px, py, pz + 3.0), radius=0.25, height=6.0, material=mats["palm_bark"], segments=8)
    # Crown fronds
    add_cylinder(collection, f"{name}_fronds_b", (px, py, pz + 6.2), radius=2.2, height=0.6, material=mats["palm_fronds"], segments=10)
    add_cylinder(collection, f"{name}_fronds_t", (px, py, pz + 6.8), radius=1.4, height=0.5, material=mats["palm_fronds"], segments=8)


def build_mirage_perimeter_and_ground(col, mats):
    # Main Sandstone ground terrain (70m x 70m)
    add_box(col, "Terrain_Sandstone", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["sandstone_paving"])

    # High Perimeter Sandstone Walls (Height 10m)
    add_box(col, "Wall_Perimeter_S", (35.0, 4.0, 5.0), (62.0, 1.2, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_N", (35.0, 66.0, 5.0), (62.0, 1.2, 10.0), mats["sandstone_ochre"])
    add_box(col, "Wall_Perimeter_E", (66.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["moorish_plaster_warm"])
    add_box(col, "Wall_Perimeter_W", (4.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["moorish_plaster_warm"])

    # Blue Mosaic Tile Decorative Frieze
    add_box(col, "Mosaic_Frieze_S", (35.0, 4.6, 9.6), (62.0, 0.4, 0.5), mats["mosaic_tile_blue"])
    add_box(col, "Mosaic_Frieze_N", (35.0, 65.4, 9.6), (62.0, 0.4, 0.5), mats["mosaic_tile_blue"])


def build_site_a_and_palace(col, mats):
    """Bomb Site A with Palace interior colonnade, Tetris, Triple, and Ticket."""
    # Palace Building Outer Walls (X: 48..64, Y: 18..36, Z: 0..7.0)
    add_box(col, "Palace_Wall_W", (48.0, 27.0, 3.5), (1.2, 18.0, 7.0), mats["moorish_plaster_warm"])
    add_box(col, "Palace_Wall_S", (56.0, 18.0, 3.5), (16.0, 1.2, 7.0), mats["sandstone_light"])

    # Palace Balcony Floor overlooking Site A at Z = 2.8m (X: 46..52, Y: 36..40)
    add_box(col, "Palace_Balcony_Floor", (49.0, 38.0, 2.8), (6.0, 4.0, 0.3), mats["cedar_wood"])
    add_box(col, "Palace_Balcony_Railing", (46.0, 38.0, 3.4), (0.2, 4.0, 1.0), mats["wrought_iron"])

    # Palace Pillars
    for py in (22.0, 28.0, 34.0):
        add_cylinder(col, f"Palace_Pillar_{int(py)}", (52.0, py, 1.4), radius=0.45, height=2.8, material=mats["sandstone_light"], segments=12)

    # Tetris Wooden Boxes at (46.0, 46.0)
    add_box(col, "Tetris_Box_Base", (46.0, 46.0, 0.6), (2.4, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "Tetris_Box_Top", (46.0, 46.0, 1.7), (1.2, 1.2, 1.0), mats["wood_crate"])

    # Triple Box Stack at (54.0, 52.0)
    add_box(col, "Triple_Box_1", (54.0, 52.0, 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "Triple_Box_2", (55.3, 52.0, 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "Triple_Box_Top", (54.0, 52.0, 1.7), (1.1, 1.1, 1.0), mats["wood_crate"])

    # Ticket Booth & CT Ramp at (54.0, 60.0)
    add_box(col, "Ticket_Booth", (54.0, 60.0, 1.4), (3.0, 3.0, 2.8), mats["sandstone_light"])
    add_box(col, "Ticket_Ramp", (50.0, 60.0, 0.5), (4.0, 2.8, 1.0), mats["sandstone_paving"])

    # A Site Default Plant Marker (50.0, 50.0)
    add_cylinder(col, "SiteA_Plant_Marker", (50.0, 50.0, 0.05), radius=3.8, height=0.1, material=mats["sandstone_ochre"], segments=16)


def build_middle_and_window(col, mats):
    """Middle Courtyard, Sniper Window Room, Connector, and Catwalk."""
    # Mid Dividing East Wall (separating Mid from A)
    add_box(col, "Wall_Mid_East", (42.0, 32.0, 4.5), (1.2, 28.0, 9.0), mats["sandstone_light"])

    # Sniper's Nest / Window Room (X: 30..38, Y: 48..56, Floor Z = 2.4m)
    add_box(col, "Sniper_Nest_Floor", (34.0, 52.0, 2.4), (8.0, 8.0, 0.3), mats["cedar_wood"])
    add_box(col, "Sniper_Nest_Front_Wall", (34.0, 48.0, 4.5), (8.0, 1.0, 4.2), mats["moorish_plaster_warm"])
    # Sniper Window cutout sill overlooking Mid at (34.0, 48.0, 2.4)
    add_box(col, "Sniper_Window_Sill", (34.0, 48.0, 3.1), (2.8, 1.2, 0.4), mats["cedar_wood"])

    # Connector stone staircase and arch connecting Mid to A at (40.0, 44.0)
    add_arch(col, "Arch_Connector", (40.0, 44.0, 0.0), span=3.2, height=4.2, depth=1.4, material=mats["sandstone_light"])
    add_box(col, "Connector_Stairs", (39.0, 44.0, 0.6), (3.0, 3.0, 1.2), mats["sandstone_paving"])

    # Catwalk / Short B walkway at (26.0, 44.0, Z=2.2m)
    add_box(col, "Catwalk_Walkway", (26.0, 44.0, 2.2), (3.0, 10.0, 0.3), mats["sandstone_light"])
    add_box(col, "Catwalk_Railing", (24.4, 44.0, 2.8), (0.2, 10.0, 1.0), mats["wrought_iron"])

    # Underpass subterranean floor at (30.0, 34.0, Z=-1.4m)
    add_box(col, "Underpass_Floor", (30.0, 34.0, -0.7), (4.0, 12.0, 1.4), mats["sandstone_ochre"])


def build_site_b_and_apartments(col, mats):
    """Bomb Site B, B Apartments (z=2.8m), Market/Kitchen, and Van barricade."""
    # B Apartments Building (X: 10..24, Y: 22..40, Floor Z = 2.8m)
    add_box(col, "Apts_Outer_Wall_W", (10.0, 31.0, 4.5), (1.2, 18.0, 9.0), mats["sandstone_ochre"])
    add_box(col, "Apts_Outer_Wall_E", (24.0, 31.0, 4.5), (1.2, 18.0, 9.0), mats["moorish_plaster_warm"])
    add_box(col, "Apts_Second_Floor_Slab", (17.0, 31.0, 2.8), (14.0, 18.0, 0.3), mats["cedar_wood"])

    # B Apartments Arched Jump Window overlooking B Site at (18.0, 40.0, Z=2.8m)
    add_arch(col, "Arch_B_Apts_Window", (18.0, 40.0, 2.8), span=2.6, height=3.4, depth=1.2, material=mats["sandstone_light"])

    # Delivery Van / Truck Cover on Site B at (14.0, 48.0)
    add_box(col, "SiteB_Van_Chassis", (14.0, 48.0, 0.5), (4.2, 2.2, 1.0), mats["wrought_iron"])
    add_box(col, "SiteB_Van_Body", (14.0, 48.0, 1.5), (4.0, 2.0, 1.2), mats["van_metal_olive"])
    add_box(col, "SiteB_Van_Cabin", (15.2, 48.0, 2.3), (1.6, 1.8, 0.8), mats["van_metal_olive"])

    # Site B Default Pillar and Elevated Platform at (18.0, 52.0)
    add_box(col, "SiteB_Platform", (18.0, 52.0, 0.4), (5.0, 5.0, 0.8), mats["sandstone_light"])
    add_cylinder(col, "SiteB_Central_Pillar", (18.0, 52.0, 2.2), radius=0.6, height=3.6, material=mats["sandstone_ochre"], segments=12)

    # Market / Kitchen Room (X: 18..28, Y: 56..64) with service window
    add_box(col, "Market_Wall_S", (23.0, 56.0, 2.5), (10.0, 1.0, 5.0), mats["moorish_plaster_warm"])
    add_box(col, "Market_Counter_Window", (23.0, 56.0, 1.2), (2.8, 1.2, 0.4), mats["cedar_wood"])


def build_spawns_and_props(col, mats):
    """T & CT Spawns, Date Palm Trees, Crimson Canopies, and Terracotta Urns."""
    # T Spawn Palm trees & Sun Canopy
    add_palm_tree(col, "T_Palm_1", (26.0, 10.0, 0.0), mats)
    add_palm_tree(col, "T_Palm_2", (38.0, 10.0, 0.0), mats)
    add_box(col, "T_Sun_Canopy", (32.0, 12.0, 4.2), (10.0, 6.0, 0.1), mats["canopy_crimson"])

    # CT Spawn Palm trees & Blue Frieze Colonnade
    add_palm_tree(col, "CT_Palm_1", (32.0, 62.0, 0.0), mats)
    add_palm_tree(col, "CT_Palm_2", (46.0, 62.0, 0.0), mats)

    # Terracotta Wine / Water Urns
    for ux, uy in [(30.0, 14.0), (34.0, 14.0), (48.0, 44.0), (14.0, 44.0), (54.0, 56.0)]:
        add_cylinder(col, f"Urn_{int(ux)}_{int(uy)}", (ux, uy, 0.45), radius=0.35, height=0.9, material=mats["terracotta_urn"], segments=10)


def build_mirage_scene():
    clear_scene()
    mats = setup_materials()

    c_terrain = get_or_create_collection("Terrain")
    c_site_a = get_or_create_collection("SiteA")
    c_mid = get_or_create_collection("Middle")
    c_site_b = get_or_create_collection("SiteB")
    c_props = get_or_create_collection("Props")

    build_mirage_perimeter_and_ground(c_terrain, mats)
    build_site_a_and_palace(c_site_a, mats)
    build_middle_and_window(c_mid, mats)
    build_site_b_and_apartments(c_site_b, mats)
    build_spawns_and_props(c_props, mats)


def export_glb(filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        use_selection=False,
        export_apply=True,
        export_yup=True,
    )
    print(f"Exported GLB to {filepath} ({os.path.getsize(filepath):,} bytes)")


def main():
    print("Building Desert Courtyard (hd_mirage)...")
    build_mirage_scene()

    backend_glb = os.path.abspath("backend/modules/hassault/maps/hd_mirage.glb")
    export_glb(backend_glb)

    web_glb = os.path.abspath("apps/web/public/hd_mirage.glb")
    shutil.copyfile(backend_glb, web_glb)
    print(f"Mirrored GLB to {web_glb}")
    print("=== hd_mirage 3D Generation Complete! ===")


if __name__ == "__main__":
    main()
