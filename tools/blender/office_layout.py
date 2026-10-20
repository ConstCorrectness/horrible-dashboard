"""Where Office is cut open, shared by the generator and by whatever has to move a
hand-placed coordinate with it. See `cutlayout.py` for why a map is cut, not scaled.

A spawn, an item, a bomb site or a bot's cover node is a coordinate in *cubes* (3
per metre) in the same frame the generator authors in, so the same cuts apply to
it; `warp_cubes` does it. No `bpy` here: it has to import anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutlayout import Cuts  # noqa: E402

# 58 x 56 m inside the walls becomes 66 x 62 m. The scan
# (`HASSAULT_OFFICE_SCAN=<file>`) found only long walls, the floor, the ceiling
# and the façade glass across these lines. x: the mid, west of the meeting rooms
# (clear of the troffers either side) and between the mid and the cubicles;
# both lengthen only T's walk, because CT's spawn and both sites lie west of
# them. y: across the mid between the elevator core and the boardroom, and
# between two rows of server racks, which lengthens the south corridor.
LAYOUT_CUTS = {
    "x": [(25.2, 3.0), (38.5, 5.0)],
    "y": [(11.2, 3.0), (37.2, 3.0)],
}

CUBES_PER_METRE = 3.0

CUTS = Cuts(LAYOUT_CUTS)


def warp_metres(v, axis, upper=False):
    """`v` metres through the cuts (see `cutlayout.Cuts.metres`)."""
    return CUTS.metres(v, axis, upper)


def warp_cubes(x, y):
    """A point in cubes, through the same cuts."""
    return CUTS.cubes(x, y, CUBES_PER_METRE)
