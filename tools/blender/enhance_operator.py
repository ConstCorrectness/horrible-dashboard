#!/usr/bin/env python3
"""Dress an operator: gear and team colours added to a shipped operator GLB.

    blender --background --factory-startup --python tools/blender/enhance_operator.py \\
        -- <side: t|ct> <in.glb> <out.glb>

The operators were exported by three (`THREE.GLTFExporter`) from a source that is
not in the repo: two rigs (CT's 34 bones over nine skinned meshes, T's 65 over
one), twenty-three clips each, and a scale on the root node that both clients
read their 5.2-cube height through. A round trip through Blender's importer and
exporter would re-orient bones, resample every clip and rename things the
clients look up by name, and each of those is a test somewhere.

So nothing that is there is touched. Blender models the new pieces — bevelled
plates and pouches, banded straps — and this script **appends** them to the
GLB: one more mesh under the same root, with its own skin whose joints are
bones the rig already has and whose inverse binds are copied from the skins
already there. Each piece follows exactly one bone, rigidly. Every byte of the
original file is kept; the new data goes after it.

The pieces are sized from the body itself, read out of the file in its bind
pose: the torso's extent for the plate carrier, the arm's radius for the band
around it, the helmet's for CT's stripe. Nothing is a number typed in for one
model, so the same script dresses either rig.

- **T** gets a plate carrier with magazine pouches and a radio pouch, a belt
  with two pouches, a scarf at the neck, and an armband on each arm in the T
  side's warm colour.
- **CT** already wears a carrier and a helmet. It gets the same armbands in
  the CT side's blue, and a blue stripe round the helmet.

The armbands are the point: at fifty metres, which side somebody is on is the
colour of their arms.

A file that already has `Gear` in it is refused: run this on the undressed
model (`git show <rev>:apps/web/public/<file>`), never on its own output.
"""

from __future__ import annotations

import json
import struct
import sys

import bmesh
import numpy as np
from mathutils import Vector

GEAR_NODE = "Gear"

#: Linear base colours. The accents are the clients' own side colours
#: (`ui_theme::SIDE_T`, `SIDE_CT`), deepened so they survive the team wash.
COLOURS = {
    "carrier": ((0.055, 0.058, 0.048), 0.85),
    "webbing": ((0.035, 0.036, 0.032), 0.9),
    "pouch": ((0.075, 0.078, 0.062), 0.85),
    "belt": ((0.05, 0.035, 0.022), 0.75),
    "scarf": ((0.42, 0.26, 0.13), 0.95),
    "accent_t": ((0.86, 0.32, 0.04), 0.7),
    "accent_ct": ((0.03, 0.2, 0.75), 0.7),
}

COMPONENTS = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


# -- reading the GLB ------------------------------------------------------------


def read_glb(path):
    data = open(path, "rb").read()
    length = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20 : 20 + length])
    at = 20 + length
    blob_len = struct.unpack("<I", data[at : at + 4])[0]
    blob = bytearray(data[at + 8 : at + 8 + blob_len])
    return doc, blob


def accessor(doc, blob, index):
    a = doc["accessors"][index]
    view = doc["bufferViews"][a["bufferView"]]
    dtype = np.dtype(COMPONENTS[a["componentType"]])
    n = WIDTH[a["type"]]
    start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = view.get("byteStride") or dtype.itemsize * n
    rows = np.empty((a["count"], n), dtype=dtype)
    for k in range(a["count"]):
        o = start + k * stride
        rows[k] = np.frombuffer(blob, dtype=dtype, count=n, offset=o)
    out = rows.astype(np.float64)
    if a.get("normalized"):
        out /= np.iinfo(dtype).max
    return out


def body(doc, blob):
    """Every skinned vertex in bind space, its dominant joint as a node index,
    and each joint node's bind-pose world matrix (the inverse of its inverse
    bind, which is the space the vertices are in)."""
    bind = {}
    for skin in doc["skins"]:
        ibms = accessor(doc, blob, skin["inverseBindMatrices"])
        for local, node in enumerate(skin["joints"]):
            if node not in bind:
                # glTF matrices are column-major.
                bind[node] = np.linalg.inv(ibms[local].reshape(4, 4).T)
    positions, owners, meshes = [], [], {}
    for node_index, node in enumerate(doc["nodes"]):
        if "mesh" not in node or "skin" not in node:
            continue
        joints_of = doc["skins"][node["skin"]]["joints"]
        mesh_points = []
        # T's eighty primitives are index ranges into one vertex buffer: read
        # each buffer once, or the body is counted eighty times over.
        seen = set()
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            if prim["attributes"]["POSITION"] in seen:
                continue
            seen.add(prim["attributes"]["POSITION"])
            p = accessor(doc, blob, prim["attributes"]["POSITION"])
            j = accessor(doc, blob, prim["attributes"]["JOINTS_0"]).astype(int)
            w = accessor(doc, blob, prim["attributes"]["WEIGHTS_0"])
            dominant = j[np.arange(len(j)), w.argmax(axis=1)]
            positions.append(p)
            owners.append(np.array([joints_of[d] for d in dominant]))
            mesh_points.append(p)
        meshes[node.get("name", str(node_index))] = np.concatenate(mesh_points)
    return np.concatenate(positions), np.concatenate(owners), bind, meshes


# -- the rig, by name -----------------------------------------------------------


class Rig:
    def __init__(self, doc, bind):
        self.doc, self.bind = doc, bind
        self.by_name = {}
        for node_index in bind:
            name = doc["nodes"][node_index].get("name", "")
            key = name.split(":")[-1].replace("mixamorig", "")
            self.by_name.setdefault(key, node_index)

    def node(self, bone):
        return self.by_name[bone]

    def head(self, bone):
        return Vector(self.bind[self.node(bone)][:3, 3])


# -- measuring the body ---------------------------------------------------------


def extent(points):
    return Vector(points.min(axis=0)), Vector(points.max(axis=0))


def core(points, around, bin_size, bridge=3):
    """The body's own extent, per axis: the run of occupied bins that holds
    `around`, across gaps of up to `bridge` empty bins. What hangs off the body
    with air between — the sword across T's back, weighted to his spine — is a
    separate run and is left out, where a plain min and max would size a
    carrier to include it. The bridge is because a body is a shell: through
    the chest, the middle bins are empty too."""
    lo, hi = [], []
    for axis in range(3):
        v = points[:, axis]
        edges = np.arange(v.min(), v.max() + bin_size, bin_size)
        counts, edges = np.histogram(v, bins=edges)
        floor = max(1, counts.max() * 0.01)
        at = min(max(np.searchsorted(edges, around[axis]) - 1, 0), len(counts) - 1)
        filled = counts >= floor
        a = b = at
        while True:
            step = next((k for k in range(1, bridge + 2) if a - k >= 0 and filled[a - k]), None)
            if step is None:
                break
            a -= step
        while True:
            step = next((k for k in range(1, bridge + 2) if b + k < len(counts) and filled[b + k]), None)
            if step is None:
                break
            b += step
        keep = (v >= edges[a]) & (v <= edges[b + 1])
        lo.append(v[keep].min())
        hi.append(v[keep].max())
    return Vector(lo), Vector(hi)


def owned(positions, owners, rig, bones):
    nodes = [rig.node(b) for b in bones]
    return positions[np.isin(owners, nodes)]


def torso_box(positions, owners, rig, y0, y1, half_width):
    """The torso's x and z extent between two heights, arms left out."""
    pts = owned(positions, owners, rig, ("Spine", "Spine1", "Spine2"))
    cx = (rig.head("LeftArm").x + rig.head("RightArm").x) / 2
    keep = (pts[:, 1] >= y0) & (pts[:, 1] <= y1) & (np.abs(pts[:, 0] - cx) < half_width)
    spine = rig.head("Spine1")
    return core(pts[keep], (cx, (y0 + y1) / 2, spine.z), (y1 - y0) * 0.06)


def limb_radius(positions, owners, rig, bone, child, at, slab=0.1):
    """How far the body reaches from a bone's axis around a point along it."""
    a, b = rig.head(bone), rig.head(child)
    axis = b - a
    length = axis.length
    axis.normalize()
    pts = owned(positions, owners, rig, (bone,))
    rel = pts - np.array(a)
    t = rel @ np.array(axis) / length
    near = rel[np.abs(t - at) < slab]
    along = np.outer(near @ np.array(axis), np.array(axis))
    radius = np.linalg.norm(near - along, axis=1).max()
    return a + axis * (length * at), axis, radius, length


# -- modelling ------------------------------------------------------------------


class Piece:
    def __init__(self, bm, bone, material):
        self.bm, self.bone, self.material = bm, bone, material


def box(center, size, bevel, rotation=None):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    if bevel > 0:
        bmesh.ops.bevel(
            bm,
            geom=bm.edges[:] + bm.verts[:],
            offset=bevel,
            segments=2,
            profile=0.5,
            affect="EDGES",
            clamp_overlap=True,
        )
    if rotation is not None:
        bmesh.ops.rotate(bm, cent=Vector(), matrix=rotation, verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    return bm


def band(center, axis, radius_x, radius_z, length, segments=20, bevel=0.0):
    """A short closed cylinder round an axis, elliptical across it."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=segments,
        radius1=1.0,
        radius2=1.0,
        depth=1.0,
    )
    # Built along z: squash across, stretch along, then turn onto the axis.
    bmesh.ops.scale(bm, vec=Vector((radius_x, radius_z, length)), verts=bm.verts)
    if bevel > 0:
        rims = [
            e for e in bm.edges if len(e.link_faces) == 2 and e.calc_face_angle(0) > 0.6
        ]
        bmesh.ops.bevel(
            bm,
            geom=rims,
            offset=bevel,
            segments=2,
            profile=0.5,
            affect="EDGES",
            clamp_overlap=True,
        )
    turn = Vector((0, 0, 1)).rotation_difference(Vector(axis)).to_matrix().to_4x4()
    bmesh.ops.rotate(bm, cent=Vector(), matrix=turn, verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=bm.verts)
    return bm


def gear_t(positions, owners, rig):
    pieces = []
    up = Vector((0, 1, 0))
    toe = rig.head("LeftToeBase") - rig.head("LeftFoot")
    forward = 1.0 if toe.z > 0 else -1.0
    shoulder_half = abs(rig.head("LeftArm").x - rig.head("RightArm").x) / 2
    spine1, spine2, neck = rig.head("Spine1"), rig.head("Spine2"), rig.head("Neck")
    # The carrier: from just above the belt to a little under the collar.
    spine = rig.head("Spine")
    y0 = spine.y + (spine1.y - spine.y) * 0.25
    y1 = spine2.y + (neck.y - spine2.y) * 0.45
    lo, hi = torso_box(positions, owners, rig, y0, y1, shoulder_half * 0.78)
    depth = hi.z - lo.z
    grow = depth * 0.05
    lo -= Vector((grow, 0, grow))
    hi += Vector((grow, 0, grow))
    center = (lo + hi) / 2
    size = hi - lo
    pieces.append(Piece(box(center, size, depth * 0.2), "Spine2", "carrier"))
    # Shoulder straps up over the trapezius to the back plate.
    for side in (-1, 1):
        x = center.x + side * size.x * 0.28
        rise = (neck.y - y1) * 0.7
        pieces.append(
            Piece(
                box(
                    (x, y1 + rise / 2 - depth * 0.04, center.z),
                    (size.x * 0.12, rise + depth * 0.08, size.z * 0.86),
                    depth * 0.04,
                ),
                "Spine2",
                "webbing",
            )
        )
    # Three magazine pouches across the front, and a radio pouch on the left.
    front = hi.z if forward > 0 else lo.z
    for k in (-1, 0, 1):
        pieces.append(
            Piece(
                box(
                    (
                        center.x + k * size.x * 0.24,
                        lo.y + size.y * 0.3,
                        front + forward * depth * 0.11,
                    ),
                    (size.x * 0.2, size.y * 0.42, depth * 0.24),
                    depth * 0.05,
                ),
                "Spine2",
                "pouch",
            )
        )
    pieces.append(
        Piece(
            box(
                (lo.x - depth * 0.08, center.y, center.z + forward * depth * 0.1),
                (depth * 0.22, size.y * 0.5, depth * 0.36),
                depth * 0.05,
            ),
            "Spine2",
            "pouch",
        )
    )
    # A belt at the hips, and a pouch either side.
    hips = rig.head("Hips")
    pts = owned(positions, owners, rig, ("Hips",))
    keep = np.abs(pts[:, 1] - (hips.y + depth * 0.15)) < depth * 0.12
    keep &= np.abs(pts[:, 0] - hips.x) < shoulder_half * 0.85
    blo, bhi = core(pts[keep], (hips.x, hips.y + depth * 0.15, hips.z), depth * 0.05)
    bgrow = depth * 0.025
    pieces.append(
        Piece(
            band(
                ((blo.x + bhi.x) / 2, hips.y + depth * 0.15, (blo.z + bhi.z) / 2),
                up,
                (bhi.x - blo.x) / 2 + bgrow,
                (bhi.z - blo.z) / 2 + bgrow,
                depth * 0.12,
                segments=24,
            ),
            "Hips",
            "belt",
        )
    )
    for side in (-1, 1):
        x = (bhi.x if side > 0 else blo.x) + side * depth * 0.02
        pieces.append(
            Piece(
                box(
                    (x, hips.y + depth * 0.02, (blo.z + bhi.z) / 2),
                    (depth * 0.2, depth * 0.36, depth * 0.34),
                    depth * 0.05,
                ),
                "Hips",
                "pouch",
            )
        )
    # A scarf, loose at the base of the neck.
    head = rig.head("Head")
    ny = neck.y + (head.y - neck.y) * 0.15
    pts = owned(positions, owners, rig, ("Neck", "Spine2"))
    keep = np.abs(pts[:, 1] - ny) < (head.y - neck.y) * 0.25
    keep &= np.abs(pts[:, 0] - neck.x) < shoulder_half * 0.45
    nlo, nhi = core(pts[keep], (neck.x, ny, neck.z), (head.y - neck.y) * 0.08)
    pieces.append(
        Piece(
            band(
                ((nlo.x + nhi.x) / 2, ny, (nlo.z + nhi.z) / 2),
                up,
                (nhi.x - nlo.x) / 2 * 1.1,
                (nhi.z - nlo.z) / 2 * 1.1,
                (head.y - neck.y) * 0.36,
                segments=20,
                bevel=(head.y - neck.y) * 0.08,
            ),
            "Neck",
            "scarf",
        )
    )
    pieces += armbands(positions, owners, rig, "accent_t")
    return pieces


def armbands(positions, owners, rig, accent):
    pieces = []
    for side in ("Left", "Right"):
        center, axis, radius, length = limb_radius(
            positions, owners, rig, f"{side}Arm", f"{side}ForeArm", 0.42
        )
        pieces.append(
            Piece(
                band(
                    center,
                    axis,
                    radius * 1.1,
                    radius * 1.1,
                    length * 0.16,
                    segments=18,
                    bevel=length * 0.015,
                ),
                f"{side}Arm",
                accent,
            )
        )
    return pieces


def gear_ct(positions, owners, rig, meshes):
    pieces = armbands(positions, owners, rig, "accent_ct")
    # A stripe round the helmet, a third of the way up it.
    hat = meshes.get("Hats")
    if hat is not None:
        lo, hi = extent(hat)
        y = lo.y + (hi.y - lo.y) * 0.42
        keep = np.abs(hat[:, 1] - y) < (hi.y - lo.y) * 0.05
        slo, shi = extent(hat[keep])
        pieces.append(
            Piece(
                band(
                    ((slo.x + shi.x) / 2, y, (slo.z + shi.z) / 2),
                    (0, 1, 0),
                    (shi.x - slo.x) / 2 * 1.025,
                    (shi.z - slo.z) / 2 * 1.025,
                    (hi.y - lo.y) * 0.09,
                    segments=28,
                ),
                "Head",
                "accent_ct",
            )
        )
    return pieces


# -- writing it back ------------------------------------------------------------


def triangles(bm):
    """Flat-shaded triangles, as positions, normals and box-projected UVs."""
    bm.normal_update()
    tris = bm.calc_loop_triangles()
    pos, nrm, uv = [], [], []
    for tri in tris:
        n = tri[0].face.normal
        a = max(range(3), key=lambda i: abs(n[i]))
        u_axis, v_axis = [(1, 2), (0, 2), (0, 1)][a]
        for loop in tri:
            co = loop.vert.co
            pos.append(tuple(co))
            nrm.append(tuple(loop.vert.normal if loop.face.smooth else n))
            uv.append((co[u_axis] * 0.05, co[v_axis] * 0.05))
    return pos, nrm, uv


def smooth(bm):
    for f in bm.faces:
        f.smooth = True


def append(doc, blob, pieces, rig):
    """Add the pieces as one skinned mesh under the root, one primitive per
    material, one skin over the bones they follow."""
    bones = sorted({p.bone for p in pieces}, key=lambda b: rig.node(b))
    slot = {b: i for i, b in enumerate(bones)}

    def view(data: bytes, target=None):
        while len(blob) % 4:
            blob.append(0)
        offset = len(blob)
        blob.extend(data)
        v = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            v["target"] = target
        doc["bufferViews"].append(v)
        return len(doc["bufferViews"]) - 1

    def acc(view_index, component, count, kind, minmax=None, normalized=False):
        a = {
            "bufferView": view_index,
            "componentType": component,
            "count": count,
            "type": kind,
        }
        if minmax:
            a["min"], a["max"] = minmax
        if normalized:
            a["normalized"] = True
        doc["accessors"].append(a)
        return len(doc["accessors"]) - 1

    materials = {}
    for key, (rgb, rough) in COLOURS.items():
        if not any(p.material == key for p in pieces):
            continue
        materials[key] = len(doc["materials"])
        doc["materials"].append(
            {
                "name": f"Gear_{key}",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [*rgb, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": rough,
                },
            }
        )

    by_material = {}
    for p in pieces:
        by_material.setdefault(p.material, []).append(p)
    prims = []
    for material, group in by_material.items():
        pos, nrm, uv, joints = [], [], [], []
        for p in group:
            if p.material in ("scarf", "belt") or p.material.startswith("accent"):
                smooth(p.bm)
            a, b, c = triangles(p.bm)
            pos += a
            nrm += b
            uv += c
            joints += [slot[p.bone]] * len(a)
            p.bm.free()
        n = len(pos)
        P = np.array(pos, dtype=np.float32)
        N = np.array(nrm, dtype=np.float32)
        N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-8)
        UV = np.array(uv, dtype=np.float32)
        J = np.zeros((n, 4), dtype=np.uint8)
        J[:, 0] = joints
        W = np.zeros((n, 4), dtype=np.float32)
        W[:, 0] = 1.0
        attrs = {
            "POSITION": acc(
                view(P.tobytes(), 34962),
                5126,
                n,
                "VEC3",
                (P.min(0).tolist(), P.max(0).tolist()),
            ),
            "NORMAL": acc(view(N.tobytes(), 34962), 5126, n, "VEC3"),
            "TEXCOORD_0": acc(view(UV.tobytes(), 34962), 5126, n, "VEC2"),
            "JOINTS_0": acc(view(J.tobytes(), 34962), 5121, n, "VEC4"),
            "WEIGHTS_0": acc(view(W.tobytes(), 34962), 5126, n, "VEC4"),
        }
        prims.append({"attributes": attrs, "material": materials[material], "mode": 4})
    ibm = np.stack(
        [np.linalg.inv(rig.bind[rig.node(b)]).T.reshape(16) for b in bones]
    ).astype(np.float32)
    skin = {
        "name": GEAR_NODE,
        "joints": [rig.node(b) for b in bones],
        "inverseBindMatrices": acc(view(ibm.tobytes()), 5126, len(bones), "MAT4"),
    }
    doc["skins"].append(skin)
    doc["meshes"].append({"name": GEAR_NODE, "primitives": prims})
    # Under the same root as every other skinned mesh in the file, with the
    # same (identity) transform, so three binds it exactly as it binds them.
    root = next(
        i for i, node in enumerate(doc["nodes"]) if "mesh" in node and "skin" in node
    )
    parent = next(
        i for i, node in enumerate(doc["nodes"]) if root in node.get("children", [])
    )
    doc["nodes"].append(
        {
            "name": GEAR_NODE,
            "mesh": len(doc["meshes"]) - 1,
            "skin": len(doc["skins"]) - 1,
        }
    )
    doc["nodes"][parent].setdefault("children", []).append(len(doc["nodes"]) - 1)
    doc["buffers"][0]["byteLength"] = len(blob)
    return len(prims), sum(len(p) for p in prims)


def write_glb(path, doc, blob):
    while len(blob) % 4:
        blob.append(0)
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * ((4 - len(text) % 4) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(text), 0x4E4F534A) + text)
        f.write(struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))


def main(argv):
    if len(argv) != 3 or argv[0] not in ("t", "ct"):
        raise SystemExit(__doc__)
    side, src, out = argv
    doc, blob = read_glb(src)
    if any(node.get("name") == GEAR_NODE for node in doc["nodes"]):
        raise SystemExit(f"{src} is already dressed: run on the undressed model")
    positions, owners, bind, meshes = body(doc, blob)
    rig = Rig(doc, bind)
    pieces = (
        gear_t(positions, owners, rig)
        if side == "t"
        else gear_ct(positions, owners, rig, meshes)
    )
    prims, _ = append(doc, blob, pieces, rig)
    write_glb(out, doc, blob)
    print(f"dressed {side}: {len(pieces)} pieces in {prims} primitives -> {out}")


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
