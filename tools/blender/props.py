"""Natural prop placement for the modelled maps.

A hand-typed coordinate list puts a prop where the author happened to be looking;
filling a quota puts one wherever there is room. Neither says *why* a crate is
there. This places props the way a place gets cluttered: things are pushed against
walls, gather in corners, stand where someone was working, and the open lanes are
left open except for the few pieces of cover a lane is meant to have.

It works on a plan of the finished map (the footprints of everything already
built), in the map's final metres, and builds oriented, merged meshes:

- `Plan` is a grid of what is solid, what is forbidden, and how far every cell is
  from a wall.
- A *vignette* is a small arrangement (a row of crates, a barrel cluster, jars in a
  line) laid out in a frame aligned to the wall it stands against.
- `dress` walks the cells near walls in a seeded random order, picks a vignette the
  zone and the geometry suggest, and keeps it only if every part fits.

Deterministic for a given seed, so a regenerated GLB is byte-identical. Bpy is
imported lazily so the planning half can be tested anywhere.
"""

import math

CELL = 0.25  # metres per grid cell


# --------------------------------------------------------------------------- mesh


class Mesher:
    """Oriented boxes and frusta merged into one mesh, so a hundred crates cost
    one node, not a hundred."""

    def __init__(self):
        import bmesh

        self._bmesh = bmesh
        self.bm = bmesh.new()
        self.parts = 0

    def box(self, center, size, yaw=0.0, tilt=0.0, roll=0.0):
        from mathutils import Matrix, Vector

        ops = self._bmesh.ops
        verts = ops.create_cube(self.bm, size=1.0)["verts"]
        ops.scale(self.bm, vec=Vector(size), verts=verts)
        rot = (
            Matrix.Rotation(yaw, 3, "Z")
            @ Matrix.Rotation(tilt, 3, "X")
            @ Matrix.Rotation(roll, 3, "Y")
        )
        ops.rotate(self.bm, cent=(0.0, 0.0, 0.0), matrix=rot, verts=verts)
        ops.translate(self.bm, vec=Vector(center), verts=verts)
        self.parts += 1

    def frustum(self, center, r_bottom, r_top, height, segments=12, tilt=0.0, yaw=0.0):
        from mathutils import Matrix, Vector

        ops = self._bmesh.ops
        verts = ops.create_cone(
            self.bm,
            cap_ends=True,
            cap_tris=False,
            segments=segments,
            radius1=r_bottom,
            radius2=r_top,
            depth=height,
        )["verts"]
        if tilt or yaw:
            rot = Matrix.Rotation(yaw, 3, "Z") @ Matrix.Rotation(tilt, 3, "X")
            ops.rotate(self.bm, cent=(0.0, 0.0, 0.0), matrix=rot, verts=verts)
        ops.translate(self.bm, vec=Vector(center), verts=verts)
        self.parts += 1

    def commit(self, collection, name, material):
        import bpy

        mesh = bpy.data.meshes.new(name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        if material:
            obj.data.materials.append(material)
        return obj


# ---------------------------------------------------------------------------- plan


class Plan:
    """The map as a grid: solid, forbidden, placed, and distance-to-solid."""

    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.w = int(math.ceil((x1 - x0) / CELL))
        self.h = int(math.ceil((y1 - y0) / CELL))
        n = self.w * self.h
        self.solid = bytearray(n)
        self.forbid = bytearray(n)
        self.placed = bytearray(n)
        self.dist = None

    def _i(self, ix, iy):
        return iy * self.w + ix

    def cell(self, x, y):
        return int(math.floor((x - self.x0) / CELL)), int(
            math.floor((y - self.y0) / CELL)
        )

    def centre(self, ix, iy):
        return self.x0 + (ix + 0.5) * CELL, self.y0 + (iy + 0.5) * CELL

    def inside(self, ix, iy):
        return 0 <= ix < self.w and 0 <= iy < self.h

    def _rect_cells(self, xlo, xhi, ylo, yhi):
        ix0, iy0 = self.cell(xlo, ylo)
        ix1, iy1 = self.cell(xhi, yhi)
        for iy in range(max(iy0, 0), min(iy1, self.h - 1) + 1):
            for ix in range(max(ix0, 0), min(ix1, self.w - 1) + 1):
                yield ix, iy

    def mark_rect(self, layer, xlo, xhi, ylo, yhi, pad=0.0):
        """Cells whose centre is inside the rectangle, so a wall is not fattened by
        the half cell a partial overlap would add."""
        xlo, xhi, ylo, yhi = xlo - pad, xhi + pad, ylo - pad, yhi + pad
        for ix, iy in self._rect_cells(xlo, xhi, ylo, yhi):
            cx, cy = self.centre(ix, iy)
            if xlo <= cx <= xhi and ylo <= cy <= yhi:
                layer[self._i(ix, iy)] = 1

    def mark_disc(self, layer, x, y, r):
        for ix, iy in self._rect_cells(x - r, x + r, y - r, y + r):
            cx, cy = self.centre(ix, iy)
            if (cx - x) ** 2 + (cy - y) ** 2 <= r * r:
                layer[self._i(ix, iy)] = 1

    def forbid_outside(self, xlo, xhi, ylo, yhi):
        for iy in range(self.h):
            for ix in range(self.w):
                cx, cy = self.centre(ix, iy)
                if not (xlo <= cx <= xhi and ylo <= cy <= yhi):
                    self.forbid[self._i(ix, iy)] = 1

    def solve(self):
        """Distance in metres from each cell to the nearest solid one (chamfer)."""
        big = 1e9
        w, h = self.w, self.h
        d = [0.0 if self.solid[k] else big for k in range(w * h)]
        diag = CELL * math.sqrt(2.0)
        for iy in range(h):
            for ix in range(w):
                k = iy * w + ix
                best = d[k]
                if ix > 0:
                    best = min(best, d[k - 1] + CELL)
                if iy > 0:
                    best = min(best, d[k - w] + CELL)
                    if ix > 0:
                        best = min(best, d[k - w - 1] + diag)
                    if ix < w - 1:
                        best = min(best, d[k - w + 1] + diag)
                d[k] = best
        for iy in range(h - 1, -1, -1):
            for ix in range(w - 1, -1, -1):
                k = iy * w + ix
                best = d[k]
                if ix < w - 1:
                    best = min(best, d[k + 1] + CELL)
                if iy < h - 1:
                    best = min(best, d[k + w] + CELL)
                    if ix < w - 1:
                        best = min(best, d[k + w + 1] + diag)
                    if ix > 0:
                        best = min(best, d[k + w - 1] + diag)
                d[k] = best
        self.dist = d

    def d_at(self, x, y):
        ix, iy = self.cell(x, y)
        if not self.inside(ix, iy):
            return 0.0
        return self.dist[self._i(ix, iy)]

    def rect_free(self, xlo, xhi, ylo, yhi):
        """Every cell of the rectangle is open floor: no wall, ban or earlier prop."""
        for ix, iy in self._rect_cells(xlo, xhi, ylo, yhi):
            k = self._i(ix, iy)
            if self.solid[k] or self.forbid[k] or self.placed[k]:
                return False
        return xlo >= self.x0 and ylo >= self.y0 and xhi <= self.x1 and yhi <= self.y1

    def free_fraction(self, x, y, r):
        """How much of the disc round (x, y) is open floor: a narrow passage scores low."""
        free = total = 0
        for ix, iy in self._rect_cells(x - r, x + r, y - r, y + r):
            cx, cy = self.centre(ix, iy)
            if (cx - x) ** 2 + (cy - y) ** 2 > r * r:
                continue
            total += 1
            k = self._i(ix, iy)
            if not (self.solid[k] or self.placed[k]):
                free += 1
        return free / total if total else 0.0

    def normal_at(self, x, y):
        """Unit vector pointing away from the nearest wall (the distance gradient)."""
        e = 2 * CELL
        gx = self.d_at(x + e, y) - self.d_at(x - e, y)
        gy = self.d_at(x, y + e) - self.d_at(x, y - e)
        n = math.hypot(gx, gy)
        return (gx / n, gy / n) if n > 1e-6 else None


# ------------------------------------------------------------------------ vignettes


class Piece:
    """One part of a vignette, in world metres: what it is, where, and its footprint."""

    __slots__ = ("kind", "x", "y", "z", "size", "yaw", "radius")

    def __init__(self, kind, x, y, z, size, yaw, radius):
        self.kind, self.x, self.y, self.z = kind, x, y, z
        self.size, self.yaw, self.radius = size, yaw, radius


def _frame(normal):
    """A wall-aligned frame: `t` along the wall, `n` out from it."""
    nx, ny = normal
    return (-ny, nx), (nx, ny)


def _at(anchor, t, n, u, v):
    """The point `u` along the wall and `v` out from it, from the anchor on its face."""
    return anchor[0] + t[0] * u + n[0] * v, anchor[1] + t[1] * u + n[1] * v


def _crate(rng, anchor, t, n, u, v, yaw0, size, z=0.0, jitter=0.12):
    """A crate whose centre is `v` out from the wall and `u` along it."""
    x, y = _at(anchor, t, n, u, v)
    return Piece(
        "crate",
        x,
        y,
        z,
        (size, size, size * 0.9),
        yaw0 + rng.uniform(-jitter, jitter),
        size * 0.5,
    )


def crates_row(rng, anchor, normal):
    t, n = _frame(normal)
    yaw0 = math.atan2(t[1], t[0])
    pieces, u = [], 0.0
    for _ in range(rng.choice((1, 2, 2, 3))):
        s = rng.choice((0.8, 0.95, 1.1, 1.25))
        v = s / 2 + rng.uniform(0.04, 0.14)
        # `u` is this crate's near edge, so a big crate after a small one still clears it.
        centre = u + s / 2
        pieces.append(_crate(rng, anchor, t, n, centre, v, yaw0, s))
        if rng.random() < 0.4:
            small = s * rng.uniform(0.7, 0.85)
            pieces.append(
                _crate(
                    rng,
                    anchor,
                    t,
                    n,
                    centre + rng.uniform(-0.1, 0.1),
                    v,
                    yaw0,
                    small,
                    z=s * 0.9,
                    jitter=0.35,
                )
            )
        u += s + rng.uniform(0.04, 0.35)
    return pieces


def barrel_cluster(rng, anchor, normal):
    t, n = _frame(normal)
    pieces = []
    count = rng.choice((2, 3, 3, 4))
    for i in range(count):
        r = rng.uniform(0.38, 0.45)
        u = (i - (count - 1) / 2) * rng.uniform(0.8, 0.95) + rng.uniform(-0.1, 0.1)
        v = r + rng.uniform(0.04, 0.2) + (0.55 if i % 2 else 0.0)
        x, y = _at(anchor, t, n, u, v)
        if i == count - 1 and count > 2 and rng.random() < 0.3:
            pieces.append(
                Piece(
                    "barrel_down",
                    x,
                    y,
                    0.0,
                    (r, r, 0.95),
                    rng.uniform(0, math.tau),
                    0.4,
                )
            )
        else:
            pieces.append(
                Piece("barrel", x, y, 0.0, (r, r, rng.uniform(0.92, 1.05)), 0.0, r)
            )
    return pieces


def sack_pile(rng, anchor, normal):
    t, n = _frame(normal)
    yaw0 = math.atan2(t[1], t[0])
    pieces = []
    base = rng.choice((2, 3, 3))
    for i in range(base):
        u = (i - (base - 1) / 2) * 0.62 + rng.uniform(-0.05, 0.05)
        x, y = _at(anchor, t, n, u, 0.3 + rng.uniform(0.02, 0.1))
        pieces.append(
            Piece(
                "sack",
                x,
                y,
                0.0,
                (0.65, 0.42, 0.26),
                yaw0 + rng.uniform(-0.25, 0.25),
                0.26,
            )
        )
    for i in range(base - 1):
        if rng.random() < 0.7:
            u = (i - (base - 2) / 2) * 0.62 + rng.uniform(-0.05, 0.05)
            x, y = _at(anchor, t, n, u, 0.3 + rng.uniform(0.02, 0.08))
            pieces.append(
                Piece(
                    "sack",
                    x,
                    y,
                    0.26,
                    (0.62, 0.4, 0.24),
                    yaw0 + rng.uniform(-0.35, 0.35),
                    0.26,
                )
            )
    return pieces


def jar_row(rng, anchor, normal):
    t, n = _frame(normal)
    pieces = []
    count = rng.choice((2, 3, 3, 4))
    for i in range(count):
        r = rng.uniform(0.26, 0.34)
        u = (i - (count - 1) / 2) * rng.uniform(0.72, 0.9)
        x, y = _at(anchor, t, n, u, r + rng.uniform(0.05, 0.18))
        pieces.append(
            Piece(
                "jar",
                x,
                y,
                0.0,
                (r, r, rng.uniform(0.8, 1.0)),
                rng.uniform(0, math.tau),
                r,
            )
        )
    return pieces


def corner_stack(rng, anchor, normal):
    """Tucked into a corner: a tall stack, barrels at its foot."""
    t, n = _frame(normal)
    yaw0 = math.atan2(t[1], t[0])
    s = rng.choice((1.0, 1.15, 1.25))
    pieces = [_crate(rng, anchor, t, n, 0.0, s / 2 + 0.06, yaw0, s, jitter=0.05)]
    s2 = s * rng.uniform(0.72, 0.85)
    pieces.append(
        _crate(
            rng,
            anchor,
            t,
            n,
            rng.uniform(-0.05, 0.05),
            s / 2 + 0.08,
            yaw0,
            s2,
            z=s * 0.9,
            jitter=0.3,
        )
    )
    r = rng.uniform(0.38, 0.45)
    x, y = _at(anchor, t, n, s / 2 + r + 0.08, r + 0.1)
    pieces.append(Piece("barrel", x, y, 0.0, (r, r, 1.0), 0.0, r))
    if rng.random() < 0.6:
        x, y = _at(anchor, t, n, -(s / 2 + r + 0.1), r + 0.1 + rng.uniform(0, 0.25))
        pieces.append(Piece("barrel", x, y, 0.0, (r, r, 1.0), 0.0, r))
    return pieces


def lane_cover(rng, anchor, axis):
    """Cover standing in an open lane, long side across the lane's line of travel."""
    yaw0 = math.atan2(axis[1], axis[0]) + math.pi / 2 + rng.uniform(-0.3, 0.3)
    t = (math.cos(yaw0), math.sin(yaw0))
    pieces = []
    sizes = [rng.choice((1.0, 1.15, 1.3)) for _ in range(rng.choice((2, 3)))]
    # Laid edge to edge and centred on the anchor, whatever each crate's size.
    edge = -(sum(sizes) + 0.06 * (len(sizes) - 1)) / 2
    for s in sizes:
        u = edge + s / 2
        edge += s + 0.06
        pieces.append(
            Piece(
                "crate",
                anchor[0] + t[0] * u,
                anchor[1] + t[1] * u,
                0.0,
                (s, s, s * 0.95),
                yaw0 + rng.uniform(-0.08, 0.08),
                s * 0.5,
            )
        )
    if rng.random() < 0.55:
        s = 0.9
        pieces.append(
            Piece(
                "crate",
                anchor[0] + t[0] * 0.1,
                anchor[1] + t[1] * 0.1,
                pieces[0].size[2],
                (s, s, s * 0.9),
                yaw0 + rng.uniform(-0.3, 0.3),
                s * 0.5,
            )
        )
    return pieces


def debris(rng, anchor, normal):
    """Rubble that settled against a wall: low enough that no eye is ever in it."""
    t, n = _frame(normal)
    pieces = []
    for _ in range(rng.choice((2, 3, 3, 4))):
        x, y = _at(anchor, t, n, rng.uniform(-0.7, 0.7), rng.uniform(0.15, 0.8))
        a, b = rng.uniform(0.18, 0.45), rng.uniform(0.15, 0.4)
        pieces.append(
            Piece(
                "rubble",
                x,
                y,
                0.0,
                (a, b, rng.uniform(0.07, 0.2)),
                rng.uniform(0, math.tau),
                0.0,
            )
        )
    return pieces


# ------------------------------------------------------------------------- placing


def _cells(plan, p):
    r = p.radius
    for ix, iy in plan._rect_cells(p.x - r, p.x + r, p.y - r, p.y + r):
        cx, cy = plan.centre(ix, iy)
        if (cx - p.x) ** 2 + (cy - p.y) ** 2 <= r * r:
            yield plan._i(ix, iy)


def fits(plan, pieces):
    """Every piece's footprint is on open floor, clear of walls, bans and each other."""
    for p in pieces:
        if p.kind == "rubble":
            ix, iy = plan.cell(p.x, p.y)
            if not plan.inside(ix, iy):
                return False
            k = plan._i(ix, iy)
            if plan.solid[k] or plan.forbid[k]:
                return False
            continue
        for k in _cells(plan, p):
            if plan.solid[k] or plan.forbid[k] or plan.placed[k]:
                return False
    # Pieces of one vignette must not sit in each other either. Things resting on
    # others (z above the floor) are meant to overlap in plan.
    ground = [p for p in pieces if p.kind != "rubble" and p.z == 0.0]
    for i, a in enumerate(ground):
        for b in ground[i + 1 :]:
            if math.hypot(a.x - b.x, a.y - b.y) < (a.radius + b.radius) * 0.92:
                return False
    return True


def commit_cells(plan, pieces):
    for p in pieces:
        if p.kind != "rubble":
            for k in _cells(plan, p):
                plan.placed[k] = 1


WALL_VIGNETTES = {
    "crates": crates_row,
    "barrels": barrel_cluster,
    "sacks": sack_pile,
    "jars": jar_row,
}


def dress(
    plan,
    rng,
    zone_of,
    min_open=0.7,
    spacing=3.8,
    lane_spacing=11.0,
    lane_budget=None,
    stats=None,
):
    """Place vignettes. `zone_of(x, y)` -> `(density, weights, lane_axis_or_None)`.

    Returns a list of `(vignette_name, pieces)`. A vignette is kept only if every
    piece fits and the floor round it is still mostly open, so a clutter of props
    never closes a passage.
    """
    out, centres, lane_centres = [], [], []
    lane_left = lane_budget if lane_budget is not None else 10**9

    band, lanes = [], []
    for iy in range(plan.h):
        for ix in range(plan.w):
            k = plan._i(ix, iy)
            if plan.solid[k] or plan.forbid[k]:
                continue
            d = plan.dist[k]
            if 0.3 <= d <= 0.6:
                band.append((ix, iy))
            elif d >= 3.4:
                lanes.append((ix, iy))
    rng.shuffle(band)
    rng.shuffle(lanes)

    def far_enough(x, y, pool, gap):
        return all((x - cx) ** 2 + (y - cy) ** 2 >= gap * gap for cx, cy in pool)

    def at_wall(x, y):
        """The wall behind a cell: its outward normal, the point on its face, and
        whether it turns a corner within a couple of metres."""
        n = plan.normal_at(x, y)
        if n is None:
            return None
        # Open out from the wall: a disc centred on the wall is half wall by definition.
        if plan.free_fraction(x + n[0] * 2.0, y + n[1] * 2.0, 1.6) < min_open:
            return None
        d = plan.d_at(x, y)
        anchor = (x - n[0] * (d - CELL * 0.5), y - n[1] * (d - CELL * 0.5))
        t = (-n[1], n[0])
        corner = any(
            plan.d_at(x + t[0] * s * 1.5, y + t[1] * s * 1.5) < 0.2 for s in (-1, 1)
        )
        return n, anchor, corner

    # Corners first, so a stack gets the corner rather than a row happening to be there.
    # Then the wall faces between them.
    for phase in ("corner", "wall"):
        for ix, iy in band:
            x, y = plan.centre(ix, iy)
            density, weights, _ = zone_of(x, y)
            if density <= 0 or rng.random() > min(1.0, 0.8 * density):
                continue
            gap = spacing / math.sqrt(max(density, 0.3))
            if not far_enough(x, y, centres, gap * (1.3 if phase == "corner" else 1.0)):
                continue
            wall = at_wall(x, y)
            if wall is None:
                continue
            n, anchor, corner = wall
            if corner != (phase == "corner"):
                continue
            # A few tries, each a fresh draw and a little further from the wall: the
            # face is only known to a cell, and a row that clips it may clear it
            # a hand's width out, or fit as barrels where crates did not.
            for attempt in range(5):
                lean = (
                    anchor[0] + n[0] * 0.06 * attempt,
                    anchor[1] + n[1] * 0.06 * attempt,
                )
                if phase == "corner":
                    name, pieces = "corner", corner_stack(rng, lean, n)
                else:
                    name = rng.choices(list(weights), weights=list(weights.values()))[0]
                    pieces = WALL_VIGNETTES[name](rng, lean, n)
                if fits(plan, pieces):
                    break
            else:
                continue
            commit_cells(plan, pieces)
            centres.append((x, y))
            out.append((name, pieces))
            if rng.random() < 0.5:
                rubble = debris(rng, anchor, n)
                if fits(plan, rubble):
                    out.append(("debris", rubble))

    for ix, iy in lanes:
        if lane_left <= 0:
            break
        x, y = plan.centre(ix, iy)
        _, _, axis = zone_of(x, y)
        if axis is None or rng.random() > 0.08:
            continue
        if (
            not far_enough(x, y, lane_centres, lane_spacing)
            or plan.free_fraction(x, y, 4.0) < 0.85
        ):
            continue
        pieces = lane_cover(rng, (x, y), axis)
        if not fits(plan, pieces):
            continue
        commit_cells(plan, pieces)
        lane_centres.append((x, y))
        out.append(("cover", pieces))
        lane_left -= 1
    return out


# -------------------------------------------------------------------------- emitting


def emit(vignettes, collection, mats):
    """Turn placed pieces into merged objects. Solid pieces collide; rubble does not.

    Everything solid is named without `NonCol`, so the bake treats it as cover and a
    body cannot be inside it; rubble is under a quarter metre, below any eye.
    """
    meshers = {}

    def mesher(group, material):
        if (group, material) not in meshers:
            meshers[(group, material)] = Mesher()
        return meshers[(group, material)]

    for _, pieces in vignettes:
        for p in pieces:
            x, y, z = p.x, p.y, p.z
            sx, sy, sz = p.size
            if p.kind == "crate":
                m = mesher("crates", "wood_crate")
                m.box((x, y, z + sz / 2), (sx, sy, sz), yaw=p.yaw)
                m.box((x, y, z + sz + 0.025), (sx * 1.04, sy * 1.04, 0.05), yaw=p.yaw)
                # Straps read on a big crate and are lost on a small one.
                for off in (-0.28, 0.28) if sx >= 1.0 else ():
                    ox, oy = math.cos(p.yaw) * off * sx, math.sin(p.yaw) * off * sx
                    m.box(
                        (x + ox, y + oy, z + sz / 2),
                        (0.06, sy * 1.03, sz * 1.01),
                        yaw=p.yaw,
                    )
            elif p.kind in ("barrel", "barrel_down"):
                r, h = sx, sz
                wood = mesher("barrels", "wood_cedar_weathered")
                iron = mesher("barrels", "metal_iron_rusted")
                if p.kind == "barrel_down":
                    wood.frustum(
                        (x, y, z + r), r, r, h, 12, tilt=math.pi / 2, yaw=p.yaw
                    )
                    for off in (-0.28, 0.28):
                        ox, oy = -math.sin(p.yaw) * off, math.cos(p.yaw) * off
                        iron.frustum(
                            (x + ox, y + oy, z + r),
                            r + 0.02,
                            r + 0.02,
                            0.05,
                            12,
                            tilt=math.pi / 2,
                            yaw=p.yaw,
                        )
                else:
                    wood.frustum((x, y, z + h * 0.25), r, r * 1.1, h * 0.5, 10)
                    wood.frustum((x, y, z + h * 0.75), r * 1.1, r, h * 0.5, 10)
                    for frac in (0.22, 0.78):
                        iron.frustum((x, y, z + h * frac), r * 1.12, r * 1.12, 0.06, 8)
            elif p.kind == "sack":
                m = mesher("sacks", "sack")
                m.box((x, y, z + sz / 2), (sx, sy, sz), yaw=p.yaw, tilt=0.04)
                m.box(
                    (
                        x + math.cos(p.yaw) * sx * 0.38,
                        y + math.sin(p.yaw) * sx * 0.38,
                        z + sz * 0.45,
                    ),
                    (sx * 0.18, sy * 0.7, sz * 0.8),
                    yaw=p.yaw,
                )
            elif p.kind == "jar":
                m = mesher("jars", "sandstone_ochre")
                r, h = sx, sz
                m.frustum((x, y, z + h * 0.33), r * 0.8, r, h * 0.66, 8)
                m.frustum((x, y, z + h * 0.83), r, r * 0.45, h * 0.34, 8)
            elif p.kind == "rubble":
                mesher("rubble", "sandstone_dark").box(
                    (x, y, z + sz / 2), (sx, sy, sz), yaw=p.yaw, tilt=0.12, roll=0.08
                )
    counts = {}
    for (group, material), m in sorted(meshers.items()):
        name = f"Dress_{group.capitalize()}_{material}" + (
            "_NonCol" if group == "rubble" else ""
        )
        m.commit(collection, name, mats[material])
        counts[name] = m.parts
    return counts
