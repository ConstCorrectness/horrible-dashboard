#!/usr/bin/env python3
"""
High-Fidelity Procedural Generator for High-Rise Corporate Headquarters (hd_office).
Generates an ultra-realistic corporate skyscraper floor on a 64m x 64m footprint (centered at 32, 32):
- Executive Boardroom (X: 6..24, Y: 40..58) with mahogany conference table, executive chairs, and AV screen.
- IT Datacenter / Server Vault (X: 6..24, Y: 6..24) with 42U server racks, status LEDs, and observation window.
- Central Reception Lobby (X: 24..38, Y: 24..40) with marble desk and stainless steel elevators.
- Open-Plan Cubicle Office (X: 38..60, Y: 10..54) with acoustic partitions, L-desks, dual monitors, and filing cabinets.
- Perimeter curtain-wall mullioned windows with metropolitan views.
- Suspended ceiling grid with recessed fluorescent troffer lighting.

Outputs:
  - backend/modules/hassault/maps/hd_office.glb
  - apps/web/public/hd_office.glb
"""

import os
import sys

try:
    import bpy
    import bmesh
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print(
        "Error: generate_office.py must be run from within Blender 4.2+ (e.g. `blender --background --python ...`)"
    )
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cutlayout  # noqa: E402
import maplib  # noqa: E402  (a sibling, found via the path above)
import office_layout  # noqa: E402
import props  # noqa: E402


# The layout below is authored in metres (a 4 m storey, 0.75 m desks), like
# every generator here; `maplib` converts the finished scene to cubes on export.


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


def create_pbr_material(
    name,
    base_color,
    metallic=0.0,
    roughness=0.5,
    emission_color=None,
    emission_strength=0.0,
    alpha=1.0,
    bump_strength=0.0,
    bump_scale=24.0,
):
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
        mat.blend_method = "BLEND"

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
    mats["marble_floor"] = create_pbr_material(
        "mat_office_marble_floor",
        (0.88, 0.88, 0.90),
        metallic=0.05,
        roughness=0.15,
        bump_strength=0.06,
        bump_scale=30.0,
    )
    mats["carpet_tile"] = create_pbr_material(
        "mat_office_carpet_tile",
        (0.20, 0.22, 0.24),
        metallic=0.0,
        roughness=0.85,
        bump_strength=0.25,
        bump_scale=35.0,
    )
    mats["mahogany"] = create_pbr_material(
        "mat_office_mahogany",
        (0.28, 0.12, 0.08),
        metallic=0.0,
        roughness=0.25,
        bump_strength=0.22,
        bump_scale=14.0,
    )
    mats["wall_drywall"] = create_pbr_material(
        "mat_office_wall_drywall",
        (0.84, 0.85, 0.86),
        metallic=0.0,
        roughness=0.7,
        bump_strength=0.12,
        bump_scale=22.0,
    )
    mats["wall_accent"] = create_pbr_material(
        "mat_office_wall_accent",
        (0.14, 0.24, 0.35),
        metallic=0.05,
        roughness=0.5,
        bump_strength=0.10,
        bump_scale=25.0,
    )
    # The server room's raised access floor: pale tiles, not bare steel.
    mats["server_floor"] = create_pbr_material(
        "mat_office_server_floor",
        (0.62, 0.63, 0.66),
        metallic=0.0,
        roughness=0.45,
    )
    mats["stainless"] = create_pbr_material(
        "mat_office_stainless",
        (0.75, 0.76, 0.78),
        metallic=0.92,
        roughness=0.22,
        bump_strength=0.08,
        bump_scale=32.0,
    )
    mats["black_metal"] = create_pbr_material(
        "mat_office_black_metal",
        (0.08, 0.08, 0.09),
        metallic=0.7,
        roughness=0.45,
        bump_strength=0.10,
        bump_scale=28.0,
    )
    mats["smoked_glass"] = create_pbr_material(
        "mat_office_smoked_glass", (0.2, 0.25, 0.3), metallic=0.1, roughness=0.05, alpha=0.35
    )
    mats["acoustic_felt"] = create_pbr_material(
        "mat_office_acoustic_felt",
        (0.15, 0.35, 0.45),
        metallic=0.0,
        roughness=0.9,
        bump_strength=0.30,
        bump_scale=40.0,
    )
    mats["ceiling_tile"] = create_pbr_material(
        "mat_office_ceiling_tile",
        (0.92, 0.92, 0.93),
        metallic=0.0,
        roughness=0.8,
        bump_strength=0.18,
        bump_scale=20.0,
    )
    mats["fluorescent"] = create_pbr_material(
        "mat_office_fluorescent",
        (0.95, 0.97, 1.0),
        metallic=0.0,
        roughness=0.2,
        emission_color=(0.95, 0.97, 1.0),
        emission_strength=4.0,
    )
    mats["server_leds"] = create_pbr_material(
        "mat_office_server_leds",
        (0.0, 1.0, 0.6),
        metallic=0.0,
        roughness=0.2,
        emission_color=(0.0, 1.0, 0.6),
        emission_strength=6.0,
    )
    mats["screen_display"] = create_pbr_material(
        "mat_office_screen_display",
        (0.1, 0.4, 0.8),
        metallic=0.0,
        roughness=0.15,
        emission_color=(0.1, 0.5, 0.9),
        emission_strength=3.0,
    )
    # The clutter's own (see `build_natural_dressing`): archive boxes and bins.
    mats["cardboard_box"] = create_pbr_material(
        "mat_office_cardboard_box",
        (0.52, 0.40, 0.27),
        metallic=0.0,
        roughness=0.85,
    )
    mats["bin_plastic"] = create_pbr_material(
        "mat_office_bin_plastic",
        (0.20, 0.30, 0.42),
        metallic=0.0,
        roughness=0.5,
    )
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
        depth=height,
    )
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    if material:
        obj.data.materials.append(material)
    return obj


# ---------------------------------------------------------------------------
# Layout: the map is cut along a few empty lines and the halves spread apart (see
# `cutlayout.py`, and `office_layout.py` for the cuts). Every primitive goes
# through the warper.
# ---------------------------------------------------------------------------
W = cutlayout.Warper(office_layout.CUTS)

add_box = W.box(add_box)
add_cylinder = W.cylinder(add_cylinder)
add_boxes = W.boxes(maplib.add_boxes)
add_plinths = W.plinths(maplib.add_plinths)
add_wall_with_door = W.wall_with_door(maplib.add_wall_with_door)


def build_office_architecture(col, mats):
    """Floor slab, carpet zones, server raised floor, and perimeter curtain walls."""
    # Main marble floor slab (60m x 58m, centered at 32, 32, Z = 0.0)
    add_box(
        col,
        "Floor_Main_Marble",
        (32.0, 32.0, -0.2),
        (60.0, 58.0, 0.4),
        mats["marble_floor"],
    )

    # Boardroom and Server room floor offsets
    add_box(
        col,
        "Floor_Boardroom_Carpet",
        (12.0, 48.0, -0.18),
        (18.0, 20.0, 0.4),
        mats["carpet_tile"],
    )
    add_box(
        col,
        "Floor_Server_Raised",
        (12.0, 16.0, -0.1),
        (18.0, 20.0, 0.4),
        mats["server_floor"],
    )

    # High ceiling slab with acoustic drop tiles (z = 4.2m)
    add_box(
        col, "Ceiling_Main", (32.0, 32.0, 4.2), (60.0, 58.0, 0.4), mats["ceiling_tile"]
    )

    # North exterior wall with breakable curtain windows (Y = 60.0). The mullions
    # are solid: hosted play treats the glass as open, and a NonCol mullion was a
    # 0.4 m column a body stood inside, unseen from there.
    for x in range(6, 60, 8):
        add_box(
            col,
            f"Window_Glass_Curtain_N_{x}",
            (x, 60.0, 2.0),
            (7.4, 0.2, 4.0),
            mats["smoked_glass"],
        )
        add_box(
            col,
            f"Mullion_N_{x}",
            (x + 3.8, 60.0, 2.0),
            (0.4, 0.4, 4.0),
            mats["black_metal"],
        )

    # South exterior wall with breakable curtain windows (Y = 4.0)
    for x in range(6, 60, 8):
        add_box(
            col,
            f"Window_Glass_Curtain_S_{x}",
            (x, 4.0, 2.0),
            (7.4, 0.2, 4.0),
            mats["smoked_glass"],
        )
        add_box(
            col,
            f"Mullion_S_{x}",
            (x + 3.8, 4.0, 2.0),
            (0.4, 0.4, 4.0),
            mats["black_metal"],
        )

    # The curtain glass is breakable, and past it there is nothing but a drop off
    # the slab edge. An invisible collision-only barrier just outside each façade
    # keeps a shattered pane from being an exit from the map; the glass still
    # breaks and the view stays open. The barrier is flush against the glass: with
    # a 0.2 m gap the server let a body stand with its eye inside the pane.
    add_box(
        col,
        "Exterior_Barrier_N_ColOnly",
        (32.0, 60.25, 2.0),
        (60.0, 0.3, 4.0),
        mats["smoked_glass"],
    )
    add_box(
        col,
        "Exterior_Barrier_S_ColOnly",
        (32.0, 3.75, 2.0),
        (60.0, 0.3, 4.0),
        mats["smoked_glass"],
    )

    # East/West solid perimeter walls
    add_box(
        col,
        "Wall_West_Drywall",
        (3.0, 32.0, 2.0),
        (0.4, 56.0, 4.0),
        mats["wall_drywall"],
    )
    add_box(
        col,
        "Wall_East_Drywall",
        (61.0, 32.0, 2.0),
        (0.4, 56.0, 4.0),
        mats["wall_drywall"],
    )

    # Heavy structural concrete pillars
    for px in [12.0, 27.0, 42.0, 56.0]:
        for py in [14.0, 32.0, 50.0]:
            add_box(
                col,
                f"Pillar_{int(px)}_{int(py)}",
                (px, py, 2.0),
                (1.2, 1.2, 4.0),
                mats["wall_accent"],
            )


def build_executive_boardroom(col, mats):
    """Executive Boardroom (X: 4..22, Y: 38..58) with conference table, chairs, and AV screen."""
    # The boardroom is bomb site A, with three ways in: T's north corridor (the
    # gap by the north glass), T's mid lane through the double doors in the east
    # wall, and CT's door in the south wall, from the elevators. The south wall
    # runs to the east wall now: a 1 m slot at the corner was a fourth way in.
    add_wall_with_door(
        col,
        "Boardroom_Wall_E_Door",
        (22.0, 48.0, 2.0),
        (0.3, 20.0, 4.0),
        mats["wall_drywall"],
        48.0,
        2.4,
        2.6,
    )
    add_wall_with_door(
        col,
        "Boardroom_Wall_S_Door",
        (12.575, 38.0, 2.0),
        (19.15, 0.3, 4.0),
        mats["wall_drywall"],
        8.5,
        1.8,
        2.6,
    )

    # 8.0m x 2.4m Mahogany Conference Table at (12.0, 48.0, 0.75) (penetrable wood)
    add_box(
        col,
        "Wood_Table_Top_Mahogany",
        (12.0, 48.0, 0.75),
        (8.0, 2.4, 0.1),
        mats["mahogany"],
    )
    add_box(
        col, "Table_Pedestal_W", (9.5, 48.0, 0.35), (0.8, 1.4, 0.7), mats["stainless"]
    )
    add_box(
        col, "Table_Pedestal_E", (14.5, 48.0, 0.35), (0.8, 1.4, 0.7), mats["stainless"]
    )
    add_box(
        col,
        "Table_Cable_Well_NonCol",
        (12.0, 48.0, 0.76),
        (2.0, 0.3, 0.02),
        mats["black_metal"],
    )

    # Executive Leather Chairs around conference table. The backs are solid: a
    # crouched eye sat inside a NonCol back, which vanished from there.
    for i in range(5):
        cx = 9.0 + i * 1.5
        # North side
        add_box(
            col,
            f"Chair_Seat_N_{i}_NonCol",
            (cx, 49.8, 0.48),
            (0.55, 0.55, 0.08),
            mats["black_metal"],
        )
        add_box(
            col,
            f"Chair_Back_N_{i}",
            (cx, 50.05, 0.85),
            (0.55, 0.08, 0.7),
            mats["black_metal"],
        )
        # South side
        add_box(
            col,
            f"Chair_Seat_S_{i}_NonCol",
            (cx, 46.2, 0.48),
            (0.55, 0.55, 0.08),
            mats["black_metal"],
        )
        add_box(
            col,
            f"Chair_Back_S_{i}",
            (cx, 45.95, 0.85),
            (0.55, 0.08, 0.7),
            mats["black_metal"],
        )

    # 98-inch 4K Wall AV Presentation Screen on West Wall
    add_box(
        col,
        "AV_Display_Frame_NonCol",
        (3.3, 48.0, 2.2),
        (0.1, 4.2, 2.2),
        mats["black_metal"],
    )
    add_box(
        col,
        "AV_Display_Screen_NonCol",
        (3.36, 48.0, 2.2),
        (0.02, 4.0, 2.0),
        mats["screen_display"],
    )


def build_cubicle_farm(col, mats):
    """Open-plan office with acoustic divider cubicles and computer workstations (X: 38..60)."""
    for row_x in [42.0, 49.0, 56.0]:
        for pod_y in [16.0, 24.0, 34.0, 42.0, 50.0]:
            # Acoustic fabric center privacy divider (1.4m high) (penetrable)
            add_box(
                col,
                f"Wood_Cubicle_Spine_{int(row_x)}_{int(pod_y)}",
                (row_x, pod_y, 0.7),
                (4.5, 0.1, 1.4),
                mats["acoustic_felt"],
            )
            add_box(
                col,
                f"Wood_Cubicle_Divider_1_{int(row_x)}_{int(pod_y)}",
                (row_x - 2.2, pod_y, 0.7),
                (0.1, 2.6, 1.4),
                mats["acoustic_felt"],
            )
            add_box(
                col,
                f"Wood_Cubicle_Divider_2_{int(row_x)}_{int(pod_y)}",
                (row_x + 2.2, pod_y, 0.7),
                (0.1, 2.6, 1.4),
                mats["acoustic_felt"],
            )

            # Desks North and South of the spine
            add_box(
                col,
                f"Desk_N_{int(row_x)}_{int(pod_y)}",
                (row_x, pod_y + 0.65, 0.72),
                (4.0, 1.0, 0.06),
                mats["wall_drywall"],
            )
            add_box(
                col,
                f"Desk_S_{int(row_x)}_{int(pod_y)}",
                (row_x, pod_y - 0.65, 0.72),
                (4.0, 1.0, 0.06),
                mats["wall_drywall"],
            )

            # Dual monitors (NonCol)
            add_box(
                col,
                f"Monitor_N1_{int(row_x)}_{int(pod_y)}_NonCol",
                (row_x - 0.6, pod_y + 0.9, 1.05),
                (0.7, 0.05, 0.45),
                mats["black_metal"],
            )
            add_box(
                col,
                f"Screen_N1_{int(row_x)}_{int(pod_y)}_NonCol",
                (row_x - 0.6, pod_y + 0.88, 1.05),
                (0.66, 0.01, 0.41),
                mats["screen_display"],
            )

            # Filing cabinets
            add_box(
                col,
                f"Cabinet_N_{int(row_x)}_{int(pod_y)}",
                (row_x - 1.8, pod_y + 0.65, 0.35),
                (0.5, 0.7, 0.7),
                mats["black_metal"],
            )
            add_box(
                col,
                f"Cabinet_S_{int(row_x)}_{int(pod_y)}",
                (row_x + 1.8, pod_y - 0.65, 0.35),
                (0.5, 0.7, 0.7),
                mats["black_metal"],
            )


def build_reception_and_elevators(col, mats):
    """Central reception lobby with curved marble desk and twin elevators (X: 24..38, Y: 24..40)."""
    # Reception counter at (27.0, 32.0, 0.55)
    add_box(
        col,
        "Reception_Counter_Main",
        (27.0, 32.0, 0.55),
        (1.4, 5.0, 1.1),
        mats["marble_floor"],
    )
    add_box(
        col,
        "Reception_Counter_WingN",
        (27.8, 34.6, 0.55),
        (2.0, 0.8, 1.1),
        mats["marble_floor"],
    )
    add_box(
        col,
        "Reception_Counter_WingS",
        (27.8, 29.4, 0.55),
        (2.0, 0.8, 1.1),
        mats["marble_floor"],
    )
    add_box(
        col,
        "Reception_Top_Quartz",
        (27.0, 32.0, 1.12),
        (1.5, 5.2, 0.06),
        mats["stainless"],
    )

    # Twin Elevator Core at (17.0, 32.0, 2.0). 5.2 m long: at 9 m it left a
    # 1.5 m slot to the boardroom wall north of it and another to the server
    # room's south of it, and a bot steering at the mid wedged in one for a
    # minute. Now each is a 3.25 m passage out of CT's spawn.
    add_box(
        col,
        "Elevator_Shaft_Wall",
        (17.0, 32.0, 2.0),
        (3.0, 5.2, 4.0),
        mats["wall_accent"],
    )

    # Elevator A (North)
    add_box(
        col,
        "Elevator_A_Frame_NonCol",
        (18.6, 33.3, 1.5),
        (0.2, 2.4, 3.0),
        mats["stainless"],
    )
    add_box(
        col,
        "Elevator_A_DoorL_NonCol",
        (18.62, 32.7, 1.5),
        (0.05, 1.1, 2.9),
        mats["stainless"],
    )
    add_box(
        col,
        "Elevator_A_DoorR_NonCol",
        (18.62, 33.9, 1.5),
        (0.05, 1.1, 2.9),
        mats["stainless"],
    )
    add_box(
        col,
        "Elevator_A_Indicator_NonCol",
        (18.65, 33.3, 3.2),
        (0.02, 0.8, 0.25),
        mats["screen_display"],
    )

    # Elevator B (South)
    add_box(
        col,
        "Elevator_B_Frame_NonCol",
        (18.6, 30.7, 1.5),
        (0.2, 2.4, 3.0),
        mats["stainless"],
    )
    add_box(
        col,
        "Elevator_B_DoorL_NonCol",
        (18.62, 30.1, 1.5),
        (0.05, 1.1, 2.9),
        mats["stainless"],
    )
    add_box(
        col,
        "Elevator_B_DoorR_NonCol",
        (18.62, 31.3, 1.5),
        (0.05, 1.1, 2.9),
        mats["stainless"],
    )
    add_box(
        col,
        "Elevator_B_Indicator_NonCol",
        (18.65, 30.7, 3.2),
        (0.02, 0.8, 0.25),
        mats["screen_display"],
    )


def build_server_vault(col, mats):
    """High-density IT server room with glass observation wall and server banks (X: 4..22, Y: 6..26)."""
    # The north side was glass too, which put the whole site in view of CT's
    # spawn. A wall now, with CT's door at its west end, by the spawn.
    add_wall_with_door(
        col,
        "Server_Wall_N_Door",
        (12.575, 26.0, 2.0),
        (19.15, 0.3, 4.0),
        mats["wall_drywall"],
        6.0,
        1.8,
        2.6,
    )
    # The server room is bomb site B. Its east side was glass, which hosted play
    # treats as open: 20 m of site with no corner. It is a wall now, with T's two
    # doors, one off the south corridor and one off the mid lobby.
    add_wall_with_door(
        col,
        "Server_Wall_E_South_Door",
        (22.0, 9.55, 2.0),
        (0.3, 10.9, 4.0),
        mats["wall_drywall"],
        7.0,
        1.8,
        2.6,
    )
    add_wall_with_door(
        col,
        "Server_Wall_E_Mid_Door",
        (22.0, 20.55, 2.0),
        (0.3, 11.1, 4.0),
        mats["wall_drywall"],
        21.0,
        1.8,
        2.6,
    )

    # 4 rows of 42U server racks
    for r in range(4):
        rx = 7.0 + r * 3.5
        for s in range(5):
            sy = 10.0 + s * 2.4
            add_box(
                col,
                f"Server_Rack_{r}_{s}",
                (rx, sy, 1.1),
                (0.9, 1.8, 2.2),
                mats["black_metal"],
            )
            # Status indicator LED strip (NonCol)
            add_box(
                col,
                f"Server_LEDs_{r}_{s}_NonCol",
                (rx + 0.46, sy, 1.1),
                (0.02, 1.5, 1.8),
                mats["server_leds"],
            )


def build_lighting(col, mats):
    """Recessed fluorescent troffer panels (NonCol)."""
    for lx in range(8, 58, 8):
        for ly in range(8, 58, 8):
            add_box(
                col,
                f"Light_Troffer_{lx}_{ly}_NonCol",
                (lx, ly, 4.0),
                (1.4, 2.4, 0.08),
                mats["fluorescent"],
            )


def build_layout_revision(col, mats):
    """Two meeting rooms in the open middle of the floor plate.

    The band between the elevator core and the cubicles was 20 m of open marble
    from the south spawn to the boardroom door. Each room has two doors on
    different sides, so it is a route with corners rather than a box to hide in.
    """
    rooms = [
        # name, x0, x1, y0, y1, (wall, door_at) pairs
        ("Meeting_N", 26.0, 34.0, 39.0, 48.0, {"S": 30.0, "W": 43.5}),
        ("Meeting_S", 29.0, 37.0, 18.0, 26.0, {"N": 33.0, "E": 22.0}),
    ]
    trims = []
    for name, x0, x1, y0, y1, doors in rooms:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sides = {
            "S": ((cx, y0, 2.0), (x1 - x0 + 0.2, 0.2, 4.0), cx),
            "N": ((cx, y1, 2.0), (x1 - x0 + 0.2, 0.2, 4.0), cx),
            "W": ((x0, cy, 2.0), (0.2, y1 - y0, 4.0), cy),
            "E": ((x1, cy, 2.0), (0.2, y1 - y0, 4.0), cy),
        }
        for side, (center, size, _) in sides.items():
            wall = f"{name}_Wall_{side}"
            if side in doors:
                add_wall_with_door(col, wall, center, size, mats["wall_drywall"], doors[side], 1.6, 2.6)
            else:
                add_box(col, wall, center, size, mats["wall_drywall"])
            trims.append((center, size))
        add_box(col, f"{name}_Table", (cx, cy, 0.38), (3.6, 1.4, 0.76), mats["mahogany"])
    add_plinths(col, "Meeting_Baseboards_NonCol", trims, mats["black_metal"], height=0.12, proud=0.03)


def build_flank_corridors(col, mats):
    """A corridor along each glass façade, from the cubicles to a site.

    The north and south bands were open marble the whole width of the floor, so
    every route was one room. A wall across each, with a door in its middle,
    makes the façade a corridor of its own: T reach A along the north one and B
    along the south one, and the door lets either side rotate into the middle
    halfway along. Painted in the accent colour, so a corridor reads as one.
    """
    for name, y in (("Corridor_N_Wall_Door", 54.5), ("Corridor_S_Wall_Door", 9.5)):
        add_wall_with_door(
            col,
            name,
            (29.6, y, 2.0),
            (14.9, 0.2, 4.0),
            mats["wall_accent"],
            30.0,
            2.0,
            2.6,
        )


def build_dressing_and_lights(col, mats):
    """Baseboards, planters and every troffer as a light; then the sky."""
    walls = [
        ((3.0, 32.0, 2.0), (0.4, 56.0, 4.0)),
        ((61.0, 32.0, 2.0), (0.4, 56.0, 4.0)),
    ]
    add_plinths(col, "Trim_Baseboards_NonCol", walls, mats["black_metal"], height=0.12, proud=0.03)

    leaves = maplib.make_material("mat_office_plant_leaves", (0.18, 0.4, 0.16), roughness=0.8)
    spots = [(5.0, 5.5), (46.0, 6.5), (58.5, 44.5), (5.5, 56.5), (46.0, 57.5), (35.0, 36.0)]
    for i, (x, y) in enumerate(spots):
        # Solid, and as wide as the leaves: a body stood inside NonCol foliage.
        add_cylinder(col, f"Planter_{i}", (x, y, 0.3), radius=0.4, height=0.6, material=mats["black_metal"], segments=10)
    add_boxes(col, "Planter_Leaves_NonCol", [((x, y, 1.0), (0.8, 0.8, 0.9)) for x, y in spots], leaves)

    maplib.lights_from_emitters("mat_office_fluorescent", color=(235, 242, 255), radius=9.0, intensity=1.3, spacing=6.0, drop=0.3)
    maplib.lights_from_emitters("mat_office_server_leds", color=(90, 170, 255), radius=5.0, intensity=1.2, spacing=4.0, drop=-1.0)
    maplib.set_atmosphere("interior_cool")


# Where the clutter gathers, in the authored (pre-cut) frame so each zone stretches
# with the map: (x0, y0, x1, y1), density, vignette weights, lane direction or None.
# An office mid-move: archive boxes against the walls, bins, potted plants.
_OFFICE = {"crates": 4, "barrels": 1, "jars": 2}
_ZONES = [
    # The mouths of the sites' mid doors and the corridors' doors: a lane cover
    # dropped there closed B's mid door to all but a sidestep.
    ((22, 15, 28.5, 27), 0.0, _OFFICE, None),
    ((22, 43, 28.5, 53), 0.0, _OFFICE, None),
    ((26.5, 50, 33.5, 54.5), 0.0, _OFFICE, None),
    ((26.5, 9.5, 33.5, 14), 0.0, _OFFICE, None),
    # The duel line across the mid (the JSON's `duelSpawns`): kept bare, or a
    # duel's two players start behind a stack of boxes.
    ((19, 34, 39, 38), 0.0, _OFFICE, None),
    # The corridors are passages: bots slide along their walls (see Nuke's Lobby).
    ((22, 54.5, 38, 61), 0.0, _OFFICE, None),
    ((22, 3, 38, 9.5), 0.0, _OFFICE, None),
    ((3, 38, 22, 61), 0.6, _OFFICE, None),  # the boardroom, site A
    ((3, 3, 22, 26), 0.4, {"crates": 3, "barrels": 2}, None),  # the server room, site B
    ((3, 26, 22, 38), 0.5, _OFFICE, None),  # CT's elevator lobby
    ((22, 9.5, 38, 54.5), 0.7, _OFFICE, (0.0, 1.0)),  # the mid, round the meeting rooms
    ((38, 3, 62, 61), 0.3, _OFFICE, None),  # the cubicles: busy enough already
]
_DRESS_SEED = 20261020
# Anything with one of these in its name is a way through: no clutter within 2 m.
# The meeting rooms' door walls are named without the word, so they are listed.
_WAYS = (
    "Door",
    "Window",
    "Elevator",
    "Meeting_N_Wall_S",
    "Meeting_N_Wall_W",
    "Meeting_S_Wall_N",
    "Meeting_S_Wall_E",
)
# What each kind of prop is made of, by this map's own material keys.
_PROP_MATERIALS = {
    "crate": "cardboard_box",
    "barrel_wood": "bin_plastic",
    "barrel_iron": "stainless",
    "sack": "acoustic_felt",
    "jar": "black_metal",
    "rubble": "ceiling_tile",  # fallen from the grid overhead
}


def build_natural_dressing(col, mats):
    """Clutter laid out against the finished map rather than typed in.

    Plans on everything already built (so it must run last among the builders),
    keeps clear of spawns, bomb sites, doorways and the corridors, and places
    vignettes where an office gets cluttered: against walls, in corners, and a
    few pieces of cover round the meeting rooms. See `props.py`.
    """
    import json
    import random

    with open(
        os.path.join(maplib.REPO_ROOT, "backend", "modules", "hassault", "maps", "hd_office.json"),
        encoding="utf-8",
    ) as f:
        placed = json.load(f)
    x1 = office_layout.warp_metres(61.0, "x")
    y1 = office_layout.warp_metres(60.0, "y")
    plan = W.plan(
        props,
        extent=(2.0, 2.0, x1 + 2.0, y1 + 2.0),
        domain=(3.3, x1 - 0.3, 4.2, y1 - 0.2),
        placed=placed,
        ways=_WAYS,
    )
    zone_of = W.zone_of(_ZONES, (0.4, _OFFICE, None))
    rng = random.Random(_DRESS_SEED)
    vignettes = props.dress(plan, rng, zone_of, lane_budget=4)
    counts = props.emit(vignettes, col, mats, _PROP_MATERIALS)
    tally = {}
    for name, _ in vignettes:
        tally[name] = tally.get(name, 0) + 1
    print("dressing:", tally, counts)


def build_office_scene():
    clear_scene()
    mats = setup_materials()

    c_arch = get_or_create_collection("Architecture")
    c_boardroom = get_or_create_collection("Boardroom")
    c_cubicles = get_or_create_collection("Cubicles")
    c_lobby = get_or_create_collection("Lobby")
    c_server = get_or_create_collection("ServerRoom")
    c_lights = get_or_create_collection("Lighting")

    build_office_architecture(c_arch, mats)
    build_executive_boardroom(c_boardroom, mats)
    build_cubicle_farm(c_cubicles, mats)
    build_reception_and_elevators(c_lobby, mats)
    build_server_vault(c_server, mats)
    build_lighting(c_lights, mats)
    build_layout_revision(get_or_create_collection("Revision"), mats)
    build_flank_corridors(get_or_create_collection("Corridors"), mats)
    build_dressing_and_lights(get_or_create_collection("Dressing"), mats)
    build_natural_dressing(get_or_create_collection("Clutter"), mats)  # last: it plans on all of the above
    if os.environ.get("HASSAULT_OFFICE_SCAN"):
        W.dump_scan(os.environ["HASSAULT_OFFICE_SCAN"])

    print("=== High-Rise Corporate Office (hd_office) Built Successfully! ===")


def export_glb():
    """Scale the metre-authored scene to cubes and export it (see maplib)."""
    maplib.export_map_glb("hd_office")


if __name__ == "__main__":
    build_office_scene()
    export_glb()
