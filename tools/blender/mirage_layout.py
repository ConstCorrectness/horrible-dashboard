"""Where Mirage is cut open, shared by the generator and by whatever has to move a
hand-placed coordinate with it. See `cutlayout.py` for why a map is cut, not scaled.

A spawn, an item, a bomb site or a bot's cover node is a coordinate in *cubes* (3
per metre) in the same frame the generator authors in, so the same cuts apply to
it; `warp_cubes` does it. No `bpy` here: it has to import anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutlayout import Cuts  # noqa: E402

# The scan (`HASSAULT_MIRAGE_SCAN=<file>`) found no small object across any of these:
# each is a line through open ground or through a floor or wall that is long in
# that direction (which only lengthens). x: the west strip, B Apartments, the
# middle street, the Palace. y: out of T spawn, B and the Palace, the catwalk, the
# approach to the Window Room.
# 62 x 62 m inside the walls becomes 74 x 78 m: about 1.55 times the area.
LAYOUT_CUTS = {
    "x": [(8.0, 3.0), (15.2, 3.0), (34.5, 3.0), (50.5, 3.0)],
    "y": [(7.0, 4.0), (19.25, 4.0), (32.5, 4.0), (43.5, 4.0)],
}

CUBES_PER_METRE = 3.0

CUTS = Cuts(LAYOUT_CUTS)


def warp_metres(v, axis, upper=False):
    """`v` metres through the cuts (see `cutlayout.Cuts.metres`)."""
    return CUTS.metres(v, axis, upper)


def warp_cubes(x, y):
    """A point in cubes, through the same cuts."""
    return CUTS.cubes(x, y, CUBES_PER_METRE)
