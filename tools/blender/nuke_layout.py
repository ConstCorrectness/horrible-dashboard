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

# 62 x 62 m inside the walls becomes 74 x 76 m: about 1.46 times the area. The scan
# (`HASSAULT_NUKE_SCAN=<file>`) found nothing small across any of these lines:
# x: the west alley, the hall either side of the plant (clear of the troffers and
# the crane's trolley), the lane between the hall and the garage; y: twice across
# the south yard and the ramp room, which only lengthens the attackers' walk and
# eases the ramp, and across the hall and B between the vent and the north tank.
# Nothing is cut north of A: every line there would lengthen CT's walk to B too.
LAYOUT_CUTS = {
    "x": [(6.0, 3.0), (32.5, 3.0), (45.8, 3.0), (51.1, 3.0)],
    "y": [(18.5, 5.0), (21.0, 5.0), (32.2, 4.0)],
}

CUBES_PER_METRE = 3.0

CUTS = Cuts(LAYOUT_CUTS)


def warp_metres(v, axis, upper=False):
    """`v` metres through the cuts (see `cutlayout.Cuts.metres`)."""
    return CUTS.metres(v, axis, upper)


def warp_cubes(x, y):
    """A point in cubes, through the same cuts."""
    return CUTS.cubes(x, y, CUBES_PER_METRE)
