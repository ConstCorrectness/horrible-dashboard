"""Waypoint graphs for bots: where to walk when the straight line is blocked.

A bot steers reactively (`bots._steer`): it heads for what it wants and probes a
step ahead with `physics.can_stand`. That is enough in the open and hopeless in
a building. A bot whose goal is behind a wall walks into the wall, slides along
it, and on Office and Bank some doors were never used at all.

So each defuse map carries a small graph in `nav/<map>.json`, written by
`tools/hassault_nav_build.py` and hand-tunable. It is a handful of points in
cubes (`nodes`) and the directed links between them that a body can walk in a
straight line (`links`). It lives outside `maps/` on purpose: the generators and
`hassault_fit_placements.py` rewrite the map JSON, and a regenerated map must
not lose its graph.

Links are directed because a drop is not a climb. A body can step off a ledge
of any height, but it can only step up `physics.STEP_HEIGHT`. `walkable` is the
one test both the builder and the runtime use, so the graph means exactly what
the simulation allows.

The bot asks for a path only when the straight line to its goal is not
walkable, and then follows it. A* runs on a re-plan, not every tick.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from backend.modules.hassault import physics

NAV_DIR = Path(__file__).parent / "nav"

#: Distance between samples when walking a segment, in cubes. Under half a
#: cube, so a one-cube wall can never fall between two samples.
STEP = 0.45

#: How many of the nearest nodes are tried when joining a point to the graph.
#: The nearest by distance is often through a wall.
JOIN_CANDIDATES = 10


@dataclass(frozen=True, slots=True)
class Graph:
    """One map's waypoints. `succ[i]` lists the nodes walkable from node `i`."""

    nodes: tuple[tuple[float, float, str], ...]
    succ: tuple[tuple[int, ...], ...]
    pred: tuple[tuple[int, ...], ...] = field(default=())

    @classmethod
    def from_json(cls, data: dict) -> Graph:
        nodes = tuple(
            (float(n[0]), float(n[1]), str(n[2]) if len(n) > 2 else "")
            for n in data["nodes"]
        )
        succ: list[list[int]] = [[] for _ in nodes]
        pred: list[list[int]] = [[] for _ in nodes]
        for a, b in data["links"]:
            succ[a].append(b)
            pred[b].append(a)
        return cls(nodes, tuple(map(tuple, succ)), tuple(map(tuple, pred)))

    def node(self, i: int) -> tuple[float, float]:
        return self.nodes[i][0], self.nodes[i][1]

    def nearest(self, x: float, y: float) -> list[int]:
        return sorted(
            range(len(self.nodes)),
            key=lambda i: (self.nodes[i][0] - x) ** 2 + (self.nodes[i][1] - y) ** 2,
        )[:JOIN_CANDIDATES]


@lru_cache(maxsize=32)
def load(map_name: str) -> Graph | None:
    """The graph for a bundled map, or `None` when it has none."""
    path = NAV_DIR / f"{map_name.lower()}.json"
    if not path.is_file():
        return None
    return Graph.from_json(json.loads(path.read_text(encoding="utf-8")))


def floor_under(world: physics.World, x: float, y: float) -> float | None:
    """The floor a body centred here rests on, or `None` where none fits."""
    floor, _ceil, enclosed = physics._support(world, x, y)
    if enclosed or not physics.can_stand(world, x, y, floor):
        return None
    return floor


def walkable(world: physics.World, ax: float, ay: float, bx: float, by: float) -> bool:
    """Whether a body can walk the straight line from `a` to `b`.

    Every sample must fit a standing body on the floor under it, and no sample
    may be more than a step above the one before. Drops are allowed, so
    `walkable(a, b)` and `walkable(b, a)` can differ.
    """
    length = math.hypot(bx - ax, by - ay)
    steps = max(1, math.ceil(length / STEP))
    prev = floor_under(world, ax, ay)
    if prev is None:
        return False
    for k in range(1, steps + 1):
        t = k / steps
        z = floor_under(world, ax + (bx - ax) * t, ay + (by - ay) * t)
        if z is None or z > prev + physics.STEP_HEIGHT:
            return False
        prev = z
    return True


def _join(
    world: physics.World, graph: Graph, x: float, y: float, *, into: bool
) -> int | None:
    """The nearest node a body at `(x, y)` can walk to (or, `into`, from)."""
    for i in graph.nearest(x, y):
        nx, ny = graph.node(i)
        ok = walkable(world, nx, ny, x, y) if into else walkable(world, x, y, nx, ny)
        if ok:
            return i
    return None


def path(
    world: physics.World,
    graph: Graph,
    start: tuple[float, float],
    goal: tuple[float, float],
) -> list[tuple[float, float]] | None:
    """Waypoints from `start` to `goal`, ending at `goal`; `None` if no route.

    A* over the links, weighted by length, from the nearest node `start` can
    walk to, to the nearest node that can walk to `goal`.
    """
    s = _join(world, graph, start[0], start[1], into=False)
    if s is None:
        return None
    g = _join(world, graph, goal[0], goal[1], into=True)
    if g is not None:
        return _astar(graph, s, g, goal)
    # A goal no walk ends at — a site's centre under its table, a ledge only a
    # jump reaches — is approached through the nearest node there is a route
    # to; the bot's own steering (and a jump when it wedges) does the rest.
    for g in graph.nearest(goal[0], goal[1]):
        route = _astar(graph, s, g, graph.node(g))
        if route is not None:
            return route
    return None


def _astar(
    graph: Graph, s: int, g: int, goal: tuple[float, float]
) -> list[tuple[float, float]] | None:
    gx, gy = graph.node(g)
    best = {s: 0.0}
    came: dict[int, int] = {}
    frontier = [(math.hypot(graph.nodes[s][0] - gx, graph.nodes[s][1] - gy), s)]
    while frontier:
        _, i = heapq.heappop(frontier)
        if i == g:
            out = [goal]
            while i in came:
                out.append(graph.node(i))
                i = came[i]
            out.append(graph.node(i))
            out.reverse()
            return out
        ix, iy = graph.node(i)
        for j in graph.succ[i]:
            jx, jy = graph.node(j)
            cost = best[i] + math.hypot(jx - ix, jy - iy)
            if cost < best.get(j, math.inf):
                best[j] = cost
                came[j] = i
                heapq.heappush(frontier, (cost + math.hypot(jx - gx, jy - gy), j))
    return None


def reaches(graph: Graph, a: int, b: int) -> bool:
    """Whether node `b` can be reached from node `a` over the links."""
    seen = {a}
    todo = [a]
    while todo:
        i = todo.pop()
        if i == b:
            return True
        for j in graph.succ[i]:
            if j not in seen:
                seen.add(j)
                todo.append(j)
    return False
