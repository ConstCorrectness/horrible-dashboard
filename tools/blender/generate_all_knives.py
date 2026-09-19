#!/usr/bin/env python3
"""Procedural 3D Knife Suite & Rarity Texture Generator for Blender 4.2+

Designs and builds 6 competitive-grade tactical knives with CS-inspired geometry:
1. Default Tactical Tanto Combat Knife (M9/Tanto hybrid, serrated spine, fuller, glass breaker)
2. Karambit (Curved raptor claw, reverse-grip scales, safety index finger ring)
3. Butterfly Knife / Balisong (Clip-point swedge blade, dual channeled skeleton handles, latch)
4. M9 Bayonet (Military combat bayonet, sawback spine, muzzle-ring guard, barrel lug)
5. Skeleton Knife (One-piece full-tang, large finger hole, paracord wrapped skeletal handle)
6. Huntsman Knife (Heavy survival recurve tanto, double saw teeth, tactical grip scales)

Generates authentic PBR rarity finish textures:
- Fade / Marble Fade (Chromatic tri-color anodized gradient)
- Case Hardened "Blue Gem" (Heat-treated tempered steel with cobalt blue and fire scale)
- Crimson Web (Deep blood-red lacquer with procedural spiderweb lattice)
- Damascus Steel (Acid-etched folding billet topographical contours)
- Doppler Phase 2 (Sapphire & celestial nebula galaxy smoke)
- Lore (24k polished gold blade with Celtic knotwork filigree)
- Tiger Tooth (Golden amber base with laser-etched tiger claw stripes)
- Slaughter (High-gloss reflective ruby zebra chrome)
"""

import sys
import os
import math
from pathlib import Path

try:
    import bpy
    import mathutils
    from mathutils import Vector, Matrix, Euler
except ImportError:
    print("Error: Run this script inside Blender: blender -b -P tools/blender/generate_all_knives.py")
    sys.exit(1)


def clear_objects():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def full_clear():
    clear_objects()
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)
    for img in list(bpy.data.images):
        bpy.data.images.remove(img, do_unlink=True)


def create_texture_image(name, width, height, generator_fn):
    """Generates an embedded Blender image with custom pixel logic."""
    img = bpy.data.images.new(name, width=width, height=height, alpha=True)
    pixels = [0.0] * (width * height * 4)
    for y in range(height):
        ny = y / float(height - 1)
        for x in range(width):
            nx = x / float(width - 1)
            r, g, b, a = generator_fn(nx, ny)
            idx = (y * width + x) * 4
            pixels[idx] = max(0.0, min(1.0, r))
            pixels[idx + 1] = max(0.0, min(1.0, g))
            pixels[idx + 2] = max(0.0, min(1.0, b))
            pixels[idx + 3] = max(0.0, min(1.0, a))
    img.pixels = pixels
    img.pack()
    return img


# --- Procedural Texture Generators ---

def tex_fade(nx, ny):
    """Amber Gold -> Vivid Magenta -> Deep Cyan Blue -> Violet gradient with brushed sheen."""
    t = nx * 0.75 + ny * 0.25
    brush = (((int(nx * 512) * 17 + int(ny * 512) * 31) % 19) / 19.0 - 0.5) * 0.04
    if t < 0.33:
        p = t / 0.33
        r = 1.0 * (1.0 - p) + 0.95 * p
        g = 0.75 * (1.0 - p) + 0.15 * p
        b = 0.08 * (1.0 - p) + 0.58 * p
    elif t < 0.68:
        p = (t - 0.33) / 0.35
        r = 0.95 * (1.0 - p) + 0.12 * p
        g = 0.15 * (1.0 - p) + 0.65 * p
        b = 0.58 * (1.0 - p) + 0.98 * p
    else:
        p = (t - 0.68) / 0.32
        r = 0.12 * (1.0 - p) + 0.45 * p
        g = 0.65 * (1.0 - p) + 0.10 * p
        b = 0.98 * (1.0 - p) + 0.88 * p
    return (r + brush, g + brush, b + brush, 1.0)


def tex_marble_fade(nx, ny):
    """Tricolor Red / Gold / Blue Marbled Fade."""
    angle = nx * 2.5 + math.sin(ny * 8.0) * 0.35
    t = (angle % 3.0) / 3.0
    if t < 0.35:
        p = t / 0.35
        r, g, b = (0.95 * (1.0 - p) + 1.0 * p, 0.15 * (1.0 - p) + 0.80 * p, 0.18 * (1.0 - p) + 0.10 * p)
    elif t < 0.70:
        p = (t - 0.35) / 0.35
        r, g, b = (1.0 * (1.0 - p) + 0.12 * p, 0.80 * (1.0 - p) + 0.55 * p, 0.10 * (1.0 - p) + 0.98 * p)
    else:
        p = (t - 0.70) / 0.30
        r, g, b = (0.12 * (1.0 - p) + 0.95 * p, 0.55 * (1.0 - p) + 0.15 * p, 0.98 * (1.0 - p) + 0.18 * p)
    return (r, g, b, 1.0)


def tex_case_hardened(nx, ny):
    """Tempered heat-treated steel with vibrant blue gem islands and gold/purple oxides."""
    s1 = math.sin(nx * 14.0 + math.cos(ny * 11.0) * 2.0)
    s2 = math.cos(ny * 16.0 + math.sin(nx * 12.0) * 1.8)
    val = (s1 + s2) * 0.5
    if val > 0.35:
        p = (val - 0.35) / 0.65
        r = 0.12 * (1.0 - p) + 0.22 * p
        g = 0.55 * (1.0 - p) + 0.82 * p
        b = 0.95 * (1.0 - p) + 1.00 * p
    elif val > 0.05:
        p = (val - 0.05) / 0.30
        r = 0.68 * (1.0 - p) + 0.12 * p
        g = 0.22 * (1.0 - p) + 0.55 * p
        b = 0.72 * (1.0 - p) + 0.95 * p
    elif val > -0.35:
        p = (val + 0.35) / 0.40
        r = 0.88 * (1.0 - p) + 0.68 * p
        g = 0.68 * (1.0 - p) + 0.22 * p
        b = 0.15 * (1.0 - p) + 0.72 * p
    else:
        r, g, b = (0.35, 0.33, 0.30)
    return (r, g, b, 1.0)


def tex_crimson_web(nx, ny):
    """Glossy ruby red with black spiderweb lattice lines."""
    cx, cy = 0.45, 0.55
    dx, dy = nx - cx, ny - cy
    dist = math.sqrt(dx * dx + dy * dy)
    angle = math.atan2(dy, dx)
    is_spoke = abs((angle * 4.0 / math.pi) % 1.0 - 0.5) < 0.06
    ring = (dist * 18.0) % 1.0
    is_ring = abs(ring - 0.5) < 0.07 and dist > 0.04
    if is_spoke or is_ring:
        return (0.08, 0.08, 0.09, 1.0)
    grain = (((int(nx * 256) * 13 + int(ny * 256) * 37) % 23) / 23.0 - 0.5) * 0.03
    return (0.82 + grain, 0.06 + grain, 0.08 + grain, 1.0)


def tex_damascus(nx, ny):
    """Flowing topographical acid-etched damascus billet waves."""
    w1 = math.sin(nx * 32.0 + math.sin(ny * 16.0) * 3.5)
    w2 = math.cos(ny * 48.0 + math.cos(nx * 24.0) * 2.8)
    wave = (w1 + w2) * 0.5
    bright = 0.5 + 0.5 * math.sin(wave * 7.0)
    v = 0.28 + 0.58 * bright
    return (v * 0.98, v, v * 1.02, 1.0)


def tex_doppler_phase2(nx, ny):
    """Sapphire base with cosmic magenta/ruby galaxy smoke."""
    nebula = math.sin(nx * 9.0 + math.sin(ny * 12.0) * 1.5) * math.cos(ny * 10.0 + nx * 5.0)
    if nebula > 0.15:
        p = (nebula - 0.15) / 0.85
        r = 0.94 * p + 0.15 * (1.0 - p)
        g = 0.18 * p + 0.12 * (1.0 - p)
        b = 0.65 * p + 0.35 * (1.0 - p)
    else:
        p = max(0.0, (nebula + 1.0) / 1.15)
        r = 0.05 * p + 0.02 * (1.0 - p)
        g = 0.08 * p + 0.04 * (1.0 - p)
        b = 0.35 * p + 0.12 * (1.0 - p)
    return (r, g, b, 1.0)


def tex_lore_gold(nx, ny):
    """24k Mirror Gold with green dragon filigree accents."""
    knot = math.sin(nx * 28.0) * math.cos(ny * 24.0)
    if abs(knot) > 0.45:
        return (0.12, 0.52, 0.22, 1.0)
    return (0.98, 0.82, 0.20, 1.0)


def tex_tiger_tooth(nx, ny):
    """Amber gold base with laser-etched tiger stripes."""
    stripe = math.sin(nx * 32.0 + ny * 18.0 + math.sin(ny * 36.0) * 0.8)
    if stripe > 0.55:
        return (0.28, 0.12, 0.04, 1.0)
    return (0.98, 0.72, 0.10, 1.0)


def tex_slaughter(nx, ny):
    """Vibrant ruby lacquer with zebra chrome reflections."""
    zebra = math.sin(nx * 24.0 + math.sin(ny * 16.0) * 2.2)
    if zebra > 0.25:
        return (0.85, 0.14, 0.16, 1.0)
    return (0.58, 0.08, 0.10, 1.0)


def tex_steel(nx, ny):
    """Brushed high-carbon stainless steel with fine longitudinal grain."""
    grain = (((int(nx * 512) * 23 + int(ny * 512) * 41) % 17) / 17.0 - 0.5) * 0.03
    v = 0.86 + grain
    return (v * 0.98, v, v * 1.02, 1.0)


def create_pbr_material(name, base_color, metallic=0.9, roughness=0.25, tex_img=None):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        if tex_img:
            tex_node = nodes.new('ShaderNodeTexImage')
            tex_node.image = tex_img
            tex_coord = nodes.new('ShaderNodeTexCoord')
            mat.node_tree.links.new(tex_coord.outputs['Generated'], tex_node.inputs['Vector'])
            mat.node_tree.links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
    return mat


# --- Geometry Helpers ---

def add_box(col, name, min_pt, max_pt, material=None, bevel=False, bevel_width=0.003):
    dx = max_pt[0] - min_pt[0]
    dy = max_pt[1] - min_pt[1]
    dz = max_pt[2] - min_pt[2]
    cx = (min_pt[0] + max_pt[0]) / 2.0
    cy = (min_pt[1] + max_pt[1]) / 2.0
    cz = (min_pt[2] + max_pt[2]) / 2.0

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(cx, cy, cz))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (dx, dy, dz)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if bevel:
        mod = obj.modifiers.new(name="Bevel", type='BEVEL')
        mod.width = bevel_width
        mod.segments = 2
        mod.limit_method = 'ANGLE'
        mod.angle_limit = math.radians(35)
        bpy.ops.object.modifier_apply(modifier="Bevel")

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def add_cylinder(col, name, center, radius, height, material=None, segments=16, rot_axis='Z', rot_angle=0.0):
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius,
        depth=height,
        vertices=segments,
        location=center
    )
    obj = bpy.context.active_object
    obj.name = name

    if rot_angle != 0.0:
        if rot_axis == 'X':
            obj.rotation_euler[0] = rot_angle
        elif rot_axis == 'Y':
            obj.rotation_euler[1] = rot_angle
        elif rot_axis == 'Z':
            obj.rotation_euler[2] = rot_angle
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def add_torus(col, name, center, major_r, minor_r, material=None, major_seg=24, minor_seg=12, rot_axis='Y', rot_angle=0.0):
    bpy.ops.mesh.primitive_torus_add(
        location=center,
        major_radius=major_r,
        minor_radius=minor_r,
        major_segments=major_seg,
        minor_segments=minor_seg
    )
    obj = bpy.context.active_object
    obj.name = name

    if rot_angle != 0.0:
        if rot_axis == 'X':
            obj.rotation_euler[0] = rot_angle
        elif rot_axis == 'Y':
            obj.rotation_euler[1] = rot_angle
        elif rot_axis == 'Z':
            obj.rotation_euler[2] = rot_angle
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if material:
        obj.data.materials.append(material)

    if obj.name not in col.objects:
        col.objects.link(obj)
    if bpy.context.scene.collection.objects.get(obj.name):
        bpy.context.scene.collection.objects.unlink(obj)

    return obj


def finalize_knife(col, knife_name):
    """Joins all parts in the collection, sets proper transform and export normals."""
    bpy.ops.object.select_all(action='DESELECT')
    for obj in col.objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = col.objects[0]
    bpy.ops.object.join()
    knife = bpy.context.active_object
    knife.name = knife_name
    knife.rotation_euler[0] = math.radians(-90)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    bpy.ops.object.shade_smooth()
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
    bpy.ops.object.mode_set(mode='OBJECT')
    print(f"Built '{knife_name}' with {len(knife.data.polygons)} polygons.")
    return knife


# --- 1. Default Tactical Combat Knife ---
def build_default_knife(mat_blade, mat_edge, mat_grip, mat_guard, mat_pommel):
    c = bpy.data.collections.new("Default_Knife")
    bpy.context.scene.collection.children.link(c)

    # Blade Spine & Body
    add_box(c, "Blade_Body", (-0.003, -0.018, -0.13), (0.003, 0.016, 0.0), mat_blade, bevel=True, bevel_width=0.001)
    # Tanto Tip
    add_box(c, "Blade_Tanto_Tip", (-0.0025, -0.014, -0.17), (0.0025, 0.008, -0.13), mat_edge, bevel=True, bevel_width=0.001)
    # Lower Cutting Edges
    add_box(c, "Blade_Edge_Main", (-0.001, -0.022, -0.13), (0.001, -0.014, -0.005), mat_edge)
    add_box(c, "Blade_Edge_Tanto", (-0.0008, -0.018, -0.17), (0.0008, -0.012, -0.13), mat_edge)

    # Spine Serrations
    for sz in range(6):
        z_pos = -0.018 - (sz * 0.007)
        add_box(c, f"Serration_{sz}", (-0.0032, 0.015, z_pos - 0.002), (0.0032, 0.019, z_pos + 0.002), mat_blade)

    # Fuller Grooves
    add_box(c, "Fuller_L", (-0.0034, -0.003, -0.11), (-0.0026, 0.005, -0.02), mat_guard)
    add_box(c, "Fuller_R", (0.0026, -0.003, -0.11), (0.0034, 0.005, -0.02), mat_guard)

    # Crossguard & Jimping
    add_box(c, "Crossguard", (-0.012, -0.026, -0.005), (0.012, 0.022, 0.006), mat_guard, bevel=True, bevel_width=0.002)
    for j in [-0.002, 0.001, 0.004]:
        add_box(c, f"Jimping_{int((j+0.01)*1000)}", (-0.008, 0.021, j), (0.008, 0.024, j + 0.0015), mat_guard)

    # Handle Core & Choils
    add_box(c, "Handle_Core", (-0.011, -0.018, 0.006), (0.011, 0.018, 0.115), mat_grip, bevel=True, bevel_width=0.003)
    for f_idx, fz in enumerate([0.025, 0.050, 0.075, 0.100]):
        add_cylinder(c, f"Choil_{f_idx}", (0.0, -0.019, fz), radius=0.008, height=0.024, material=mat_grip, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Grip Screws
    for s_idx, sz in enumerate([0.035, 0.085]):
        add_cylinder(c, f"Handle_Screw_{s_idx}", (0.0, 0.0, sz), radius=0.0035, height=0.025, material=mat_blade, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Pommel & Breaker Tip
    add_box(c, "Pommel_Base", (-0.010, -0.017, 0.115), (0.010, 0.017, 0.126), mat_pommel, bevel=True, bevel_width=0.002)
    add_cylinder(c, "Breaker_Tip", (0.0, 0.0, 0.129), radius=0.004, height=0.006, material=mat_blade, segments=8)
    add_cylinder(c, "Lanyard_Hole", (0.0, 0.006, 0.121), radius=0.003, height=0.022, material=mat_guard, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    return finalize_knife(c, "Weapon_Knife_Default")


# --- 2. Karambit ---
def build_karambit(mat_blade, mat_edge, mat_grip, mat_ring):
    c = bpy.data.collections.new("Karambit")
    bpy.context.scene.collection.children.link(c)

    # Curved Talon Blade (approx arc in -Z and -Y)
    segments = 7
    prev_z, prev_y = 0.0, 0.0
    for i in range(1, segments + 1):
        t = i / float(segments)
        cur_z = -0.15 * math.sin(t * math.pi * 0.5)
        cur_y = -0.065 * (t ** 1.8)
        cz = (prev_z + cur_z) / 2.0
        cy = (prev_y + cur_y) / 2.0
        depth = math.sqrt((cur_z - prev_z)**2 + (cur_y - prev_y)**2)

        thickness = 0.0035 * (1.0 - t * 0.6)
        width = 0.022 * (1.0 - t * 0.7)
        # Blade segment
        add_box(c, f"Karambit_Blade_{i}", (-thickness, cy - width * 0.5, cz - depth * 0.5), (thickness, cy + width * 0.5, cz + depth * 0.5), mat_blade)
        # Concave Razor Edge
        add_box(c, f"Karambit_Edge_{i}", (-thickness * 0.5, cy - width * 0.65, cz - depth * 0.5), (thickness * 0.5, cy - width * 0.35, cz + depth * 0.5), mat_edge)
        prev_z, prev_y = cur_z, cur_y

    # Curved Ergonomic Reverse-Grip Handle
    for h_idx in range(4):
        hz = 0.015 + (h_idx * 0.024)
        hy = 0.008 * math.sin(h_idx * 0.6)
        add_box(c, f"Karambit_Handle_{h_idx}", (-0.010, hy - 0.014, hz - 0.012), (0.010, hy + 0.014, hz + 0.012), mat_grip, bevel=True, bevel_width=0.003)
        # Finger Scallops on inner face
        add_cylinder(c, f"Karambit_Choil_{h_idx}", (0.0, hy + 0.015, hz), radius=0.007, height=0.022, material=mat_grip, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Signature Safety Index Finger Ring at the pommel
    ring_center = (0.0, 0.012, 0.118)
    add_torus(c, "Karambit_Safety_Ring", ring_center, major_r=0.013, minor_r=0.004, material=mat_ring, major_seg=24, minor_seg=12, rot_axis='Y', rot_angle=math.radians(90))
    # Inner chamfer sleeve
    add_cylinder(c, "Karambit_Ring_Inner", ring_center, radius=0.010, height=0.010, material=mat_edge, segments=24, rot_axis='Y', rot_angle=math.radians(90))

    # Fastener Screws
    add_cylinder(c, "Karambit_Screw_0", (0.0, 0.002, 0.030), radius=0.0035, height=0.022, material=mat_blade, segments=12, rot_axis='Y', rot_angle=math.radians(90))
    add_cylinder(c, "Karambit_Screw_1", (0.0, 0.006, 0.075), radius=0.0035, height=0.022, material=mat_blade, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    return finalize_knife(c, "Weapon_Knife_Karambit")


# --- 3. Butterfly Knife / Balisong ---
def build_butterfly(mat_blade, mat_edge, mat_handle, mat_hardware):
    c = bpy.data.collections.new("Butterfly")
    bpy.context.scene.collection.children.link(c)

    # Swedge Clip-Point Blade (z: -0.16 to 0.0)
    add_box(c, "Balisong_Blade_Body", (-0.0028, -0.014, -0.12), (0.0028, 0.012, 0.0), mat_blade, bevel=True, bevel_width=0.001)
    add_box(c, "Balisong_Blade_Tip", (-0.002, -0.008, -0.16), (0.002, 0.004, -0.12), mat_edge, bevel=True, bevel_width=0.001)
    add_box(c, "Balisong_Cutting_Edge", (-0.0008, -0.018, -0.15), (0.0008, -0.012, -0.01), mat_edge)
    add_box(c, "Balisong_Swedge", (-0.0015, 0.010, -0.15), (0.0015, 0.014, -0.06), mat_edge)

    # Tang & Stop Pins
    add_box(c, "Balisong_Tang", (-0.0035, -0.016, -0.012), (0.0035, 0.016, 0.008), mat_hardware)
    add_cylinder(c, "Balisong_Tang_Pin_L", (0.0, -0.014, -0.006), radius=0.0025, height=0.014, material=mat_hardware, segments=10, rot_axis='Y', rot_angle=math.radians(90))
    add_cylinder(c, "Balisong_Tang_Pin_R", (0.0, 0.014, -0.006), radius=0.0025, height=0.014, material=mat_hardware, segments=10, rot_axis='Y', rot_angle=math.radians(90))

    # Dual Pivot Pins
    add_cylinder(c, "Pivot_Pin_Safe", (0.0, -0.011, 0.006), radius=0.0035, height=0.024, material=mat_hardware, segments=12, rot_axis='Y', rot_angle=math.radians(90))
    add_cylinder(c, "Pivot_Pin_Bite", (0.0, 0.011, 0.006), radius=0.0035, height=0.024, material=mat_hardware, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Channeled Skeleton Handles (Safe Handle & Bite Handle)
    for side, sign in [("Safe", -1), ("Bite", 1)]:
        hy = sign * 0.013
        # Main handle channel
        add_box(c, f"Handle_{side}_Body", (-0.0075, hy - 0.006, 0.006), (0.0075, hy + 0.006, 0.130), mat_handle, bevel=True, bevel_width=0.0015)
        # Skeletonized Lightening Holes
        for hole_i, hz in enumerate([0.030, 0.052, 0.074, 0.096, 0.118]):
            add_cylinder(c, f"Hole_{side}_{hole_i}", (0.0, hy, hz), radius=0.0032, height=0.018, material=mat_hardware, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Spring Latch at the base of the Bite Handle
    add_cylinder(c, "Balisong_Latch_Pin", (0.0, 0.013, 0.132), radius=0.0025, height=0.018, material=mat_hardware, segments=10, rot_axis='Y', rot_angle=math.radians(90))
    add_box(c, "Balisong_Latch_Bar", (-0.004, 0.004, 0.130), (0.004, 0.015, 0.144), mat_hardware, bevel=True, bevel_width=0.001)

    return finalize_knife(c, "Weapon_Knife_Butterfly")


# --- 4. M9 Tactical Bayonet ---
def build_bayonet(mat_blade, mat_edge, mat_handle, mat_guard, mat_pommel):
    c = bpy.data.collections.new("Bayonet")
    bpy.context.scene.collection.children.link(c)

    # Long Spear-Point Blade (z: -0.20 to 0.0)
    add_box(c, "Bayonet_Blade_Main", (-0.0035, -0.019, -0.16), (0.0035, 0.015, 0.0), mat_blade, bevel=True, bevel_width=0.001)
    add_box(c, "Bayonet_Spear_Tip", (-0.0025, -0.014, -0.20), (0.0025, 0.008, -0.16), mat_edge, bevel=True, bevel_width=0.001)
    add_box(c, "Bayonet_Edge", (-0.001, -0.024, -0.17), (0.001, -0.016, -0.005), mat_edge)

    # Aggressive Sawback Spine Teeth (7 teeth)
    for st in range(7):
        sz = -0.025 - (st * 0.012)
        add_box(c, f"Sawtooth_{st}", (-0.0038, 0.014, sz - 0.004), (0.0038, 0.020, sz + 0.004), mat_blade)

    # Deep Fuller
    add_box(c, "Bayonet_Fuller_L", (-0.004, -0.004, -0.14), (-0.003, 0.005, -0.02), mat_guard)
    add_box(c, "Bayonet_Fuller_R", (0.003, -0.004, -0.14), (0.004, 0.005, -0.02), mat_guard)

    # Muzzle-Ring Crossguard
    add_box(c, "Bayonet_Guard_Plate", (-0.014, -0.022, -0.006), (0.014, 0.028, 0.006), mat_guard, bevel=True, bevel_width=0.002)
    # Rifle barrel mounting ring
    add_torus(c, "Bayonet_Muzzle_Ring", (0.0, 0.034, 0.0), major_r=0.012, minor_r=0.003, material=mat_guard, major_seg=20, minor_seg=10, rot_axis='Z', rot_angle=0.0)

    # Ribbed Cylindrical Polymer/Steel Handle (z: 0.006 to 0.122)
    add_cylinder(c, "Bayonet_Grip_Core", (0.0, 0.0, 0.064), radius=0.013, height=0.116, material=mat_handle, segments=20)
    for rg in range(6):
        rz = 0.020 + (rg * 0.016)
        add_torus(c, f"Grip_Rib_{rg}", (0.0, 0.0, rz), major_r=0.0138, minor_r=0.0018, material=mat_guard, major_seg=20, minor_seg=8)

    # Steel Pommel with Rifle Attachment Lug
    add_box(c, "Bayonet_Pommel_Base", (-0.013, -0.015, 0.122), (0.013, 0.015, 0.138), mat_pommel, bevel=True, bevel_width=0.002)
    add_box(c, "Bayonet_Barrel_Lug", (-0.006, 0.012, 0.124), (0.006, 0.022, 0.136), mat_pommel)
    add_cylinder(c, "Bayonet_Lock_Release", (0.0, -0.016, 0.130), radius=0.004, height=0.008, material=mat_blade, segments=10, rot_axis='Y', rot_angle=math.radians(90))

    return finalize_knife(c, "Weapon_Knife_Bayonet")


# --- 5. Skeleton Knife ---
def build_skeleton_knife(mat_blade, mat_edge, mat_wrap, mat_hardware):
    c = bpy.data.collections.new("Skeleton")
    bpy.context.scene.collection.children.link(c)

    # Full-Tang Single-Piece Blade & Frame (z: -0.16 to 0.12)
    # Blade Portion (z: -0.16 to 0.0)
    add_box(c, "Skeleton_Blade_Body", (-0.0024, -0.016, -0.12), (0.0024, 0.013, 0.0), mat_blade, bevel=True, bevel_width=0.001)
    add_box(c, "Skeleton_Blade_Tip", (-0.0018, -0.010, -0.16), (0.0018, 0.006, -0.12), mat_edge, bevel=True, bevel_width=0.001)
    add_box(c, "Skeleton_Recurve_Edge", (-0.0008, -0.020, -0.15), (0.0008, -0.014, -0.01), mat_edge)

    # Large Center Finger Hole at the balance point (for finger twirls!)
    hole_center = (0.0, -0.002, 0.010)
    add_torus(c, "Skeleton_Center_Ring", hole_center, major_r=0.011, minor_r=0.003, material=mat_hardware, major_seg=24, minor_seg=10, rot_axis='Y', rot_angle=math.radians(90))
    add_cylinder(c, "Skeleton_Ring_Hole", hole_center, radius=0.009, height=0.012, material=mat_edge, segments=24, rot_axis='Y', rot_angle=math.radians(90))

    # Skeletal Tang Frame (z: 0.022 to 0.115)
    add_box(c, "Skeleton_Tang_Spine", (-0.0025, 0.008, 0.022), (0.0025, 0.015, 0.115), mat_blade)
    add_box(c, "Skeleton_Tang_Belly", (-0.0025, -0.015, 0.022), (0.0025, -0.008, 0.115), mat_blade)
    add_box(c, "Skeleton_Tang_End", (-0.003, -0.015, 0.110), (0.003, 0.015, 0.122), mat_hardware, bevel=True, bevel_width=0.002)

    # Paracord Handle Wrap (Criss-Cross wraps over the skeletal frame)
    for wrap_i in range(8):
        wz = 0.028 + (wrap_i * 0.010)
        slant = 0.003 * ((wrap_i % 2) * 2 - 1)
        add_torus(c, f"Paracord_Wrap_{wrap_i}", (0.0, slant, wz), major_r=0.0125, minor_r=0.0022, material=mat_wrap, major_seg=16, minor_seg=8, rot_axis='Z', rot_angle=math.radians(12 * ((wrap_i % 2) * 2 - 1)))

    return finalize_knife(c, "Weapon_Knife_Skeleton")


# --- 6. Huntsman Knife ---
def build_huntsman(mat_blade, mat_edge, mat_grip, mat_hardware):
    c = bpy.data.collections.new("Huntsman")
    bpy.context.scene.collection.children.link(c)

    # Massive Heavy Recurve Tanto Blade (z: -0.17 to 0.0, y: -0.024 to 0.018)
    add_box(c, "Huntsman_Blade_Body", (-0.0038, -0.022, -0.12), (0.0038, 0.016, 0.0), mat_blade, bevel=True, bevel_width=0.001)
    add_box(c, "Huntsman_Blade_Tanto", (-0.0032, -0.016, -0.17), (0.0032, 0.008, -0.12), mat_edge, bevel=True, bevel_width=0.001)
    add_box(c, "Huntsman_Recurve_Belly", (-0.001, -0.028, -0.12), (0.001, -0.018, -0.01), mat_edge)
    add_box(c, "Huntsman_Tanto_Edge", (-0.0008, -0.022, -0.17), (0.0008, -0.014, -0.12), mat_edge)

    # Heavy Double-Row Sawback Spine Teeth (8 teeth)
    for ht in range(8):
        hz = -0.015 - (ht * 0.011)
        add_box(c, f"Huntsman_Tooth_{ht}", (-0.0042, 0.015, hz - 0.0035), (0.0042, 0.022, hz + 0.0035), mat_blade)

    # Gut Hook / Choil Indent at Ricasso
    add_cylinder(c, "Huntsman_Choil", (0.0, -0.024, -0.008), radius=0.006, height=0.012, material=mat_hardware, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Machined G10 Tactical Scales with Finger Grooves
    add_box(c, "Huntsman_Grip_Core", (-0.012, -0.020, 0.005), (0.012, 0.018, 0.125), mat_grip, bevel=True, bevel_width=0.003)
    for fg_i, fz in enumerate([0.028, 0.055, 0.082, 0.108]):
        add_cylinder(c, f"Huntsman_Finger_Groove_{fg_i}", (0.0, -0.022, fz), radius=0.009, height=0.026, material=mat_grip, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Heavy Grip Fasteners
    for hf_i, hz in enumerate([0.035, 0.072, 0.105]):
        add_cylinder(c, f"Huntsman_Screw_{hf_i}", (0.0, 0.0, hz), radius=0.004, height=0.026, material=mat_hardware, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    # Pommel Impact Surface with Lanyard Cutout
    add_box(c, "Huntsman_Pommel", (-0.011, -0.018, 0.125), (0.011, 0.018, 0.138), mat_hardware, bevel=True, bevel_width=0.002)
    add_cylinder(c, "Huntsman_Lanyard_Loop", (0.0, 0.008, 0.132), radius=0.0035, height=0.024, material=mat_blade, segments=12, rot_axis='Y', rot_angle=math.radians(90))

    return finalize_knife(c, "Weapon_Knife_Huntsman")


def main():
    repo_root = Path(__file__).resolve().parent.parent.parent
    web_public = repo_root / "apps/web/public"
    web_public.mkdir(parents=True, exist_ok=True)

    full_clear()
    print("=== [1/3] Generating Procedural Rarity Texture Bitmaps in Blender ===")
    t_fade = create_texture_image("Tex_Fade", 256, 256, tex_fade)
    t_marble = create_texture_image("Tex_Marble_Fade", 256, 256, tex_marble_fade)
    t_case = create_texture_image("Tex_Case_Hardened", 256, 256, tex_case_hardened)
    t_crimson = create_texture_image("Tex_Crimson_Web", 256, 256, tex_crimson_web)
    t_damascus = create_texture_image("Tex_Damascus", 256, 256, tex_damascus)
    t_doppler = create_texture_image("Tex_Doppler_P2", 256, 256, tex_doppler_phase2)
    t_lore = create_texture_image("Tex_Lore", 256, 256, tex_lore_gold)
    t_tiger = create_texture_image("Tex_Tiger_Tooth", 256, 256, tex_tiger_tooth)
    t_slaughter = create_texture_image("Tex_Slaughter", 256, 256, tex_slaughter)
    t_steel = create_texture_image("Tex_Steel", 256, 256, tex_steel)

    print("=== [2/3] Setting Up PBR Shader Materials ===")
    mat_steel = create_pbr_material("Mat_Steel", (0.85, 0.86, 0.88), metallic=0.96, roughness=0.22, tex_img=t_steel)
    mat_edge = create_pbr_material("Mat_Edge", (0.95, 0.96, 0.98), metallic=0.98, roughness=0.12)
    mat_grip = create_pbr_material("Mat_Grip", (0.08, 0.09, 0.10), metallic=0.05, roughness=0.75)
    mat_guard = create_pbr_material("Mat_Guard", (0.12, 0.13, 0.15), metallic=0.75, roughness=0.35)
    mat_pommel = create_pbr_material("Mat_Pommel", (0.45, 0.46, 0.48), metallic=0.90, roughness=0.30)

    # Rarity Skin Materials
    mat_fade = create_pbr_material("Mat_Skin_Fade", (0.9, 0.5, 0.5), metallic=0.98, roughness=0.15, tex_img=t_fade)
    mat_marble = create_pbr_material("Mat_Skin_Marble", (0.8, 0.5, 0.5), metallic=0.98, roughness=0.15, tex_img=t_marble)
    mat_case = create_pbr_material("Mat_Skin_CaseHardened", (0.4, 0.6, 0.9), metallic=0.95, roughness=0.18, tex_img=t_case)
    mat_crimson = create_pbr_material("Mat_Skin_Crimson", (0.8, 0.1, 0.1), metallic=0.85, roughness=0.25, tex_img=t_crimson)
    mat_damascus = create_pbr_material("Mat_Skin_Damascus", (0.7, 0.7, 0.7), metallic=0.92, roughness=0.28, tex_img=t_damascus)
    mat_doppler = create_pbr_material("Mat_Skin_Doppler", (0.2, 0.1, 0.4), metallic=0.98, roughness=0.12, tex_img=t_doppler)
    mat_lore = create_pbr_material("Mat_Skin_Lore", (0.98, 0.82, 0.20), metallic=0.98, roughness=0.14, tex_img=t_lore)
    mat_tiger = create_pbr_material("Mat_Skin_Tiger", (0.95, 0.70, 0.10), metallic=0.96, roughness=0.16, tex_img=t_tiger)
    mat_slaughter = create_pbr_material("Mat_Skin_Slaughter", (0.75, 0.12, 0.15), metallic=0.95, roughness=0.15, tex_img=t_slaughter)
    mat_paracord = create_pbr_material("Mat_Paracord", (0.15, 0.16, 0.18), metallic=0.10, roughness=0.85)

    knives = [
        ("Default Tactical Knife", "hassault-weapon-knife.glb", lambda: build_default_knife(mat_steel, mat_edge, mat_grip, mat_guard, mat_pommel)),
        ("Karambit Fade", "hassault-weapon-knife-karambit.glb", lambda: build_karambit(mat_fade, mat_edge, mat_grip, mat_pommel)),
        ("Butterfly Marble Fade", "hassault-weapon-knife-butterfly.glb", lambda: build_butterfly(mat_marble, mat_edge, mat_grip, mat_guard)),
        ("M9 Bayonet Lore", "hassault-weapon-knife-bayonet.glb", lambda: build_bayonet(mat_lore, mat_edge, mat_grip, mat_guard, mat_pommel)),
        ("Skeleton Crimson Web", "hassault-weapon-knife-skeleton.glb", lambda: build_skeleton_knife(mat_crimson, mat_edge, mat_paracord, mat_guard)),
        ("Huntsman Case Hardened", "hassault-weapon-knife-huntsman.glb", lambda: build_huntsman(mat_case, mat_edge, mat_grip, mat_pommel)),
    ]

    print("=== [3/3] Constructing, Modeling, and Exporting All 6 Knife Props ===")
    for title, glb_name, build_fn in knives:
        clear_objects()
        print(f"\n--- Generating {title} -> {glb_name} ---")
        knife_obj = build_fn()

        out_path = web_public / glb_name
        print(f"Exporting GLB to: {out_path}")
        bpy.ops.export_scene.gltf(
            filepath=str(out_path),
            export_format='GLB',
            use_selection=False,
            export_apply=True,
            export_yup=True,
            export_materials='EXPORT',
            export_lights=False,
            export_cameras=False
        )

    print("\nAll 6 Knife Props and Rarity Skins generated and exported successfully!")


if __name__ == "__main__":
    main()
