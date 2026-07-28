"""Generate the cross-language physics conformance fixture for HorribleAssault.

    PYTHONPATH=. uv run python scripts/gen_hassault_vectors.py

The output is committed and replayed by *both*
`backend/tests/test_hassault_physics.py` and
`packages/core/src/modules/hassault/__tests__/conformance.test.ts`, which is what
keeps the server's `physics.py` and the browser's `player.ts`/`world.ts` from
drifting apart. See docs/modules/hassault.mdx.

Expectations are generated from the Python side, so this pins *agreement*, not
correctness — each suite's own unit tests are the argument that the rules are
right. Regenerate only when a movement rule genuinely changes, change both
implementations in the same commit, and make both suites pass before committing.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.hassault.physics import (
    MoveInput,
    PlayerState,
    World,
    spawn_at,
    step,
)

OUT = Path("packages/core/src/modules/hassault/__tests__/physics-vectors.json")

WORLDS = {
    # A plain room, floor 0, ceiling 16. Twelve cells across, so the 2.2-cube
    # body has somewhere to walk.
    "room": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 16}
        ],
    },
    # The same room with the eastern half raised one step, and a two-cube ledge
    # beyond it that the player cannot climb.
    "steps": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 16},
            {"x0": 8, "y0": 2, "x1": 10, "y1": 13, "type": 4, "floor": 1, "ceil": 16},
            {"x0": 11, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 4, "ceil": 16},
        ],
    },
    # A floor heightfield descending eastward. Each column carries a different
    # vdelta, so a body spanning three cells samples three different corner sums
    # and the /16 divisor is what decides the height it stands at.
    "slope": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 8, "ceil": 24},
        ]
        + [
            {
                "x0": x,
                "y0": 2,
                "x1": x,
                "y1": 13,
                "type": 2,
                "floor": 8,
                "ceil": 24,
                "vdelta": 2 * (x - 3),
            }
            for x in range(4, 12)
        ],
    },
    # A corridor two open cells wide in y — narrower than the body needs, so
    # every lateral move is refused. Pins the clearance rule across languages.
    "corridor": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 6, "x1": 13, "y1": 7, "type": 4, "floor": 0, "ceil": 16}
        ],
    },
}


def build(spec) -> World:
    ssize = spec["ssize"]
    n = ssize * ssize
    types = bytearray([0]) * n  # SOLID
    floor = bytearray(n)
    ceil = bytearray([16]) * n
    vdelta = bytearray(n)
    for rect in spec["rects"]:
        for y in range(rect["y0"], rect["y1"] + 1):
            for x in range(rect["x0"], rect["x1"] + 1):
                i = y * ssize + x
                types[i] = rect.get("type", 4)
                floor[i] = rect.get("floor", 0) & 0xFF
                ceil[i] = rect.get("ceil", 16) & 0xFF
                vdelta[i] = rect.get("vdelta", 0)
    return World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes(floor),
        ceil=bytes(ceil),
        vdelta=bytes(vdelta),
    )


def repeat(count, **kw):
    return [dict(kw) for _ in range(count)]


CASES = [
    {
        "name": "walks east across a flat room",
        "world": "room",
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0},
        "steps": repeat(20, forward=1.0, dt=1 / 60),
    },
    {
        "name": "stops against the far wall",
        "world": "room",
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0},
        "steps": repeat(120, forward=1.0, dt=1 / 60),
    },
    {
        "name": "slides north along the east wall",
        "world": "room",
        "start": {"x": 11.0, "y": 8.0, "z": 0.0, "yaw": 0.7},
        "steps": repeat(60, forward=1.0, dt=1 / 60),
    },
    {
        "name": "jump arc returns to the floor",
        "world": "room",
        "start": {"x": 8.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": [{"jump": True, "dt": 1 / 60}] + repeat(50, dt=1 / 60),
    },
    {
        "name": "walks up a one-unit step",
        "world": "steps",
        "start": {"x": 5.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(30, forward=1.0, dt=1 / 60),
    },
    {
        "name": "is refused by a four-unit ledge",
        "world": "steps",
        "start": {"x": 5.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(120, forward=1.0, dt=1 / 60),
    },
    {
        "name": "walks down a heightfield ramp",
        "world": "slope",
        "start": {"x": 4.0, "y": 8.0, "z": 8.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(18, forward=1.0, dt=1 / 60),
    },
    {
        "name": "walks up the same ramp the other way",
        "world": "slope",
        "start": {
            "x": 10.0,
            "y": 8.0,
            "z": 5.0,
            "yaw": 3.141592653589793,
            "on_ground": True,
        },
        "steps": repeat(18, forward=1.0, dt=1 / 60),
    },
    {
        "name": "falls onto the floor under gravity",
        "world": "room",
        "start": {"x": 8.0, "y": 8.0, "z": 9.0},
        "steps": repeat(60, dt=1 / 60),
    },
    {
        "name": "a huge dt is clamped, not integrated",
        "world": "room",
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0},
        "steps": [{"forward": 1.0, "dt": 5.0}],
    },
    {
        "name": "strafes right at an angle",
        "world": "room",
        "start": {"x": 8.0, "y": 8.0, "z": 0.0, "yaw": 1.2},
        "steps": repeat(20, strafe=1.0, dt=1 / 60),
    },
    {
        "name": "cannot move sideways in a two-cell corridor",
        "world": "corridor",
        "start": {"x": 5.0, "y": 7.0, "z": 0.0, "yaw": 1.5707963267948966},
        "steps": repeat(30, forward=1.0, dt=1 / 60),
    },
]


class Spawn:
    """The four fields `spawn_at` reads off a `playerstart` entity."""

    def __init__(self, x: float, y: float, z: float, yaw: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw


# Spawn placement is duplicated across the two implementations exactly like
# `step` is, so it is pinned here for the same reason. The cases are chosen
# around the one thing that is easy to get wrong — that a `playerstart`'s `z` is
# the mapper's eye at placement time and not a ground height, so the world
# decides where the feet go.
SPAWN_CASES = [
    {
        "name": "a spawn floating above flat ground stands on the ground",
        "world": "room",
        "entity": {"x": 8, "y": 8, "z": 12},
    },
    {
        "name": "a spawn buried below the floor is lifted onto it",
        "world": "room",
        "entity": {"x": 8, "y": 8, "z": -3},
    },
    {
        "name": "entity z does not change the height at all",
        "world": "room",
        "entity": {"x": 8, "y": 8, "z": 99},
    },
    {
        "name": "a spawn over the raised half stands on the raised half",
        "world": "steps",
        "entity": {"x": 9, "y": 8, "z": 20},
    },
    {
        "name": "a spawn over the four-unit ledge stands on the ledge",
        "world": "steps",
        "entity": {"x": 12, "y": 8, "z": 2},
    },
    {
        "name": "a body straddling a step is placed on the higher floor",
        "world": "steps",
        "entity": {"x": 7, "y": 8, "z": 6},
    },
    {
        "name": "a spawn on a heightfield takes the corner-averaged floor",
        "world": "slope",
        "entity": {"x": 6, "y": 8, "z": 30},
    },
    {
        "name": "entity yaw is degrees clockwise, converted to radians",
        "world": "room",
        "entity": {"x": 8, "y": 8, "z": 4, "yaw": 90.0},
    },
]


def main() -> None:
    out_cases = []
    for case in CASES:
        world = build(WORLDS[case["world"]])
        s = case["start"]
        player = PlayerState(
            x=s["x"],
            y=s["y"],
            z=s["z"],
            vel_z=s.get("vel_z", 0.0),
            yaw=s.get("yaw", 0.0),
            pitch=s.get("pitch", 0.0),
            on_ground=s.get("on_ground", False),
        )
        for raw in case["steps"]:
            if "yaw" in raw:
                player.yaw = raw["yaw"]
            move = MoveInput(
                forward=raw.get("forward", 0.0),
                strafe=raw.get("strafe", 0.0),
                jump=raw.get("jump", False),
            )
            step(world, player, move, raw["dt"])
        out_cases.append(
            {
                **case,
                "expect": {
                    "x": player.x,
                    "y": player.y,
                    "z": player.z,
                    "velZ": player.vel_z,
                    "onGround": player.on_ground,
                },
            }
        )

    out_spawns = []
    for case in SPAWN_CASES:
        world = build(WORLDS[case["world"]])
        e = case["entity"]
        placed = spawn_at(world, Spawn(e["x"], e["y"], e["z"], e.get("yaw", 0.0)))
        out_spawns.append(
            {
                **case,
                "expect": {
                    "x": placed.x,
                    "y": placed.y,
                    "z": placed.z,
                    "yaw": placed.yaw,
                    "onGround": placed.on_ground,
                },
            }
        )

    payload = {
        "_comment": (
            "Cross-language physics conformance vectors. Read by BOTH "
            "backend/tests/test_hassault_physics.py and "
            "packages/core/src/modules/hassault/__tests__/conformance.test.ts. "
            "These pin that the two implementations agree, not that either is "
            "correct in the abstract - correctness is what each side's own unit "
            "tests are for. Regenerate only when a rule genuinely changes, and "
            "make both suites pass before committing."
        ),
        "tolerance": 1e-9,
        "worlds": WORLDS,
        "cases": out_cases,
        "spawns": out_spawns,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT} with {len(out_cases)} step cases, {len(out_spawns)} spawn cases"
    )
    for c in out_cases:
        e = c["expect"]
        print(
            f"  {c['name']:<48} x={e['x']:.4f} y={e['y']:.4f} z={e['z']:.4f} g={e['onGround']}"
        )
    print("  --- spawns ---")
    for c in out_spawns:
        e = c["expect"]
        print(
            f"  {c['name']:<58} entz={c['entity']['z']:>3} -> "
            f"x={e['x']:.2f} y={e['y']:.2f} z={e['z']:.4f} yaw={e['yaw']:.4f}"
        )


if __name__ == "__main__":
    main()
