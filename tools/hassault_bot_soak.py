"""Let bots play a map for a while with no one watching, and report where they went.

    uv run python tools/hassault_bot_soak.py hd_dust2 [seconds] [bots] [mode]

Opens a room the way the server does (`assets.load_map`, `World.from_map`,
objectives, pickups), fills it with bots and ticks it at the server's rate. Bots
have no navmesh (see `bots.py`): they roam and probe, so a map with new corridors
can leave them using none of them. This reports, for the map's own cover nodes
(`bots.TACTICAL_COVER_NODES`), how many bot-visits each one got, plus how much of
the map's walkable floor was ever stood on and whether any bot sat still for ten
seconds or more (stuck). It cannot say a map is good, but it does say a place is
unreachable *to a bot*, which the walking-path lint cannot.
"""

import math
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.modules.hassault import assets, bots, maplint, modes, pickups  # noqa: E402
from backend.modules.hassault.modes import objectives  # noqa: E402
from backend.modules.hassault.match import MatchRoom  # noqa: E402
from backend.modules.hassault.physics import World  # noqa: E402

TICK = 1 / 20
#: A node counts as visited when a bot's centre is this close, in cubes.
NODE_RADIUS = 8.0
#: Sitting inside this radius for this long is stuck.
STUCK_RADIUS = 2.0
STUCK_SECONDS = 10.0


def build_room(name, mode):
    cgz = assets.load_map(name)
    world = World.from_map(cgz)
    game_mode = modes.build(mode)
    placed = objectives.place(world, cgz)
    room = MatchRoom(
        "soak",
        name,
        world,
        cgz.spawns(),
        pickups.place(world, cgz.entities),
        mode=game_mode,
        objectives=placed,
    )
    return cgz, world, room


def main():
    name = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    mode = sys.argv[4] if len(sys.argv) > 4 else "dm"
    random.seed(7)
    cgz, world, room = build_room(name, mode)
    members = bots.add_bots(room, count, skill="normal")
    standable = maplint._standable(world)
    seen = set()
    nodes = bots.TACTICAL_COVER_NODES.get(name, [])
    visits = Counter()
    anchor = {b.id: (b.state.x, b.state.y, 0.0) for b in members}
    stuck = defaultdict(float)
    t = 0.0
    while t < seconds:
        room.simulate(TICK)
        t += TICK
        for b in members:
            if not b.alive:
                anchor[b.id] = (b.state.x, b.state.y, t)
                continue
            seen.add((int(b.state.x), int(b.state.y)))
            for x, y, label in nodes:
                if math.hypot(b.state.x - x, b.state.y - y) <= NODE_RADIUS:
                    visits[label] += 1
            ax, ay, since = anchor[b.id]
            if math.hypot(b.state.x - ax, b.state.y - ay) > STUCK_RADIUS:
                anchor[b.id] = (b.state.x, b.state.y, t)
            else:
                stuck[b.id] = max(stuck[b.id], t - since)
    covered = len(seen & standable)
    print(f"{name} ({mode}), {count} bots, {seconds:.0f} s simulated")
    print(
        f"  walkable floor stood on: {covered} of {len(standable)} cells ({100 * covered / len(standable):.0f}%)"
    )
    print("  visits to cover nodes (ticks within {:.0f} cubes):".format(NODE_RADIUS))
    for x, y, label in nodes:
        print(f"    {label:20s} {visits[label]:6d}")
    longest = max(stuck.values(), default=0.0)
    print(
        f"  longest a bot sat within {STUCK_RADIUS:.0f} cubes of one spot: {longest:.0f} s"
        f"{'  (STUCK)' if longest >= STUCK_SECONDS else ''}"
    )


if __name__ == "__main__":
    main()
