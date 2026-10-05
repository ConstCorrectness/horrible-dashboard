"""Where the server lets a body stand on a baked map, for `probe_eye_inside.py`.

    uv run python tools/hassault_body_points.py hd_dust2 > dust2-bodies.json

Every cell reachable from the first spawn (the `maplint` walk), sampled on a
quarter-cube grid and kept where `physics.can_stand` says a body fits. The eye
heights a body can rest at, crouched and standing, ride along so the Blender side
needs no copy of them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.modules.hassault import maplint, mapsource, physics  # noqa: E402

OFFSETS = (0.125, 0.375, 0.625, 0.875)


def body_points(name: str) -> dict:
    cmap = mapsource.load_bundled(name)
    world = physics.World.from_map(cmap)
    spawn = cmap.spawns()[0]
    cells = maplint._standable(world)
    reach = maplint._reachable(
        world, (int(spawn.x), int(spawn.y)), cells, climb=maplint.JUMP_CLIMB
    )
    bodies = []
    for x, y in sorted(reach):
        floor = float(world.floor_at(x, y))
        for ox in OFFSETS:
            for oy in OFFSETS:
                if physics.can_stand(world, x + ox, y + oy, floor):
                    bodies.append((x + ox, y + oy, floor))
    return {
        "map": name,
        "eyes": [physics.CROUCH_EYE_HEIGHT, physics.PLAYER_EYE_HEIGHT],
        "bodies": bodies,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: hassault_body_points.py <map name, e.g. hd_dust2>")
    json.dump(body_points(sys.argv[1]), sys.stdout)
