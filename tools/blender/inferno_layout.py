"""Where Inferno is cut open, shared by the generator and by whatever has to move a
hand-placed coordinate with it. See `cutlayout.py` for why a map is cut, not scaled.

A spawn, an item, a bomb site or a bot's cover node is a coordinate in *cubes* (3
per metre) in the same frame the generator authors in, so the same cuts apply to
it; `warp_cubes` does it. No `bpy` here: it has to import anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutlayout import Cuts  # noqa: E402

# 62 x 62 m inside the walls becomes 74 x 78 m: about 1.55 times the area. The scan
# (`HASSAULT_INFERNO_SCAN=<file>`) found nothing small across any of these lines:
# x: the west strip, between Banana's east wall and the middle street, the apartments'
# east half, the east house; y: out of T's court, Banana's centre, behind the
# apartments' balcony, B and CT.
LAYOUT_CUTS = {
    "x": [(8.0, 3.0), (27.6, 3.0), (44.5, 3.0), (59.0, 3.0)],
    "y": [(15.0, 4.0), (22.2, 4.0), (44.5, 4.0), (60.45, 4.0)],
}

CUBES_PER_METRE = 3.0

CUTS = Cuts(LAYOUT_CUTS)


def warp_metres(v, axis, upper=False):
    """`v` metres through the cuts (see `cutlayout.Cuts.metres`)."""
    return CUTS.metres(v, axis, upper)


def warp_cubes(x, y):
    """A point in cubes, through the same cuts."""
    return CUTS.cubes(x, y, CUBES_PER_METRE)
