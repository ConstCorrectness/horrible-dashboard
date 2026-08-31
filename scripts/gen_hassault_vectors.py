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
import math
from pathlib import Path

from backend.modules.hassault.physics import (
    LADDER_ENTITY,
    NO_WATER,
    MoveInput,
    PlayerState,
    World,
    ladders_from,
    apply_impulse,
    spawn_at,
    step,
)
from backend.modules.hassault import hitbox
from backend.modules.hassault.weapons import (
    BODY_HEIGHT,
    WEAPON_BY_ID as weapon_by_id,
    WEAPONS,
    aim_vector,
    apply_spray,
    ray_hits_body,
    raycast_world_face,
    residual_spread,
    spray_offset,
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
    # A room split by a band of low ceiling: 5 cubes, which a crouched body
    # (4.075) fits under and a standing one (5.2) does not. The one world that
    # can tell the two heights apart, so it pins both halves of crouching —
    # getting through, and not being able to stand up once you are in there.
    "vent": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 16},
            {"x0": 7, "y0": 2, "x1": 9, "y1": 13, "type": 4, "floor": 0, "ceil": 5},
        ],
    },
    # A tall room. Long drops need somewhere to happen, and the gravity ramp is
    # only visible over more height than the standard room has.
    "shaft": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 120}
        ],
    },
    # A flooded room. Floor at 0, water at 6 — deep enough that a standing body
    # (5.2) is fully under, which is what separates swimming from wading, and
    # shallow enough that the two states are both reachable in one world.
    "pool": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 32}
        ],
        "waterlevel": 6,
    },
    # A tall room with water only at the bottom of a long drop: the case water
    # exists for. The fall is long enough to be lethal dry.
    "well": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 120}
        ],
        "waterlevel": 8,
    },
    # A tall room with a ladder up one wall. The ladder is an *entity*, so both
    # sides run their own `ladders_from` / `laddersFrom` on it — the derivation
    # is part of what these vectors pin, not a number handed to both.
    "climb": {
        "ssize": 16,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 13, "y1": 13, "type": 4, "floor": 0, "ceil": 60},
            # A gallery 24 cubes up along the west side, which is what the ladder
            # is *for*: a case that only reaches the top proves the clamp, and a
            # case that steps off it proves `LADDER_TOP_RELEASE`.
            {"x0": 2, "y0": 2, "x1": 7, "y1": 13, "type": 4, "floor": 24, "ceil": 60},
        ],
        # One cell clear of the gallery edge. Any closer and the 2.2-cube body
        # standing at the ladder's foot already overlaps a gallery cell, and
        # `_support` would rest it 24 cubes up without the ladder being involved
        # at all — which would make the ladder redundant in exactly the cases
        # meant to exercise it. Any further and stepping off the top is a jump.
        "ladders": [{"x": 9, "y": 8, "height": 24}],
    },
    # Open ground, sixty cubes across. Momentum cases need room to run: at 22
    # cubes a second a one-second case crosses the 16-cube room twice over, and a
    # case that ends against a wall measures the wall, not the movement.
    "field": {
        "ssize": 64,
        "rects": [
            {"x0": 2, "y0": 2, "x1": 61, "y1": 61, "type": 4, "floor": 0, "ceil": 24}
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
    world = World(
        ssize=ssize,
        type=bytes(types),
        floor=bytes(floor),
        ceil=bytes(ceil),
        vdelta=bytes(vdelta),
        waterlevel=float(spec.get("waterlevel", NO_WATER)),
    )
    # Ladders go in as *entities* and are resolved by the same `ladders_from` the
    # real map pipeline uses, so the fixture exercises the derivation rather than
    # handing both sides a pre-computed span. `conformance.test.ts` does the same
    # on its side.
    world.ladders = ladders_from(
        ssize,
        world.floor_at,
        [
            _LadderEntity(spec_l["x"], spec_l["y"], spec_l["height"])
            for spec_l in spec.get("ladders", [])
        ],
    )
    return world


class _LadderEntity:
    """The three fields `ladders_from` reads off a map entity."""

    def __init__(self, x: int, y: int, height: int) -> None:
        self.type = LADDER_ENTITY
        self.x = x
        self.y = y
        self.attr1 = height


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
        # Short enough to stop on the raised half rather than run on into the
        # four-unit ledge beyond it, which is the *next* case's business.
        "start": {"x": 5.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(14, forward=1.0, dt=1 / 60),
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
        # On the ground, so this measures the dt clamp rather than the much
        # gentler air response a falling body would accelerate at.
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
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
    # -- water ------------------------------------------------------------
    {
        "name": "wades slower than it walks",
        "world": "pool",
        # Feet under the water, head out: the wading state.
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(40, forward=1.0, dt=1 / 60),
    },
    {
        "name": "sinks slowly instead of falling",
        "world": "pool",
        # Dropped in from above the surface, fully submerged on the way down.
        "start": {"x": 8.0, "y": 8.0, "z": 20.0},
        "steps": repeat(90, dt=1 / 60),
    },
    {
        "name": "swims up while jump is held",
        "world": "pool",
        "start": {"x": 8.0, "y": 8.0, "z": 0.0, "yaw": 0.0},
        "steps": repeat(60, jump=True, dt=1 / 60),
    },
    {
        "name": "dives while crouch is held",
        "world": "pool",
        "start": {"x": 8.0, "y": 8.0, "z": 4.0, "yaw": 0.0},
        "steps": repeat(40, crouch=True, dt=1 / 60),
    },
    {
        "name": "cannot jump off the bottom while submerged",
        "world": "pool",
        # On the floor with the water well over the head: `jump` is the swim
        # control down here, and the difference between the two readings is
        # nineteen cubes a second.
        "start": {"x": 8.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": [{"jump": True, "dt": 1 / 60}],
    },
    {
        "name": "a long drop into water arrives gently",
        "world": "well",
        "start": {"x": 8.0, "y": 8.0, "z": 100.0},
        "steps": repeat(240, dt=1 / 60),
    },
    # -- ladders ----------------------------------------------------------
    {
        "name": "climbs while facing the ladder",
        "world": "climb",
        # Facing +x at the ladder cell, one cube short of its centre.
        "start": {
            "x": 10.5,
            "y": 8.5,
            "z": 0.0,
            "yaw": 3.141592653589793,
            "on_ground": True,
        },
        "steps": repeat(60, forward=1.0, dt=1 / 60),
    },
    {
        "name": "descends when pressing back on a ladder",
        "world": "climb",
        "start": {"x": 10.5, "y": 8.5, "z": 12.0, "yaw": 3.141592653589793},
        "steps": repeat(60, forward=-1.0, dt=1 / 60),
    },
    {
        "name": "holds position on a ladder with no input",
        "world": "climb",
        "start": {"x": 10.5, "y": 8.5, "z": 12.0, "yaw": 3.141592653589793},
        "steps": repeat(60, dt=1 / 60),
    },
    {
        "name": "strafing along a ladder does not climb it",
        "world": "climb",
        # Facing +y, so the ladder is off to the side: the alignment is zero and
        # the climb rate with it. This is the case that stops a player running
        # past a ladder being launched up it.
        "start": {
            "x": 10.5,
            "y": 8.5,
            "z": 6.0,
            "yaw": 1.5707963267948966,
            "on_ground": False,
        },
        "steps": repeat(30, forward=1.0, dt=1 / 60),
    },
    {
        "name": "the climb stops at the top rung",
        "world": "climb",
        "start": {"x": 10.5, "y": 8.5, "z": 20.0, "yaw": 3.141592653589793},
        "steps": repeat(120, forward=1.0, dt=1 / 60),
    },
    {
        # Leaving at the top is the case `LADDER_TOP_RELEASE` exists for: at the
        # last rung the axial grip lets go, so forward walks onto the ledge
        # instead of holding you against a ladder you have finished climbing.
        "name": "walks off the top of a ladder onto the ledge",
        "world": "climb",
        "start": {"x": 10.5, "y": 8.5, "z": 24.0, "yaw": 3.141592653589793},
        "steps": repeat(40, forward=1.0, dt=1 / 60),
    },
    {
        "name": "a jump on a ladder hops up a rung and re-grabs",
        "world": "climb",
        "start": {"x": 10.5, "y": 8.5, "z": 10.0, "yaw": 3.141592653589793},
        "steps": [{"forward": 1.0, "jump": True, "dt": 1 / 60}] + repeat(20, dt=1 / 60),
    },
    # -- momentum ---------------------------------------------------------
    {
        "name": "momentum carries through a jump with no input",
        "world": "field",
        "start": {"x": 6.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        # Run up to speed, jump, then let go of everything: air response is slow
        # enough that most of the speed survives the arc.
        "steps": repeat(40, forward=1.0, dt=1 / 60)
        + [{"forward": 1.0, "jump": True, "dt": 1 / 60}]
        + repeat(24, dt=1 / 60),
    },
    {
        "name": "releasing the key on the ground brakes hard",
        "world": "field",
        # The pair to the case above: identical run, no jump, and the ground
        # response takes the speed away in a fraction of the distance.
        "start": {"x": 6.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(40, forward=1.0, dt=1 / 60) + repeat(25, dt=1 / 60),
    },
    {
        "name": "gravity ramps with time in air",
        "world": "shaft",
        "start": {"x": 8.0, "y": 8.0, "z": 100.0},
        "steps": repeat(60, dt=1 / 60),
    },
    # -- the chained-jump boost -------------------------------------------
    #
    # Held jump rather than a hand-timed rejump: `step` jumps on any frame the
    # body is on the ground, so holding the key auto-hops and every landing lands
    # inside the window by construction. That is also how it is played, and it
    # keeps the vector independent of exactly how many frames a jump lasts.
    {
        "name": "chained strafing hops build speed past the run cap",
        "world": "field",
        "start": {"x": 6.0, "y": 6.0, "z": 0.0, "yaw": 0.8, "on_ground": True},
        "steps": repeat(30, forward=1.0, strafe=1.0, dt=1 / 60)
        + repeat(90, forward=1.0, strafe=1.0, jump=True, dt=1 / 60),
    },
    {
        "name": "the same chained hops without strafe do not boost",
        "world": "field",
        "start": {"x": 6.0, "y": 6.0, "z": 0.0, "yaw": 0.8, "on_ground": True},
        "steps": repeat(30, forward=1.0, dt=1 / 60)
        + repeat(90, forward=1.0, jump=True, dt=1 / 60),
    },
    # -- crouching --------------------------------------------------------
    {
        "name": "crouching on the ground moves at 40 per cent",
        "world": "field",
        "start": {"x": 6.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(60, forward=1.0, crouch=True, dt=1 / 60),
    },
    {
        "name": "a crouched body fits under a five-cube ceiling",
        "world": "vent",
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        # Long enough to finish the crouch transition and walk right through.
        "steps": repeat(300, forward=1.0, crouch=True, dt=1 / 60),
    },
    {
        "name": "a standing body is stopped by the same ceiling",
        "world": "vent",
        "start": {"x": 4.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(120, forward=1.0, dt=1 / 60),
    },
    {
        "name": "releasing crouch under the ceiling does not stand up",
        "world": "vent",
        # Started already inside the low band and already crouched, so the case is
        # about one thing: letting go of crouch with no room to rise into. Walking
        # in first would end wherever the run happened to stop.
        "start": {
            "x": 8.0,
            "y": 8.0,
            "z": 0.0,
            "yaw": 0.0,
            "on_ground": True,
            "crouch": 1.0,
        },
        "steps": repeat(60, crouch=False, dt=1 / 60),
    },
    {
        "name": "crouching in mid-air keeps full speed",
        "world": "field",
        "start": {"x": 6.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        # Up to speed, jump, then crouch only once airborne — AC's
        # `crouchedinair` exemption, which is what makes a crouch-jump free.
        "steps": repeat(40, forward=1.0, dt=1 / 60)
        + [{"forward": 1.0, "jump": True, "dt": 1 / 60}]
        + repeat(20, forward=1.0, crouch=True, dt=1 / 60),
    },
    # -- external impulses (weapon recoil) --------------------------------
    {
        "name": "an upward impulse launches a grounded player",
        "world": "shaft",
        "start": {"x": 8.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        # The shoot-jump: one step, then the kick a shot straight down produces,
        # applied where `match._fire` applies it — after the step. Stopped short
        # of the landing, or the case would only prove that gravity works.
        "steps": [{"dt": 1 / 60, "impulse": [0.0, 0.0, 14.0]}] + repeat(12, dt=1 / 60),
    },
    {
        "name": "a backward impulse cancels forward speed",
        "world": "field",
        "start": {"x": 6.0, "y": 8.0, "z": 0.0, "yaw": 0.0, "on_ground": True},
        "steps": repeat(40, forward=1.0, dt=1 / 60)
        + [{"forward": 1.0, "dt": 1 / 60, "impulse": [-20.0, 0.0, 0.0]}]
        + repeat(10, dt=1 / 60),
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


# Shot geometry is duplicated the same way movement is, now that the training
# range traces its own shots (`packages/core/src/modules/hassault/trace.ts`).
# The DDA is the part worth pinning: it is a dozen lines where an off-by-one on a
# cell boundary produces shots that stop a fraction early, which nothing visibly
# reports and which would teach a player the wrong thing about their own aim.
#
# Angles rather than raw direction vectors, so a case reads as "stand here and
# look there" and a disagreement about `aim_vector` itself is also caught.
TRACE_CASES = [
    {
        "name": "a level shot down a corridor stops on the far wall",
        "world": "room",
        "origin": [3.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.0,
        "max_distance": 100.0,
    },
    {
        "name": "a shot into the floor stops at the floor",
        "world": "room",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": -1.2,
        "max_distance": 100.0,
    },
    {
        "name": "a shot into the ceiling stops at the ceiling",
        "world": "room",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 1.3,
        "max_distance": 100.0,
    },
    {
        "name": "a diagonal shot crosses cells and still finds the corner",
        "world": "room",
        "origin": [3.0, 3.0, 4.5],
        "yaw": 0.7853981633974483,
        "pitch": 0.0,
        "max_distance": 100.0,
    },
    {
        "name": "a shot that reaches its range limit reports the range",
        "world": "room",
        "origin": [3.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.0,
        "max_distance": 2.0,
    },
    {
        "name": "a shot at a step is stopped by the raised floor beyond it",
        "world": "steps",
        "origin": [3.0, 8.0, 1.5],
        "yaw": 0.0,
        "pitch": 0.0,
        "max_distance": 100.0,
    },
    {
        "name": "a shot over a step clears it",
        "world": "steps",
        "origin": [3.0, 8.0, 6.0],
        "yaw": 0.0,
        "pitch": 0.0,
        "max_distance": 100.0,
    },
    {
        "name": "a heightfield is traced as a step, not as a slope",
        "world": "slope",
        "origin": [3.0, 8.0, 9.0],
        "yaw": 0.0,
        "pitch": 0.0,
        "max_distance": 100.0,
    },
    {
        "name": "a shot straight up leaves through the ceiling of its own cell",
        "world": "room",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 1.5707963267948966,
        "max_distance": 100.0,
    },
    {
        "name": "a shot backwards down the corridor stops on the near wall",
        "world": "room",
        "origin": [12.0, 8.0, 4.5],
        "yaw": 3.141592653589793,
        "pitch": 0.0,
        "max_distance": 100.0,
    },
]

# The body test is the other half of a shot: a cylinder, an interval intersection,
# and two cases that are easy to get backwards — a shot that starts inside
# somebody (point blank, a hit at zero, not a miss) and one aimed over their head.
BODY_CASES = [
    {
        "name": "a body straight ahead is hit at its near face",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.0,
        "feet": [20.0, 8.0, 0.0],
    },
    {
        "name": "a shot past a body misses",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.3,
        "pitch": 0.0,
        "feet": [20.0, 8.0, 0.0],
    },
    {
        "name": "a muzzle already inside a body is point blank, not a miss",
        "origin": [20.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.0,
        "feet": [20.0, 8.0, 0.0],
    },
    {
        "name": "a shot over a standing body misses",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.35,
        "feet": [20.0, 8.0, 0.0],
    },
    {
        "name": "a shot straight down into a body underfoot connects",
        "origin": [20.0, 8.0, 9.0],
        "yaw": 0.0,
        "pitch": -1.5707963267948966,
        "feet": [20.0, 8.0, 0.0],
    },
    # A pair, and only meaningful as one: the same shot at the same angle, which
    # a standing body takes in the shoulders and a crouched body ducks. Pitched
    # to land between the two heights on purpose — at any steeper angle both miss
    # and the crouch case would pass while proving nothing.
    {
        "name": "a high shot still catches a standing body",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.05,
        "feet": [20.0, 8.0, 0.0],
    },
    {
        "name": "a crouched body is shorter and the same shot sails over it",
        "origin": [8.0, 8.0, 4.5],
        "yaw": 0.0,
        "pitch": 0.05,
        "feet": [20.0, 8.0, 0.0],
        "height": 3.9,
    },
]


#: `{name, weapon, index, yaw, pitch}` — one shot of a burst.
#:
#: What these pin is the **application**, not the table: the offsets themselves
#: are served on `GET /api/hassault/weapons`, so there is one copy of them by
#: construction. What can drift is what each port does with one — and the
#: absolute-versus-delta mistake is silent, reading as a tuning problem rather
#: than as a bug.
SPRAY_CASES: list[dict] = [
    {
        "name": "the first shot of a burst is exactly where you aimed",
        "weapon": "assault",
        "index": 0,
        "yaw": 0.4,
        "pitch": 0.1,
    },
    {
        "name": "the third shot has climbed",
        "weapon": "assault",
        "index": 2,
        "yaw": 0.4,
        "pitch": 0.1,
    },
    {
        "name": "the tenth shot has drifted left as well",
        "weapon": "assault",
        "index": 9,
        "yaw": 0.0,
        "pitch": 0.0,
    },
    {
        "name": "the last entry of the table",
        "weapon": "assault",
        "index": 19,
        "yaw": -1.2,
        "pitch": -0.3,
    },
    {
        "name": "past the end of the table the pattern holds rather than wrapping",
        "weapon": "assault",
        "index": 40,
        "yaw": -1.2,
        "pitch": -0.3,
    },
    {
        "name": "a weapon with no pattern is aimed exactly where it is pointed",
        "weapon": "pistol",
        "index": 5,
        "yaw": 2.0,
        "pitch": 0.2,
    },
    {
        "name": "the shotgun keeps its whole cone",
        "weapon": "shotgun",
        "index": 3,
        "yaw": 0.0,
        "pitch": 0.0,
    },
    {
        "name": "an unscoped sniper keeps its hipfire cone",
        "weapon": "sniper",
        "index": 1,
        "yaw": 0.0,
        "pitch": 0.0,
        "scoped": 0,
    },
    {
        "name": "a scoped sniper keeps its scoped cone",
        "weapon": "sniper",
        "index": 1,
        "yaw": 0.0,
        "pitch": 0.0,
        "scoped": 1,
    },
]


#: `{name, world, x, y, eyeZ, yaw, pitch, lob, inherit}` — one throw.
#:
#: What these pin is the **integration**: the origin, the velocity (including the
#: thrower's own, which is the whole reason the preview exists) and where the
#: flight first touches something. Bounces are deliberately not in the table —
#: the preview stops at first contact, because a bounce is chaotic enough that a
#: 1e-6 disagreement in the floor comparison puts the marker in the next room.
THROW_CASES: list[dict] = [
    {
        "name": "a flat throw from a standstill",
        "world": "field",
        "x": 8.0,
        "y": 32.0,
        "eyeZ": 4.5,
        "yaw": 0.0,
        "pitch": 0.0,
        "lob": False,
        "inherit": [0.0, 0.0, 0.0],
    },
    {
        "name": "the same throw while running at it",
        "world": "field",
        "x": 8.0,
        "y": 32.0,
        "eyeZ": 4.5,
        "yaw": 0.0,
        "pitch": 0.0,
        "lob": False,
        "inherit": [20.0, 0.0, 0.0],
    },
    {
        "name": "the same throw while jumping",
        "world": "field",
        "x": 8.0,
        "y": 32.0,
        "eyeZ": 4.5,
        "yaw": 0.0,
        "pitch": 0.0,
        "lob": False,
        "inherit": [0.0, 0.0, 22.0],
    },
    {
        "name": "an underhand lob goes nowhere near as far",
        "world": "field",
        "x": 8.0,
        "y": 32.0,
        "eyeZ": 4.5,
        "yaw": 0.0,
        "pitch": 0.0,
        "lob": True,
        "inherit": [0.0, 0.0, 0.0],
    },
    {
        "name": "thrown at the floor it lands almost underfoot",
        "world": "field",
        "x": 32.0,
        "y": 32.0,
        "eyeZ": 4.5,
        "yaw": 0.0,
        "pitch": -1.1,
        "lob": True,
        "inherit": [0.0, 0.0, 0.0],
    },
    {
        "name": "thrown up and across, it carries a long way",
        "world": "field",
        "x": 32.0,
        "y": 32.0,
        "eyeZ": 4.5,
        "yaw": 1.57079632679,
        "pitch": 0.7,
        "lob": False,
        "inherit": [0.0, 0.0, 0.0],
    },
]


def _simulate_throw(world, case, seconds: float, substeps_per_sample: int):
    """The reference integration, in the shape a client's preview draws.

    Deliberately **not** `step_grenade`: that one bounces, and the preview stops
    at first contact. What is shared is the arithmetic up to that point — the
    same substep, the same gravity, the same axis order, and the same three
    questions `_blocked` asks — which is exactly the part a client has to agree
    about.
    """
    from backend.modules.hassault import grenades

    origin = grenades.throw_origin(
        case["x"], case["y"], case["eyeZ"], case["yaw"], case["pitch"]
    )
    velocity = grenades.throw_velocity(
        case["yaw"],
        case["pitch"],
        case["lob"],
        tuple(case["inherit"]),
    )
    x, y, z = origin
    vx, vy, vz = velocity
    points = [[x, y, z]]
    steps = max(1, int(round(seconds / grenades.SUBSTEP)))
    for step in range(steps):
        h = grenades.SUBSTEP
        vz -= grenades.GRAVITY * h
        contact = None
        landed = False
        nx = x + vx * h
        if grenades._blocked(world, nx, y, z):
            contact = [x, y, z]
        else:
            x = nx
        if contact is None:
            ny = y + vy * h
            if grenades._blocked(world, x, ny, z):
                contact = [x, y, z]
            else:
                y = ny
        if contact is None:
            nz = z + vz * h
            if grenades._blocked(world, x, y, nz):
                contact = [x, y, z]
                landed = vz < 0
            else:
                z = nz
        if contact is not None:
            points.append(contact)
            return origin, velocity, points, contact, landed
        if step % substeps_per_sample == 0:
            points.append([x, y, z])
    points.append([x, y, z])
    return origin, velocity, points, None, False


def _face_is_ambiguous(world, case) -> bool:
    """Whether a one-ULP change in the aim vector would report a different face.

    A probe rather than a geometric argument, and **one ULP is exactly the right
    size** because that is the disagreement being modelled. At yaw = pi/4,
    Python's `math.cos` and `math.sin` return the *same* double, so the ray
    crosses its x and y boundaries at literally the same instant; V8's return
    values one ULP apart, so it does not. Which face is reported then depends on
    whose trigonometry ran, and no port can be held to that.

    The distance is unaffected — the corner is where the ray stops either way —
    so only the face is dropped.
    """
    origin = (case["origin"][0], case["origin"][1], case["origin"][2])
    dx, dy, dz = aim_vector(case["yaw"], case["pitch"])
    faces = set()
    for ndx in (math.nextafter(dx, -math.inf), dx, math.nextafter(dx, math.inf)):
        for ndy in (math.nextafter(dy, -math.inf), dy, math.nextafter(dy, math.inf)):
            faces.add(
                raycast_world_face(
                    world, origin, (ndx, ndy, dz), case["max_distance"]
                )[1]
            )
    return len(faces) > 1


def main() -> None:
    out_cases = []
    for case in CASES:
        world = build(WORLDS[case["world"]])
        s = case["start"]
        player = PlayerState(
            x=s["x"],
            y=s["y"],
            z=s["z"],
            vel_x=s.get("vel_x", 0.0),
            vel_y=s.get("vel_y", 0.0),
            vel_z=s.get("vel_z", 0.0),
            yaw=s.get("yaw", 0.0),
            pitch=s.get("pitch", 0.0),
            on_ground=s.get("on_ground", False),
            crouch=s.get("crouch", 0.0),
        )
        for raw in case["steps"]:
            if "yaw" in raw:
                player.yaw = raw["yaw"]
            move = MoveInput(
                forward=raw.get("forward", 0.0),
                strafe=raw.get("strafe", 0.0),
                jump=raw.get("jump", False),
                crouch=raw.get("crouch", False),
            )
            step(world, player, move, raw["dt"])
            # Applied *after* the step, which is where the match server applies
            # weapon recoil (`simulate` steps, then `_handle_combat` fires). The
            # client's replay has to match that order or every shot mispredicts.
            if "impulse" in raw:
                apply_impulse(player, *raw["impulse"])
        out_cases.append(
            {
                **case,
                "expect": {
                    "x": player.x,
                    "y": player.y,
                    "z": player.z,
                    "velX": player.vel_x,
                    "velY": player.vel_y,
                    "velZ": player.vel_z,
                    "crouch": player.crouch,
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

    out_traces = []
    for case in TRACE_CASES:
        world = build(WORLDS[case["world"]])
        direction = aim_vector(case["yaw"], case["pitch"])
        origin = (case["origin"][0], case["origin"][1], case["origin"][2])
        distance, face = raycast_world_face(
            world, origin, direction, case["max_distance"]
        )
        # `face` is which surface stopped the ray, as an index into
        # `weapons.FACE_NORMALS`. Carried here because it is now on the wire and
        # both clients orient a bullet mark from it — a fourth implementation
        # that disagreed about the face would draw every mark inside its wall,
        # where nothing would ever report it.
        #
        # **`null` where the geometry has no answer.** A ray fired at exactly 45°
        # from a cell corner crosses its x and y boundaries at the same instant,
        # so which face it "hit" is decided by the last bit of `cos(yaw)` — and
        # Python's libm and V8's `Math.cos` do not agree to the last bit. The
        # distance is unaffected and stays pinned; the face is genuinely
        # ambiguous, and asserting one would make the suite hostage to whose
        # trigonometry ran. Detected rather than hand-marked, so a case that
        # becomes degenerate later is caught the day it does.
        out_traces.append(
            {
                **case,
                "expect": distance,
                "face": None if _face_is_ambiguous(world, case) else face,
            }
        )

    out_sprays = []
    for case in SPRAY_CASES:
        weapon = weapon_by_id[case["weapon"]]
        offset = spray_offset(weapon, case["index"])
        yaw, pitch = apply_spray(case["yaw"], case["pitch"], offset)
        out_sprays.append(
            {
                **case,
                "expect": {
                    "offset": [offset[0], offset[1]],
                    "yaw": yaw,
                    "pitch": pitch,
                    "direction": list(aim_vector(yaw, pitch)),
                    "cone": residual_spread(weapon, case.get("scoped", 0)),
                },
            }
        )

    out_throws = []
    #: Must match `ARC_PREVIEW_SECONDS` and `ARC_SAMPLES` in `arc.ts` / `arc.rs`.
    #: Carried in the payload rather than assumed, so a client reading a longer
    #: window than the fixture was generated for fails loudly.
    preview_seconds = 2.0
    arc_samples = 48
    from backend.modules.hassault import grenades as _g

    per_sample = max(1, int(preview_seconds / _g.SUBSTEP) // arc_samples)
    for case in THROW_CASES:
        world = build(WORLDS[case["world"]])
        origin, velocity, points, contact, landed = _simulate_throw(
            world, case, preview_seconds, per_sample
        )
        out_throws.append(
            {
                **case,
                "expect": {
                    "origin": list(origin),
                    "velocity": list(velocity),
                    "points": points,
                    "contact": contact,
                    "landed": landed,
                },
            }
        )

    out_bodies = []
    for case in BODY_CASES:
        direction = aim_vector(case["yaw"], case["pitch"])
        hit = ray_hits_body(
            (case["origin"][0], case["origin"][1], case["origin"][2]),
            direction,
            (case["feet"][0], case["feet"][1], case["feet"][2]),
            height=case.get("height", BODY_HEIGHT),
        )
        out_bodies.append({**case, "expect": hit})

    payload = {
        "_comment": (
            "Cross-language physics conformance vectors. Read by BOTH "
            "backend/tests/test_hassault_physics.py and "
            "packages/core/src/modules/hassault/__tests__/conformance.test.ts. "
            "These pin that the two implementations agree, not that either is "
            "correct in the abstract - correctness is what each side's own unit "
            "tests are for. Regenerate only when a rule genuinely changes, and "
            "make both suites pass before committing. 'hitboxSpecId' is the "
            "content hash of the body these vectors were generated against "
            "(backend/modules/hassault/hitbox.py). It is checked by the Python "
            "suite: change a hit-deciding dimension and this file is stale, "
            "which is the one failure mode a hand-maintained revision number "
            "would let you forget."
        ),
        # The body these vectors were generated against. Both suites read it:
        # the Python one to refuse a stale fixture, the TypeScript one to catch
        # its own default having drifted from the server's. This generator had
        # lost the stamp while the committed fixture still carried it, so
        # regenerating silently dropped two guards - restored here.
        "hitboxSpecId": hitbox.DEFAULT.spec_id,
        "hitbox": hitbox.DEFAULT.to_dict(),
        "tolerance": 1e-9,
        "worlds": WORLDS,
        "cases": out_cases,
        "spawns": out_spawns,
        "traces": out_traces,
        "bodies": out_bodies,
        # The served weapon table, verbatim from `Weapon.to_dict`. The spray
        # cases below index into it rather than carrying their own copy of the
        # offsets — the whole point of serving the pattern is that there is one
        # copy of it, and a fixture with a second one would be exactly the thing
        # this design refuses.
        "weapons": {w.id: w.to_dict() for w in WEAPONS},
        "sprays": out_sprays,
        # The throw preview. **A looser tolerance than the rest of this file, on
        # purpose**: the global 1e-9 is right for a single movement step and
        # wrong for an integrator run for two seconds, where the three ports'
        # float widths diverge steadily. Stated here rather than left to be
        # discovered as flakiness and then deleted.
        "throwTolerance": 1e-4,
        "throwPreviewSeconds": preview_seconds,
        "throwArcSamples": arc_samples,
        "throws": out_throws,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT} with {len(out_cases)} step cases, {len(out_spawns)} spawn "
        f"cases, {len(out_traces)} traces, {len(out_bodies)} body cases, "
        f"{len(out_sprays)} spray cases, {len(out_throws)} throws"
    )
    for c in out_cases:
        e = c["expect"]
        speed = (e["velX"] ** 2 + e["velY"] ** 2) ** 0.5
        print(
            f"  {c['name']:<52} x={e['x']:7.3f} y={e['y']:7.3f} z={e['z']:7.3f} "
            f"|v|={speed:6.3f} crouch={e['crouch']:.2f} g={int(e['onGround'])}"
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
