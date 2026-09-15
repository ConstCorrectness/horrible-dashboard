#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for Tuscan Village (hd_inferno).
Authentic competitive tournament arena on a 70m x 70m footprint:
- Banana: Curved cobblestone alley with wooden car barricade, sandbags, and wall boost.
- Bomb Site B: Open cobblestone square with central sculpted stone fountain, church stone arch, and coffins.
- Middle & Alt-Mid: Central cobblestone street, boiler room doorway, and T-ramp.
- Apartments / Boiler: Two-story residential villa with wood floor (z=2.8m) and balcony overlooking Site A.
- Bomb Site A: Graveyard stone wall, sunken Pit, bicycle cart cover, balcony platform, and CT porch.
- T & CT Spawns: Terracotta courtyards with Tuscan cypress trees, amphoras, and terracotta tile roofs.

Outputs:
  - backend/modules/hassault/maps/hd_inferno.glb
  - apps/web/public/hd_inferno.glb
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
    print("Error: generate_inferno.py must be run from within Blender (e.g. `blender --background --python ...`)")
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
    # Tuscan cobblestone & masonry
    mats["cobblestone_street"] = create_pbr_material("mat_cobblestone_street", (0.50, 0.48, 0.45), metallic=0.02, roughness=0.82)
    mats["tuscan_stucco_warm"] = create_pbr_material("mat_tuscan_stucco_warm", (0.86, 0.76, 0.62), metallic=0.01, roughness=0.88)
    mats["tuscan_stucco_ochre"] = create_pbr_material("mat_tuscan_stucco_ochre", (0.80, 0.60, 0.42), metallic=0.01, roughness=0.90)
    mats["limestone_carved"] = create_pbr_material("mat_limestone_carved", (0.72, 0.70, 0.66), metallic=0.02, roughness=0.75)
    mats["terracotta_roof"] = create_pbr_material("mat_terracotta_roof", (0.70, 0.32, 0.18), metallic=0.0, roughness=0.78)

    # Woods & Metals
    mats["italian_cypress_trunk"] = create_pbr_material("mat_italian_cypress_trunk", (0.28, 0.22, 0.18), metallic=0.0, roughness=0.92)
    mats["italian_cypress_leaves"] = create_pbr_material("mat_italian_cypress_leaves", (0.12, 0.28, 0.10), metallic=0.0, roughness=0.80)
    mats["wood_chestnut_aged"] = create_pbr_material("mat_wood_chestnut_aged", (0.34, 0.24, 0.16), metallic=0.0, roughness=0.72)
    mats["wood_crate"] = create_pbr_material("mat_wood_crate", (0.54, 0.40, 0.26), metallic=0.0, roughness=0.65)
    mats["wrought_iron"] = create_pbr_material("mat_wrought_iron", (0.16, 0.16, 0.18), metallic=0.85, roughness=0.42)
    mats["fountain_water"] = create_pbr_material("mat_fountain_water", (0.20, 0.45, 0.60), metallic=0.10, roughness=0.10, alpha=0.6)

    # Fabrics & accents
    mats["sandbag_burlap"] = create_pbr_material("mat_sandbag_burlap", (0.68, 0.60, 0.46), metallic=0.0, roughness=0.95)
    mats["lantern_brass"] = create_pbr_material("mat_lantern_brass", (0.78, 0.64, 0.24), metallic=0.88, roughness=0.22)
    mats["lantern_glow"] = create_pbr_material("mat_lantern_glow", (1.0, 0.75, 0.35), metallic=0.0, roughness=0.10, emission_color=(1.0, 0.75, 0.35), emission_strength=5.0)

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

    # Top lintel
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


def add_cypress_tree(collection, name, pos, mats):
    px, py, pz = pos
    # Trunk
    add_cylinder(collection, f"{name}_trunk", (px, py, pz + 1.5), radius=0.2, height=3.0, material=mats["italian_cypress_trunk"], segments=8)
    # Slender vertical conical foliage
    add_cylinder(collection, f"{name}_foliage_b", (px, py, pz + 4.5), radius=0.9, height=4.5, material=mats["italian_cypress_leaves"], segments=12)
    add_cylinder(collection, f"{name}_foliage_t", (px, py, pz + 7.5), radius=0.5, height=3.5, material=mats["italian_cypress_leaves"], segments=10)


def build_inferno_perimeter_and_terrain(col, mats):
    """Ground cobblestone terrain and Tuscan stucco perimeter villa walls."""
    # Main cobblestone paving slab
    add_box(col, "Terrain_Cobblestone", (35.0, 35.0, -0.5), (70.0, 70.0, 1.0), mats["cobblestone_street"])

    # High Perimeter Tuscan Villa Facades (height 10.0m)
    add_box(col, "Wall_Perimeter_South", (35.0, 4.0, 5.0), (62.0, 1.2, 10.0), mats["tuscan_stucco_warm"])
    add_box(col, "Wall_Perimeter_North", (35.0, 66.0, 5.0), (62.0, 1.2, 10.0), mats["tuscan_stucco_warm"])
    add_box(col, "Wall_Perimeter_East", (66.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["tuscan_stucco_ochre"])
    add_box(col, "Wall_Perimeter_West", (4.0, 35.0, 5.0), (1.2, 62.0, 10.0), mats["tuscan_stucco_ochre"])

    # Terracotta roof eaves trim
    add_box(col, "Roof_Eaves_S", (35.0, 4.6, 10.1), (62.0, 0.8, 0.3), mats["terracotta_roof"])
    add_box(col, "Roof_Eaves_N", (35.0, 65.4, 10.1), (62.0, 0.8, 0.3), mats["terracotta_roof"])


def build_banana(col, mats):
    """Curved Banana alleyway, car barricade, sandbags, and wall boost."""
    # Banana dividing east wall (separating Banana from Mid)
    add_box(col, "Wall_Banana_East", (26.0, 26.0, 4.5), (1.2, 28.0, 9.0), mats["tuscan_stucco_warm"])
    # Curved outer wall
    add_box(col, "Wall_Banana_West", (12.0, 28.0, 4.5), (1.2, 24.0, 9.0), mats["tuscan_stucco_ochre"])

    # Banana Car Barricade at (18.0, 28.0)
    add_box(col, "Banana_Car_Chassis", (18.0, 28.0, 0.5), (3.6, 1.8, 1.0), mats["wrought_iron"])
    add_box(col, "Banana_Car_Cabin", (17.5, 28.0, 1.3), (2.0, 1.6, 0.8), mats["wood_chestnut_aged"])

    # Sandbags cluster at (15.0, 34.0)
    add_box(col, "Banana_Sandbags_1", (15.0, 34.0, 0.4), (2.4, 0.9, 0.8), mats["sandbag_burlap"])
    add_box(col, "Banana_Sandbags_2", (15.0, 34.0, 1.0), (1.8, 0.7, 0.5), mats["sandbag_burlap"])

    # Wall Boost Wooden Cart at (24.5, 32.0)
    add_box(col, "Banana_Boost_Cart", (24.5, 32.0, 0.6), (1.4, 2.2, 1.2), mats["wood_crate"])


def build_site_b(col, mats):
    """Bomb Site B cobblestone square with central sculpted stone fountain and ruins arch."""
    # B Site perimeter walls
    add_box(col, "SiteB_North_Wall", (18.0, 62.0, 4.5), (26.0, 1.2, 9.0), mats["tuscan_stucco_warm"])
    add_box(col, "SiteB_West_Wall", (6.0, 50.0, 4.5), (1.2, 24.0, 9.0), mats["tuscan_stucco_ochre"])

    # Central Sculpted Stone Fountain at (18.0, 50.0)
    # Basin wall
    add_cylinder(col, "Fountain_Basin_Outer", (18.0, 50.0, 0.4), radius=2.6, height=0.8, material=mats["limestone_carved"], segments=20)
    add_cylinder(col, "Fountain_Water", (18.0, 50.0, 0.65), radius=2.3, height=0.3, material=mats["fountain_water"], segments=20)
    # Center pillar and bowl
    add_cylinder(col, "Fountain_Pillar", (18.0, 50.0, 1.2), radius=0.5, height=1.6, material=mats["limestone_carved"], segments=12)
    add_cylinder(col, "Fountain_Bowl", (18.0, 50.0, 2.0), radius=1.1, height=0.4, material=mats["limestone_carved"], segments=16)

    # Church / Ruins Stone Archway at (14.0, 56.0)
    add_arch(col, "Ruins_Church_Arch", (14.0, 56.0, 0.0), span=4.2, height=4.6, depth=1.6, material=mats["limestone_carved"])

    # Coffins / Wooden Box Stack (22.0, 52.0)
    add_box(col, "SiteB_Coffins_1", (22.0, 52.0, 0.6), (1.3, 2.4, 1.2), mats["wood_crate"])
    add_box(col, "SiteB_Coffins_2", (22.0, 52.0, 1.6), (1.1, 1.8, 0.8), mats["wood_crate"])

    # Construction Ramp connecting B Site to CT
    add_box(col, "SiteB_CT_Ramp", (25.0, 57.0, 0.6), (4.0, 6.0, 1.2), mats["cobblestone_street"])


def build_middle_and_alt_mid(col, mats):
    """Middle street, Alt-Mid, boiler room entrance, and T-ramp."""
    # Mid dividing east wall
    add_box(col, "Wall_Mid_East", (42.0, 26.0, 4.5), (1.2, 32.0, 9.0), mats["tuscan_stucco_warm"])

    # Boiler Room doorway portal at (36.0, 30.0)
    add_arch(col, "Arch_Boiler_Door", (36.0, 30.0, 0.0), span=2.8, height=3.6, depth=1.2, material=mats["limestone_carved"])

    # T-Ramp ascending from T Spawn into Mid
    add_box(col, "Mid_TRamp", (34.0, 18.0, 0.5), (6.0, 8.0, 1.0), mats["cobblestone_street"])

    # Hay cart cover in Alt-Mid at (32.0, 24.0)
    add_box(col, "AltMid_Cart_Bed", (32.0, 24.0, 0.6), (2.0, 3.2, 1.2), mats["wood_chestnut_aged"])


def build_apartments(col, mats):
    """Two-story residential villa apartments with wood floors and Site A balcony."""
    # Ground floor walls of Apartments (X: 34..46, Y: 32..46, Z: 0..5.6)
    add_box(col, "Apts_Outer_Wall_W", (34.0, 38.0, 2.8), (1.2, 14.0, 5.6), mats["tuscan_stucco_ochre"])
    add_box(col, "Apts_Outer_Wall_N", (40.0, 46.0, 2.8), (12.0, 1.2, 5.6), mats["tuscan_stucco_warm"])

    # 2nd floor wooden floor slab (Z = 2.8m)
    add_box(col, "Apts_Second_Floor_Slab", (40.0, 39.0, 2.8), (10.0, 12.0, 0.3), mats["wood_chestnut_aged"])

    # Balcony overlooking Site A at (46.0, 42.0, Z=2.8m)
    add_box(col, "Apts_Balcony_Floor", (47.0, 42.0, 2.8), (3.0, 4.0, 0.3), mats["wood_chestnut_aged"])
    add_box(col, "Apts_Balcony_Railing", (48.4, 42.0, 3.4), (0.1, 4.0, 1.0), mats["wrought_iron"])

    # Internal apartment stairs leading from ground to 2nd floor
    add_box(col, "Apts_Interior_Stairs", (37.0, 34.0, 1.4), (3.0, 4.0, 2.8), mats["wood_chestnut_aged"])


def build_site_a(col, mats):
    """Bomb Site A with Graveyard stone wall, sunken Pit, bicycle cart, and porch."""
    # Graveyard stone boundary wall at (56.0, 56.0)
    add_box(col, "Graveyard_Wall_W", (54.0, 56.0, 1.5), (0.8, 8.0, 3.0), mats["limestone_carved"])
    add_box(col, "Graveyard_Wall_N", (58.0, 60.0, 1.5), (8.0, 0.8, 3.0), mats["limestone_carved"])

    # Sunken Pit at (54.0, 44.0, Z = -1.2m)
    add_box(col, "SiteA_Pit_Floor", (54.0, 44.0, -0.6), (6.0, 6.0, 1.2), mats["cobblestone_street"])
    add_box(col, "SiteA_Pit_Ramp", (54.0, 48.0, -0.3), (4.0, 3.0, 0.6), mats["cobblestone_street"])

    # Moto / Bicycle Cart Cover at (48.0, 52.0)
    add_box(col, "SiteA_Bicycle_Cart", (48.0, 52.0, 0.6), (1.4, 2.4, 1.2), mats["wood_crate"])

    # Default A Plant Boxes at (50.0, 50.0)
    add_box(col, "SiteA_Default_Boxes_1", (50.0, 50.0, 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "SiteA_Default_Boxes_2", (50.0, 51.3, 0.6), (1.2, 1.2, 1.2), mats["wood_crate"])
    add_box(col, "SiteA_Default_Boxes_Top", (50.0, 50.0, 1.7), (1.1, 1.1, 1.0), mats["wood_crate"])

    # Porch entrance from CT spawn
    add_arch(col, "SiteA_CT_Porch_Arch", (48.0, 58.0, 0.0), span=3.6, height=4.2, depth=1.4, material=mats["limestone_carved"])


def build_spawns_and_props(col, mats):
    """T and CT Spawns, Tuscan cypress trees, amphoras, and lanterns."""
    # T Spawn Cypress trees
    add_cypress_tree(col, "T_Cypress_1", (26.0, 8.0, 0.0), mats)
    add_cypress_tree(col, "T_Cypress_2", (38.0, 8.0, 0.0), mats)

    # CT Spawn Cypress trees
    add_cypress_tree(col, "CT_Cypress_1", (32.0, 62.0, 0.0), mats)
    add_cypress_tree(col, "CT_Cypress_2", (44.0, 62.0, 0.0), mats)

    # Site A Exterior Cypress tree
    add_cypress_tree(col, "SiteA_Cypress", (62.0, 48.0, 0.0), mats)

    # Terracotta wine amphoras / urns
    for ax, ay in [(28.0, 12.0), (28.8, 12.2), (36.0, 12.0), (52.0, 56.0)]:
        add_cylinder(col, f"Amphora_{int(ax*10)}", (ax, ay, 0.45), radius=0.35, height=0.9, material=mats["terracotta_roof"], segments=12)

    # Wrought iron hanging wall lanterns
    for lx, ly, lz in [(35.0, 31.0, 3.2), (18.0, 46.0, 2.8), (48.0, 56.0, 3.0)]:
        add_box(col, f"Lantern_Body_{int(lx)}_{int(ly)}", (lx, ly, lz), (0.25, 0.25, 0.4), mats["wrought_iron"])
        add_box(col, f"Lantern_Glow_{int(lx)}_{int(ly)}", (lx, ly, lz), (0.15, 0.15, 0.25), mats["lantern_glow"])


def build_inferno_scene():
    clear_scene()
    mats = setup_materials()

    c_terrain = get_or_create_collection("Terrain")
    c_banana = get_or_create_collection("Banana")
    c_site_b = get_or_create_collection("SiteB")
    c_mid = get_or_create_collection("Middle")
    c_apts = get_or_create_collection("Apartments")
    c_site_a = get_or_create_collection("SiteA")
    c_props = get_or_create_collection("Props")

    build_inferno_perimeter_and_terrain(c_terrain, mats)
    build_banana(c_banana, mats)
    build_site_b(c_site_b, mats)
    build_middle_and_alt_mid(c_mid, mats)
    build_apartments(c_apts, mats)
    build_site_a(c_site_a, mats)
    build_spawns_and_props(c_props, mats)

    print("=== Tuscan Citadel (hd_inferno) Built Successfully! ===")


def export_glb():
    backend_map_dir = "/home/horrible/horrible-dashboard/backend/modules/hassault/maps"
    web_public_dir = "/home/horrible/horrible-dashboard/apps/web/public"

    os.makedirs(backend_map_dir, exist_ok=True)
    os.makedirs(web_public_dir, exist_ok=True)

    backend_glb_path = os.path.join(backend_map_dir, "hd_inferno.glb")
    web_glb_path = os.path.join(web_public_dir, "hd_inferno.glb")

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
    print("=== Inferno Generation & Export Complete! ===")


if __name__ == "__main__":
    build_inferno_scene()
    export_glb()
