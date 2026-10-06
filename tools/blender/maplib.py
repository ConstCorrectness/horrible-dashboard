"""Shared export step for the modelled hassault maps (`generate_*.py`).

Every generator here is authored in **metres** — a 70 m Dust II, a 4 m office
storey, a 2.6 m shipping container. The game is not in metres: one world unit is
one cube, and a standing player is 5.2 of them (eye 4.5 + 0.7 above it). Read
as cubes, a metre-scale map puts a 1.75 m person in a world built for someone a
third their size: a 2.2-unit body squeezing through a 4.4-unit "grand" archway,
a 4.2-unit tunnel it cannot stand up in, and a whole Dust II that is thirty body
widths across. That is what "the maps are too small" was.

So the conversion lives here, once, applied to the finished scene just before
export: `MAP_SCALE` cubes per metre, uniformly. 3.0 is the player's own
proportion (5.2 cubes / ~1.75 m) and the figure `generate_office.py` already
used for its heights. It is **uniform** on purpose — scaling only the footprint
would turn every crate into a slab and every barrel into an ellipse.

The generators keep authoring in metres. Everything that places something *on*
a map — the JSON's brushes, spawns, items and sites, the `world3d.ts` /
`world3d.rs` tables, bot cover nodes — is in cubes, i.e. already multiplied.

`HASSAULT_MAP_SCALE` and `HASSAULT_MAP_OUT` override the factor and the output
directory, so a generator can be checked against the committed GLB (run it at
1.0 into a scratch directory and compare) without touching the repo.

**Lights and atmosphere.** A GLB carries geometry and materials; how the map is
*lit* lives in its JSON, where both clients and the server read it
(`backend/modules/hassault/atmosphere.py`). A generator records point lights with
`add_light` and picks a sky with `set_atmosphere`, and `export_map_glb` writes
both into `<name>.json` beside the GLB — replacing that file's `light` entities
and `atmosphere` block, so a re-run is idempotent and nothing else in the JSON
(brushes, spawns, sites) is touched.

**Colour.** glTF's base colour is *linear*, and every generator here picked its
colours by eye as sRGB — `(0.84, 0.74, 0.58)` is a sandstone swatch, not a
linear reflectance. Exported as-is, each surface was about twice as bright as
meant and most of its saturation was gone: Dust II's sandstone drew as pale
grey. `export_map_glb` converts each Principled base colour once, on the way
out (lifting only the deepest darks, see `DARK_FLOOR`), so the generators
keep the numbers a person can read.
"""

from __future__ import annotations

import json
import os
import shutil

import bmesh
import bpy
import math

from mathutils import Matrix, Vector

MAP_SCALE = 3.0

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)


def _bake_modifiers() -> None:
    """Apply every modifier, so a bevel's width scales with the mesh it rounds.

    Scaling the mesh under a live Bevel leaves its width in absolute units — a
    5 cm chamfer on a crate three times the size. Baking first makes the whole
    object, modifiers included, one thing that scales together.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in list(bpy.data.objects):
        if obj.type != "MESH" or not obj.modifiers:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
        )
        old = obj.data
        obj.modifiers.clear()
        obj.data = mesh
        if old.users == 0:
            bpy.data.meshes.remove(old)


#: Edges sharper than this stay hard on a smooth-shaded face (a cylinder's rim).
SHARP_ANGLE = math.radians(35.0)


def clean_meshes() -> dict:
    """Make every mesh's faces point out, drop slivers, and keep hard edges hard.

    Both clients cull back faces and both derive normals from winding (the
    browser via `computeVertexNormals`, native via `face_normal`), so a mesh
    built inside-out is drawn as its own interior: hollow from the outside, and
    — because `bake_collision.py` counts solid by face orientation — wrong in
    the server's grid too. The bank's 460 gold ingots and Junk Flea's bridge
    ramps shipped that way.

    `recalc_face_normals` runs only on **closed** meshes. On an open sheet
    "outward" is a guess, and a guess that flips an authored sheet is worse
    than leaving it. Vertices are never welded: `add_boxes` packs touching boxes
    into one mesh, and welding them would make it non-manifold.

    Sharp edges are marked by `mark_sharp_edges`, after `soften_edges` has
    added the geometry they are judged on.
    """
    stats = {"flipped": 0, "degenerate": 0, "open": []}
    for mesh in bpy.data.meshes:
        if not mesh.users or not mesh.polygons:
            continue
        bm = bmesh.new()
        bm.from_mesh(mesh)
        before = len(bm.faces)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
        stats["degenerate"] += before - len(bm.faces)
        if all(e.is_manifold for e in bm.edges):
            normals = [f.normal.copy() for f in bm.faces]
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            if any(f.normal.dot(n) < 0 for f, n in zip(bm.faces, normals)):
                stats["flipped"] += 1
        else:
            stats["open"].append(mesh.name)
        bm.to_mesh(mesh)
        bm.free()
    print(
        f"clean_meshes: {stats['flipped']} meshes re-wound, "
        f"{stats['degenerate']} slivers dropped, {len(stats['open'])} open: "
        + ", ".join(stats["open"][:12])
    )
    return stats


def mark_sharp_edges() -> None:
    """Keep hard edges hard on smooth-shaded faces.

    Unmarked, a smooth-shaded cylinder's cap and side share vertices and the rim
    is averaged into a dark gradient (13k such vertices on Assault). Marked, the
    exporter splits them, and both clients' recomputed normals stay split.
    """
    for mesh in bpy.data.meshes:
        if not (mesh.users and mesh.polygons):
            continue
        # Added to, never overwritten: `soften_edges` has already marked where a
        # rounding strip meets the flat face it came from, at an angle below
        # this one, and `set_sharp_from_angle` would clear those.
        bm = bmesh.new()
        bm.from_mesh(mesh)
        for e in bm.edges:
            if not e.is_manifold or e.calc_face_angle(0.0) > SHARP_ANGLE:
                e.smooth = False
        bm.to_mesh(mesh)
        bm.free()


#: Edges sharper than this get rounded by `soften_edges`. Above 45° on purpose:
#: an eight-sided post's facets meet at 45°, and rounding every facet seam of a
#: thin cylinder collapses the bevels into each other. Its rims still round.
SOFTEN_ANGLE = math.radians(50.0)

#: Rounding per surface kind, as (radius in metres, fraction of the object's
#: thinnest side). The radius is the most a kind ever gets; the fraction keeps a
#: thin shelf or post in proportion. Kinds are the game's own classification of
#: a material name (`glb-surfaces.json`, read by both clients), so "what is
#: stone" is decided in one place. Worn masonry and plaster are where a hard
#: 90° edge reads most as a Cube level, so they get the most.
SOFTEN_BY_KIND = {
    "masonry": (0.15, 0.25),
    "plaster": (0.12, 0.25),
    "brick": (0.06, 0.2),
    "cobblestone": (0.05, 0.2),
    "roof_tile": (0.05, 0.2),
    "marble": (0.03, 0.15),
    "wood": (0.04, 0.2),
    "crate": (0.04, 0.2),
    "carpet": (0.03, 0.3),
    "container": (0.025, 0.15),
    "vault_steel": (0.025, 0.15),
    "gold": (0.02, 0.2),
    "asphalt": (0.04, 0.2),
    "hazard": (0.025, 0.15),
    # Photographed kinds (Dust II): each rounds as the generated kind it replaces,
    # so giving a wall a photograph does not change its shape.
    "sandstone": (0.15, 0.25),
    "limewash": (0.15, 0.25),
    "paving": (0.15, 0.25),
    "dunesand": (0.12, 0.25),
    "cedar": (0.04, 0.2),
    "souk_crate": (0.04, 0.2),
    "rust_iron": (0.025, 0.15),
    "burlap": (0.03, 0.3),
    "glaze": (0.03, 0.15),
}
#: A material the table does not classify.
SOFTEN_DEFAULT = (0.04, 0.2)
#: Below this thinnest side (metres) an object is trim or a cable: left sharp,
#: because a bevel there costs triangles nobody can see.
SOFTEN_MIN_SIDE = 0.05
#: How far past an edge (metres) the seam probe looks for a surface carrying on.
SEAM_PROBE = 0.02

_SURFACES_JSON = os.path.join(
    REPO_ROOT, "packages", "core", "src", "modules", "hassault", "glb-surfaces.json"
)


def _surface_kind(material_name: str, rules: list) -> str:
    """The kind `glb-surfaces.ts` gives a material: first matching rule wins."""
    name = material_name.lower()
    for rule in rules:
        if any(m in name for m in rule["match"]):
            return rule["kind"]
    return "default"


def _world_bvh(exclude=None):
    """One BVH over every visible map surface, in world space, optionally
    leaving one object out."""
    from mathutils.bvhtree import BVHTree

    verts: list = []
    polys: list = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or "ColOnly" in obj.name or "Invisible" in obj.name:
            continue
        if obj is exclude:
            continue
        mesh = obj.evaluated_get(depsgraph).data
        base = len(verts)
        m = obj.matrix_world
        verts.extend(m @ v.co for v in mesh.vertices)
        polys.extend([base + i for i in p.vertices] for p in mesh.polygons)
    return BVHTree.FromPolygons(verts, polys)


def _is_seam(bvh, edge, matrix, normal_matrix, probe: float) -> bool:
    """Whether a face beside `edge` carries on into, or rests against, another
    surface just past it: two floor slabs laid edge to edge, a wall standing on
    the ground, a wall butting into another. Rounding there carves a gutter.

    For each face, step `probe` past the edge within that face's plane, then
    look along the face normal for a surface lying in the same plane.
    """
    a = matrix @ edge.verts[0].co
    b = matrix @ edge.verts[1].co
    mid = (a + b) / 2
    along = (b - a).normalized()
    for face in edge.link_faces:
        n = (normal_matrix @ face.normal).normalized()
        # In-plane, perpendicular to the edge, pointing away from the face.
        out = n.cross(along).normalized()
        centre = matrix @ face.calc_center_median()
        if out.dot(centre - mid) > 0:
            out = -out
        p = mid + out * probe
        lift = probe * 2.5
        hit, hit_n, _i, dist = bvh.ray_cast(p + n * lift, -n, lift * 2)
        if (
            hit is not None
            and abs(dist - lift) < probe * 0.5
            and abs(hit_n.dot(n)) > 0.98
        ):
            return True
    return False


#: How far `separate_coplanar` lifts a losing face per layer, metres. Not 1 cm
#: on purpose: the generators stack their own decals in 1 cm steps, and a lift
#: equal to that step lands one decal exactly on the next (Assault's bay floor
#: ping-ponged for four passes). 7 mm is still resolved by the depth buffer out
#: to about 150 cubes at the camera's 0.1-600 range, and far too little for the
#: physics or the collision bake to notice.
COPLANAR_LIFT = 0.007
#: The most layers one plane's stack is lifted by: a guard, since layers are per
#: plane and a real stack is two to four deep. Before they were per plane, a
#: depth inherited across planes compounded to 8 cm on Assault's curbs.
COPLANAR_MAX_LAYERS = 6


def separate_coplanar(factor: float = 1.0, passes: int = 6) -> dict:
    """Run `_separate_coplanar_pass` until nothing fights, or `passes` runs.

    More than one pass because a lift can land a face on a plane that was
    already taken: generators stack decals in 1 cm steps of their own, so a
    crosswalk stripe lifted 1 cm off the asphalt met the manhole cover its author
    had put exactly 1 cm up. The next pass lifts the cover.
    """
    total = {"faces": 0, "objects": 0}
    for _ in range(passes):
        done = _separate_coplanar_pass(factor)
        total["faces"] += done["faces"]
        total["objects"] += done["objects"]
        if not done["faces"]:
            break
    return total


def _separate_coplanar_pass(factor: float = 1.0) -> dict:
    """Stop two surfaces drawn in the same plane from z-fighting.

    Two faces of different objects and different materials, facing the same way
    in the same plane and overlapping, are never meant: the depth buffer cannot
    order them, so they flicker as the camera moves. Every map shipped some:
    the souk canopies drawn twice, hazard stripes inside the wall they mark,
    monitor screens flush with their bezels, a brick skirt in the plane of its
    wall, floor slabs over the tops of pit walls.

    Within each cluster of faces that overlap in one plane, the largest object
    is the base and each smaller one is dressing laid on it, one
    `COPLANAR_LIFT` per rank toward the viewer: a mosaic field on its brass
    border on a floor sits two lifts up, the border one. Same-material overlaps are left
    alone, since the two shade identically and the fight cannot be seen.
    """
    lift = COPLANAR_LIFT * factor
    tol_d = 0.004 * factor
    # Overlaps thinner than this (a shared edge, rounding) do not count.
    eps = 0.002 * factor
    objects = [
        o
        for o in bpy.data.objects
        if o.type == "MESH"
        and o.data.polygons
        and "ColOnly" not in o.name
        and "Invisible" not in o.name
    ]
    size = {o.name: sum(p.area for p in o.data.polygons) for o in objects}
    # (object, polygon index, normal, plane offset, world triangles)
    buckets: dict = {}
    for o in objects:
        m = o.matrix_world
        nm = m.to_3x3().inverted_safe().transposed()
        verts = [m @ v.co for v in o.data.vertices]
        # Blender's own triangulation: a fan from the first corner is wrong for
        # the concave n-gons that rounding and weathering leave behind.
        o.data.calc_loop_triangles()
        poly_tris: dict = {}
        for lt in o.data.loop_triangles:
            poly_tris.setdefault(lt.polygon_index, []).append(
                tuple(verts[i] for i in lt.vertices)
            )
        for p in o.data.polygons:
            if p.area < 1e-8:
                continue
            n = (nm @ p.normal).normalized()
            pts = [verts[i] for i in p.vertices]
            d = n.dot(pts[0])
            tris = poly_tris.get(p.index, [])
            key = (round(n.x, 2), round(n.y, 2), round(n.z, 2))
            mat = p.material_index
            name = (
                o.data.materials[mat].name
                if mat < len(o.data.materials) and o.data.materials[mat]
                else ""
            )
            buckets.setdefault(key, []).append((o, p.index, n, d, tris, name))

    def flat(tri, n):
        """The triangle in 2D, dropping the normal's dominant axis."""
        ax = max(range(3), key=lambda i: abs(n[i]))
        keep = [i for i in range(3) if i != ax]
        return [(v[keep[0]], v[keep[1]]) for v in tri]

    def overlaps(t1, t2, eps) -> bool:
        """Separating-axis test: true only for a positive-area overlap, so two
        faces that merely share an edge are not a fight."""
        for tri in (t1, t2):
            for i in range(3):
                (x1, y1), (x2, y2) = tri[i], tri[(i + 1) % 3]
                ax, ay = y1 - y2, x2 - x1
                p1 = [ax * x + ay * y for x, y in t1]
                p2 = [ax * x + ay * y for x, y in t2]
                scale = (ax * ax + ay * ay) ** 0.5 or 1.0
                if min(p1) >= max(p2) - eps * scale or min(p2) >= max(p1) - eps * scale:
                    return False
        return True

    # Union-find over faces: each component is one cluster of faces that
    # overlap in one plane. Layers are assigned per cluster, so a stack is only
    # as deep as what actually shares that plane, never a chain inherited from
    # other planes (which lifted Assault's curbs 8 cm).
    parent: dict = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    face_of: dict = {}
    pairs: list = []
    for faces in buckets.values():
        if len(faces) < 2:
            continue
        # Same facing; now same plane, as a window over the sorted offsets.
        faces.sort(key=lambda f: f[3])
        for i, fa in enumerate(faces):
            for fb in faces[i + 1 :]:
                if fb[3] - fa[3] > tol_d:
                    break
                oa, ob = fa[0], fb[0]
                if oa is ob or fa[5] == fb[5]:
                    continue
                if fa[2].dot(fb[2]) < 0.999:
                    continue
                ta = [flat(t, fa[2]) for t in fa[4]]
                tb = [flat(t, fa[2]) for t in fb[4]]
                if not any(overlaps(x, y, eps) for x in ta for y in tb):
                    continue
                ka, kb = (oa.name, fa[1]), (ob.name, fb[1])
                face_of[ka], face_of[kb] = fa, fb
                parent[find(ka)] = find(kb)
                pairs.append((ka, kb))

    # Within a cluster, an object sits one layer above the highest larger
    # object it actually overlaps there. Ranking a whole cluster by size
    # instead (a street with every stripe and stain on it) ran out of layers,
    # and stripes that never touch each other do not need different heights.
    above: dict = {}
    for ka, kb in pairs:
        a, b = ka[0], kb[0]
        small, big = (ka, kb) if (size[a], a) <= (size[b], b) else (kb, ka)
        above.setdefault((find(ka), small[0]), set()).add(big[0])

    memo: dict = {}

    def layer(cluster, name: str) -> int:
        key = (cluster, name)
        if key not in memo:
            memo[key] = 0
            memo[key] = 1 + max(
                (layer(cluster, b) for b in above.get(key, ())), default=-1
            )
        return memo[key]

    # Per object, per polygon: how many layers up it goes.
    lifts: dict = {}
    for k in face_of:
        n, pi = k
        up = layer(find(k), n)
        if up:
            per = lifts.setdefault(n, {})
            per[pi] = max(per.get(pi, 0), up)

    moved = 0
    for name, polys in lifts.items():
        o = bpy.data.objects[name]
        mesh = o.data
        to_local = o.matrix_world.inverted_safe()
        nm = o.matrix_world.to_3x3().inverted_safe().transposed()
        offsets: dict = {}
        for pi, layers in polys.items():
            p = mesh.polygons[pi]
            n = (nm @ p.normal).normalized() * (lift * min(layers, COPLANAR_MAX_LAYERS))
            for vi in p.vertices:
                offsets[vi] = offsets.get(vi, Vector()) + n
        m = o.matrix_world
        for vi, off in offsets.items():
            v = mesh.vertices[vi]
            v.co = to_local @ (m @ v.co + off)
        moved += len(polys)
    print(f"separate_coplanar: {moved} faces lifted on {len(lifts)} objects")
    return {"faces": moved, "objects": len(lifts)}


#: Materials whose walls are hand-built: undulated by `weather_walls`. Lowercase
#: substrings, like `glb-surfaces.json`; `WEATHER_EXCLUDE` wins over a match.
WEATHER_MATCH = (
    "sandstone",
    "stucco",
    "adobe",
    "plaster",
    "limestone",
    "granite",
    "stone",
    "sand",
    "terracotta",
)
WEATHER_EXCLUDE = (
    "drywall",
    "office",
    "ceiling",
    "paving",
    "tile",
    "marble",
    "wall_accent",
)
#: Grid the faces are sliced into before displacing, metres.
WEATHER_CELL = 1.25
#: Peak displacement, metres. Under 3.5 cm on purpose: `bake_collision.py`
#: samples 4x4 rays per one-cube cell, the outermost a quarter of a cube (8 cm)
#: in from the cell edge, so a wall can bulge this far without moving the grid.
WEATHER_AMPLITUDE = 0.03
#: Only faces at least this large (m²) are worth slicing.
WEATHER_MIN_AREA = 3.0
#: A decoration this close (metres) in front of a wall pins the wall flat there.
WEATHER_CLEARANCE = 0.12


def _weathers(material_name: str) -> bool:
    name = material_name.lower()
    return any(m in name for m in WEATHER_MATCH) and not any(
        x in name for x in WEATHER_EXCLUDE
    )


def weather_walls(factor: float = 1.0) -> dict:
    """Give big masonry and plaster walls a gentle hand-built undulation.

    A rounded edge fixes a box's corners; a wall forty metres long is still a
    single perfect plane, and that flatness under a raking sun is the other half
    of what reads as a Cube level. This slices each large vertical stone or
    plaster face into a `WEATHER_CELL` grid and pushes the interior vertices in
    and out along the face normal by low-frequency noise in world space, so two
    walls that meet agree at the seam.

    Vertices on an edge never move (their faces disagree on a normal), so
    corners, seams and the later rounding all stay true; and a vertex with any
    other surface within `WEATHER_CLEARANCE` of it, in any direction, stays put:
    a rug or sconce mounted flush, a trim in the wall's own plane, the floor at
    its foot. Otherwise the wall would weave through them. Floors are never
    touched.
    """
    from mathutils import noise

    stats = {"objects": 0, "moved": 0}
    done: set[str] = set()
    cell = WEATHER_CELL * factor
    amp = WEATHER_AMPLITUDE * factor
    clearance = WEATHER_CLEARANCE * factor
    min_area = WEATHER_MIN_AREA * factor * factor
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.data.name in done or obj.data.users > 1:
            continue
        if "ColOnly" in obj.name or "Invisible" in obj.name:
            continue
        mesh = obj.data
        material = (
            mesh.materials[0].name if mesh.materials and mesh.materials[0] else ""
        )
        if not _weathers(material):
            continue
        matrix = obj.matrix_world
        normal_matrix = matrix.to_3x3().inverted_safe().transposed()
        to_local = matrix.inverted_safe()
        big = [
            p
            for p in mesh.polygons
            if p.area * abs(matrix.determinant()) ** (2 / 3) >= min_area
            and abs((normal_matrix @ p.normal).normalized().z) < 0.2
        ]
        if not big:
            continue
        done.add(mesh.name)
        others = _world_bvh(exclude=obj)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        # Boxes packed into one mesh (`add_boxes`) touch and overlap; slicing
        # those opens them. Only a closed mesh is sliced, and only kept closed.
        if not all(e.is_manifold for e in bm.edges):
            bm.free()
            continue
        corners = [matrix @ v.co for v in bm.verts]
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        for axis in range(3):
            step = lo[axis] + cell
            while step < hi[axis] - cell * 0.25:
                co = [0.0, 0.0, 0.0]
                no = [0.0, 0.0, 0.0]
                co[axis], no[axis] = step, 1.0
                world_co = Vector(co)
                local_co = to_local @ world_co
                local_no = (matrix.to_3x3().transposed() @ Vector(no)).normalized()
                geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
                bmesh.ops.bisect_plane(
                    bm, geom=geom, dist=1e-5, plane_co=local_co, plane_no=local_no
                )
                step += cell
        bmesh.ops.dissolve_degenerate(bm, dist=1e-5 * factor, edges=bm.edges[:])
        if not all(e.is_manifold for e in bm.edges):
            bm.free()
            continue
        bm.normal_update()
        moved = 0
        for v in bm.verts:
            faces = v.link_faces
            if not faces:
                continue
            n0 = faces[0].normal
            if any(f.normal.dot(n0) < 0.9999 for f in faces) or len(faces) < 3:
                continue
            n = (normal_matrix @ n0).normalized()
            if abs(n.z) >= 0.2:
                continue
            p = matrix @ v.co
            # The reach is a cell and a half, not just the clearance: a triangle
            # interpolates between its corners, so one pinned corner and one
            # moved corner a cell away still tilt through whatever is beside the
            # pinned one. Clear of everything by that much, a moved triangle is
            # clear along its whole extent.
            if others.find_nearest(p, clearance + cell * 1.5)[0] is not None:
                continue
            q = p / (cell * 2.2)
            d = noise.noise(q) * 0.7 + noise.noise(q * 2.3) * 0.3
            v.co = to_local @ (p + n * (d * amp))
            for f in faces:
                f.smooth = True
            moved += 1
        bm.to_mesh(mesh)
        bm.free()
        if moved:
            stats["objects"] += 1
            stats["moved"] += moved
    print(f"weather_walls: {stats['objects']} objects, {stats['moved']} vertices moved")
    return stats


def soften_edges(factor: float = 1.0, skip: set[str] | None = None) -> dict:
    """Round every hard edge on every map, so the world stops reading as boxes.

    The generators build almost everything from `create_cube`, and a raw cube's
    90° edges are the single thing that makes a map look like a Cube level: in
    real places, plaster, stone and pressed steel all catch light along a
    rounded edge. Two segments put each step at 22.5°, under `SHARP_ANGLE`, so
    the rounding shades smooth instead of as a second hard chamfer.

    Seams are left sharp (`_is_seam`), judged against the map as it stood before
    any rounding, so the order objects are visited in cannot change the answer.

    `factor` is the scene's units per metre (run after `scale_scene`). `skip`
    names objects that already carry their own bevel, so a crate's authored
    chamfer is not rounded a second time.
    """
    skip = skip or set()
    with open(_SURFACES_JSON, encoding="utf-8") as f:
        rules = json.load(f)["rules"]
    bvh = _world_bvh()
    probe = SEAM_PROBE * factor
    stats = {
        "objects": 0,
        "edges": 0,
        "seams": 0,
        "reverted": 0,
        "faces_before": 0,
        "faces_after": 0,
    }
    done: set[str] = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.name in skip or obj.data.name in done:
            continue
        if "ColOnly" in obj.name or "Invisible" in obj.name:
            continue
        mesh = obj.data
        done.add(mesh.name)
        material = (
            mesh.materials[0].name if mesh.materials and mesh.materials[0] else ""
        )
        kind = _surface_kind(material, rules)
        radius, fraction = SOFTEN_BY_KIND.get(kind, SOFTEN_DEFAULT)
        if kind in ("none", "glass", "site_a", "site_b"):
            continue
        dims = sorted(abs(d) for d in obj.dimensions)
        thinnest = dims[0] / factor if dims else 0.0
        if thinnest < SOFTEN_MIN_SIDE:
            continue
        width = min(radius, thinnest * fraction) * factor
        matrix = obj.matrix_world
        normal_matrix = matrix.to_3x3().inverted_safe().transposed()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        # An open mesh has no inside for a bevel to keep; leave it as authored.
        if not all(e.is_manifold for e in bm.edges):
            bm.free()
            continue
        edges = []
        for e in bm.edges:
            if e.calc_face_angle(0.0) <= SOFTEN_ANGLE:
                continue
            if _is_seam(bvh, e, matrix, normal_matrix, probe):
                stats["seams"] += 1
                continue
            edges.append(e)
        if edges:
            before = len(bm.faces)
            rounded = bmesh.ops.bevel(
                bm,
                geom=edges,
                offset=width,
                offset_type="OFFSET",
                segments=2,
                profile=0.5,
                affect="EDGES",
                clamp_overlap=True,
                loop_slide=True,
            )
            # The strip shades round; where it meets the face it was cut from is
            # a hard edge. Otherwise the face shares the strip's vertices, and a
            # recomputed normal (both clients recompute) leans its corners
            # toward the rounding: a flat wall shaded as a slow gradient.
            strip = set(rounded["faces"])
            for f in strip:
                f.smooth = True
            for e in bm.edges:
                inside = sum(1 for f in e.link_faces if f in strip)
                if 0 < inside < len(e.link_faces):
                    e.smooth = False
            bmesh.ops.dissolve_degenerate(bm, dist=1e-6 * factor, edges=bm.edges[:])
            # A bevel that opened the mesh is thrown away: a hole is worse than a
            # sharp edge, and the object keeps exactly the shape it was built with.
            if all(e.is_manifold for e in bm.edges):
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
                stats["objects"] += 1
                stats["edges"] += len(edges)
                stats["faces_before"] += before
                stats["faces_after"] += len(bm.faces)
                bm.to_mesh(mesh)
            else:
                stats["reverted"] += 1
        bm.free()
    print(
        f"soften_edges: {stats['objects']} objects, {stats['edges']} edges rounded, "
        f"{stats['seams']} seams kept, {stats['reverted']} reverted, "
        f"{stats['faces_before']} -> {stats['faces_after']} faces"
    )
    return stats


def scale_scene(factor: float) -> None:
    """Scale the whole scene about the origin by `factor`, hierarchy intact.

    For a uniform `D = factor·I`, conjugating any object matrix `[A | t]` gives
    `[A | factor·t]` — D commutes with rotation and scale — so scaling every
    translation in the chain (basis and parent-inverse) and every mesh's
    vertices once yields exactly `D · world` for every object, without
    flattening the node tree the clients read names from.
    """
    if factor == 1.0:
        return
    _bake_modifiers()
    for mesh in bpy.data.meshes:
        if mesh.users:
            mesh.transform(Matrix.Scale(factor, 4))
    for obj in bpy.data.objects:
        basis = obj.matrix_basis.copy()
        basis.translation = basis.translation * factor
        obj.matrix_basis = basis
        inverse = obj.matrix_parent_inverse.copy()
        inverse.translation = inverse.translation * factor
        obj.matrix_parent_inverse = inverse
        if obj.type == "LIGHT":
            obj.data.energy *= factor * factor
    bpy.context.view_layer.update()


def add_wedge(collection, name, center, size, material, rise):
    """A ramp: a box whose top slopes from its bottom to its full height.

    `rise` is the side the high end faces (`+x`, `-x`, `+y`, `-y`). Several
    generators drew their ramps with `add_box`, which made every one a cliff —
    harmless while a body was a third of its drawn size and could hop onto it,
    impassable once the maps were scaled. Metres in, like everything here.
    """
    cx, cy, cz = center
    sx, sy, sz = size
    x0, x1, y0, y1 = cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2

    def top(x, y):
        t = {
            "+x": (x - x0) / sx,
            "-x": (x1 - x) / sx,
            "+y": (y - y0) / sy,
            "-y": (y1 - y) / sy,
        }[rise]
        return z0 + (z1 - z0) * t

    bm = bmesh.new()
    b = [bm.verts.new((x, y, z0)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    t = [
        bm.verts.new((x, y, top(x, y)))
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    ]
    bm.verts.ensure_lookup_table()
    bm.faces.new((t[0], t[1], t[2], t[3]))
    bm.faces.new((b[3], b[2], b[1], b[0]))
    for i in range(4):
        j = (i + 1) % 4
        # Where the slope meets the floor a side collapses to a triangle.
        ring = [b[i], b[j], t[j], t[i]]
        unique = []
        for v in ring:
            if all((v.co - u.co).length > 1e-6 for u in unique):
                unique.append(v)
        if len(unique) >= 3:
            bm.faces.new(unique)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def _box(collection, name, lo, hi, material):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    size = [h - a for a, h in zip(lo, hi)]
    center = [(a + h) / 2 for a, h in zip(lo, hi)]
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def add_boxes(collection, name, boxes, material):
    """Many axis-aligned boxes as **one** object: `boxes` is `(center, size)` pairs.

    For dressing — trim, beams, slats, sandbags — where a node per piece would
    grow the GLB by its node and accessor overhead rather than its geometry, and
    every client would walk a few hundred more nodes at load. Give it a `NonCol`
    name unless the pieces are meant to be stood on.
    """
    bm = bmesh.new()
    for center, size in boxes:
        piece = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        bmesh.ops.scale(bm, vec=Vector(size), verts=piece)
        bmesh.ops.translate(bm, vec=Vector(center), verts=piece)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if material:
        obj.data.materials.append(material)
    return obj


def add_plinths(collection, name, walls, material, height=0.5, proud=0.08, base=0.0):
    """A proud course along both long faces of each wall: `walls` is `(center, size)`.

    The cheapest thing that stops a long wall reading as a flat plane. `base` is
    the floor height the course sits on.
    """
    boxes = []
    for (cx, cy, _), (sx, sy, _) in walls:
        if sx >= sy:
            for side in (-1, 1):
                boxes.append(
                    (
                        (cx, cy + side * (sy / 2 + proud / 2), base + height / 2),
                        (sx, proud, height),
                    )
                )
        else:
            for side in (-1, 1):
                boxes.append(
                    (
                        (cx + side * (sx / 2 + proud / 2), cy, base + height / 2),
                        (proud, sy, height),
                    )
                )
    return add_boxes(collection, name, boxes, material)


def add_stairs(collection, name, start, width, rise, run, steps, axis, material):
    """A flight of solid steps: `start` is the bottom front centre, in metres.

    `axis` is the direction of climb (`+x`, `-x`, `+y`, `-y`). Each step is a box
    from the floor up, so the underside is solid and the collision bake sees a
    staircase rather than a floating ramp. Riser `rise / steps` stays under the
    body's step height as long as a step is below ~0.45 m.
    """
    sx, sy, sz = start
    dx, dy = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}[axis]
    tread = run / steps
    boxes = []
    for i in range(steps):
        h = rise * (i + 1) / steps
        along = tread * (i + 0.5)
        cx, cy = sx + dx * along, sy + dy * along
        size = (tread, width, h) if dx else (width, tread, h)
        boxes.append(((cx, cy, sz + h / 2), size))
    return add_boxes(collection, name, boxes, material)


def add_wall_with_door(
    collection, name, center, size, material, door_at, door_width, door_height
):
    """A wall along its longer axis with a doorway cut through it.

    `door_at` is the doorway's centre along that axis. Three pieces: the two
    wall sections either side, and a lintel over the opening, so the doorway is
    a real hole in the collision mesh and not a painted one.
    """
    cx, cy, cz = center
    sx, sy, sz = size
    lo = [cx - sx / 2, cy - sy / 2, cz - sz / 2]
    hi = [cx + sx / 2, cy + sy / 2, cz + sz / 2]
    axis = 0 if sx >= sy else 1
    a, b = door_at - door_width / 2, door_at + door_width / 2
    left_hi = list(hi)
    left_hi[axis] = a
    right_lo = list(lo)
    right_lo[axis] = b
    lintel_lo = list(lo)
    lintel_hi = list(hi)
    lintel_lo[axis], lintel_hi[axis] = a, b
    lintel_lo[2] = lo[2] + door_height
    _box(collection, f"{name}_A", lo, left_hi, material)
    _box(collection, f"{name}_B", right_lo, hi, material)
    if lintel_lo[2] < hi[2]:
        _box(collection, f"{name}_Lintel", lintel_lo, lintel_hi, material)


def add_safety_floor(collection, name="Safety_Floor_ColOnly", thickness=0.5):
    """An invisible floor under the whole map, at its lowest surface.

    Some generators lay a floor slab per room and stop it at the walls, so the
    strip under a doorway has no floor at all: offline a body walking through
    the doorway fell out of the world, and the collision bake read the doorway
    as solid. `ColOnly` makes both clients collide with it and neither draw it.
    """
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = [min(a, b) for a, b in zip(lo, w)]
            hi = [max(a, b) for a, b in zip(hi, w)]
    if not math.isfinite(lo[0]):
        return None
    return _box(
        collection, name, (lo[0], lo[1], lo[2] - thickness), (hi[0], hi[1], lo[2]), None
    )


#: Name prefix of the ring `add_boundary_walls` adds. Unique on purpose: the
#: bake leaves exactly these out, and Junk Flea's own perimeter walls are called
#: `Boundary_Wall_*`. Excluding by a looser prefix dropped them from the
#: server's grid and opened the map's edge.
BOUNDS_PREFIX = "Map_Bounds_"


def add_boundary_walls(collection, thickness=0.5, overhead=3.0):
    """Invisible walls around the whole map's footprint, the safety floor's sides.

    A map is only closed where its generator remembered to close it. Assault's
    south street ran to the edge of the model at both ends with nothing there,
    and so did its north-east yard, so offline (where Rapier walks the GLB
    itself) a body walked off the map onto the safety floor. Hosted matches
    never showed it, since the server's baked grid ends in solid rock, and
    `test_glb_colliders.rs` only caught it once a rounded corner stopped one
    of its rays clipping a wall's exact tip.

    Run after `add_safety_floor`, so the ring encloses the floor too. The walls
    rise `overhead` metres above the highest surface, so they cannot be jumped
    from a roof.
    """
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = [min(a, b) for a, b in zip(lo, w)]
            hi = [max(a, b) for a, b in zip(hi, w)]
    if not math.isfinite(lo[0]):
        return []
    t = thickness
    z0, z1 = lo[2], hi[2] + overhead
    sides = {
        "West": ((lo[0] - t, lo[1] - t, z0), (lo[0], hi[1] + t, z1)),
        "East": ((hi[0], lo[1] - t, z0), (hi[0] + t, hi[1] + t, z1)),
        "South": ((lo[0], lo[1] - t, z0), (hi[0], lo[1], z1)),
        "North": ((lo[0], hi[1], z0), (hi[0], hi[1] + t, z1)),
    }
    return [
        _box(collection, f"{BOUNDS_PREFIX}{side}_ColOnly", a, b, None)
        for side, (a, b) in sides.items()
    ]


# ---- lights and atmosphere ----------------------------------------------------------

#: Point lights recorded by `add_light`, in metres, until `export_map_glb`.
_LIGHTS: list[dict] = []
#: The map's atmosphere block, as `set_atmosphere` left it.
_ATMOSPHERE: dict = {}

#: Named skies. Keys are `atmosphere.py`'s; a map may override any of them. Sun
#: and fill directions are three-space — x east, y **up**, z the cube map's y —
#: and point toward the light.
ATMOSPHERES: dict[str, dict] = {
    # Hard noon over pale stone: a high warm sun, a sandy bounce from below and
    # a haze at the horizon rather than a blue wall.
    "desert_noon": {
        "skyZenith": 0x2E6CC7,
        "skyHorizon": 0xD6DCE0,
        "hemiSky": 0xB4CCEB,
        "hemiGround": 0xB58C5C,
        "hemiIntensity": 1.25,
        "sunColor": 0xFFE7C6,
        "sunIntensity": 3.1,
        "sunDir": [0.42, 0.8, 0.43],
        "fillColor": 0xFFD6A3,
        "fillIntensity": 0.35,
        "fillDir": [-0.45, 0.25, -0.6],
        "fogColor": 0xDAD4C6,
        "fogDensity": 0.0011,
        "exposure": 1.0,
        "sunDisc": True,
    },
    # Late morning on the coast: a lower, whiter sun and a bluer, clearer sky.
    "coastal_morning": {
        "skyZenith": 0x3572C4,
        "skyHorizon": 0xC7DAEA,
        "hemiSky": 0xB9D2F2,
        "hemiGround": 0xA88A66,
        "hemiIntensity": 1.3,
        "sunColor": 0xFFF0DA,
        "sunIntensity": 2.8,
        "sunDir": [-0.55, 0.62, 0.55],
        "fillColor": 0xCFE0FF,
        "fillIntensity": 0.35,
        "fillDir": [0.5, 0.3, -0.6],
        "fogColor": 0xCBD8E2,
        "fogDensity": 0.0012,
        "exposure": 1.0,
        "sunDisc": True,
    },
    # A low amber sun raking across terracotta, long shadows, warm dust.
    "golden_hour": {
        "skyZenith": 0x3B5E9C,
        "skyHorizon": 0xF0C89A,
        "hemiSky": 0xA9B8D8,
        "hemiGround": 0x8C5E3C,
        "hemiIntensity": 1.15,
        "sunColor": 0xFFC98F,
        "sunIntensity": 3.0,
        "sunDir": [0.72, 0.42, -0.55],
        "fillColor": 0x8FA6D8,
        "fillIntensity": 0.4,
        "fillDir": [-0.6, 0.35, 0.5],
        "fogColor": 0xE3C4A0,
        "fogDensity": 0.0016,
        "exposure": 1.05,
        "sunDisc": True,
    },
    # Flat grey industrial daylight: a soft sun through cloud, a big sky term,
    # and a cold haze that stops the far fence reading sharp.
    "overcast": {
        "skyZenith": 0x75808E,
        "skyHorizon": 0xB8BEC4,
        "hemiSky": 0xC6CFD9,
        "hemiGround": 0x5E5A54,
        "hemiIntensity": 2.0,
        "sunColor": 0xE8ECF0,
        "sunIntensity": 1.3,
        "sunDir": [0.3, 0.85, 0.42],
        "fillColor": 0xB8C4D2,
        "fillIntensity": 0.4,
        "fillDir": [-0.5, 0.4, -0.6],
        "fogColor": 0xAEB4BA,
        "fogDensity": 0.0022,
        "exposure": 1.05,
        "sunDisc": False,
    },
    # Indoors under strip lights: no sun to speak of, a cool even ambient, and
    # the map's own lamps doing the modelling.
    "interior_cool": {
        "skyZenith": 0x1A2230,
        "skyHorizon": 0x3A4656,
        "hemiSky": 0xD8E4F4,
        "hemiGround": 0x4A4640,
        "hemiIntensity": 1.7,
        "sunColor": 0xF2F6FF,
        "sunIntensity": 0.9,
        "sunDir": [0.25, 0.9, 0.35],
        "fillColor": 0xB8C8E0,
        "fillIntensity": 0.35,
        "fillDir": [-0.5, 0.4, -0.6],
        "fogColor": 0x2A323E,
        "fogDensity": 0.0018,
        "exposure": 1.1,
        "sunDisc": False,
    },
    # Marble and brass under warm chandeliers.
    "interior_warm": {
        "skyZenith": 0x20242C,
        "skyHorizon": 0x4A4A4E,
        "hemiSky": 0xFFE8CC,
        "hemiGround": 0x4E4034,
        "hemiIntensity": 1.6,
        "sunColor": 0xFFF0DC,
        "sunIntensity": 1.1,
        "sunDir": [0.3, 0.86, 0.4],
        "fillColor": 0xC8D4F0,
        "fillIntensity": 0.3,
        "fillDir": [-0.5, 0.4, -0.6],
        "fogColor": 0x2E2A28,
        "fogDensity": 0.0016,
        "exposure": 1.1,
        "sunDisc": False,
    },
    # Dusk over a scrapyard: sodium lamps against a violet sky.
    "industrial_dusk": {
        "skyZenith": 0x28304E,
        "skyHorizon": 0xB88A78,
        "hemiSky": 0x8E9CC4,
        "hemiGround": 0x5A4A3C,
        "hemiIntensity": 1.4,
        "sunColor": 0xFFB482,
        "sunIntensity": 1.9,
        "sunDir": [-0.7, 0.35, 0.6],
        "fillColor": 0x9AA8D8,
        "fillIntensity": 0.45,
        "fillDir": [0.5, 0.4, -0.6],
        "fogColor": 0x8A7E86,
        "fogDensity": 0.002,
        "exposure": 1.1,
        "sunDisc": True,
    },
}


def set_atmosphere(preset: str, **overrides) -> dict:
    """Choose the map's sky and light rig: a named preset, then any overrides."""
    global _ATMOSPHERE
    _ATMOSPHERE = {**ATMOSPHERES[preset], **overrides}
    return _ATMOSPHERE


def add_light(pos, color=(255, 214, 160), radius=8.0, intensity=1.0) -> dict:
    """Record a point light at `pos`, in metres, reaching `radius` metres.

    Only the light: the fixture it hangs from is the generator's own mesh (a
    lantern, a strip light), which has an emissive face and a `NonCol` name.
    `color` is 0-255 sRGB; `intensity` a multiplier on the radius's default
    brightness (see `atmosphere.lights`).
    """
    light = {
        "pos": tuple(float(v) for v in pos),
        "color": tuple(int(c) for c in color),
        "radius": float(radius),
        "intensity": float(intensity),
    }
    _LIGHTS.append(light)
    return light


def make_material(
    name, color, emission=None, strength=0.0, metallic=0.0, roughness=0.6
):
    """A plain Principled material, reused by name."""
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
        bsdf.inputs[key].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    return mat


def add_lantern(
    collection, name, pos, hang=0.6, color=(255, 186, 112), radius=7.0, intensity=2.0
):
    """A hanging brass lantern whose flame is a light: `pos` is where it hangs from.

    One merged fixture mesh plus one flame mesh, both `NonCol`, and a recorded
    light at the flame. `hang` is the chain length in metres.
    """
    brass = make_material(
        "mat_lantern_brass", (0.75, 0.6, 0.22), metallic=0.88, roughness=0.25
    )
    flame = make_material(
        "mat_lantern_flame", (1.0, 0.7, 0.3), emission=(1.0, 0.7, 0.3), strength=5.0
    )
    x, y, z = pos
    body = z - hang - 0.25
    add_boxes(
        collection,
        f"{name}_Fixture_NonCol",
        [
            ((x, y, z - hang / 2), (0.04, 0.04, hang)),
            ((x, y, body + 0.25), (0.36, 0.36, 0.06)),
            ((x, y, body - 0.25), (0.3, 0.3, 0.06)),
            ((x - 0.16, y, body), (0.03, 0.03, 0.5)),
            ((x + 0.16, y, body), (0.03, 0.03, 0.5)),
            ((x, y - 0.16, body), (0.03, 0.03, 0.5)),
            ((x, y + 0.16, body), (0.03, 0.03, 0.5)),
        ],
        brass,
    )
    add_boxes(
        collection, f"{name}_Flame_NonCol", [((x, y, body), (0.16, 0.16, 0.3))], flame
    )
    return add_light((x, y, body), color=color, radius=radius, intensity=intensity)


def lights_from_emitters(
    match,
    color=(255, 240, 220),
    radius=8.0,
    intensity=1.5,
    spacing=4.0,
    drop=0.35,
    limit=None,
) -> int:
    """A light at every fixture already modelled: objects whose material name
    contains any of `match`.

    The maps model their lamps (troffers, chandeliers, floodlights) as emissive
    meshes, which glow and light nothing. This puts a point light just under
    each one — `drop` metres below its centre, so it is in the room rather than
    in the ceiling — and merges fixtures closer than `spacing`, since a row of
    troffer tubes is one light source to the eye and 64 is the native budget
    for the whole view. Returns how many lights were added.
    """
    needles = [m.lower() for m in ([match] if isinstance(match, str) else match)]
    centers = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        names = [
            (s.material.name if s.material else "").lower() for s in obj.material_slots
        ]
        if not any(n in name for n in needles for name in names):
            continue
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        center = sum(corners, Vector()) / 8.0
        center.z = min(c.z for c in corners) - drop
        centers.append(center)
    placed = []
    for c in centers:
        if all((c - p).length >= spacing for p in placed):
            placed.append(c)
    if limit is not None:
        placed = placed[:limit]
    for c in placed:
        add_light(tuple(c), color=color, radius=radius, intensity=intensity)
    return len(placed)


def srgb_to_linear(c: float) -> float:
    """One sRGB channel (0..1) as linear reflectance."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


#: The darkest a channel may convert to, as a fraction of its authored value.
#: The exact curve takes a 0.2 asphalt to 0.033, which under these rigs — a sky,
#: a sun and no bounce light — draws as black. Only darks are lifted: anything
#: authored above ~0.45 converts to more than this already.
DARK_FLOOR = 0.3


def albedo(c: float) -> float:
    """An authored (sRGB) channel as the linear reflectance the map exports."""
    return max(srgb_to_linear(c), DARK_FLOOR * c)


def _linearize_base_colors() -> None:
    """Convert every Principled base colour from the sRGB it was picked in."""
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs["Base Color"]
            r, g, b, a = socket.default_value
            socket.default_value = (
                albedo(r),
                albedo(g),
                albedo(b),
                a,
            )


def dump_json(v, ind: int = 0) -> str:
    """indent=2 JSON, but a row of scalars stays on one line (the files' style)."""
    flat = json.dumps(v, separators=(", ", ": "))
    if not isinstance(v, (dict, list)):
        return flat
    kids = v.values() if isinstance(v, dict) else v
    if len(flat) <= 90 and all(not isinstance(x, (dict, list)) for x in kids):
        return flat
    pad, nl = "  " * (ind + 1), "\n"
    if isinstance(v, dict):
        body = ("," + nl).join(
            f"{pad}{json.dumps(k)}: {dump_json(x, ind + 1)}" for k, x in v.items()
        )
        return "{" + nl + body + nl + "  " * ind + "}"
    body = ("," + nl).join(f"{pad}{dump_json(x, ind + 1)}" for x in v)
    return "[" + nl + body + nl + "  " * ind + "]"


def _write_map_json(path: str, factor: float) -> None:
    """Put the recorded lights and atmosphere into the map's JSON, in cubes.

    Light entities are replaced wholesale and nothing else moves; the
    atmosphere block sits after `ambient`, where a reader looks for how the map
    is lit.
    """
    if not os.path.isfile(path) or (not _LIGHTS and not _ATMOSPHERE):
        return
    with open(path, encoding="utf-8") as f:
        source = json.load(f)
    entities = [e for e in source.get("entities", []) if e.get("type") != "light"]
    for light in _LIGHTS:
        x, y, z = (v * factor for v in light["pos"])
        entities.append(
            {
                "type": "light",
                "x": int(round(x)),
                "y": int(round(y)),
                "z": int(round(z)),
                "radius": max(1, min(255, int(round(light["radius"] * factor)))),
                "color": list(light["color"]),
                "intensity": round(light["intensity"], 2),
            }
        )
    source["entities"] = entities
    if _ATMOSPHERE:
        rebuilt = {}
        for key, value in source.items():
            if key == "atmosphere":
                continue
            rebuilt[key] = value
            if key == "ambient":
                rebuilt["atmosphere"] = _ATMOSPHERE
        rebuilt.setdefault("atmosphere", _ATMOSPHERE)
        source = rebuilt
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_json(source) + "\n")
    print(f"Wrote {len(_LIGHTS)} lights and atmosphere to: {path}")


def export_map_glb(name: str) -> str:
    """Scale the built scene to cubes and write `<name>.glb` to both homes.

    Also writes the recorded lights and atmosphere into `<name>.json` when the
    output is the repo's own maps directory.
    """
    factor = float(os.environ.get("HASSAULT_MAP_SCALE", MAP_SCALE))
    add_safety_floor(bpy.context.scene.collection)
    add_boundary_walls(bpy.context.scene.collection)
    _linearize_base_colors()
    # Objects that bring their own bevel, noted before `scale_scene` bakes the
    # modifier away, so `soften_edges` does not round them twice.
    authored = {
        o.name
        for o in bpy.data.objects
        if any(m.type == "BEVEL" for m in getattr(o, "modifiers", []))
    }
    scale_scene(factor)
    clean_meshes()
    # Shape passes, both on by default. `HASSAULT_MAP_SOFTEN=0` exports the map
    # as authored, for comparing against what the passes did.
    if os.environ.get("HASSAULT_MAP_SOFTEN", "1") != "0":
        weather_walls(factor)
        soften_edges(factor, authored)
        # Again, over what the shape passes made: their slivers and any face a
        # bevel corner turned around.
        clean_meshes()
    separate_coplanar(factor)
    mark_sharp_edges()

    override = os.environ.get("HASSAULT_MAP_OUT")
    backend_dir = override or os.path.join(
        REPO_ROOT, "backend", "modules", "hassault", "maps"
    )
    web_dir = None if override else os.path.join(REPO_ROOT, "apps", "web", "public")
    os.makedirs(backend_dir, exist_ok=True)

    backend_path = os.path.join(backend_dir, f"{name}.glb")
    print(f"Exporting {name} at {factor}x to: {backend_path}")
    bpy.ops.export_scene.gltf(
        filepath=backend_path,
        export_format="GLB",
        use_selection=False,
        export_apply=True,
        export_yup=True,
        export_materials="EXPORT",
        export_lights=False,
        export_cameras=False,
    )
    if web_dir:
        os.makedirs(web_dir, exist_ok=True)
        shutil.copyfile(backend_path, os.path.join(web_dir, f"{name}.glb"))
        _write_map_json(os.path.join(backend_dir, f"{name}.json"), factor)
    return backend_path
