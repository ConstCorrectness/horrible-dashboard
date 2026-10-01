#!/usr/bin/env python3
"""The inspect choreographies, one per weapon and knife archetype.

    uv run python tools/blender/author_inspects.py

Writes two files, both read by BOTH clients:

- `packages/core/src/modules/hassault/models/inspects.json` — the clips, read by
  `inspects.ts` (browser) and `inspects.rs` (native, `include_str!`);
- `packages/core/src/modules/hassault/models/inspects.golden.json` — sampled
  values from the reference sampler below, which both clients' tests assert
  against, so the three samplers cannot drift apart.

## What a clip is

`t` is normalised over the clip's own `duration` (seconds). Each key may carry:

- `pos` / `rot` — the whole weapon, hands and all, moved on the view model's
  pivot. `pos` is [towards the centre of the screen, up, towards the camera];
  `rot` is [pitch (muzzle up), yaw (muzzle left), roll (top towards the left)].
  Interpolated with Catmull-Rom and faded to exactly zero over the first and
  last 5%, so every inspect starts and ends at rest whatever its keys say.
- `spin` — a rotation of the **weapon alone** about `spinAxis` (weapon space)
  through the prop's `spinPivot` node, in radians. The hand does not follow,
  which is the whole point: a karambit twirls on the finger in its ring, the
  hand stays where it is. Not faded, so the first and last keys must be whole
  turns — the clips here start at 0 and end on a multiple of 2π.
- `pose` — hand channels as in `viewclips.json`: `primary` / `support` offsets
  from the grip (absent means zero), and `primaryFingers` / `supportFingers`
  curls, which blend from the grip's own curl where a neighbouring key has
  none — so a clip never snaps the fingers on its first or last frame.
- `nodes` — named parts of the prop rotated about their own origin: the
  butterfly's two handles swing on their pins. `{name: {"rot": [x, y, z]}}`.

`repeatFrom` is where a knife's inspect jumps back to when F is pressed again
mid-flourish (as a fraction of the clip), so repeated presses chain the
flourish instead of restarting the raise.
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "packages" / "core" / "src" / "modules" / "hassault" / "models"
INSPECTS_JSON = MODELS / "inspects.json"
GOLDEN_JSON = MODELS / "inspects.golden.json"

TAU = 2.0 * math.pi
OPEN = [0.25, 0.2, 0.2, 0.2, 0.2]
FIST = [0.9, 1.0, 1.0, 1.0, 1.0]
REST = {"t": 0.0, "pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0]}


def rest(t):
    return {**REST, "t": t}


def key(t, pos, rot, spin=None, **extra):
    k = {"t": t, "pos": pos, "rot": rot}
    if spin is not None:
        k["spin"] = spin
    k.update(extra)
    return k


def build_clips() -> dict:
    return {
        # A tanto turned flat to the camera, rolled over in an open palm, the
        # thumb run down the spine, then edge-on.
        "knife-tactical": {
            "duration": 1.9,
            "repeatFrom": 0.30,
            "spinAxis": [0.0, 0.0, 1.0],
            "keys": [
                key(0.0, [0, 0, 0], [0, 0, 0], 0.0),
                key(
                    0.20,
                    [0.10, 0.08, 0.09],
                    [0.22, -0.50, 1.45],
                    0.0,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.34,
                    [0.11, 0.10, 0.10],
                    [0.30, -0.40, 1.55],
                    0.0,
                    pose={"primaryFingers": OPEN},
                ),
                key(
                    0.50,
                    [0.11, 0.10, 0.10],
                    [0.30, -0.35, 1.55],
                    TAU,
                    pose={"primaryFingers": OPEN},
                ),
                key(
                    0.62,
                    [0.10, 0.09, 0.09],
                    [0.45, -0.25, 1.30],
                    TAU,
                    pose={"primaryFingers": [0.1, 1.0, 1.0, 1.0, 1.0]},
                ),
                key(
                    0.80,
                    [0.05, 0.06, 0.05],
                    [0.10, 0.35, 0.40],
                    TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(1.0, [0, 0, 0], [0, 0, 0], TAU),
            ],
        },
        # Two turns on the index finger through the ring, caught in a reverse
        # grip, then the talon shown side-on.
        "knife-karambit": {
            "duration": 1.9,
            "repeatFrom": 0.12,
            "spinPivot": "spin_origin",
            "spinAxis": [1.0, 0.0, 0.0],
            "keys": [
                key(0.0, [0, 0, 0], [0, 0, 0], 0.0),
                key(
                    0.12,
                    [0.08, 0.07, 0.08],
                    [0.20, -0.30, 0.60],
                    0.0,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.30,
                    [0.09, 0.08, 0.09],
                    [0.25, -0.35, 0.70],
                    TAU,
                    pose={"primaryFingers": [0.2, 0.9, 0.3, 0.3, 0.3]},
                ),
                key(
                    0.48,
                    [0.09, 0.08, 0.09],
                    [0.25, -0.35, 0.70],
                    2 * TAU,
                    pose={"primaryFingers": [0.2, 0.9, 0.3, 0.3, 0.3]},
                ),
                key(
                    0.58,
                    [0.08, 0.08, 0.09],
                    [0.30, -0.40, 1.40],
                    2 * TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.80,
                    [0.05, 0.05, 0.06],
                    [0.45, -0.20, 1.60],
                    2 * TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(1.0, [0, 0, 0], [0, 0, 0], 2 * TAU),
            ],
        },
        # A balisong: the bite handle swings round, both close over the blade,
        # and it flips open again — the handles are real nodes on real pins.
        "knife-butterfly": {
            "duration": 2.2,
            "repeatFrom": 0.14,
            "keys": [
                key(
                    0.0,
                    [0, 0, 0],
                    [0, 0, 0],
                    nodes={
                        "handle_bite": {"rot": [0, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
                key(
                    0.14,
                    [0.08, 0.07, 0.08],
                    [0.20, -0.30, 1.10],
                    nodes={
                        "handle_bite": {"rot": [-math.pi, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
                key(
                    0.28,
                    [0.09, 0.09, 0.09],
                    [0.30, -0.25, 1.40],
                    nodes={
                        "handle_bite": {"rot": [-math.pi, 0, 0]},
                        "handle_safe": {"rot": [math.pi, 0, 0]},
                    },
                ),
                key(
                    0.44,
                    [0.09, 0.10, 0.09],
                    [0.35, -0.20, 2.60],
                    nodes={
                        "handle_bite": {"rot": [-math.pi, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
                key(
                    0.58,
                    [0.08, 0.08, 0.09],
                    [0.25, -0.35, 1.30],
                    nodes={
                        "handle_bite": {"rot": [0, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
                key(
                    0.70,
                    [0.07, 0.07, 0.08],
                    [0.20, -0.30, 1.20],
                    nodes={
                        "handle_bite": {"rot": [-math.pi, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
                key(
                    0.84,
                    [0.04, 0.04, 0.05],
                    [0.10, -0.15, 0.50],
                    nodes={
                        "handle_bite": {"rot": [0, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
                key(
                    1.0,
                    [0, 0, 0],
                    [0, 0, 0],
                    nodes={
                        "handle_bite": {"rot": [0, 0, 0]},
                        "handle_safe": {"rot": [0, 0, 0]},
                    },
                ),
            ],
        },
        # Tossed up, one full flip in the air, caught, then the sawback shown.
        "knife-bayonet": {
            "duration": 2.0,
            "repeatFrom": 0.08,
            "spinAxis": [1.0, 0.0, 0.0],
            "keys": [
                key(0.0, [0, 0, 0], [0, 0, 0], 0.0),
                key(
                    0.10,
                    [0.06, 0.04, 0.06],
                    [0.15, -0.25, 0.40],
                    0.0,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.24,
                    [0.08, 0.26, 0.08],
                    [0.20, -0.30, 0.50],
                    math.pi,
                    pose={"primaryFingers": OPEN},
                ),
                key(
                    0.38,
                    [0.07, 0.06, 0.08],
                    [0.20, -0.30, 0.55],
                    TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.62,
                    [0.09, 0.09, 0.09],
                    [0.45, -0.45, 1.35],
                    TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(0.82, [0.04, 0.04, 0.05], [0.15, -0.20, 0.50], TAU),
                key(1.0, [0, 0, 0], [0, 0, 0], TAU),
            ],
        },
        # Spun twice on a finger through the hole, then snapped to hammer grip.
        "knife-skeleton": {
            "duration": 1.8,
            "repeatFrom": 0.10,
            "spinPivot": "spin_origin",
            "spinAxis": [1.0, 0.0, 0.0],
            "keys": [
                key(0.0, [0, 0, 0], [0, 0, 0], 0.0),
                key(
                    0.10,
                    [0.07, 0.06, 0.08],
                    [0.20, -0.30, 0.90],
                    0.0,
                    pose={"primaryFingers": [0.3, 0.9, 0.3, 0.3, 0.3]},
                ),
                key(
                    0.28,
                    [0.08, 0.07, 0.09],
                    [0.25, -0.30, 0.95],
                    -TAU,
                    pose={"primaryFingers": [0.3, 0.9, 0.3, 0.3, 0.3]},
                ),
                key(
                    0.46,
                    [0.08, 0.07, 0.09],
                    [0.25, -0.30, 0.95],
                    -2 * TAU,
                    pose={"primaryFingers": [0.3, 0.9, 0.3, 0.3, 0.3]},
                ),
                key(
                    0.56,
                    [0.07, 0.07, 0.08],
                    [0.55, -0.25, 1.10],
                    -2 * TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(0.80, [0.04, 0.05, 0.05], [0.20, -0.15, 0.60], -2 * TAU),
                key(1.0, [0, 0, 0], [0, 0, 0], -2 * TAU),
            ],
        },
        # Rolled over the knuckles about its own blade, then the heavy tilt
        # that shows off the double sawback.
        "knife-huntsman": {
            "duration": 2.0,
            "repeatFrom": 0.12,
            "spinAxis": [0.0, 0.0, 1.0],
            "keys": [
                key(0.0, [0, 0, 0], [0, 0, 0], 0.0),
                key(
                    0.12,
                    [0.08, 0.07, 0.08],
                    [0.30, -0.45, 0.90],
                    0.0,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.36,
                    [0.09, 0.09, 0.09],
                    [0.35, -0.40, 1.10],
                    -TAU,
                    pose={"primaryFingers": [0.4, 0.6, 0.6, 0.6, 0.6]},
                ),
                key(
                    0.58,
                    [0.09, 0.10, 0.09],
                    [0.70, -0.45, 0.35],
                    -TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(0.80, [0.04, 0.05, 0.05], [0.25, -0.20, 0.80], -TAU),
                key(1.0, [0, 0, 0], [0, 0, 0], -TAU),
            ],
        },
        # Tilted to the slide, a press-check with the off hand, flipped to the
        # other side, then muzzle up for a look down the sights.
        "pistol": {
            "duration": 1.9,
            "keys": [
                rest(0.0),
                key(0.18, [0.10, 0.06, 0.06], [0.15, -0.40, -0.85]),
                key(
                    0.34,
                    [0.10, 0.07, 0.07],
                    [0.22, -0.45, -0.75],
                    pose={
                        "support": [0.0, 0.03, 0.07],
                        "supportFingers": [0.8, 0.5, 0.5, 0.9, 0.9],
                    },
                ),
                key(
                    0.44,
                    [0.10, 0.07, 0.07],
                    [0.22, -0.45, -0.75],
                    pose={
                        "support": [0.0, 0.03, 0.00],
                        "supportFingers": [0.8, 0.5, 0.5, 0.9, 0.9],
                    },
                ),
                key(
                    0.62,
                    [0.12, 0.08, 0.07],
                    [0.20, -0.50, 1.60],
                    pose={"support": [-0.06, -0.14, 0.06], "supportFingers": OPEN},
                ),
                key(0.82, [0.06, 0.10, 0.05], [0.70, -0.20, 0.25]),
                rest(1.0),
            ],
        },
        # Rolled to the ejection port, the off hand drops to the magazine, tugs
        # it and slaps it home, then the rifle is rolled to its other side.
        "assault": {
            "duration": 2.1,
            "keys": [
                rest(0.0),
                key(0.16, [0.12, 0.05, 0.07], [0.25, -0.70, 1.30]),
                key(
                    0.34,
                    [0.12, 0.05, 0.07],
                    [0.35, -0.60, 1.50],
                    pose={
                        "support": [0.05, -0.16, 0.22],
                        "supportFingers": [0.4, 0.6, 0.6, 0.6, 0.6],
                    },
                ),
                key(
                    0.46,
                    [0.12, 0.03, 0.07],
                    [0.40, -0.55, 1.55],
                    pose={
                        "support": [0.05, -0.26, 0.22],
                        "supportFingers": [0.6, 0.9, 0.9, 0.9, 0.9],
                    },
                ),
                key(
                    0.56,
                    [0.12, 0.06, 0.07],
                    [0.36, -0.58, 1.50],
                    pose={
                        "support": [0.05, -0.13, 0.22],
                        "supportFingers": [0.3, 0.3, 0.3, 0.3, 0.3],
                    },
                ),
                key(0.78, [0.08, 0.05, 0.05], [0.15, -0.35, -0.65]),
                rest(1.0),
            ],
        },
        # The left side tipped up, the charging handle pulled and let go, then
        # a look down the top of the receiver.
        "fal": {
            "duration": 2.3,
            "keys": [
                rest(0.0),
                key(0.18, [0.10, 0.06, 0.07], [0.30, -0.50, -1.15]),
                key(
                    0.36,
                    [0.10, 0.06, 0.07],
                    [0.32, -0.50, -1.20],
                    pose={
                        "support": [0.02, 0.12, 0.18],
                        "supportFingers": [0.8, 0.9, 0.9, 0.9, 0.9],
                    },
                ),
                key(
                    0.50,
                    [0.10, 0.06, 0.08],
                    [0.32, -0.50, -1.20],
                    pose={
                        "support": [0.02, 0.12, 0.32],
                        "supportFingers": [0.8, 0.9, 0.9, 0.9, 0.9],
                    },
                ),
                key(
                    0.58,
                    [0.10, 0.06, 0.07],
                    [0.30, -0.50, -1.15],
                    pose={"support": [0.02, 0.12, 0.18], "supportFingers": OPEN},
                ),
                key(0.80, [0.06, 0.09, 0.05], [0.60, -0.25, 0.20]),
                rest(1.0),
            ],
        },
        # Rolled to show the loading gate, half a pump and back, then held
        # up at the shoulder.
        "shotgun": {
            "duration": 2.2,
            "keys": [
                rest(0.0),
                key(0.20, [0.08, 0.06, -0.02], [0.20, -0.60, 2.35]),
                key(
                    0.40,
                    [0.08, 0.06, -0.02],
                    [0.25, -0.55, 2.30],
                    pose={"support": [0.0, 0.0, 0.11]},
                ),
                key(
                    0.52,
                    [0.08, 0.06, -0.02],
                    [0.25, -0.55, 2.30],
                    pose={"support": [0.0, 0.0, 0.0]},
                ),
                key(0.76, [0.04, 0.07, 0.03], [0.45, -0.20, 0.40]),
                rest(1.0),
            ],
        },
        # Raised to show the glass, the trigger hand lifts the bolt and works
        # it, then the rifle is tipped to its other side.
        "sniper": {
            "duration": 2.6,
            "keys": [
                rest(0.0),
                key(0.20, [0.08, 0.06, -0.02], [0.10, -0.85, 0.30]),
                key(
                    0.36,
                    [0.08, 0.06, -0.02],
                    [0.12, -0.80, 0.35],
                    pose={
                        "primary": [0.06, 0.08, -0.04],
                        "primaryFingers": [0.7, 0.6, 0.6, 0.6, 0.6],
                    },
                ),
                key(
                    0.50,
                    [0.08, 0.06, -0.02],
                    [0.12, -0.80, 0.35],
                    pose={
                        "primary": [0.06, 0.08, 0.08],
                        "primaryFingers": [0.7, 0.6, 0.6, 0.6, 0.6],
                    },
                ),
                key(
                    0.62,
                    [0.08, 0.06, -0.02],
                    [0.12, -0.80, 0.35],
                    pose={
                        "primary": [0.0, 0.0, 0.0],
                        "primaryFingers": [0.7, 0.4, 1.0, 1.0, 1.0],
                    },
                ),
                key(0.82, [0.08, 0.05, 0.06], [0.20, -0.40, -0.70]),
                rest(1.0),
            ],
        },
        # Tossed from the palm, one flip, caught, and the pin ring checked.
        "nade": {
            "duration": 1.7,
            "spinAxis": [1.0, 0.0, 0.0],
            "keys": [
                key(0.0, [0, 0, 0], [0, 0, 0], 0.0),
                key(
                    0.12,
                    [0.04, 0.03, 0.05],
                    [0.20, 0.0, 0.30],
                    0.0,
                    pose={"primaryFingers": OPEN},
                ),
                key(
                    0.30,
                    [0.05, 0.22, 0.07],
                    [0.25, 0.0, 0.40],
                    math.pi,
                    pose={"primaryFingers": OPEN},
                ),
                key(
                    0.46,
                    [0.05, 0.05, 0.07],
                    [0.25, 0.0, 0.45],
                    TAU,
                    pose={"primaryFingers": FIST},
                ),
                key(
                    0.70,
                    [0.04, 0.08, 0.05],
                    [0.18, 0.0, 0.60],
                    TAU,
                    pose={
                        "support": [0.04, 0.02, 0.08],
                        "supportFingers": [0.8, 0.3, 0.8, 0.8, 0.8],
                    },
                ),
                key(1.0, [0, 0, 0], [0, 0, 0], TAU),
            ],
        },
    }


def catmull_rom(p0, p1, p2, p3, u):
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * u
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * (u * u)
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * (u * u * u)
    )


def smootherstep(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def segment(keys, t):
    """The four keys around `t` and how far between the middle two it is."""
    idx = 0
    while idx < len(keys) - 2 and keys[idx + 1]["t"] < t:
        idx += 1
    k1, k2 = keys[idx], keys[idx + 1]
    k0, k3 = keys[max(0, idx - 1)], keys[min(len(keys) - 1, idx + 2)]
    span = k2["t"] - k1["t"]
    u = 0.0 if span <= 1e-9 else (t - k1["t"]) / span
    return k0, k1, k2, k3, max(0.0, min(1.0, u))


def sample_clip(clip: dict, t_norm: float) -> dict:
    """The reference sampler: pos and rot faded at the ends, spin not."""
    keys = clip["keys"]
    t = max(0.0, min(1.0, t_norm))
    k0, k1, k2, k3, u = segment(keys, t)
    pos = [
        catmull_rom(k0["pos"][i], k1["pos"][i], k2["pos"][i], k3["pos"][i], u)
        for i in range(3)
    ]
    rot = [
        catmull_rom(k0["rot"][i], k1["rot"][i], k2["rot"][i], k3["rot"][i], u)
        for i in range(3)
    ]
    spin = catmull_rom(*(k.get("spin", 0.0) for k in (k0, k1, k2, k3)), u)
    fade = 1.0
    if t < 0.05:
        fade = smootherstep(t / 0.05)
    elif t > 0.95:
        fade = smootherstep((1.0 - t) / 0.05)
    return {
        "pos": [round(p * fade, 5) for p in pos],
        "rot": [round(r * fade, 5) for r in rot],
        "spin": round(spin, 5),
    }


def main():
    clips = build_clips()
    for clip_id, clip in clips.items():
        keys = clip["keys"]
        assert keys[0]["t"] == 0.0 and keys[-1]["t"] == 1.0, clip_id
        for end in (keys[0], keys[-1]):
            turns = end.get("spin", 0.0) / TAU
            assert abs(turns - round(turns)) < 1e-9, (
                f"{clip_id}: spin must end on a whole turn"
            )
    INSPECTS_JSON.write_text(json.dumps(clips, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(clips)} inspect clips to {INSPECTS_JSON}")

    samples = [i / 16 for i in range(17)]
    golden = {
        cid: [{"t": t, **sample_clip(c, t)} for t in samples]
        for cid, c in clips.items()
    }
    GOLDEN_JSON.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote golden vectors to {GOLDEN_JSON}")


if __name__ == "__main__":
    main()
