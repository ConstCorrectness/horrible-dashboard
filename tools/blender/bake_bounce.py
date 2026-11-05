#!/usr/bin/env python3
"""Bake each modelled map's bounce light into its GLB as COLOR_1.

The clients light a map with a sun, a sky hemisphere and point lights, none of
which bounce: a sunlit floor does not warm the wall beside it, and a corridor
off a bright plaza is as dark as one off a cellar. That missing second bounce
is most of what makes a map read as a box of coloured planes rather than a
place. This script computes it once, offline, as content: a Cycles DIFFUSE bake,
**indirect only**, per vertex, under exactly the rig the server serves the map
with (`atmosphere.resolve` and `atmosphere.lights`). Like the baked AO in
COLOR_0, it is the same at every quality level, so it can never be a setting
that changes what a player can see.

Run it **after fit** (`tools/hassault_fit_placements.py`), because fit moves
lights out of walls, and the lights are part of what is baked. Each map is
baked in its own Blender, one after another — never two at once:

    uv run --no-sync python tools/blender/bake_bounce.py hd_dust2 [hd_nuke ...]
    uv run --no-sync python tools/blender/bake_bounce.py --all

The GLB is patched, not re-exported: every existing byte stays where it was,
COLOR_0 included (checked), and a new COLOR_1 accessor is appended per
primitive (or rewritten in place when the map already has one). The result is
copied to `apps/web/public/`, as the generators do. Collision, nav and spawns
are not touched: bounce is light, not shape.

**The encoding.** COLOR_1 is normalised unsigned bytes, VEC4. `rgb` is the
bounce irradiance, in the units of the rig's own irradiance terms (the
`irradiance` sum in `lighting.wgsl.inc`'s `shade`, before `albedo / PI`),
stored on a square-root curve so the dim bounce in a corridor keeps its
precision and a lamp-lit wall above 1 still fits:

    bounce = BOUNCE_SCALE * c * c        (c = the decoded 0..1 channel)

`a` is 1 (padding: a vertex attribute's elements are 4-byte aligned). The
scale and curve are also written to the GLB as
`asset.extras.hassaultBounce = {"attribute": "COLOR_1", "scale": 4, "curve":
"square"}`, so a client can read them rather than hard-code them. A client adds
the decoded value to its irradiance; the sun, sky and lamps are already there.

**The light units.** Calibrated against Cycles itself (a diffuse vertex bake
reads `E / pi` for a sun or point light and `L` for world radiance), so the bake
sees the rig the clients draw:

- sun: a Cycles sun of energy `sunIntensity`, coloured `sunColor`, from
  `sunDir` (three space, toward the light);
- sky: a world whose upper half has radiance `hemiSky * hemiIntensity / pi` and
  lower half `hemiGround * hemiIntensity / pi` — three's hemisphere light, which
  gives exactly those two at the poles and their mean at the horizon;
- lamps: a point light of power `4 pi * candela` (`intensity * radius / 2`),
  so `E = candela / d^2`. Cycles has no range window; three's window is under
  half its strength only past three quarters of the radius, where `E` is
  already small.

The fill light is left out: it is a stand-in for bounce at the higher quality
levels, and baking it would count it twice. Emission is zeroed (the lamps are
already the point lights), and see-through, self-lit and invisible objects are
hidden from the bake, as in `maplib.bake_vertex_ao`.

Coordinates: Blender's glTF import turns the GLB's (x, y, z) into (x, -z, y),
and the GLB is three space with z negated, so Blender's axes are the game's
cube axes — a light entity's (x, y, z) goes in unchanged.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MAPS_DIR = os.path.join(REPO_ROOT, "backend", "modules", "hassault", "maps")
WEB_DIR = os.path.join(REPO_ROOT, "apps", "web", "public")
BLENDER = os.environ.get(
    "BLENDER", r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe"
)

#: `bounce = BOUNCE_SCALE * c^2`. Four irradiance units is twice the brightest
#: served sun; past it a vertex clamps.
BOUNCE_SCALE = 4.0
BOUNCE_ATTRIBUTE = "BOUNCE"
#: Path samples per vertex. Indirect light is noisy and a vertex has no
#: neighbouring pixels to denoise with, so this is far more than AO's 48.
BOUNCE_SAMPLES = int(os.environ.get("HASSAULT_BOUNCE_SAMPLES", "512"))
#: Passes of neighbour averaging over each mesh's own vertex graph, after the
#: bake: residual sample noise reads as blotchy triangles. glTF splits vertices
#: at hard edges, so this never blends a floor into the wall it meets.
BOUNCE_SMOOTH = int(os.environ.get("HASSAULT_BOUNCE_SMOOTH", "2"))

#: Mirrors `maplib._AO_SKIP_NAMES` / `_AO_SKIP_MATERIALS`: objects the bake
#: must not see. They get no COLOR_1.
SKIP_NAMES = ("_ColOnly", "Invisible", "Safety_Floor", "Boundary")
SKIP_MATERIALS = ("glass", "window", "glow", "emiss", "light", "lamp", "sky")
#: A light inside a fixture is boxed in by it; fixtures this small around a
#: light are hidden from the bake (the clients' lamps cast no shadow at all).
FIXTURE_SIZE = 3.0

ALL_MAPS = (
    "hd_aim",
    "hd_assault",
    "hd_bank",
    "hd_dust2",
    "hd_facility",
    "hd_inferno",
    "hd_junkflea",
    "hd_mirage",
    "hd_nuke",
    "hd_office",
)


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_linear(value: int) -> list[float]:
    return [srgb_to_linear(((value >> s) & 0xFF) / 255.0) for s in (16, 8, 0)]


# ---- the Blender half --------------------------------------------------------------


def _enable_gpu(scene) -> str:
    import bpy

    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == kind]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == kind
            scene.cycles.device = "GPU"
            return kind
    scene.cycles.device = "CPU"
    return "CPU"


def _skip_material(mat) -> bool:
    if mat is None:
        return False
    lower = mat.name.lower()
    return any(k in lower for k in SKIP_MATERIALS) or mat.blend_method == "BLEND"


def _skipped(obj) -> bool:
    if any(k in obj.name for k in SKIP_NAMES):
        return True
    mats = [s.material for s in obj.material_slots]
    return bool(mats) and all(_skip_material(m) for m in mats)


def blender_main(glb: str, rig_path: str, out_path: str) -> None:
    import bpy
    import numpy as np
    from mathutils import Vector

    with open(rig_path, encoding="utf-8") as f:
        rig = json.load(f)
    atmo = rig["atmosphere"]

    bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)
    real_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        bpy.ops.import_scene.gltf(filepath=glb, merge_vertices=False)
    finally:
        sys.stdout.close()
        sys.stdout = real_stdout
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]

    # Self-lit materials stop emitting (the lamps are the point lights), and
    # the vertex-colour node the importer made reads COLOR_0 by name, so the
    # baked attribute being active does not feed back into the albedo.
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                node.inputs["Emission Strength"].default_value = 0.0
            elif node.type == "EMISSION":
                node.inputs["Strength"].default_value = 0.0
            elif node.type == "VERTEX_COLOR" and not node.layer_name:
                node.layer_name = "Color"

    hidden = [o for o in meshes if _skipped(o)]
    # Small fixtures around a light: the lamp shade, the lantern cage.
    lights = rig["lights"]
    for o in meshes:
        if o in hidden:
            continue
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        if max(hi[i] - lo[i] for i in range(3)) > FIXTURE_SIZE:
            continue
        for light in lights:
            p = (light["x"], light["y"], light["z"])
            if all(lo[i] - 0.25 <= p[i] <= hi[i] + 0.25 for i in range(3)):
                hidden.append(o)
                break
    receivers = [o for o in meshes if o not in hidden]
    for o in hidden:
        o.hide_render = True

    # The rig.
    world = bpy.data.worlds.new("Bounce_World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    geo = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    step = nt.nodes.new("ShaderNodeMath")
    step.operation = "GREATER_THAN"
    step.inputs[1].default_value = 0.0
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    k = atmo["hemiIntensity"] / math.pi
    mix.inputs[6].default_value = (
        *[c * k for c in hex_linear(atmo["hemiGround"])],
        1.0,
    )
    mix.inputs[7].default_value = (*[c * k for c in hex_linear(atmo["hemiSky"])], 1.0)
    nt.links.new(geo.outputs["Generated"], sep.inputs[0])
    # World "Generated" coordinates are the ray direction.
    nt.links.new(sep.outputs["Z"], step.inputs[0])
    nt.links.new(step.outputs[0], mix.inputs[0])
    nt.links.new(mix.outputs[2], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs[0], out.inputs["Surface"])

    sx, sy, sz = atmo["sunDir"]  # three space: (x, height, game y)
    sun_dir = Vector((sx, sz, sy)).normalized()
    sun = bpy.data.lights.new("Bounce_Sun", "SUN")
    sun.energy = atmo["sunIntensity"]
    sun.color = hex_linear(atmo["sunColor"])
    sun.angle = math.radians(0.53)
    sun_obj = bpy.data.objects.new("Bounce_Sun", sun)
    # A sun shines down its local -Z.
    sun_obj.rotation_euler = (-sun_dir).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(sun_obj)

    for i, light in enumerate(lights):
        candela = light["intensity"] * light["radius"] * 0.5
        if candela <= 0:
            continue
        data = bpy.data.lights.new(f"Bounce_Lamp_{i}", "POINT")
        data.energy = 4.0 * math.pi * candela
        data.color = hex_linear(light["color"])
        data.shadow_soft_size = 0.25
        obj = bpy.data.objects.new(f"Bounce_Lamp_{i}", data)
        obj.location = (light["x"], light["y"], light["z"])
        scene.collection.objects.link(obj)

    scene.render.engine = "CYCLES"
    device = _enable_gpu(scene)
    scene.cycles.samples = BOUNCE_SAMPLES
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.max_bounces = 4
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 1
    scene.cycles.transmission_bounces = 0
    scene.cycles.transparent_max_bounces = 2
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.sample_clamp_indirect = 4.0
    scene.cycles.seed = 0
    print(
        f"bounce: {len(receivers)} receivers, {len(hidden)} hidden, "
        f"{len(lights)} lamps, {BOUNCE_SAMPLES} samples on {device}"
    )

    for o in receivers:
        attrs = o.data.color_attributes
        if BOUNCE_ATTRIBUTE in attrs:
            attrs.remove(attrs[BOUNCE_ATTRIBUTE])
        attr = attrs.new(BOUNCE_ATTRIBUTE, "FLOAT_COLOR", "POINT")
        attrs.active_color = attr
    bpy.ops.object.select_all(action="DESELECT")
    for o in receivers:
        o.select_set(True)
    bpy.context.view_layer.objects.active = receivers[0]
    bpy.ops.object.bake(
        type="DIFFUSE", pass_filter={"INDIRECT"}, target="VERTEX_COLORS", use_clear=True
    )

    # Per glTF mesh (the importer names each Blender mesh after it, one Blender
    # vertex per glTF vertex, in order: the driver checks the count).
    names, arrays = [], []
    for o in receivers:
        data = o.data.color_attributes[BOUNCE_ATTRIBUTE].data
        buf = np.empty(len(data) * 4, np.float32)
        data.foreach_get("color", buf)
        # The bake reads E / pi; the clients add E.
        arrays.append(buf.reshape(-1, 4)[:, :3] * math.pi)
        names.append(o.data.name)
    np.savez(out_path, *arrays)
    with open(out_path + ".json", "w", encoding="utf-8") as f:
        json.dump(names, f)


# ---- the driver half ----------------------------------------------------------------


def resolve_rig(name: str) -> dict:
    """The atmosphere and lights `GET /maps/{name}` serves, from the backend."""
    sys.path.insert(0, REPO_ROOT)
    from backend.modules.hassault import atmosphere, mapsource

    with open(os.path.join(MAPS_DIR, f"{name}.json"), encoding="utf-8") as f:
        source = json.load(f)
    world = mapsource.build(source, name=name)
    return {
        "atmosphere": atmosphere.resolve(
            world.atmosphere, source.get("format", "gltf")
        ),
        "lights": atmosphere.lights(world),
    }


def read_glb(path: str) -> tuple[dict, bytearray]:
    with open(path, "rb") as f:
        data = f.read()
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_len])
    bin_len = struct.unpack_from("<I", data, 20 + json_len)[0]
    blob = bytearray(data[20 + json_len + 8 : 20 + json_len + 8 + bin_len])
    return doc, blob


def write_glb(path: str, doc: dict, blob: bytes) -> None:
    blob = bytes(blob) + b"\0" * ((4 - len(blob) % 4) % 4)
    doc["buffers"][0]["byteLength"] = len(blob)
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * ((4 - len(text) % 4) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(text), 0x4E4F534A) + text)
        f.write(struct.pack("<II", len(blob), 0x004E4942) + blob)
    os.replace(tmp, path)


def _view_bytes(doc: dict, blob: bytes, accessor: int) -> bytes:
    acc = doc["accessors"][accessor]
    view = doc["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0)
    return bytes(blob[start : start + view["byteLength"]])


def encode(bounce, np):
    """Linear irradiance -> normalised RGBA bytes on the square-root curve."""
    c = np.sqrt(np.clip(bounce / BOUNCE_SCALE, 0.0, 1.0))
    rgb = np.clip(np.round(c * 255.0), 0, 255).astype(np.uint8)
    out = np.full((len(rgb), 4), 255, np.uint8)
    out[:, :3] = rgb
    return out


def smooth(values, indices, np, passes: int):
    """Average each vertex with its triangle neighbours, `passes` times."""
    if passes <= 0 or indices is None or len(indices) == 0:
        return values
    tris = indices.reshape(-1, 3)
    a = np.concatenate(
        [tris[:, 0], tris[:, 1], tris[:, 2], tris[:, 1], tris[:, 2], tris[:, 0]]
    )
    b = np.concatenate(
        [tris[:, 1], tris[:, 2], tris[:, 0], tris[:, 0], tris[:, 1], tris[:, 2]]
    )
    n = len(values)
    degree = np.bincount(a, minlength=n).astype(np.float64)
    out = values.astype(np.float64)
    for _ in range(passes):
        acc = np.zeros_like(out)
        np.add.at(acc, a, out[b])
        nb = acc / np.maximum(degree, 1)[:, None]
        out = np.where(degree[:, None] > 0, 0.5 * out + 0.5 * nb, out)
    return out


def _colour0(doc: dict, blob: bytes) -> list[bytes]:
    """Every primitive's COLOR_0 bytes, in order: what must not change."""
    return [
        _view_bytes(doc, blob, p["attributes"]["COLOR_0"])
        for m in doc["meshes"]
        for p in m["primitives"]
        if "COLOR_0" in p["attributes"]
    ]


def patch_glb(path: str, names: list[str], arrays: list) -> dict:
    import numpy as np

    doc, blob = read_glb(path)
    colour0_before = _colour0(doc, blob)
    by_name = {m.get("name"): i for i, m in enumerate(doc["meshes"])}
    stats = []
    for name, values in zip(names, arrays):
        mi = by_name.get(name)
        if mi is None:
            raise SystemExit(f"bounce: no glTF mesh named {name!r}")
        prims = doc["meshes"][mi]["primitives"]
        counts = [doc["accessors"][p["attributes"]["POSITION"]]["count"] for p in prims]
        if sum(counts) != len(values):
            raise SystemExit(
                f"bounce: {name}: {len(values)} baked vertices for {sum(counts)} in the GLB"
            )
        offset = 0
        for p, count in zip(prims, counts):
            part = values[offset : offset + count]
            idx = None
            if "indices" in p:
                ia = doc["accessors"][p["indices"]]
                dt = {5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[
                    ia["componentType"]
                ]
                raw = _view_bytes(doc, blob, p["indices"])
                idx = np.frombuffer(
                    raw, dt, ia["count"], ia.get("byteOffset", 0)
                ).astype(np.int64)
            part = smooth(part, idx, np, BOUNCE_SMOOTH)
            stats.append(part)
            packed = encode(part, np).tobytes()
            offset += count
            existing = p["attributes"].get("COLOR_1")
            if existing is not None:
                acc = doc["accessors"][existing]
                view = doc["bufferViews"][acc["bufferView"]]
                if acc["count"] != count or view["byteLength"] != len(packed):
                    raise SystemExit(
                        f"bounce: {name}: COLOR_1 changed size; regenerate"
                    )
                start = view.get("byteOffset", 0)
                blob[start : start + len(packed)] = packed
                continue
            while len(blob) % 4:
                blob.append(0)
            doc["bufferViews"].append(
                {
                    "buffer": 0,
                    "byteOffset": len(blob),
                    "byteLength": len(packed),
                    "target": 34962,
                }
            )
            blob += packed
            doc["accessors"].append(
                {
                    "bufferView": len(doc["bufferViews"]) - 1,
                    "componentType": 5121,
                    "normalized": True,
                    "count": count,
                    "type": "VEC4",
                }
            )
            p["attributes"]["COLOR_1"] = len(doc["accessors"]) - 1
    asset = doc.setdefault("asset", {})
    asset.setdefault("extras", {})["hassaultBounce"] = {
        "attribute": "COLOR_1",
        "scale": BOUNCE_SCALE,
        "curve": "square",
    }
    colour0_after = _colour0(doc, blob)
    if colour0_after != colour0_before:
        raise SystemExit("bounce: COLOR_0 would change; refusing to write")
    write_glb(path, doc, blob)
    allv = np.concatenate(stats) if stats else np.zeros((1, 3))
    lum = allv @ np.array([0.2126, 0.7152, 0.0722])
    return {
        "vertices": int(len(allv)),
        "mean": round(float(lum.mean()), 4),
        "p50": round(float(np.percentile(lum, 50)), 4),
        "p99": round(float(np.percentile(lum, 99)), 4),
        "max": round(float(lum.max()), 4),
        "clamped": int((allv.max(axis=1) > BOUNCE_SCALE).sum()),
    }


def bake_map(name: str) -> dict:
    import numpy as np

    glb = os.path.join(MAPS_DIR, f"{name}.glb")
    work = tempfile.mkdtemp(prefix=f"bounce_{name}_")
    try:
        rig_path = os.path.join(work, "rig.json")
        with open(rig_path, "w", encoding="utf-8") as f:
            json.dump(resolve_rig(name), f)
        out = os.path.join(work, "bounce.npz")
        cmd = [
            BLENDER,
            "--background",
            "--factory-startup",
            "--python",
            os.path.abspath(__file__),
            "--",
            "--blender",
            glb,
            rig_path,
            out,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        for line in proc.stdout.splitlines():
            if line.startswith("bounce:") or "Error" in line:
                print(f"  {line}")
        if proc.returncode != 0 or not os.path.isfile(out):
            print(proc.stdout[-4000:], proc.stderr[-4000:])
            raise SystemExit(f"bounce: Blender failed on {name}")
        with open(out + ".json", encoding="utf-8") as f:
            names = json.load(f)
        with np.load(out) as npz:
            arrays = [npz[f"arr_{i}"] for i in range(len(names))]
        stats = patch_glb(glb, names, arrays)
        shutil.copyfile(glb, os.path.join(WEB_DIR, f"{name}.glb"))
        return stats
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> None:
    names = list(ALL_MAPS) if argv == ["--all"] else argv
    if not names:
        raise SystemExit(__doc__)
    import time

    for name in names:
        start = time.time()
        print(f"{name}: baking bounce")
        stats = bake_map(name)
        print(f"{name}: {stats} in {time.time() - start:.0f}s")


if __name__ == "__main__":
    if "--blender" in sys.argv:
        args = sys.argv[sys.argv.index("--blender") + 1 :]
        blender_main(*args)
    else:
        main(sys.argv[1:])
