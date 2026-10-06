"""How long it takes to walk from each team's spawns to each bomb site.

    uv run python tools/hassault_route_times.py hd_dust2 [path/to/map.json ...]

Shortest walking path over the baked cube grid, with the playability lint's own
rules for what a body can stand in and step onto (`maplint._standable`), so this
measures the map the server simulates, not the model. Eight-way moves, a diagonal
costing root two and only taken when both cells beside it are open. Distances are
in cubes; a cube is a third of a metre and a player runs at `MOVE_SPEED` cubes a
second, so the time is the fastest the fight can begin at a site, not a typical
round: it ignores crouching, stairs' slope, and any path that has to be walked
round something in the way.

Give it several JSONs to compare maps (the committed one, an older revision from
`git show HEAD:<path>`). Also prints how many *different* routes a team has to a
site: the shortest path, then the shortest one that stays at least `SEPARATION`
cubes from every cell of the ones already found. A map is more tactical the
more of these there are, and the closer in length.
"""

import heapq
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.modules.hassault import maplint, mapsource, physics  # noqa: E402

#: Cells a second route must keep from the ones already taken, in cubes.
SEPARATION = 14
#: A route this much longer than the shortest is still worth taking.
SLACK = 1.6

DIRS = [
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)),
    (-1, -1, math.sqrt(2)),
]


def load(path):
    with open(path, encoding="utf-8") as f:
        source = json.load(f)
    cmap = mapsource.build(source, name=os.path.basename(path))
    return source, cmap, physics.World.from_map(cmap)


#: What a jump costs on top of the cells it crosses, in cubes of running: the
#: time to leave the ground and land, at the speed a body runs.
JUMP_COST = 3.0


def dijkstra(world, cells, starts, blocked=frozenset()):
    """Distance in cubes from the nearest of `starts` to every standable cell.

    Walks, steps up to `STEP_HEIGHT`, and jumps up to the lint's `JUMP_CLIMB`
    (two cells at a time, as `maplint._reachable` does, because a body cannot stand
    in the cell hugging a ledge it cannot step onto), a jump costing `JUMP_COST`.
    """
    dist = {s: 0.0 for s in starts if s in cells}
    prev = {}
    heap = [(0.0, s) for s in dist]
    heapq.heapify(heap)
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist.get(cur, 1e18):
            continue
        cx, cy = cur
        here = world.floor_at(cx, cy)
        moves = []
        for dx, dy, cost in DIRS:
            nxt = (cx + dx, cy + dy)
            if nxt not in cells or nxt in blocked:
                continue
            rise = world.floor_at(*nxt) - here
            if dx and dy:
                if rise > physics.STEP_HEIGHT:
                    continue
                if (cx + dx, cy) not in cells or (cx, cy + dy) not in cells:
                    continue
                moves.append((nxt, cost))
            elif rise <= physics.STEP_HEIGHT:
                moves.append((nxt, cost))
            elif rise <= maplint.JUMP_CLIMB:
                moves.append((nxt, cost + JUMP_COST))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near, far = (cx + dx, cy + dy), (cx + dx * 2, cy + dy * 2)
            if (
                near in cells
                or far not in cells
                or far in blocked
                or world.is_solid(*near)
            ):
                continue
            rise = world.floor_at(*far) - here
            if physics.STEP_HEIGHT < rise <= maplint.JUMP_CLIMB:
                moves.append((far, 2.0 + JUMP_COST))
        for nxt, cost in moves:
            nd = d + cost
            if nd < dist.get(nxt, 1e18):
                dist[nxt] = nd
                prev[nxt] = cur
                heapq.heappush(heap, (nd, nxt))
    return dist, prev


def path(prev, goal):
    out = [goal]
    while out[-1] in prev:
        out.append(prev[out[-1]])
    return out


def nearest(cells, x, y):
    return min(cells, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)


def teams(source):
    out = defaultdict(list)
    for sp in source["spawns"]:
        out[sp["team"]].append((int(sp["x"]), int(sp["y"])))
    return out


def measure(path_json):
    """`{(team, site id): [seconds of the shortest route, then of each further one]}`.

    A pair with no way there maps to an empty list. Also returns the world and its
    standable cells, so a caller can ask about a place in particular.
    """
    source, cmap, world = load(path_json)
    cells = maplint._standable(world)
    sites = source["objectives"]["sites"]
    out = {}
    for team, spawns in sorted(teams(source).items()):
        starts = [nearest(cells, x, y) for x, y in spawns]
        dist, prev = dijkstra(world, cells, starts)
        for site in sites:
            # The nearest cell of the site that this team can actually get to: the
            # centre may be a crate's top, or a pocket nobody can enter.
            radius = float(site.get("radius", 12)) + 2
            inside = [
                c
                for c in dist
                if (c[0] - site["x"]) ** 2 + (c[1] - site["y"]) ** 2 <= radius * radius
            ]
            if not inside:
                out[(team, site["id"])] = []
                continue
            goal = min(
                inside, key=lambda c: (c[0] - site["x"]) ** 2 + (c[1] - site["y"]) ** 2
            )
            best = dist[goal]
            # Distinct routes: ban a corridor round each path found, and look again.
            banned, found = set(), [best]
            current = path(prev, goal)
            for _ in range(5):
                for cx, cy in current:
                    for dx in range(-SEPARATION, SEPARATION + 1, 2):
                        for dy in range(-SEPARATION, SEPARATION + 1, 2):
                            if dx * dx + dy * dy <= SEPARATION * SEPARATION:
                                banned.add((cx + dx, cy + dy))
                # The ends are shared by every route: leave them open.
                for end in (goal, *starts):
                    for dx in range(-SEPARATION, SEPARATION + 1):
                        for dy in range(-SEPARATION, SEPARATION + 1):
                            banned.discard((end[0] + dx, end[1] + dy))
                d2, p2 = dijkstra(world, cells, starts, frozenset(banned))
                if goal not in d2 or d2[goal] > best * SLACK:
                    break
                found.append(d2[goal])
                current = path(p2, goal)
            out[(team, site["id"])] = [f / physics.MOVE_SPEED for f in found]
    return out, world, cells


def report(label, path_json):
    routes, _, cells = measure(path_json)
    print(f"\n== {label} ({os.path.basename(path_json)}): {len(cells)} standable cells")
    for (team, site), times in routes.items():
        if not times:
            print(f"  team {team} -> {site}: UNREACHABLE")
            continue
        alt = ", ".join(f"{t:.1f}s" for t in times)
        print(
            f"  team {team} -> {site}: {times[0]:4.1f} s"
            f"   routes within {SLACK}x: {len(times)} ({alt})"
        )


if __name__ == "__main__":
    name = sys.argv[1]
    paths = sys.argv[2:] or [
        os.path.join("backend", "modules", "hassault", "maps", f"{name}.json")
    ]
    for p in paths:
        report(os.path.basename(os.path.dirname(p)) or "map", p)
