"""Move a baked map's spawns, items and bomb sites onto floor players can reach.

Run after `tools/blender/bake_collision.py`:

    uv run python tools/hassault_fit_placements.py hd_bank [hd_mirage ...]

The placements in a map JSON were drawn against the old hand-sketched brush
world, or copied from the client tables (which are checked against the GLB with
Rapier, a different body on different geometry). Against a baked world a few of
them land inside a counter, on a crate top, or in a corner a 2.2-wide body cannot
stand in. This asks exactly the questions `maplint` asks — `spawn_at` then
`can_stand`, reachability from the first spawn, the site/spawn margin — and moves
each offender to the nearest cell that passes, breadth first, so nothing moves
further than it has to. Anything already valid is left exactly where it is.
"""

from __future__ import annotations

import json
import math
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.modules.hassault import maplint, mapsource, physics, pickups  # noqa: E402
from backend.modules.hassault.cgz import PLAYERSTART, MapEntity  # noqa: E402

MAPS = ROOT / "backend" / "modules" / "hassault" / "maps"


def dump(v, ind: int = 0) -> str:
    """indent=2 JSON, but a row of scalars stays on one line (the files' style)."""
    flat = json.dumps(v, separators=(", ", ": "))
    if not isinstance(v, (dict, list)):
        return flat
    kids = v.values() if isinstance(v, dict) else v
    if len(flat) <= 90 and all(not isinstance(x, (dict, list)) for x in kids):
        return flat
    pad, nl = "  " * (ind + 1), "\n"
    if isinstance(v, dict):
        body = ("," + nl).join(
            f"{pad}{json.dumps(k)}: {dump(x, ind + 1)}" for k, x in v.items()
        )
        return "{" + nl + body + nl + "  " * ind + "}"
    body = ("," + nl).join(f"{pad}{dump(x, ind + 1)}" for x in v)
    return "[" + nl + body + nl + "  " * ind + "]"


def nearest(start: tuple[int, int], ok) -> tuple[int, int]:
    queue, seen = deque([start]), {start}
    while queue:
        cell = queue.popleft()
        if ok(cell):
            return cell
        x, y = cell
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n not in seen and 0 <= n[0] < 256 and 0 <= n[1] < 256:
                seen.add(n)
                queue.append(n)
    raise LookupError(f"nowhere valid near {start}")


def fit(name: str) -> list[str]:
    path = MAPS / f"{name}.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    world = mapsource.build(source, name=name)
    sim = physics.World.from_map(world)
    cells = maplint._standable(sim)
    moved: list[str] = []

    def stands(cell) -> bool:
        """`maplint`'s spawn question: rest where `spawn_at` puts you, then fit?"""
        spawn = MapEntity(type=PLAYERSTART, name="playerstart", x=cell[0], y=cell[1], z=0)
        state = physics.spawn_at(sim, spawn)
        return physics.can_stand(sim, state.x, state.y, state.z)

    starts = [e for e in source["entities"] if e["type"] == "playerstart"]
    # The first spawn anchors reachability, so it must itself be valid first.
    first = starts[0]
    if (first["x"], first["y"]) not in cells or not stands((first["x"], first["y"])):
        cell = nearest((first["x"], first["y"]), lambda c: c in cells and stands(c))
        moved.append(f"spawn 0 {first['x'], first['y']} -> {cell}")
        first["x"], first["y"] = cell
    reached = maplint._reachable(
        sim, (first["x"], first["y"]), cells, maplint.JUMP_CLIMB
    )

    for i, s in enumerate(starts[1:], 1):
        here = (s["x"], s["y"])
        if here in reached and stands(here):
            continue
        cell = nearest(here, lambda c: c in reached and stands(c))
        moved.append(f"spawn {i} {here} -> {cell}")
        s["x"], s["y"] = cell
    spawn_xy = [(s["x"] + 0.5, s["y"] + 0.5) for s in starts]

    def item_ok(c) -> bool:
        if c not in reached:
            return False
        z = sim.floor_at(*c)
        if not physics.can_stand(sim, c[0] + 0.5, c[1] + 0.5, z):
            return False
        return all(
            math.dist((c[0] + 0.5, c[1] + 0.5), s) > pickups.PICKUP_RADIUS + 1
            for s in spawn_xy
        )

    for e in source["entities"]:
        if e["type"] in ("playerstart", "ladder", "ctf_flag"):
            continue
        here = (int(e["x"]), int(e["y"]))
        if item_ok(here):
            continue
        cell = nearest(here, item_ok)
        moved.append(f"{e['type']} {here} -> {cell}")
        e["x"], e["y"] = cell
        e["z"] = int(sim.floor_at(*cell))

    for site in (source.get("objectives") or {}).get("sites", []):
        margin = site.get("radius", mapsource.SITE_RADIUS) + pickups.PICKUP_RADIUS

        def site_ok(c, margin=margin) -> bool:
            return c in reached and all(
                math.dist((c[0], c[1]), (sx - 0.5, sy - 0.5)) >= margin
                for sx, sy in spawn_xy
            )

        here = (int(site["x"]), int(site["y"]))
        if site_ok(here):
            continue
        cell = nearest(here, site_ok)
        moved.append(f"site {site['id']} {here} -> {cell}")
        site["x"], site["y"] = cell

    source["spawns"] = [
        {k: s[k] for k in ("x", "y", "z", "yaw", "team") if k in s} for s in starts
    ]
    path.write_text(dump(source) + "\n", encoding="utf-8")
    return moved


if __name__ == "__main__":
    for map_name in sys.argv[1:]:
        for line in fit(map_name):
            print(f"{map_name}: {line}")
