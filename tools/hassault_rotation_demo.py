"""A rotation through a map, as a `.hademo` of one player running it.

    uv run python tools/hassault_rotation_demo.py hd_dust2 out.hademo [--from A --to B]

The route is the one the server's own grid allows (`hassault_route_times`): the
shortest walk from one bomb site to the other, over the cells a body can stand
in, stepping, jumping and dropping where a player would. A map with no sites runs
from the T spawn to the CT one. The player runs it at `MOVE_SPEED`, looking a
little way down the path ahead with the yaw eased rather than snapped, so the
picture turns into corners the way a person's does.

Watch it with the native client and export it as footage:

    hassault-native --replay=out.hademo            # watch
    hassault-native --replay=out.hademo --export   # out.mp4 beside it, 60 fps

Times are milliseconds from the start, angles the wire's (radians, yaw 0 = +x),
positions cubes with `z` the floor the body stands on — `demo.ts`'s format.
"""

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import hassault_route_times as routes  # noqa: E402
from backend.modules.hassault import maplint, physics  # noqa: E402

TICK_RATE = 30
#: Cubes ahead along the path the eye aims at.
LOOKAHEAD = 14.0
#: How quickly the yaw closes on where it is aiming, per second.
TURN_RATE = 4.0
#: Seconds held still at either end, so the footage starts and finishes on a shot.
HOLD_START = 1.0
HOLD_END = 1.8
#: Cubes short of the far end the run stops, to look at it rather than into it.
STOP_SHORT = 10.0
MAPS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "backend",
    "modules",
    "hassault",
    "maps",
)


def on_ground(world, cells, x, y, radius):
    """A site's own floor: the lowest standable cell within its radius, then the
    one nearest its centre. The very nearest cell is often a crate's top, and a
    rotation that ends standing on the cover is not one a player runs."""
    near = [c for c in cells if (c[0] - x) ** 2 + (c[1] - y) ** 2 <= radius * radius]
    if not near:
        return routes.nearest(cells, x, y)
    low = min(world.floor_at(*c) for c in near)
    flat = [c for c in near if world.floor_at(*c) <= low + physics.STEP_HEIGHT]
    return min(flat, key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2)


def endpoints(source, world, cells, origin, target):
    """The cells the route runs between: two sites, or the two teams' spawns."""
    sites = {s["id"]: s for s in source.get("objectives", {}).get("sites", [])}
    if origin in sites and target in sites:
        a, b = sites[origin], sites[target]
        ends = [
            on_ground(world, cells, s["x"], s["y"], float(s.get("radius", 12)))
            for s in (a, b)
        ]
        return ends[0], ends[1], f"{origin} to {target}"
    teams = routes.teams(source)
    t, ct = teams[min(teams)][0], teams[max(teams)][0]
    return routes.nearest(cells, *t), routes.nearest(cells, *ct), "T spawn to CT spawn"


def keep_off_walls(points, cells, reach=4, most=2.5):
    """Each point pushed away from what it cannot stand in nearby.

    The shortest path over a grid hugs every corner it turns — the eye then
    scrapes along a wall a hand away, which nobody running a rotation does. A
    point moves away from the blocked cells round it, inversely with distance,
    by at most `most` cubes, and only onto a cell a body can stand in."""
    out = []
    for x, y in points:
        cx, cy = int(x), int(y)
        px = py = 0.0
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                d2 = dx * dx + dy * dy
                if 0 < d2 <= reach * reach and (cx + dx, cy + dy) not in cells:
                    px -= dx / d2
                    py -= dy / d2
        size = math.hypot(px, py)
        if size > 1e-6:
            scale = min(most, size * 0.8) / size
            nx, ny = x + px * scale, y + py * scale
            if (int(nx), int(ny)) in cells:
                x, y = nx, ny
        out.append((x, y))
    return out


def smooth(points, passes=3):
    """Chaikin corner-cutting: a grid path's stair-steps into curves."""
    for _ in range(passes):
        out = [points[0]]
        for p, q in zip(points, points[1:]):
            out.append((0.75 * p[0] + 0.25 * q[0], 0.75 * p[1] + 0.25 * q[1]))
            out.append((0.25 * p[0] + 0.75 * q[0], 0.25 * p[1] + 0.75 * q[1]))
        out.append(points[-1])
        points = out
    return points


def resample(points, step):
    """Points `step` cubes apart along the polyline."""
    out, carry = [points[0]], 0.0
    for p, q in zip(points, points[1:]):
        seg = math.dist(p, q)
        t = step - carry
        while t <= seg:
            f = t / seg
            out.append((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f))
            t += step
        carry = seg - (t - step)
    if math.dist(out[-1], points[-1]) > step * 0.25:
        out.append(points[-1])
    return out


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def build(name, origin, target, speed):
    source, _, world = routes.load(os.path.join(MAPS_DIR, f"{name}.json"))
    cells = maplint._standable(world)
    start, goal, label = endpoints(source, world, cells, origin, target)
    dist, prev = routes.dijkstra(world, cells, [start])
    if goal not in dist:
        raise SystemExit(f"{name}: no walkable route {label}")
    cells_path = list(reversed(routes.path(prev, goal)))
    xy = smooth(keep_off_walls([(cx + 0.5, cy + 0.5) for cx, cy in cells_path], cells))
    step = speed / TICK_RATE
    track = resample(xy, step)
    # Stop short and look at it: run all the way in and the last shot is a
    # crate's face from a hand away.
    end = xy[-1]
    while len(track) > 2 and math.dist(track[-1], end) < STOP_SHORT:
        track.pop()

    def floor(p):
        # The highest floor the body's footprint covers: on a step's edge it
        # stands on the step, not in it.
        return max(
            world.floor_at(int(p[0] + dx), int(p[1] + dy))
            for dx in (-0.3, 0.3)
            for dy in (-0.3, 0.3)
        )

    def aim(i):
        ahead = min(len(track) - 1, i + max(1, int(LOOKAHEAD / step)))
        a, b = track[i], track[ahead]
        if math.dist(a, b) < 1e-6:
            return None
        return math.atan2(b[1] - a[1], b[0] - a[0])

    frames, yaw, z, vz = [], aim(0) or 0.0, floor(track[0]), 0.0
    hold_a, hold_b = int(HOLD_START * TICK_RATE), int(HOLD_END * TICK_RATE)
    order = [0] * hold_a + list(range(len(track))) + [len(track) - 1] * hold_b
    dt = 1.0 / TICK_RATE
    for tick, i in enumerate(order):
        p = track[i]
        want = (
            aim(i)
            if tick < hold_a + len(track) - 1
            else math.atan2(end[1] - p[1], end[0] - p[0])
        )
        if want is not None:
            yaw = wrap(yaw + wrap(want - yaw) * min(1.0, TURN_RATE * dt))
        # Up a step at once, down a drop under gravity: never below the floor.
        ground = floor(p)
        if ground >= z:
            z, vz = ground, 0.0
        else:
            vz -= physics.GRAVITY * dt
            z = max(ground, z + vz * dt)
            if z == ground:
                vz = 0.0
        frames.append(
            {
                "tick": tick,
                "timestamp": tick * 1000.0 / TICK_RATE,
                "players": [
                    {
                        "id": "rotation",
                        "name": "Rotation",
                        "team": 0,
                        "x": round(p[0], 4),
                        "y": round(p[1], 4),
                        "z": round(z, 4),
                        "yaw": round(yaw, 5),
                        "pitch": -0.04,
                        "weapon": 2,
                        "weaponId": "assault",
                        "hp": 100,
                        "crouch": 0,
                        "alive": True,
                    }
                ],
                "events": [],
            }
        )
    length = sum(math.dist(p, q) for p, q in zip(track, track[1:]))
    demo = {
        "header": {
            "magic": "HADEMO",
            "version": 1,
            "map": name,
            "recordedAt": time.time() * 1000.0,
            "tickRate": TICK_RATE,
            "playerNames": {"rotation": "Rotation"},
            "durationMs": frames[-1]["timestamp"],
            "pov": "rotation",
        },
        "frames": frames,
        "bookmarks": [],
    }
    return demo, label, length


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("map")
    ap.add_argument("out")
    ap.add_argument("--from", dest="origin", default="A")
    ap.add_argument("--to", dest="target", default="B")
    ap.add_argument(
        "--speed", type=float, default=physics.MOVE_SPEED, help="cubes a second"
    )
    args = ap.parse_args()
    demo, label, length = build(args.map, args.origin, args.target, args.speed)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(demo, f, separators=(",", ":"))
    seconds = demo["header"]["durationMs"] / 1000.0
    print(f"{args.map}: {label}, {length:.0f} cubes, {seconds:.1f} s -> {args.out}")


if __name__ == "__main__":
    main()
