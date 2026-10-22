"""Build a map's bot waypoint graph, `backend/modules/hassault/nav/<map>.json`.

    uv run python tools/hassault_nav_build.py hd_office [hd_bank ...]

The graph is what `nav.py` routes bots over when the straight line to where they
want to go is blocked (see its docstring). This builds one from the map the
server simulates, so a regenerated map gets a rebuilt graph, not a stale one:

1. Key points: every spawn, each site's centre, and the map's cover nodes
   (`bots.TACTICAL_COVER_NODES`), each moved to the nearest cell a body can
   stand in.
2. The walking route between every pair of key points over the cube grid
   (steps up to `STEP_HEIGHT`, drops of any height, no jumps: bots do not plan
   jumps), *string-pulled*: from each waypoint, the furthest cell along the
   route still reachable in a straight `nav.walkable` line becomes the next
   waypoint. That puts nodes at corners and in doorways, where they are needed.
3. Fill: a node every `FILL` cubes over the rest of the walkable floor, so a bot
   roaming somewhere no key route passes still has a node in sight.
4. Links: every ordered pair of nodes within `MAX_LINK` cubes whose straight
   line is `nav.walkable`.

It then checks that every spawn reaches every site and every cover node over
the links, and exits non-zero if not. Hand edits to the file are fine: the
tests in `test_hassault_nav.py` hold any edit to the same rules.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.modules.hassault import assets, bots, maplint, nav, physics  # noqa: E402
from backend.modules.hassault.modes import objectives  # noqa: E402

#: Spacing of the fill nodes, in cubes.
FILL = 15
#: Nodes this close merge into one.
MERGE = 4.0
#: Longest link tried, in cubes.
MAX_LINK = 30.0

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


def walk_dijkstra(world, cells, start):
    """Shortest walking distance from `start` to every cell, and the tree."""
    dist = {start: 0.0}
    prev = {}
    heap = [(0.0, start)]
    while heap:
        d, cur = heapq.heappop(heap)
        if d > dist[cur]:
            continue
        cx, cy = cur
        here = world.floor_at(cx, cy)
        moves = []
        for dx, dy, cost in DIRS:
            nxt = (cx + dx, cy + dy)
            if nxt not in cells or world.floor_at(*nxt) - here > physics.STEP_HEIGHT:
                continue
            if dx and dy and ((cx + dx, cy) not in cells or (cx, cy + dy) not in cells):
                continue
            moves.append((nxt, cost))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near, far = (cx + dx, cy + dy), (cx + dx * 2, cy + dy * 2)
            if near in cells or far not in cells or world.is_solid(*near):
                continue
            if world.floor_at(*far) - here < -physics.STEP_HEIGHT:
                moves.append((far, 2.0))
        for nxt, cost in moves:
            nd = d + cost
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                prev[nxt] = cur
                heapq.heappush(heap, (nd, nxt))
    return dist, prev


def route(prev, start, goal):
    out = [goal]
    while out[-1] != start:
        out.append(prev[out[-1]])
    out.reverse()
    return out


def pull(world, cells_path):
    """String-pull a cell route into waypoints a body can walk between."""
    pts = [(c[0] + 0.5, c[1] + 0.5) for c in cells_path]
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        j = i + 1
        k = i + 1
        while k < len(pts):
            if nav.walkable(world, pts[i][0], pts[i][1], pts[k][0], pts[k][1]):
                j = k
            elif k - j > 12:
                break
            k += 2 if k + 2 < len(pts) else 1
        out.append(pts[j])
        i = j
    return out


def nearest_cell(cells, x, y):
    return min(cells, key=lambda c: (c[0] + 0.5 - x) ** 2 + (c[1] + 0.5 - y) ** 2)


def build(name):
    cgz = assets.load_map(name)
    world = physics.World.from_map(cgz)
    standable = maplint._standable(world)
    spawns = [(int(s.x), int(s.y)) for s in cgz.spawns()]
    reach = set()
    for s in spawns:
        if s in standable and s not in reach:
            reach |= maplint._reachable(world, s, standable)
    # Cells a body can be centred on and actually walk out of along a line.
    cells = {
        c for c in reach if nav.floor_under(world, c[0] + 0.5, c[1] + 0.5) is not None
    }

    keys: list[tuple[tuple[int, int], str]] = []
    for i, s in enumerate(cgz.spawns()):
        team = getattr(s, "attr2", None)
        keys.append((nearest_cell(cells, s.x + 0.5, s.y + 0.5), f"spawn_t{team}_{i}"))
    for site in objectives.place(world, cgz).sites:
        keys.append((nearest_cell(cells, site.x, site.y), f"site_{site.id}"))
    for x, y, label in bots.TACTICAL_COVER_NODES.get(name, []):
        c = nearest_cell(cells, x, y)
        moved = math.hypot(c[0] + 0.5 - x, c[1] + 0.5 - y)
        if moved > 3:
            print(f"  cover node {label} moved {moved:.1f} cubes to a standable cell")
        keys.append((c, label))

    nodes: list[list] = []

    def add(x, y, label=""):
        for n in nodes:
            if math.hypot(n[0] - x, n[1] - y) < MERGE:
                if label and not n[2]:
                    n[2] = label
                return
        nodes.append([x, y, label])

    for c, label in keys:
        add(c[0] + 0.5, c[1] + 0.5, label)
    trees = {c: walk_dijkstra(world, cells, c) for c, _ in keys}
    for a, _ in keys:
        dist, prev = trees[a]
        for b, _ in keys:
            if b == a or b not in dist:
                continue
            for x, y in pull(world, route(prev, a, b))[1:-1]:
                add(x, y)

    for cy in range(0, world.ssize, FILL):
        for cx in range(0, world.ssize, FILL):
            c = (cx, cy)
            if c in cells and all(
                (cx + dx, cy + dy) in cells for dx in (-2, 2) for dy in (-2, 2)
            ):
                add(cx + 0.5, cy + 0.5)

    links = []
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if i != j and math.hypot(a[0] - b[0], a[1] - b[1]) <= MAX_LINK:
                if nav.walkable(world, a[0], a[1], b[0], b[1]):
                    links.append([i, j])

    graph = nav.Graph.from_json({"nodes": nodes, "links": links})
    # The runtime's own question, from where things really are: a target only a
    # jump reaches (Dust II's goose) is routed to the nearest node, as in play.
    targets = [(s.x, s.y, f"site_{s.id}") for s in objectives.place(world, cgz).sites]
    targets += list(bots.TACTICAL_COVER_NODES.get(name, []))
    missing = [
        f"spawn ({s.x}, {s.y}) -> {label}"
        for s in cgz.spawns()
        for x, y, label in targets
        if nav.path(world, graph, (s.x + 0.5, s.y + 0.5), (x, y)) is None
    ]

    out = {
        "_doc": "Bot waypoints for this map, in cubes. Built by tools/hassault_nav_build.py; read by backend/modules/hassault/nav.py. links are directed [from, to].",
        "nodes": [[round(n[0], 1), round(n[1], 1), n[2]] for n in nodes],
        "links": links,
    }
    path = nav.NAV_DIR / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    rows = ",\n    ".join(json.dumps(n) for n in out["nodes"])
    link_rows = ",\n    ".join(
        ", ".join(json.dumps(link) for link in out["links"][k : k + 12])
        for k in range(0, len(out["links"]), 12)
    )
    text = f'{{\n  "_doc": {json.dumps(out["_doc"])},\n  "nodes": [\n    {rows}\n  ],\n  "links": [\n    {link_rows}\n  ]\n}}\n'
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{name}: {len(nodes)} nodes, {len(links)} links -> {path}")
    for m in missing:
        print(f"  UNREACHABLE {m}")
    return not missing


if __name__ == "__main__":
    ok = all([build(n) for n in sys.argv[1:]])
    nav.load.cache_clear()
    sys.exit(0 if ok else 1)
