"""Where Nuke is cut open, shared by the generator and by whatever has to move a
hand-placed coordinate with it. See `cutlayout.py` for why a map is cut, not scaled.

A spawn, an item, a bomb site or a bot's cover node is a coordinate in *cubes* (3
per metre) in the same frame the generator authors in, so the same cuts apply to
it; `warp_cubes` does it. No `bpy` here: it has to import anywhere.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cutlayout import Cuts  # noqa: E402

LAYOUT_CUTS = {"x": [], "y": []}

CUBES_PER_METRE = 3.0

CUTS = Cuts(LAYOUT_CUTS)


def warp_metres(v, axis, upper=False):
    """`v` metres through the cuts (see `cutlayout.Cuts.metres`)."""
    return CUTS.metres(v, axis, upper)


def warp_cubes(x, y):
    """A point in cubes, through the same cuts."""
    return CUTS.cubes(x, y, CUBES_PER_METRE)
