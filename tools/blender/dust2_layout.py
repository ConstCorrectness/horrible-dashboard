"""Where Dust II is cut open, shared by the generator and by whatever has to move
a hand-placed coordinate with it.

`generate_dust2.py` authors the map in metres in one frame, then spreads it apart
along these lines (see `LAYOUT_CUTS` there for why cutting, not scaling). A spawn,
an item, a bomb site or a bot's cover node is a coordinate in *cubes* (3 per
metre) in that same frame, so the same cuts apply to it; `warp_cubes` does it.

No `bpy` here: it has to import anywhere.
"""

# The scan (`HASSAULT_DUST2_SCAN=<file>`) found no small object across any of these.
# x: west tunnels and B, the Mid street, the Long A side. y: out of T spawn, the
# lower and middle lanes, and the north approach to both sites.
# 62 x 62 m inside the walls becomes 74 x 78 m: about 1.55 times the area.
LAYOUT_CUTS = {
    "x": [(13.0, 3.0), (34.5, 5.0), (48.0, 4.0)],
    "y": [(14.25, 4.0), (22.0, 4.0), (30.5, 4.0), (50.0, 4.0)],
}

CUBES_PER_METRE = 3.0


def warp_metres(v, axis, upper=False):
    """`v` metres through the cuts. A cut's own coordinate belongs to the side
    above it when `v` is a lower edge and to the side below when it is an upper
    edge, so a box that ends on a cut does not grow."""
    out = v
    for at, gap in LAYOUT_CUTS[axis]:
        if v > at or (v == at and not upper):
            out += gap
    return out


def warp_cubes(x, y):
    """A point in cubes, through the same cuts."""
    return (
        warp_metres(x / CUBES_PER_METRE, "x") * CUBES_PER_METRE,
        warp_metres(y / CUBES_PER_METRE, "y") * CUBES_PER_METRE,
    )
