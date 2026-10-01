#!/usr/bin/env python3
"""The first-person arms: a sleeved forearm and a gloved hand with real fingers.

    "C:/Program Files/Blender Foundation/Blender 4.1/blender.exe" --background \
        --factory-startup --python tools/blender/generate_arms.py -- [--out PATH] [--render PNG]

Writes `apps/web/public/hassault-hands.glb`, which both clients load: the browser
through three (`packages/core/src/modules/hassault/arms.ts`) and the native client
through `include_bytes!` (`apps/native-fps/src/arms.rs`).

## Ours, end to end

The module's rule is that other people's art is supported, never bundled. The
rigged "FPS Female Arms" GLB that sits beside the weapon props has no licence in
`assets/horribleAssault/README.md`, so it is not what the game draws. Every vertex
here is placed by this script.

## One arm, mirrored at runtime

Only the **right** arm is built. The left is the same mesh drawn with `x` negated,
and both clients solve it as a right arm in mirrored space — so there is one rig,
one set of finger poses, and no chance of the two hands drifting apart.

## The rest pose is the contract

Authored in **cube units** (1 cube = 36 cm), exported Y-up, so in glTF space:

- the shoulder is at the origin and the arm points straight down **-Z**;
- the thumb side is **+Y**, the palm faces **-X** (a handshake);
- the fingers are straight.

Both runtimes read the bones' rest transforms out of the file and solve *relative*
to them, so the numbers above are not hardcoded anywhere else — but they are why
a curl is a rotation about the hand's +Y, and why `grip` points along +Y.

## Bones

`upper`, `fore`, `hand`, `thumb_1..3`, `index_1..3`, `middle_1..3`, `ring_1..3`,
`pinky_1..3`, plus `grip` — a non-deforming bone at the centre of a closed fist,
pointing along the hole. The runtime puts `grip` on the weapon's grip anchor and
works the wrist out from it, so a hand closes *around* the handle rather than
hovering where a box used to be.

`upper` and `fore` are exactly `UPPER_LEN` and `LOWER_LEN` from `arms.ts`, so the
two-bone solve and the skin agree about where the elbow is. Asserted below.

## Weights

Computed here, not by Blender's bone heat. Heat weighting fails silently on a
mesh made of overlapping parts ("failed to find solution" and a zero-weight hand
that stays behind at the origin), and a hand made of overlapping parts is exactly
what this is. Each part names the bones it may follow, and each vertex takes an
inverse-distance blend of the nearest of those — so a ring-finger vertex can never
be pulled by the pinky.
"""

import math
import sys
from pathlib import Path

try:
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
except ImportError:
    print(
        "Run inside Blender: blender -b --factory-startup -P tools/blender/generate_arms.py"
    )
    sys.exit(1)


# --- Units and proportions -------------------------------------------------

CM = 1.0 / 36.0
UPPER_LEN = 0.84
LOWER_LEN = 0.76

# Authored in Blender space: +Y along the arm, +Z the thumb side, +X the back of
# the hand. The Y-up export turns these into -Z, +Y and +X respectively.
SHOULDER = Vector((0.0, 0.0, 0.0))
ELBOW = Vector((0.0, UPPER_LEN, 0.0))
WRIST = Vector((0.0, UPPER_LEN + LOWER_LEN, 0.0))
HAND_LEN = 8.0 * CM

# name: (knuckle offset from the wrist (x, y, z), splay in degrees towards +Z,
#        phalanx lengths in cm, radii at knuckle / middle / end joints / tip)
FINGERS = {
    "index": (
        (0.004, 0.214, 0.080),
        5.0,
        (4.0, 2.5, 2.0),
        (0.028, 0.025, 0.022, 0.019),
    ),
    "middle": (
        (0.006, 0.222, 0.027),
        1.0,
        (4.5, 2.9, 2.2),
        (0.029, 0.026, 0.023, 0.020),
    ),
    "ring": (
        (0.004, 0.212, -0.026),
        -3.0,
        (4.2, 2.7, 2.1),
        (0.027, 0.024, 0.021, 0.018),
    ),
    "pinky": (
        (0.000, 0.190, -0.074),
        -8.0,
        (3.4, 2.0, 1.9),
        (0.023, 0.021, 0.019, 0.016),
    ),
}
THUMB_BASE = (-0.028, 0.030, 0.052)
THUMB_DIRS = ((-0.45, 0.55, 0.70), (-0.32, 0.76, 0.55), (-0.22, 0.86, 0.45))
THUMB_LENS = (4.6, 3.3, 2.7)
THUMB_RADII = (0.037, 0.031, 0.027, 0.021)

# What a curl rotates each finger about, in the rest frame. The fingers fold
# towards the palm about the thumb axis; the thumb folds across the palm about
# its own. Both runtimes carry the same two vectors (in glTF axes there:
# (x, z, -y) of these) — see `FLEX_AXIS` in `arms.ts` and `arms.rs`.
FINGER_FLEX = (0.0, 0.0, 1.0)
THUMB_FLEX = (-0.70, -0.57, 0.0)

# The centre of a closed fist, and the hole's axis. Measured off the preview
# render with the fingers at a full curl.
GRIP_OFFSET = (-0.085, 0.175, 0.0)


# --- Scene plumbing --------------------------------------------------------


def reset_scene():
    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def select_only(obj):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def apply_modifiers(obj):
    select_only(obj)
    for mod in list(obj.modifiers):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def subdivide(obj, levels):
    mod = obj.modifiers.new("Subsurf", "SUBSURF")
    mod.levels = levels
    mod.render_levels = levels
    apply_modifiers(obj)


def mesh_object(name, bm, materials):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in materials:
        me.materials.append(m)
    return link(bpy.data.objects.new(name, me))


# --- Textures --------------------------------------------------------------


def _hash(ix, iy, seed=0):
    n = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


def value_noise(x, y, seed=0):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a = _hash(ix, iy, seed)
    b = _hash(ix + 1, iy, seed)
    c = _hash(ix, iy + 1, seed)
    d = _hash(ix + 1, iy + 1, seed)
    return (a + (b - a) * sx) + ((c + (d - c) * sx) - (a + (b - a) * sx)) * sy


def fbm(x, y, seed=0, octaves=4):
    total, amp, norm = 0.0, 0.5, 0.0
    for o in range(octaves):
        total += value_noise(x, y, seed + o * 17) * amp
        norm += amp
        x, y, amp = x * 2.03, y * 2.03, amp * 0.5
    return total / norm


def weave(nx, ny, cycles):
    """A plain weave: alternating over/under threads, in 0..1."""
    u, v = nx * cycles, ny * cycles
    cu, cv = math.floor(u), math.floor(v)
    over = (cu + cv) % 2 == 0
    fu, fv = u - cu, v - cv
    thread = math.sin(math.pi * (fv if over else fu))
    return 0.5 + 0.5 * thread * (1.0 if over else 0.8)


def make_image(name, size, fn, non_color=False):
    img = bpy.data.images.new(name, width=size, height=size, alpha=False)
    px = [0.0] * (size * size * 4)
    for y in range(size):
        ny = y / size
        for x in range(size):
            r, g, b = fn(x / size, ny)
            i = (y * size + x) * 4
            px[i] = min(1.0, max(0.0, r))
            px[i + 1] = min(1.0, max(0.0, g))
            px[i + 2] = min(1.0, max(0.0, b))
            px[i + 3] = 1.0
    img.pixels = px
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    img.pack()
    return img


def normal_image(name, size, height_fn, strength):
    """A tangent-space normal map from a tiling height field."""
    h = [[height_fn(x / size, y / size) for x in range(size)] for y in range(size)]

    def fn(nx, ny):
        x, y = int(nx * size), int(ny * size)
        dx = h[y][(x + 1) % size] - h[y][(x - 1) % size]
        dy = h[(y + 1) % size][x] - h[(y - 1) % size][x]
        n = Vector((-dx * strength, -dy * strength, 1.0)).normalized()
        return (n.x * 0.5 + 0.5, n.y * 0.5 + 0.5, n.z * 0.5 + 0.5)

    return make_image(name, size, fn, non_color=True)


def material(
    name, albedo_img, normal_img, roughness, metallic=0.0, normal_strength=1.0
):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = albedo_img
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if normal_img is not None:
        ntex = nodes.new("ShaderNodeTexImage")
        ntex.image = normal_img
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.inputs["Strength"].default_value = normal_strength
        links.new(ntex.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def build_materials():
    size = 256

    def sleeve_albedo(nx, ny):
        w = weave(nx, ny, 96)
        mottle = fbm(nx * 6, ny * 6, 3) - 0.5
        base = (0.20, 0.235, 0.17)
        k = 0.86 + 0.18 * w + 0.22 * mottle
        return (base[0] * k, base[1] * k, base[2] * k)

    def glove_albedo(nx, ny):
        w = weave(nx, ny, 128)
        scuff = max(0.0, fbm(nx * 10, ny * 10, 9) - 0.62) * 1.6
        base = (0.135, 0.14, 0.15)
        k = 0.85 + 0.25 * w
        return (
            base[0] * k + scuff * 0.10,
            base[1] * k + scuff * 0.10,
            base[2] * k + scuff * 0.10,
        )

    def skin_albedo(nx, ny):
        mottle = fbm(nx * 14, ny * 14, 5) - 0.5
        freckle = max(0.0, value_noise(nx * 90, ny * 90, 7) - 0.86) * 1.2
        k = 1.0 + 0.10 * mottle - freckle * 0.35
        return (0.66 * k, 0.47 * k, 0.37 * k)

    def armor_albedo(nx, ny):
        grain = fbm(nx * 40, ny * 40, 11) - 0.5
        k = 1.0 + 0.25 * grain
        return (0.095 * k, 0.098 * k, 0.105 * k)

    def strap_albedo(nx, ny):
        loops = weave(nx, ny, 160)
        k = 0.8 + 0.3 * loops + 0.15 * (fbm(nx * 8, ny * 8, 13) - 0.5)
        return (0.36 * k, 0.30 * k, 0.21 * k)

    sleeve_n = normal_image("N_Sleeve", size, lambda x, y: weave(x, y, 96), 2.2)
    glove_n = normal_image(
        "N_Glove",
        size,
        lambda x, y: weave(x, y, 128) * 0.7 + fbm(x * 12, y * 12, 2) * 0.3,
        2.6,
    )
    skin_n = normal_image("N_Skin", size, lambda x, y: fbm(x * 60, y * 60, 4, 3), 1.2)
    strap_n = normal_image("N_Strap", size, lambda x, y: weave(x, y, 160), 2.0)

    return {
        "sleeve": material(
            "Arm_Sleeve", make_image("T_Sleeve", size, sleeve_albedo), sleeve_n, 0.88
        ),
        "glove": material(
            "Arm_Glove", make_image("T_Glove", size, glove_albedo), glove_n, 0.62
        ),
        "skin": material(
            "Arm_Skin",
            make_image("T_Skin", size, skin_albedo),
            skin_n,
            0.5,
            normal_strength=0.6,
        ),
        "armor": material(
            "Arm_Armor", make_image("T_Armor", size, armor_albedo), None, 0.38
        ),
        "strap": material(
            "Arm_Strap", make_image("T_Strap", size, strap_albedo), strap_n, 0.92
        ),
    }


# --- Geometry --------------------------------------------------------------


def ring_frame(tangent, ref=Vector((1.0, 0.0, 0.0))):
    n = ref - tangent * ref.dot(tangent)
    if n.length < 1e-5:
        n = Vector((0.0, 0.0, 1.0)) - tangent * tangent.z
    n.normalize()
    return n, tangent.cross(n)


def tube(
    name,
    centers,
    radii,
    mats,
    seg=14,
    face_mat=None,
    displace=None,
    cap_start=True,
    tip=0,
):
    """Rings swept along a polyline, optionally closed with a rounded tip.

    `radii[i]` is `r` or `(r_back, r_side)`: the back-of-hand axis first, so a
    wrist can be flatter than it is wide. `face_mat(i)` picks the material slot
    for the band between ring `i` and `i + 1`; `displace(p, theta, i)` returns a
    radial offset, for sleeve folds.
    """
    bm = bmesh.new()
    rings = []
    n_c = len(centers)

    def tangent_at(i):
        a = centers[max(0, i - 1)]
        b = centers[min(n_c - 1, i + 1)]
        return (b - a).normalized()

    def ring(c, t, r, i):
        rb, rs = (r, r) if not isinstance(r, tuple) else r
        n, b = ring_frame(t)
        verts = []
        for k in range(seg):
            th = 2.0 * math.pi * k / seg
            off = n * (math.cos(th) * rb) + b * (math.sin(th) * rs)
            if displace is not None:
                off += off.normalized() * displace(c, th, i)
            verts.append(bm.verts.new(c + off))
        return verts

    for i, c in enumerate(centers):
        rings.append(ring(c, tangent_at(i), radii[i], i))

    last_t = tangent_at(n_c - 1)
    last_r = radii[-1] if not isinstance(radii[-1], tuple) else radii[-1]
    for k in range(1, tip + 1):
        f = k / (tip + 1)
        r = last_r if not isinstance(last_r, tuple) else last_r
        shrink = math.sqrt(max(0.0, 1.0 - f * f))
        rr = (r[0] * shrink, r[1] * shrink) if isinstance(r, tuple) else r * shrink
        reach = (r[0] if isinstance(r, tuple) else r) * f
        rings.append(ring(centers[-1] + last_t * reach, last_t, rr, n_c - 1))

    for i in range(len(rings) - 1):
        a, b = rings[i], rings[i + 1]
        mi = face_mat(min(i, n_c - 2)) if face_mat else 0
        for k in range(seg):
            f = bm.faces.new((a[k], a[(k + 1) % seg], b[(k + 1) % seg], b[k]))
            f.material_index = mi
    if tip:
        r = last_r[0] if isinstance(last_r, tuple) else last_r
        apex = bm.verts.new(centers[-1] + last_t * r)
        mi = face_mat(n_c - 2) if face_mat else 0
        ring_last = rings[-1]
        for k in range(seg):
            f = bm.faces.new((ring_last[k], ring_last[(k + 1) % seg], apex))
            f.material_index = mi
    if cap_start:
        f = bm.faces.new(list(reversed(rings[0])))
        f.material_index = face_mat(0) if face_mat else 0
    bm.normal_update()
    return mesh_object(name, bm, mats)


def rounded_box(name, center, size, mat, bevel=0.004, rotation=None):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
        if rotation is not None:
            v.co = rotation @ v.co
        v.co += Vector(center)
    bmesh.ops.bevel(bm, geom=list(bm.edges), offset=bevel, segments=3, affect="EDGES")
    return mesh_object(name, bm, [mat])


def palm(mats):
    """The hand's body: a subdivided box pushed into the shape of a palm.

    Narrow at the wrist, widest across the knuckles, the knuckle line an arc
    (the middle finger's is furthest out), flat on the back and padded on the
    palm side, with the thenar mound under the thumb.
    """
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=3, use_grid_fill=True)
    for v in bm.verts:
        u, s, w = (
            v.co.x,
            v.co.y,
            v.co.z,
        )  # -1..1: back/palm, wrist/knuckles, pinky/thumb
        t = (s + 1.0) * 0.5
        z = w * (0.074 + 0.044 * math.sin(t * math.pi * 0.5))
        arc = 0.022 * (1.0 - (z / 0.11) ** 2)
        y = WRIST.y - 0.012 + t * (0.200 + arc)
        if u > 0:
            x = u * (0.030 - 0.006 * t)
        else:
            thenar = max(0.0, w) * max(0.0, 1.0 - t * 1.4) * 0.028
            heel = max(0.0, 1.0 - t * 1.6) * 0.010
            x = u * (0.036 + thenar + heel + 0.004 * math.sin(t * math.pi))
        v.co = Vector((x, y, z))
    obj = mesh_object("Palm", bm, [mats["glove"]])
    subdivide(obj, 2)
    return obj


def finger_joints(name):
    base, splay, lens, _ = FINGERS[name]
    d = Vector((0.0, math.cos(math.radians(splay)), math.sin(math.radians(splay))))
    pts = [WRIST + Vector(base)]
    for L in lens:
        pts.append(pts[-1] + d * (L * CM))
    return pts


def thumb_joints():
    pts = [WRIST + Vector(THUMB_BASE)]
    for dvec, L in zip(THUMB_DIRS, THUMB_LENS):
        pts.append(pts[-1] + Vector(dvec).normalized() * (L * CM))
    return pts


def resample(joints, radii, per_segment=3, bulge=0.1):
    """Joint positions to ring centres, with a knuckle bulge at each joint."""
    centers, rs = [], []
    for i in range(len(joints) - 1):
        a, b = joints[i], joints[i + 1]
        for k in range(per_segment):
            f = k / per_segment
            centers.append(a.lerp(b, f))
            r = radii[i] + (radii[i + 1] - radii[i]) * f
            # A joint is a little wider than the bone either side of it.
            rs.append(r * (1.0 + bulge if k == 0 and i > 0 else 1.0))
    centers.append(joints[-1])
    rs.append(radii[-1])
    return centers, rs


def finger(name, joints, radii, mats, cut_off=False):
    centers, rs = resample(joints, radii)
    # Slightly flatter across the back than the side, like a finger.
    rs = [(r * 0.92, r) for r in rs]
    per = 3
    distal_from = 2 * per

    def face_mat(i):
        return 1 if cut_off and i >= distal_from else 0

    obj = tube(
        name,
        centers,
        rs,
        [mats["glove"], mats["skin"]],
        seg=12,
        face_mat=face_mat,
        tip=3,
    )
    subdivide(obj, 1)
    return obj


def arm_sleeve(mats):
    """Shoulder to mid-forearm in fabric, with folds where the elbow bunches it."""
    # Starts behind the shoulder and is capped there, so a shoulder slid
    # forward to reach a rifle never shows the inside of the sleeve.
    ys = [-0.35, 0.0, 0.2, 0.4, 0.6, 0.76, 0.84, 0.92, 1.0, 1.08, 1.13]
    radii = [
        (0.150, 0.158),
        (0.160, 0.170),
        (0.158, 0.166),
        (0.152, 0.160),
        (0.146, 0.152),
        (0.140, 0.146),
        (0.140, 0.144),
        (0.138, 0.142),
        (0.134, 0.138),
        (0.130, 0.134),
        (0.128, 0.132),
    ]
    centers = [Vector((0.0, y, 0.0)) for y in ys]

    def folds(p, th, i):
        near_elbow = math.exp(-(((p.y - ELBOW.y) / 0.14) ** 2))
        wrinkle = (
            math.sin(th * 5.0 + p.y * 23.0) * 0.006
            + math.sin(th * 9.0 - p.y * 41.0) * 0.003
        )
        seam = 0.004 * math.exp(-((((th - 0.35) % (2 * math.pi)) / 0.06) ** 2))
        return wrinkle * (0.4 + near_elbow * 1.6) + seam

    # Densified along the length so the folds have vertices to live on.
    dense_c, dense_r = [], []
    for i in range(len(ys) - 1):
        for k in range(4):
            f = k / 4
            dense_c.append(centers[i].lerp(centers[i + 1], f))
            a, b = radii[i], radii[i + 1]
            dense_r.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    dense_c.append(centers[-1])
    dense_r.append(radii[-1])
    obj = tube(
        "Sleeve",
        dense_c,
        dense_r,
        [mats["sleeve"]],
        seg=20,
        displace=folds,
        cap_start=True,
    )
    subdivide(obj, 1)
    return obj


def rolled_cuff(mats):
    """The sleeve pushed up the forearm: a thick, lumpy roll of the same cloth."""
    ys = [1.06, 1.09, 1.12, 1.15, 1.17]
    radii = [
        (0.132, 0.137),
        (0.142, 0.148),
        (0.146, 0.151),
        (0.140, 0.146),
        (0.126, 0.131),
    ]

    def lumps(p, th, i):
        return 0.004 * math.sin(th * 3.0 + 1.3) + 0.003 * math.sin(th * 7.0)

    obj = tube(
        "Cuff",
        [Vector((0.0, y, 0.0)) for y in ys],
        radii,
        [mats["sleeve"]],
        seg=20,
        displace=lumps,
        cap_start=False,
    )
    subdivide(obj, 1)
    return obj


def forearm_skin(mats):
    ys = [1.05, 1.18, 1.30, 1.42, 1.52, 1.60]
    radii = [
        (0.108, 0.118),
        (0.100, 0.112),
        (0.090, 0.104),
        (0.076, 0.094),
        (0.064, 0.086),
        (0.060, 0.082),
    ]

    def tendons(p, th, i):
        wrist = max(0.0, (p.y - 1.35) / 0.25)
        return 0.0025 * math.sin(th * 6.0) * wrist

    obj = tube(
        "Forearm",
        [Vector((0.0, y, 0.0)) for y in ys],
        radii,
        [mats["skin"]],
        seg=18,
        displace=tendons,
        cap_start=False,
    )
    subdivide(obj, 1)
    return obj


def gauntlet(mats):
    """The glove's wrist: a cuff over the forearm that runs into the palm."""
    ys = [1.47, 1.49, 1.53, 1.57, 1.61, 1.65]
    radii = [
        (0.075, 0.098),
        (0.078, 0.101),
        (0.075, 0.098),
        (0.071, 0.094),
        (0.066, 0.092),
        (0.060, 0.090),
    ]
    obj = tube(
        "Gauntlet",
        [Vector((0.0, y, 0.0)) for y in ys],
        radii,
        [mats["glove"]],
        seg=18,
        cap_start=False,
    )
    subdivide(obj, 1)
    strap_ys = [1.505, 1.515, 1.545, 1.555]
    strap_r = [(0.080, 0.103), (0.083, 0.106), (0.081, 0.104), (0.078, 0.101)]
    strap = tube(
        "Strap",
        [Vector((0.0, y, 0.0)) for y in strap_ys],
        strap_r,
        [mats["strap"]],
        seg=18,
        cap_start=False,
    )
    tab = rounded_box(
        "StrapTab",
        (0.086, 1.530, -0.020),
        (0.012, 0.050, 0.070),
        mats["strap"],
        bevel=0.004,
    )
    return [obj, strap, tab]


def knuckle_guard(mats):
    """The hard shell across the knuckles: one moulded plate, a ridge per finger.

    One piece rather than a box per knuckle, because four boxes read as beads
    threaded on the fingers. Rigid to `hand` — it is the part of a glove that
    does not bend, which is the point of it.
    """
    bm = bmesh.new()
    zs = [-0.100 + 0.205 * k / 10 for k in range(11)]
    ys = [-0.050, -0.030, -0.012, 0.004, 0.014]
    rows = []
    for yo in ys:
        row = []
        for z in zs:
            # The knuckle line is an arc; the plate follows it.
            arc = 0.022 * (1.0 - (z / 0.11) ** 2)
            y = WRIST.y + 0.200 + arc + yo
            ridge = 0.0
            for name in ("index", "middle", "ring", "pinky"):
                fz = FINGERS[name][0][2]
                ridge = max(ridge, math.exp(-(((z - fz) / 0.018) ** 2)))
            lift = 0.030 + 0.006 + 0.010 * ridge * (1.0 - abs(yo + 0.012) / 0.04)
            row.append(bm.verts.new((lift, y, z)))
        rows.append(row)
    top = rows
    bottom = [[bm.verts.new((v.co.x - 0.008, v.co.y, v.co.z)) for v in row] for row in rows]
    for grid, flip in ((top, False), (bottom, True)):
        for i in range(len(grid) - 1):
            for k in range(len(zs) - 1):
                quad = [grid[i][k], grid[i][k + 1], grid[i + 1][k + 1], grid[i + 1][k]]
                bm.faces.new(list(reversed(quad)) if flip else quad)
    # Close the rim, so it is a plate and not a sheet.
    last = len(ys) - 1
    for k in range(len(zs) - 1):
        bm.faces.new([top[0][k + 1], top[0][k], bottom[0][k], bottom[0][k + 1]])
        bm.faces.new([top[last][k], top[last][k + 1], bottom[last][k + 1], bottom[last][k]])
    for i in range(len(ys) - 1):
        bm.faces.new([top[i + 1][0], top[i][0], bottom[i][0], bottom[i + 1][0]])
        bm.faces.new([top[i][-1], top[i + 1][-1], bottom[i + 1][-1], bottom[i][-1]])
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    obj = mesh_object("KnucklePlate", bm, [mats["armor"]])
    subdivide(obj, 2)
    return [obj]


def glove_hem(name, joints, radii, at, mats):
    """The raw edge where a cut-off fingertip leaves the glove."""
    c = joints[at]
    t = (joints[at + 1] - joints[at]).normalized()
    r = radii[at] * 1.08
    return tube(
        name,
        [c - t * 0.004, c + t * 0.004],
        [(r * 0.92, r), (r * 0.92, r)],
        [mats["glove"]],
        seg=12,
        cap_start=False,
    )


# --- Armature and weights --------------------------------------------------


def build_armature():
    arm_data = bpy.data.armatures.new("HandsRig")
    rig = link(bpy.data.objects.new("HandsRig", arm_data))
    select_only(rig)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    def bone(name, head, tail, parent=None, deform=True):
        b = eb.new(name)
        b.head, b.tail = head, tail
        b.roll = 0.0
        b.use_deform = deform
        if parent is not None:
            b.parent = eb[parent]
            b.use_connect = (parent_tail(parent) - head).length < 1e-6
        return b

    def parent_tail(name):
        return eb[name].tail

    bone("upper", SHOULDER, ELBOW)
    bone("fore", ELBOW, WRIST, "upper")
    bone("hand", WRIST, WRIST + Vector((0.0, HAND_LEN, 0.0)), "fore")
    g = WRIST + Vector(GRIP_OFFSET)
    bone("grip", g, g + Vector((0.0, 0.0, 0.05)), "hand", deform=False)
    for name in FINGERS:
        j = finger_joints(name)
        bone(f"{name}_1", j[0], j[1], "hand")
        bone(f"{name}_2", j[1], j[2], f"{name}_1")
        bone(f"{name}_3", j[2], j[3], f"{name}_2")
    t = thumb_joints()
    bone("thumb_1", t[0], t[1], "hand")
    bone("thumb_2", t[1], t[2], "thumb_1")
    bone("thumb_3", t[2], t[3], "thumb_2")
    bpy.ops.object.mode_set(mode="OBJECT")

    upper = arm_data.bones["upper"].length
    fore = arm_data.bones["fore"].length
    assert abs(upper - UPPER_LEN) < 1e-6, (
        f"upper is {upper}, the solve expects {UPPER_LEN}"
    )
    assert abs(fore - LOWER_LEN) < 1e-6, (
        f"fore is {fore}, the solve expects {LOWER_LEN}"
    )
    return rig


def segment_distance(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
    return (a + ab * t - p).length


def weigh(obj, rig, bones, power=6.0, bias=None):
    """Inverse-distance weights to the named bones, top three kept.

    `bias[bone]` scales a bone's pull — the palm follows a finger's first bone
    only a little, so a curl creases the knuckle rather than dragging the palm.
    """
    segs = {}
    for name in bones:
        b = rig.data.bones[name]
        segs[name] = (b.head_local.copy(), b.tail_local.copy())
    groups = {
        name: obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        for name in bones
    }
    for v in obj.data.vertices:
        p = v.co
        scored = []
        for name, (a, b) in segs.items():
            d = max(segment_distance(p, a, b), 1e-4)
            w = (1.0 / d) ** power * (bias.get(name, 1.0) if bias else 1.0)
            scored.append((w, name))
        scored.sort(reverse=True)
        top = scored[:3]
        total = sum(w for w, _ in top)
        for w, name in top:
            if w / total > 0.01:
                groups[name].add([v.index], w / total, "REPLACE")


def rigid(obj, bone):
    g = obj.vertex_groups.new(name=bone)
    g.add([v.index for v in obj.data.vertices], 1.0, "REPLACE")


# --- Assembly --------------------------------------------------------------


def build(rig, mats):
    parts = []

    for obj in [arm_sleeve(mats), rolled_cuff(mats)]:
        weigh(obj, rig, ["upper", "fore"], power=8.0)
        parts.append(obj)

    skin = forearm_skin(mats)
    weigh(skin, rig, ["fore", "hand"], power=10.0, bias={"hand": 0.05})
    parts.append(skin)

    for obj in gauntlet(mats):
        weigh(obj, rig, ["fore", "hand"], power=10.0, bias={"hand": 0.25})
        parts.append(obj)

    p = palm(mats)
    first = [f"{n}_1" for n in FINGERS] + ["thumb_1"]
    weigh(p, rig, ["hand"] + first, power=6.0, bias={b: 0.12 for b in first})
    parts.append(p)

    for name in FINGERS:
        joints = finger_joints(name)
        radii = FINGERS[name][3]
        obj = finger(f"Finger_{name}", joints, radii, mats, cut_off=(name == "index"))
        weigh(
            obj,
            rig,
            ["hand", f"{name}_1", f"{name}_2", f"{name}_3"],
            power=8.0,
            bias={"hand": 0.4},
        )
        parts.append(obj)
    t = thumb_joints()
    thumb = finger("Thumb", t, THUMB_RADII, mats, cut_off=True)
    weigh(
        thumb,
        rig,
        ["hand", "thumb_1", "thumb_2", "thumb_3"],
        power=8.0,
        bias={"hand": 0.5},
    )
    parts.append(thumb)

    for name, joints, radii in (
        ("index", finger_joints("index"), FINGERS["index"][3]),
        ("thumb", t, THUMB_RADII),
    ):
        hem = glove_hem(f"Hem_{name}", joints, radii, 2, mats)
        rigid(hem, f"{name}_2" if name != "thumb" else "thumb_2")
        parts.append(hem)

    for obj in knuckle_guard(mats):
        rigid(obj, "hand")
        parts.append(obj)

    # One skinned mesh: one draw per material rather than one per part.
    for o in bpy.context.scene.objects:
        o.select_set(False)
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    hands = bpy.context.active_object
    hands.name = "Hands"
    # The same material may have been appended once per part; the join keeps
    # every slot, and the exporter would emit one primitive per duplicate.
    select_only(hands)
    bpy.ops.object.material_slot_remove_unused()
    bpy.ops.object.shade_smooth()

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-5)
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.004)
    bpy.ops.object.mode_set(mode="OBJECT")

    hands.parent = rig
    mod = hands.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    print(
        f"Hands: {len(hands.data.vertices)} verts, {len(hands.data.polygons)} faces, "
        f"{len(hands.data.materials)} materials"
    )
    return hands


def curl_preview(rig, amount):
    """A closed fist, for the preview render — the pose the grip bone was measured in."""
    select_only(rig)
    bpy.ops.object.mode_set(mode="POSE")
    finger_axis = Vector(FINGER_FLEX).normalized()
    thumb_axis = Vector(THUMB_FLEX).normalized()
    for pb in rig.pose.bones:
        name = pb.name
        if name.split("_")[0] in FINGERS:
            k = {"1": 1.35, "2": 1.6, "3": 1.0}[name[-1]]
        elif name.startswith("thumb"):
            k = {"1": 0.35, "2": 0.6, "3": 0.8}[name[-1]]
        else:
            continue
        up = thumb_axis if name.startswith("thumb") else finger_axis
        local_axis = (pb.bone.matrix_local.to_3x3().inverted() @ up).normalized()
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Matrix.Rotation(
            amount * k, 4, local_axis
        ).to_quaternion()
    bpy.ops.object.mode_set(mode="OBJECT")


def render_preview(path, rig):
    """Four stills: back of the hand and palm side, open and in a fist."""
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = 720
    scene.render.resolution_y = 540
    cam = link(bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")))
    cam.data.lens = 40
    scene.camera = cam
    target = WRIST + Vector((0.0, 0.10, 0.0))
    views = {
        "back": Vector((0.75, 1.20, 0.30)),
        "palm": Vector((-0.75, 1.30, 0.20)),
        "front": Vector((0.10, 2.35, 0.25)),
    }
    stem = Path(path)
    for curl in (0.0, 1.0):
        curl_preview(rig, curl)
        for view, loc in views.items():
            cam.location = loc
            cam.rotation_euler = (target - loc).to_track_quat("-Z", "Z").to_euler()
            scene.render.filepath = str(stem.with_name(f"{stem.stem}-{view}-{int(curl)}.png"))
            bpy.ops.render.render(write_still=True)
    print(f"Previews beside {path}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    repo_root = Path(__file__).resolve().parent.parent.parent
    out = repo_root / "apps/web/public/hassault-hands.glb"
    render = None
    i = 0
    while i < len(argv):
        if argv[i] == "--out":
            out = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--render":
            render = Path(argv[i + 1])
            i += 2
        else:
            raise SystemExit(f"unknown argument {argv[i]}")

    reset_scene()
    mats = build_materials()
    rig = build_armature()
    build(rig, mats)

    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.ops.export_scene.gltf(
        filepath=str(out),
        export_format="GLB",
        use_selection=False,
        export_yup=True,
        export_apply=False,
        export_skins=True,
        export_animations=False,
        export_materials="EXPORT",
        export_lights=False,
        export_cameras=False,
    )
    print(f"Wrote {out}")

    if render is not None:
        render_preview(render, rig)


if __name__ == "__main__":
    main()
