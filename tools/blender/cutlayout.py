"""Cut a map open along a few lines and spread the halves apart.

A modelled map is authored in metres in one frame, and every detail hangs off a
wall by a few centimetres (a rug, a frieze, a sconce, a plinth), so scaling a map
pulls them off the walls they were built against. Cutting leaves each side rigid:
a piece wholly on one side of a cut only moves, and one that straddles it (a long
wall, the ground, a ramp) grows by the gap. So walls stay joined and everything
mounted on them goes with them.

A map's `<map>_layout.py` holds its cuts, `{"x": [(at, gap), ...], "y": [...]}`
in metres, and `Cuts` warps through them. A generator makes a `Warper` from the
cuts and wraps its own primitives with it:

    W = cutlayout.Warper(mirage_layout.CUTS)
    add_box = W.box(add_box)               # (collection, name, center, size, material)
    add_arch = W.composite(add_arch, 2)    # many parts placed from one point
    ...
    with W.raw():                          # already in final metres: skip the cuts
        place_something_planned()

Everything the wrappers see is also recorded (`W.scan`), which is what picks cuts
(`W.dump_scan`: a candidate line is clean when no small object crosses it) and
what the prop planner plans on (`W.plan`). Pure Python: no `bpy`, so it imports
anywhere and the remap scripts can use `Cuts` without Blender.
"""

import json
from contextlib import contextmanager


class Cuts:
    """The cuts of one map, and the warp they make."""

    def __init__(self, cuts):
        self.cuts = cuts

    def metres(self, v, axis, upper=False):
        """`v` metres through the cuts. A cut's own coordinate belongs to the side
        above it when `v` is a lower edge and to the side below when it is an
        upper edge, so a box that ends on a cut does not grow."""
        out = v
        for at, gap in self.cuts[axis]:
            if v > at or (v == at and not upper):
                out += gap
        return out

    def cubes(self, x, y, cubes_per_metre=3.0):
        """A point in cubes (a spawn, an item, a bomb site), through the same cuts."""
        return (
            self.metres(x / cubes_per_metre, "x") * cubes_per_metre,
            self.metres(y / cubes_per_metre, "y") * cubes_per_metre,
        )

    def total(self, axis):
        return sum(gap for _, gap in self.cuts[axis])


class Warper:
    """Wrappers that put a generator's primitives through a map's cuts."""

    def __init__(self, cuts):
        self.cuts = cuts if isinstance(cuts, Cuts) else Cuts(cuts)
        self._inside = 0  # >0 while a composite places parts that are already placed
        self.scan = []  # (kind, name, (x0, x1), (y0, y1), (z0, z1)) per primitive

    # -- the warp ---------------------------------------------------------------

    def w(self, v, axis, upper=False):
        return self.cuts.metres(v, axis, upper)

    def w_box(self, center, size):
        c, sz = list(center[:2]), list(size[:2])
        for i, axis in enumerate(("x", "y")):
            lo, hi = c[i] - sz[i] / 2.0, c[i] + sz[i] / 2.0
            lo, hi = self.w(lo, axis), self.w(hi, axis, upper=True)
            c[i], sz[i] = (lo + hi) / 2.0, hi - lo
        return tuple(c), tuple(sz)

    def w_point(self, p):
        return (self.w(p[0], "x"), self.w(p[1], "y")) + tuple(p[2:])

    def rect(self, x0, y0, x1, y1):
        """A rectangle in the authored frame, as it ends up after the cuts."""
        return (
            self.w(x0, "x"),
            self.w(y0, "y"),
            self.w(x1, "x", upper=True),
            self.w(y1, "y", upper=True),
        )

    @contextmanager
    def raw(self):
        """Inside this block the wrappers pass everything through untouched."""
        self._inside += 1
        try:
            yield
        finally:
            self._inside -= 1

    @property
    def active(self):
        return not self._inside

    # -- recording --------------------------------------------------------------

    def record(self, kind, name, center, size, height=None):
        """Note a primitive's footprint (and its z span) before it is warped, for the
        scan and for the dressing, which plans against everything already built."""
        h = height if height is not None else size[2]
        z0 = center[2] - h / 2.0 if len(center) > 2 else 0.0
        z1 = z0 + h if len(center) > 2 else 3.0
        self.scan.append(
            (
                kind,
                name,
                (center[0] - size[0] / 2.0, center[0] + size[0] / 2.0),
                (center[1] - size[1] / 2.0, center[1] + size[1] / 2.0),
                (z0, z1),
            )
        )

    def dump_scan(self, path):
        """Write every primitive's footprint to `path` as JSON, for choosing cuts."""
        with open(path, "w") as f:
            json.dump(self.scan, f)

    # -- wrappers ---------------------------------------------------------------

    def box(self, raw):
        """`raw(collection, name, center, size, material)`."""

        def wrapped(collection, name, center, size, material):
            if self._inside:
                return raw(collection, name, center, size, material)
            self.record("box", name, center, size)
            c, sz = self.w_box(center, size[:2])
            return raw(
                collection, name, c + tuple(center[2:]), sz + tuple(size[2:]), material
            )

        return wrapped

    def cylinder(self, raw):
        """`raw(collection, name, center, radius, height, material, segments=16)`."""

        def wrapped(collection, name, center, radius, height, material, segments=16):
            if self._inside:
                return raw(
                    collection,
                    name,
                    center,
                    radius,
                    height,
                    material,
                    segments=segments,
                )
            self.record("cyl", name, center, (radius * 2, radius * 2), height)
            return raw(
                collection,
                name,
                self.w_point(center),
                radius,
                height,
                material,
                segments=segments,
            )

        return wrapped

    def wedge(self, raw):
        """`raw(collection, name, center, size, material, direction)`."""

        def wrapped(collection, name, center, size, material, direction):
            if self._inside:
                return raw(collection, name, center, size, material, direction)
            self.record("wedge", name, center, size)
            c, sz = self.w_box(center, size[:2])
            return raw(
                collection,
                name,
                c + tuple(center[2:]),
                sz + tuple(size[2:]),
                material,
                direction,
            )

        return wrapped

    def boxes(self, raw):
        """`raw(collection, name, boxes, material)`, `boxes` being `(center, size)` pairs."""

        def wrapped(collection, name, boxes, material):
            if self._inside:
                return raw(collection, name, boxes, material)
            out = []
            for center, size in boxes:
                self.record("box", name, center, size)
                c, sz = self.w_box(center, size[:2])
                out.append((c + tuple(center[2:]), sz + tuple(size[2:])))
            return raw(collection, name, out, material)

        return wrapped

    def plinths(self, raw):
        """`raw(collection, name, walls, material, **kw)`, `walls` being `(center, size)` pairs."""

        def wrapped(collection, name, walls, material, **kw):
            if self._inside:
                return raw(collection, name, walls, material, **kw)
            out = []
            for center, size in walls:
                self.record("box", name, center, size)
                c, sz = self.w_box(center, size[:2])
                out.append((c + tuple(center[2:]), sz + tuple(size[2:])))
            return raw(collection, name, out, material, **kw)

        return wrapped

    def light(self, raw):
        """`raw(pos, **kw)`."""

        def wrapped(pos, **kw):
            if self._inside:
                return raw(pos, **kw)
            return raw(self.w_point(pos), **kw)

        return wrapped

    def composite(self, fn, point_arg):
        """A helper that places many parts from one point: warp the point, then let the
        parts through untouched. A composite is a few metres across, so a cut that
        passes through one splits it; `dump_scan` shows where that would happen."""

        def wrapped(*args, **kw):
            args = list(args)
            if not self._inside:
                self.record(
                    "comp",
                    f"{fn.__name__}:{args[1]}",
                    args[point_arg][:2],
                    (3.0, 3.0, 3.0),
                )
                args[point_arg] = self.w_point(args[point_arg])
            self._inside += 1
            try:
                return fn(*args, **kw)
            finally:
                self._inside -= 1

        return wrapped

    def line(self, fn, start_arg, end_arg):
        """A helper that lays parts out along a line (battlements): warp both ends and
        lay the parts out afresh, so a cut does not leave a gap in them."""

        def wrapped(*args, **kw):
            args = list(args)
            if not self._inside:
                args[start_arg] = self.w_point(args[start_arg])
                args[end_arg] = self.w_point(args[end_arg])
            self._inside += 1
            try:
                return fn(*args, **kw)
            finally:
                self._inside -= 1

        return wrapped

    def stairs(self, raw):
        """`raw(collection, name, start, width, rise, run, steps, axis, material)`.

        Rigid from `start`, like a composite, but recorded as the footprint it really
        has (`run` along `axis`, `width` across), so the scan sees a cut through it
        and the planner keeps clutter off it."""

        def wrapped(collection, name, start, width, rise, run, steps, axis, material):
            if self._inside:
                return raw(
                    collection, name, start, width, rise, run, steps, axis, material
                )
            sign = -1.0 if axis.startswith("-") else 1.0
            along = axis[-1]
            if along == "y":
                centre = (start[0], start[1] + sign * run / 2.0, rise / 2.0)
                size = (width, run, rise)
            else:
                centre = (start[0] + sign * run / 2.0, start[1], rise / 2.0)
                size = (run, width, rise)
            self.record("stairs", name, centre, size)
            return raw(
                collection,
                name,
                self.w_point(start),
                width,
                rise,
                run,
                steps,
                axis,
                material,
            )

        return wrapped

    def wall_with_door(self, raw):
        """`raw(collection, name, center, size, material, door_at, door_width, door_height)`."""

        def wrapped(
            collection, name, center, size, material, door_at, door_width, door_height
        ):
            if self._inside:
                return raw(
                    collection,
                    name,
                    center,
                    size,
                    material,
                    door_at,
                    door_width,
                    door_height,
                )
            self.record("box", name, center, size)
            c, sz = self.w_box(center, size[:2])
            axis = "x" if size[0] >= size[1] else "y"
            return raw(
                collection,
                name,
                c + tuple(center[2:]),
                sz + tuple(size[2:]),
                material,
                self.w(door_at, axis),
                door_width,
                door_height,
            )

        return wrapped

    # -- planning ---------------------------------------------------------------

    def plan(
        self,
        props,
        extent,
        domain,
        placed,
        ways=("Arch", "Door", "Portal", "Exit", "Window", "Stairs", "Ramp"),
        sunk=("Pit_",),
        mounted=("sconce", "lantern"),
        spawn_radius=4.5,
    ):
        """A `props.Plan` of everything recorded so far, in final metres.

        `extent` is the grid's rectangle and `domain` the part of it clutter may go
        in (inside the perimeter walls). `placed` is the map's JSON: its spawns and
        bomb sites are kept clear. Anything solid below 2.2 m is solid; a way
        through (`ways` in its name, or any wedge) is kept clear for 2 m; a fixture
        mounted on a wall for 2.5 m, because a crate beside one is a step up to it
        and a body on top has an eye in the cage; and what is `sunk` below the floor
        props stand on is banned.
        """
        plan = props.Plan(*extent)
        for kind, name, xr, yr, zr in self.scan:
            if zr[1] <= 0.3 or zr[0] >= 2.2:
                continue  # a floor, or overhead
            x0, y0, x1, y1 = self.rect(xr[0], yr[0], xr[1], yr[1])
            plan.mark_rect(plan.solid, x0, x1, y0, y1)
            if kind in ("wedge", "stairs") or any(word in name for word in ways):
                plan.mark_rect(plan.forbid, x0, x1, y0, y1, pad=2.0)
            elif kind == "comp":
                fixture = any(word in name.lower() for word in mounted)
                plan.mark_rect(plan.forbid, x0, x1, y0, y1, pad=2.5 if fixture else 0.3)
        for kind, name, xr, yr, zr in self.scan:
            if any(name.startswith(prefix) for prefix in sunk):
                x0, y0, x1, y1 = self.rect(xr[0], yr[0], xr[1], yr[1])
                plan.mark_rect(plan.forbid, x0, x1, y0, y1, pad=1.5)
        for sp in placed["spawns"]:
            plan.mark_disc(plan.forbid, sp["x"] / 3.0, sp["y"] / 3.0, spawn_radius)
        for site in placed["objectives"]["sites"]:
            plan.mark_disc(
                plan.forbid,
                site["x"] / 3.0,
                site["y"] / 3.0,
                site["radius"] / 3.0 + 1.5,
            )
        plan.forbid_outside(*domain)
        plan.solve()
        return plan

    def zone_of(self, zones, default):
        """`zone_of(x, y)` for `props.dress` from `(rect, density, weights, lane_axis)`
        rows given in the authored frame; `default` is `(density, weights, axis)`."""
        warped = [(self.rect(*rect), d, wt, axis) for rect, d, wt, axis in zones]

        def zone_of(x, y):
            for (x0, y0, x1, y1), density, weights, axis in warped:
                if x0 <= x <= x1 and y0 <= y <= y1:
                    return density, weights, axis
            return default

        return zone_of
