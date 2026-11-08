"""Grenade lineups for bots: find them, write them into a map, check them.

    uv run python tools/hassault_lineups.py check [hd_dust2 ...]
    uv run python tools/hassault_lineups.py propose hd_dust2 [--write]

A lineup (`bots.Lineup`, the map JSON's `lineups` key) is a spot and an angle an
attacker throws a smoke or a flash from on its way to a site. Bots throw it
through `Command.throw`, so where it lands is whatever the server's grenade
physics makes of that spot and angle — which is what this simulates, with the
server's own `throw_origin`, `throw_velocity` and `step_grenade`, at the live
tick rate. `check` prints how far each declared lineup lands from its `land`
point (`test_hassault_bot_teamplay.py` holds the same rule).

`propose` makes a few per site from the map itself: for each of the attackers'
two most separate walking routes to a site, a staging spot some cubes of walking
short of the site (`STAGE_TRIES`); from it a smoke at the defenders' way in to the
site (`SMOKE_BACK` cubes up the defenders' own shortest route), and on the
first route a flash over the site. Each angle is searched for, and a lineup
whose grenade cannot be made to land within `TOLERANCE` is dropped. With
`--write` the result replaces the map JSON's `lineups`.

Lineups go stale when a map's geometry changes: run `check` after a re-bake.
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.modules.hassault import (  # noqa: E402
    assets,
    bots,
    grenades,
    maplint,
    mapsource,
    nav,
    physics,
)
from backend.modules.hassault.modes import objectives  # noqa: E402

#: The live server's tick: a grenade's fuse runs out on a tick boundary.
TICK = 1 / 60
#: A lineup must land this close to its point, in cubes.
TOLERANCE = 3.0
#: Walking cubes short of a site's edge the attackers stage at, in the order
#: tried.
STAGE_TRIES = (26, 20, 32, 38, 15)
#: Walking cubes up the defenders' route from a site's edge that a smoke lands.
SMOKE_BACK = 9
#: Second routes must keep this far from the first, as in `hassault_route_times`.
SEPARATION = 14


def eye_height() -> float:
    return physics.eye_height(physics.PlayerState())


def land(world, x, y, yaw, pitch, kind, power=None):
    """Where a grenade thrown standing still at `(x, y)` goes off: `(x, y, z)`."""
    floor = nav.floor_under(world, x, y)
    if floor is None:
        return None
    spec = grenades.BY_ID[kind]
    ox, oy, oz = grenades.throw_origin(x, y, floor + eye_height(), yaw, pitch)
    vx, vy, vz = grenades.throw_velocity(yaw, pitch, False, (0.0, 0.0, 0.0), power)
    nade = grenades.Grenade("probe", spec, "", 0, ox, oy, oz, vx, vy, vz, spec.fuse)
    while nade.fuse > 0:
        grenades.step_grenade(world, nade, TICK)
    return nade.x, nade.y, nade.z


def solve(world, x, y, tx, ty, kind):
    """Yaw and pitch (radians) whose grenade lands nearest `(tx, ty)`, and the miss."""
    bearing = math.atan2(ty - y, tx - x)
    best = (math.inf, 0.0, 0.0)
    for dyaw in [k * 0.02 for k in range(-20, 21)]:
        for pitch in [k * 0.03 for k in range(-6, 36)]:
            at = land(world, x, y, bearing + dyaw, pitch, kind)
            if at is None:
                return None
            miss = math.hypot(at[0] - tx, at[1] - ty)
            if miss < best[0]:
                best = (miss, bearing + dyaw, pitch)
    # Refine round the best.
    miss, yaw0, pitch0 = best
    for dyaw in [k * 0.004 for k in range(-6, 7)]:
        for dp in [k * 0.006 for k in range(-6, 7)]:
            at = land(world, x, y, yaw0 + dyaw, pitch0 + dp, kind)
            m = math.hypot(at[0] - tx, at[1] - ty)
            if m < best[0]:
                best = (m, yaw0 + dyaw, pitch0 + dp)
    return best


def _cells(world, cgz):
    from hassault_nav_build import nearest_cell, walk_dijkstra

    fits = {
        c
        for c in maplint._standable(world)
        if nav.floor_under(world, c[0] + 0.5, c[1] + 0.5) is not None
    }
    return fits, nearest_cell, walk_dijkstra


def _routes(world, fits, starts, site, nearest_cell, walk_dijkstra, count):
    """Up to `count` separate walking routes (cell lists) from `starts` to `site`."""
    from hassault_nav_build import route as unroll

    out = []
    banned: set = set()
    for _ in range(count):
        cells = fits - banned
        best = None
        for s in starts:
            if s not in cells:
                continue
            dist, prev = walk_dijkstra(world, cells, s)
            # The reached cell nearest the site's centre: the centre itself
            # may be under a table nobody walks onto.
            on = [
                c
                for c in dist
                if math.hypot(c[0] - site.x, c[1] - site.y) <= site.radius
            ]
            if not on:
                continue
            goal = min(on, key=lambda c: math.hypot(c[0] - site.x, c[1] - site.y))
            if best is None or dist[goal] < best[0]:
                best = (dist[goal], unroll(prev, s, goal))
        if best is None:
            break
        out.append(best[1])
        for cx, cy in best[1]:
            # The ends are shared by every route: leave them open.
            if math.hypot(cx - site.x, cy - site.y) < site.radius + SEPARATION or any(
                math.hypot(cx - s[0], cy - s[1]) < SEPARATION * 2 for s in starts
            ):
                continue
            for dx in range(-SEPARATION, SEPARATION + 1):
                for dy in range(-SEPARATION, SEPARATION + 1):
                    if dx * dx + dy * dy <= SEPARATION * SEPARATION:
                        banned.add((cx + dx, cy + dy))
    return out


def _back_from_site(path, site, back):
    """The cell `back` cubes of walking before `path` enters the site."""
    edge = next(
        (
            i
            for i, c in enumerate(path)
            if math.hypot(c[0] - site.x, c[1] - site.y) <= site.radius
        ),
        len(path) - 1,
    )
    walked = 0.0
    i = edge
    while i > 0 and walked < back:
        a, b = path[i], path[i - 1]
        walked += math.hypot(a[0] - b[0], a[1] - b[1])
        i -= 1
    return path[i]


#: A smoke must land this far from every attacker route, or it blinds the
#: attackers' own way in rather than the defenders'.
CLEAR_OF_T = 10.0


def _clear_of(ct_path, site, t_paths):
    """Cells of the defenders' route, walking back from the site, at least
    `SMOKE_BACK` cubes out and `CLEAR_OF_T` from every attacker route, a few
    cubes apart: the candidates for where a smoke lands."""
    t_cells = [c for p in t_paths for c in p]
    edge = next(
        (
            i
            for i, c in enumerate(ct_path)
            if math.hypot(c[0] - site.x, c[1] - site.y) <= site.radius
        ),
        len(ct_path) - 1,
    )
    walked = 0.0
    out = []
    for i in range(edge, 0, -1):
        a, b = ct_path[i], ct_path[i - 1]
        walked += math.hypot(a[0] - b[0], a[1] - b[1])
        if walked > 45 or len(out) >= 4:
            break
        if (
            walked >= SMOKE_BACK
            and all(math.hypot(b[0] - c[0], b[1] - c[1]) >= CLEAR_OF_T for c in t_cells)
            and all(math.hypot(b[0] - o[0], b[1] - o[1]) >= 4 for o in out)
        ):
            out.append(b)
    return out


def _label(name, x, y, fallback):
    near = [
        (math.hypot(cx - x, cy - y), label)
        for cx, cy, label in bots.TACTICAL_COVER_NODES.get(name, [])
    ]
    near.sort()
    return near[0][1] if near and near[0][0] < 30 else fallback


def propose(name):
    cgz = assets.load_map(name)
    world = physics.World.from_map(cgz)
    fits, nearest_cell, walk_dijkstra = _cells(world, cgz)
    sites = objectives.place(world, cgz).sites
    # In a bomb round the attackers spawn at `ATTACKER_SPAWNS` whatever their team.
    from backend.modules.hassault.modes.defuse import ATTACKER_SPAWNS

    ends = {
        e: [
            nearest_cell(fits, s.x + 0.5, s.y + 0.5)
            for s in cgz.spawns()
            if s.attr2 == e
        ]
        for e in (0, 1)
    }
    t_starts, ct_starts = ends[ATTACKER_SPAWNS], ends[1 - ATTACKER_SPAWNS]
    out = []
    for site in sites:
        used: set[str] = set()
        t_routes = _routes(world, fits, t_starts, site, nearest_cell, walk_dijkstra, 2)
        ct_routes = _routes(
            world, fits, ct_starts, site, nearest_cell, walk_dijkstra, 1
        )
        smokes = _clear_of(ct_routes[0], site, t_routes) if ct_routes else []
        for r, path in enumerate(t_routes):
            # One staging spot per route: the first, from those tried, that
            # throws everything it wants; else the one that throws most.
            wants = [("smoke", [(c[0] + 0.5, c[1] + 0.5) for c in smokes])]
            if r == 0:
                ring = [(site.x, site.y)] + [
                    (
                        site.x + math.cos(a) * site.radius * 0.4,
                        site.y + math.sin(a) * site.radius * 0.4,
                    )
                    for a in (0, math.pi / 2, math.pi, 3 * math.pi / 2)
                ]
                wants.append(("flash", ring))
            best_stage = None
            for back in STAGE_TRIES:
                cx, cy = _back_from_site(path, site, back)
                sx, sy = cx + 0.5, cy + 0.5
                got = []
                for kind, targets in wants:
                    for tx, ty in targets:
                        found = solve(world, sx, sy, tx, ty, kind)
                        if found is not None and found[0] <= TOLERANCE:
                            got.append((kind, tx, ty, found))
                            break
                if best_stage is None or len(got) > len(best_stage[2]):
                    best_stage = (sx, sy, got)
                if len(got) == len(wants):
                    break
            sx, sy, got = best_stage
            route = _label(name, sx, sy, f"route{r + 1}")
            if route in used:
                route = f"{route}_{r + 1}"
            used.add(route)
            if len(got) < len(wants):
                print(
                    f"  {name} {site.id} {route}: only {[g[0] for g in got]} of "
                    f"{[w[0] for w in wants]} land within {TOLERANCE} from any spot tried"
                )
            for kind, tx, ty, found in got:
                miss, yaw, pitch = found
                at = land(world, sx, sy, yaw, pitch, kind)
                out.append(
                    {
                        "id": f"{site.id.lower()}_{route}_{kind}",
                        "site": site.id,
                        "kind": kind,
                        "route": route,
                        "from": [round(sx, 1), round(sy, 1)],
                        "yaw": round(math.degrees(yaw), 2),
                        "pitch": round(math.degrees(pitch), 2),
                        "land": [round(at[0], 1), round(at[1], 1)],
                    }
                )
                print(
                    f"  {name} {site.id} {route:16s} {kind:5s} from ({sx:.0f},{sy:.0f}) "
                    f"lands ({at[0]:.0f},{at[1]:.0f}) aimed at ({tx:.0f},{ty:.0f}), miss {miss:.1f}"
                )
    return out


def check(name):
    cgz = assets.load_map(name)
    world = physics.World.from_map(cgz)
    bad = []
    for lu in bots.lineups(name):
        at = land(world, lu.x, lu.y, lu.yaw, lu.pitch, lu.kind, lu.power)
        miss = (
            math.inf
            if at is None
            else math.hypot(at[0] - lu.land[0], at[1] - lu.land[1])
        )
        flag = "" if miss <= TOLERANCE else "   <-- OFF"
        print(f"  {name} {lu.id:28s} miss {miss:5.1f}{flag}")
        if miss > TOLERANCE:
            bad.append(lu.id)
    return bad


def write(name, rows):
    """Replace the map JSON's `lineups`, keeping the rest of the file as it is."""
    from hassault_fit_placements import dump

    path = mapsource.source_path(name)
    with open(path, encoding="utf-8") as f:
        source = json.load(f)
    source["lineups"] = rows
    text = dump(source) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    bots.lineups.cache_clear()


def main():
    cmd, names = sys.argv[1], [a for a in sys.argv[2:] if not a.startswith("--")]
    if cmd == "propose":
        for name in names:
            rows = propose(name)
            if "--write" in sys.argv:
                write(name, rows)
                print(f"{name}: wrote {len(rows)} lineups")
    elif cmd == "check":
        names = names or [n for n in mapsource.bundled_names() if bots.lineups(n)]
        bad = [b for n in names for b in check(n)]
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
