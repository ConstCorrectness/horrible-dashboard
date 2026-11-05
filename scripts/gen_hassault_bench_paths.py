"""Generate the shared camera path both clients fly for a frame-time benchmark.

    PYTHONPATH=. uv run python scripts/gen_hassault_bench_paths.py

Writes `packages/core/src/modules/hassault/bench-paths.json`, which the
browser's `bench` console command (`bench.ts`) and the native client's
`--bench <map>` flag (`apps/native-fps/src/bench.rs`) both replay. One path, two
clients: a frame-time number from one is only comparable with the other's if
both looked at the same things for the same time.

The framings are the key-art ones (`tools/key-art/hassault-maps/README.md`): for
each target — the A and B sites where a map has them, else the map centre and
its two farthest-apart spawns, plus the midpoint between the teams' spawns — a
look from a spawn at eye level, a full turn on the target itself (the worst case
for a renderer: everything around a site is in view at some point), and a look
down from twelve cubes over the other team's spawn.

Coordinates are **map cubes**: `x`/`y` on the plan, `z` the absolute eye height,
the convention the server and both clients' physics already share. Floors come
from `physics._support`, the same query the server places bodies with, so a
camera never starts inside a wall or under a floor.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from backend.modules.hassault import mapsource, physics
from backend.modules.hassault.physics import place_body

MAPS = Path("backend/modules/hassault/maps")
#: Beside `glb-surfaces.json`, the other data file both clients read: the
#: browser bundles it (`bench.ts`), the native client `include_str!`s it.
OUT = Path("packages/core/src/modules/hassault/bench-paths.json")

#: Eye above the floor, standing. Matches the server's resting eye.
EYE = 4.5
#: The key-art `hi` framing: twelve cubes up, tilted down.
HIGH = 12.0
#: Seconds each still framing is held, and each turn takes.
HOLD = 2.0
TURN = 4.0


def _eye(world: physics.World, x: float, y: float) -> list[float]:
    px, py, floor = place_body(world, x, y)
    return [round(px, 2), round(py, 2), round(floor + EYE, 2)]


def _look(frm: list[float], at: list[float]) -> tuple[float, float]:
    """Yaw (0 = +x, 90 = +y, the server's convention) and pitch, in degrees."""
    dx, dy, dz = at[0] - frm[0], at[1] - frm[1], at[2] - frm[2]
    yaw = math.degrees(math.atan2(dy, dx)) % 360.0
    pitch = math.degrees(math.atan2(dz, max(math.hypot(dx, dy), 1e-6)))
    return round(yaw, 2), round(pitch, 2)


def _targets(source: dict, world: physics.World) -> list[tuple[str, float, float]]:
    spawns = source.get("spawns") or []
    by_team: dict[int, list[dict]] = {}
    for s in spawns:
        by_team.setdefault(int(s.get("team", 0)), []).append(s)
    sites = (source.get("objectives") or {}).get("sites") or []
    out: list[tuple[str, float, float]] = [
        (f"site {s['id']}", float(s["x"]), float(s["y"])) for s in sites
    ]
    if not out and spawns:
        xs = [float(s["x"]) for s in spawns]
        ys = [float(s["y"]) for s in spawns]
        out.append(("centre", (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        a, b = max(
            ((p, q) for p in spawns for q in spawns),
            key=lambda pq: math.hypot(pq[0]["x"] - pq[1]["x"], pq[0]["y"] - pq[1]["y"]),
        )
        out.append(("corner 1", float(a["x"]), float(a["y"])))
        out.append(("corner 2", float(b["x"]), float(b["y"])))
    if len(by_team) >= 2:
        t0, t1 = by_team[min(by_team)][0], by_team[max(by_team)][0]
        out.append(("mid", (t0["x"] + t1["x"]) / 2, (t0["y"] + t1["y"]) / 2))
    return out


def path_for(source: dict, world: physics.World) -> list[dict]:
    spawns = source.get("spawns") or []
    teams = sorted({int(s.get("team", 0)) for s in spawns}) or [0]
    first = (
        {t: next(s for s in spawns if int(s.get("team", 0)) == t) for t in teams}
        if spawns
        else {}
    )
    keys: list[dict] = []
    for i, (label, tx, ty) in enumerate(_targets(source, world)):
        target = _eye(world, tx, ty)
        near = first[teams[i % len(teams)]] if first else {"x": tx, "y": ty}
        far = first[teams[(i + 1) % len(teams)]] if first else {"x": tx, "y": ty}
        lo = _eye(world, near["x"] + 0.5, near["y"] + 0.5)
        yaw, pitch = _look(lo, target)
        keys.append(
            {
                "label": f"{label} lo",
                "eye": lo,
                "yaw": yaw,
                "pitch": pitch,
                "seconds": HOLD,
                "spin": 0,
            }
        )
        keys.append(
            {
                "label": f"{label} turn",
                "eye": target,
                "yaw": 0.0,
                "pitch": 0.0,
                "seconds": TURN,
                "spin": round(360.0 / TURN, 2),
            }
        )
        hi = _eye(world, far["x"] + 0.5, far["y"] + 0.5)
        hi[2] = round(hi[2] + HIGH, 2)
        yaw, pitch = _look(hi, target)
        keys.append(
            {
                "label": f"{label} hi",
                "eye": hi,
                "yaw": yaw,
                "pitch": pitch,
                "seconds": HOLD,
                "spin": 0,
            }
        )
    return keys


def main() -> None:
    paths: dict[str, list[dict]] = {}
    for path in sorted(MAPS.glob("hd_*.json")):
        source = json.loads(path.read_text(encoding="utf-8"))
        world = physics.World.from_map(mapsource.build(source, name=path.name))
        paths[path.stem] = path_for(source, world)
    doc = {
        "why": (
            "The camera path both clients fly for `bench <map>` / `--bench <map>`. "
            "Generated by scripts/gen_hassault_bench_paths.py from the map JSONs; do not edit."
        ),
        "units": "map cubes; eye is [x, y, z] with z the absolute eye height; yaw 0 = +x, 90 = +y; degrees; spin in deg/s",
        "warmupSeconds": 2.0,
        "maps": paths,
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    total = {k: round(sum(f["seconds"] for f in v), 1) for k, v in paths.items()}
    print(f"wrote {OUT} for {len(paths)} maps; seconds per map: {total}")


if __name__ == "__main__":
    main()
