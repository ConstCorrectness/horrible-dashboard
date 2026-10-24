#!/usr/bin/env python3
"""
hd_aim: the aim range, modelled as an indoor training range.

Builds around the brush map it replaces, at the same positions: the drills'
shooters and target rails (`hd_aim.json` `drills`, read by `drills.py`) are in
cubes, a cube is a third of a metre on every map, so authoring in metres as
cubes / 3 leaves every rail, spawn and item where it was.

- One hall, x 1.33..41.33 m, y 1.33..32.67 m, 8 m to the roof.
- Three lanes split by concrete dividers (y 10.33..11 and 21.33..22), open at
  both ends so a deathmatch can cross between them.
- A raised steel booth at the near end of each lane, where the shooter stands.
- A floor rail under every target post, painted distance lines, and boards on
  the walls at each post.
- Overhead baffles and high-bay lights; acoustic panels on the long walls.

Run headless on Blender 4.1, then bake and fit like any modelled map:

    blender --background --factory-startup --python tools/blender/generate_aim.py
"""

import json
import os
import sys

try:
    import bmesh
    import bpy
    from mathutils import Vector
except ImportError:
    print(
        "Error: generate_aim.py must be run from within Blender (e.g. `blender --background --python ...`)"
    )
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maplib  # noqa: E402

C = 1.0 / 3.0  # one cube, in metres

# The hall and the old brushes, in cubes.
X0, X1, Y0, Y1 = 4, 124, 4, 98
HEIGHT = 8.0
DIVIDERS = [(31, 33), (64, 66)]
DIVIDER_X = (16, 120)
BOOTHS = [(7, 14, 6, 7), (7, 45, 6, 7), (7, 78, 6, 7)]  # x, y, w, h

AIM_JSON = os.path.join(
    maplib.REPO_ROOT, "backend", "modules", "hassault", "maps", "hd_aim.json"
)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def collection(name):
    col = bpy.data.collections.get(name)
    if not col:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def add_box(col, name, center, size, material):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj.data.materials.append(material)
    return obj


def span(col, name, x0, y0, z0, x1, y1, z1, material):
    """A box between two corners, in metres."""
    return add_box(
        col,
        name,
        ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
        (x1 - x0, y1 - y0, z1 - z0),
        material,
    )


def add_cylinder(col, name, center, radius, height, material, segments=16):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=height,
    )
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj.data.materials.append(material)
    return obj


def materials():
    m = maplib.make_material
    return {
        "floor": m("Mat_Aim_Concrete_Floor", (0.46, 0.46, 0.45), roughness=0.85),
        "wall": m("Mat_Aim_Block_Wall", (0.58, 0.58, 0.56), roughness=0.9),
        "divider": m("Mat_Aim_Divider_Concrete", (0.52, 0.51, 0.49), roughness=0.88),
        "booth": m(
            "Mat_Aim_Booth_Plate", (0.36, 0.38, 0.4), metallic=0.85, roughness=0.35
        ),
        "steel": m(
            "Mat_Aim_Steel_Structure", (0.24, 0.27, 0.3), metallic=0.8, roughness=0.45
        ),
        "rail": m("Mat_Aim_Rail_Steel", (0.5, 0.52, 0.55), metallic=0.9, roughness=0.3),
        "roof": m("Mat_Aim_Steel_Roof", (0.3, 0.31, 0.33), metallic=0.6, roughness=0.7),
        "felt": m("Mat_Aim_Acoustic_Felt", (0.2, 0.22, 0.26), roughness=0.95),
        "baffle": m("Mat_Aim_Baffle_Plywood", (0.62, 0.5, 0.34), roughness=0.8),
        "backstop": m("Mat_Aim_Backstop_Rubber", (0.08, 0.08, 0.09), roughness=0.95),
        "yellow": m("Mat_Aim_Line_Yellow_Stripe", (0.9, 0.72, 0.1), roughness=0.6),
        "white": m("Mat_Aim_Line_White_Stripe", (0.88, 0.88, 0.86), roughness=0.6),
        "board": m("Mat_Aim_Distance_Sign", (0.92, 0.76, 0.12), roughness=0.5),
        "ink": m("Mat_Aim_Distance_Sign_Ink", (0.05, 0.05, 0.06), roughness=0.6),
        "glow": m(
            "Mat_Aim_Highbay_Glow",
            (0.98, 0.99, 1.0),
            emission=(0.98, 0.99, 1.0),
            strength=10.0,
        ),
        "hazard": m("Mat_Aim_Hazard_Stripe", (0.92, 0.76, 0.08), roughness=0.5),
    }


def build_shell(col, mats):
    x0, x1, y0, y1 = X0 * C, X1 * C, Y0 * C, Y1 * C
    t = 0.4
    span(col, "Range_Floor", x0 - t, y0 - t, -0.3, x1 + t, y1 + t, 0.0, mats["floor"])
    span(col, "Range_Wall_West", x0 - t, y0 - t, 0.0, x0, y1 + t, HEIGHT, mats["wall"])
    span(col, "Range_Wall_East", x1, y0 - t, 0.0, x1 + t, y1 + t, HEIGHT, mats["wall"])
    span(col, "Range_Wall_South", x0, y0 - t, 0.0, x1, y0, HEIGHT, mats["wall"])
    span(col, "Range_Wall_North", x0, y1, 0.0, x1, y1 + t, HEIGHT, mats["wall"])
    span(
        col,
        "Range_Roof",
        x0 - t,
        y0 - t,
        HEIGHT,
        x1 + t,
        y1 + t,
        HEIGHT + 0.3,
        mats["roof"],
    )
    # Bullet catcher: rubber cladding over the far wall, a hazard band above it.
    span(col, "Backstop_Rubber", x1 - 0.25, y0, 0.0, x1, y1, 4.2, mats["backstop"])
    span(
        col,
        "Backstop_Hazard_Stripe_NonCol",
        x1 - 0.27,
        y0,
        4.2,
        x1 - 0.02,
        y1,
        4.5,
        mats["hazard"],
    )
    # Acoustic panels along both long walls, above head height.
    for i, x in enumerate(range(8, 40, 4)):
        span(
            col,
            f"Acoustic_Panel_S{i}_NonCol",
            x,
            y0,
            2.4,
            x + 3.2,
            y0 + 0.08,
            5.6,
            mats["felt"],
        )
        span(
            col,
            f"Acoustic_Panel_N{i}_NonCol",
            x,
            y1 - 0.08,
            2.4,
            x + 3.2,
            y1,
            5.6,
            mats["felt"],
        )


def build_dividers(col, mats):
    dx0, dx1 = DIVIDER_X[0] * C, DIVIDER_X[1] * C
    for i, (ya, yb) in enumerate(DIVIDERS):
        span(
            col,
            f"Lane_Divider_{i}",
            dx0,
            ya * C,
            0.0,
            dx1,
            yb * C,
            HEIGHT,
            mats["divider"],
        )
        # A steel cap and kick plate, so the divider reads as built, not a slab.
        span(
            col,
            f"Lane_Divider_{i}_Kick_NonCol",
            dx0,
            ya * C - 0.03,
            0.0,
            dx1,
            yb * C + 0.03,
            0.3,
            mats["steel"],
        )
        span(
            col,
            f"Lane_Divider_{i}_Hazard_Stripe_NonCol",
            dx0 - 0.03,
            ya * C,
            0.0,
            dx0,
            yb * C,
            1.6,
            mats["hazard"],
        )


def build_booths(col, mats):
    for i, (bx, by, bw, bh) in enumerate(BOOTHS):
        x0, y0, x1, y1 = bx * C, by * C, (bx + bw) * C, (by + bh) * C
        # One cube up, like the brush booth it replaces.
        span(col, f"Booth_{i}_Plinth", x0, y0, 0.0, x1, y1, C, mats["booth"])
        # Side screens, waist to head height, and a baffle overhead.
        for side, y in (("S", y0), ("N", y1)):
            span(
                col,
                f"Booth_{i}_Screen_{side}",
                x0,
                y - 0.03,
                C,
                x1 - 0.4,
                y + 0.03,
                C + 1.9,
                mats["steel"],
            )
        span(
            col,
            f"Booth_{i}_Canopy_NonCol",
            x0 - 0.2,
            y0 - 0.1,
            2.9,
            x1 + 0.2,
            y1 + 0.1,
            3.0,
            mats["baffle"],
        )
        span(
            col,
            f"Booth_{i}_Firing_Line_Stripe_NonCol",
            x1,
            y0,
            0.0,
            x1 + 0.12,
            y1,
            0.008,
            mats["yellow"],
        )


def build_rails_and_markings(col, mats, drills):
    lanes = [
        (Y0, DIVIDERS[0][0]),
        (DIVIDERS[0][1], DIVIDERS[1][0]),
        (DIVIDERS[1][1], Y1),
    ]
    boards = 0
    for drill, (ly0, ly1) in zip(drills, lanes):
        sx = drill["shooter"][0]
        for k, (px, py, hw) in enumerate(drill["posts"]):
            # The rail the target runs along, flush with the floor.
            span(
                col,
                f"Rail_{drill['id']}_{k}_NonCol",
                px * C - 0.06,
                (py - hw) * C,
                0.0,
                px * C + 0.06,
                (py + hw) * C,
                0.04,
                mats["rail"],
            )
        for px in sorted({p[0] for p in drill["posts"]}):
            # A distance line across the lane at each post, and its board on the
            # lane's south wall face: bars for every 10 cubes from the shooter.
            span(
                col,
                f"Line_{drill['id']}_{px}_Stripe_NonCol",
                px * C - 0.05,
                ly0 * C,
                0.0,
                px * C + 0.05,
                ly1 * C,
                0.008,
                mats["white"],
            )
            wall_y = ly0 * C
            span(
                col,
                f"Board_{drill['id']}_{px}_Sign_NonCol",
                px * C - 0.5,
                wall_y,
                3.0,
                px * C + 0.5,
                wall_y + 0.04,
                3.7,
                mats["board"],
            )
            bars = max(1, round((px - sx) / 10))
            for b in range(min(bars, 10)):
                bx = px * C - 0.42 + b * 0.085
                span(
                    col,
                    f"Board_{drill['id']}_{px}_Ink_{b}_Sign_NonCol",
                    bx,
                    wall_y + 0.04,
                    3.12,
                    bx + 0.05,
                    wall_y + 0.05,
                    3.58,
                    mats["ink"],
                )
            boards += 1
    print(f"aim: {boards} distance boards")


def build_roof_structure(col, mats):
    x0, x1, y0, y1 = X0 * C, X1 * C, Y0 * C, Y1 * C
    # Trusses across the hall every 5 m, purlins along it.
    x = x0 + 2.5
    i = 0
    while x < x1:
        span(
            col,
            f"Truss_{i}_NonCol",
            x - 0.1,
            y0,
            HEIGHT - 0.9,
            x + 0.1,
            y1,
            HEIGHT - 0.7,
            mats["steel"],
        )
        span(
            col,
            f"Truss_{i}_Top_NonCol",
            x - 0.1,
            y0,
            HEIGHT - 0.12,
            x + 0.1,
            y1,
            HEIGHT,
            mats["steel"],
        )
        x += 5.0
        i += 1
    # Overhead baffles across each lane: a range keeps rounds off the roof.
    for k, bx in enumerate((12.0, 22.0, 32.0)):
        span(
            col,
            f"Baffle_{k}_NonCol",
            bx - 0.6,
            y0,
            5.4,
            bx + 0.6,
            y1,
            5.5,
            mats["baffle"],
        )
    # High-bay lights down the middle of each lane.
    centres = [
        (Y0 + DIVIDERS[0][0]) / 2,
        (DIVIDERS[0][1] + DIVIDERS[1][0]) / 2,
        (DIVIDERS[1][1] + Y1) / 2,
    ]
    n = 0
    for cy in centres:
        for lx in (6.0, 13.0, 20.0, 27.0, 34.0):
            add_cylinder(
                col,
                f"Highbay_{n}_Lamp_NonCol",
                (lx, cy * C, HEIGHT - 1.3),
                0.32,
                0.12,
                mats["glow"],
                segments=20,
            )
            add_cylinder(
                col,
                f"Highbay_{n}_Housing_NonCol",
                (lx, cy * C, HEIGHT - 1.18),
                0.36,
                0.14,
                mats["steel"],
                segments=20,
            )
            n += 1


def build_architectural_detail(col, mats):
    """Add motorized overhead target carrier tracks down each lane, lead-fume
    HVAC extraction ductwork, shooter booth digital scoring monitors, and an
    upper Range Master observation gallery on the West wall (`_NonCol`).
    """
    x0, x1, y0, y1 = X0 * C, X1 * C, Y0 * C, Y1 * C
    centres = [
        (Y0 + DIVIDERS[0][0]) / 2,
        (DIVIDERS[0][1] + DIVIDERS[1][0]) / 2,
        (DIVIDERS[1][1] + Y1) / 2,
    ]

    rail_boxes = []
    steel_boxes = []
    baffle_boxes = []
    ink_boxes = []
    glow_boxes = []

    # 1. Overhead target carrier monorails & truss hangers down each lane
    for cy in centres:
        ym = cy * C
        rail_boxes.append((((x0 + x1) * 0.5 + 1.5, ym, 6.15), (34.0, 0.14, 0.16)))
        for hx in range(8, 39, 5):
            steel_boxes.append(((float(hx), ym, 6.62), (0.12, 0.12, 0.76)))

    # 2. Lead-fume HVAC extraction ducts along North & South upper walls + backstop hood
    steel_boxes.append((((x0 + x1) * 0.5, y0 + 0.65, 6.65), (37.0, 0.65, 0.55)))
    steel_boxes.append((((x0 + x1) * 0.5, y1 - 0.65, 6.65), (37.0, 0.65, 0.55)))
    steel_boxes.append(((x1 - 0.55, (y0 + y1) * 0.5, 5.05), (0.75, y1 - y0 - 0.8, 0.65)))

    # 3. Shooter booth digital scoring terminals & acoustic canopy ribs
    for bx, by, bw, bh in BOOTHS:
        bx0, by0, bx1, by1 = bx * C, by * C, (bx + bw) * C, (by + bh) * C
        bcx = (bx0 + bx1) * 0.5
        # Digital lane monitor mounted on the North screen inner face (above waist)
        ink_boxes.append(((bcx, by1 - 0.065, C + 1.42), (0.52, 0.04, 0.34)))
        glow_boxes.append(((bcx, by1 - 0.095, C + 1.42), (0.44, 0.015, 0.26)))
        # Acoustic wood ribs under booth canopy
        for rx in (bx0 + 0.25, bcx, bx1 - 0.25):
            baffle_boxes.append(((rx, (by0 + by1) * 0.5, 2.84), (0.12, (by1 - by0) - 0.1, 0.08)))

    maplib.add_boxes(col, "Aim_Overhead_Rails_NonCol", rail_boxes, mats["rail"])
    maplib.add_boxes(col, "Aim_HVAC_Ducts_NonCol", steel_boxes, mats["steel"])
    maplib.add_boxes(col, "Aim_Booth_Ribs_NonCol", baffle_boxes, mats["baffle"])
    maplib.add_boxes(col, "Aim_Booth_MonitorBezels_NonCol", ink_boxes, mats["ink"])
    maplib.add_boxes(col, "Aim_Booth_MonitorScreens_NonCol", glow_boxes, mats["board"])

    # 4. Elevated Range Master Observation Gallery along the West wall (z = 4.2..6.0m)
    win_mats = {
        "trim": mats["steel"],
        "pane": mats["ink"],
    }
    maplib.add_window_row(
        col,
        "RangeMaster_Gallery",
        (x0 - 0.2, (y0 + y1) * 0.5, HEIGHT * 0.5),
        (0.4, y1 - y0, HEIGHT),
        "+x",
        z_base=4.2,
        count=5,
        win_size=(2.2, 1.35),
        mats=win_mats,
        style="industrial",
    )


def build():
    clear_scene()
    mats = materials()
    with open(AIM_JSON, encoding="utf-8") as f:
        drills = json.load(f)["drills"]
    build_shell(collection("Shell"), mats)
    build_dividers(collection("Lanes"), mats)
    build_booths(collection("Booths"), mats)
    build_rails_and_markings(collection("Markings"), mats, drills)
    build_roof_structure(collection("Roof"), mats)
    build_architectural_detail(collection("Arch_Detail"), mats)
    maplib.lights_from_emitters(
        "Mat_Aim_Highbay_Glow",
        color=(236, 242, 255),
        radius=16.0,
        intensity=1.8,
        spacing=4.0,
        drop=0.4,
    )
    maplib.set_atmosphere("interior_cool")


if __name__ == "__main__":
    build()
    maplib.export_map_glb("hd_aim")
