"""How hAssault's movement and first-shot accuracy compare with Counter-Strike's.

    uv run python tools/hassault_feel_report.py [--out report.md]

A **measurement, not a tuning pass.** Every hAssault number here comes from
stepping the real `physics.step` on a flat room, and from the real
`weapons.shot_cone`, at the native client's 250 Hz command rate. So the report
describes the rules the server enforces, not the constants as someone remembers
them. Nothing is changed. Tuning goes in after a playtest, and then into all
three ports (`physics.py`, `player.ts`, `physics.rs`) with the vectors
regenerated.

The CS column is **derived, not quoted**. It steps Source's ground-movement
loop (friction, then accelerate) with the stock cvars at 64 tick:

- `sv_friction 5.2`, `sv_stopspeed 80`, `sv_accelerate 5.5`;
- `sv_gravity 800`, jump impulse 301.993 u/s;
- run speed 250 u/s, walk 130, crouch ×0.34;
- "accurate" at or below 34% of the weapon's max speed.

That is CS:GO's model and, to the precision that matters here, CS2's. Folk
numbers ("counter-strafing takes about 80 ms") are what this loop prints, so
deriving them keeps the comparison honest about which assumptions it makes.

Lengths are compared as **body heights per second**, because the two games
share no unit. A CS player is 72 units tall; ours is
`hitbox.DEFAULT.standing_height` cubes. Times are seconds in both.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backend.modules.hassault import physics, weapons  # noqa: E402

#: The native client's command rate (`INPUT_HZ` in `app.rs`).
DT = 1.0 / 250.0

# -- Counter-Strike reference (Source movement, stock cvars) ----------------

CS_TICK = 1.0 / 64.0
CS_FRICTION = 5.2
CS_STOPSPEED = 80.0
CS_ACCELERATE = 5.5
CS_GRAVITY = 800.0
CS_JUMP_IMPULSE = 301.993
CS_RUN = 250.0
CS_WALK = 130.0
CS_DUCK_SCALE = 0.34
CS_ACCURATE_SCALE = 0.34
CS_HEIGHT = 72.0


def cs_ground(v: float, wish: float, dt: float = CS_TICK) -> float:
    """One Source ground tick along one axis: friction, then accelerate.

    `wish` is the signed wish speed: `+CS_RUN` holding toward the motion,
    `-CS_RUN` counter-strafing, `0` with the key released.
    """
    speed = abs(v)
    if speed > 0.0:
        control = max(speed, CS_STOPSPEED)
        drop = control * CS_FRICTION * dt
        v *= max(0.0, speed - drop) / speed
    if wish != 0.0:
        wishdir = math.copysign(1.0, wish)
        wishspeed = abs(wish)
        current = v * wishdir
        add = wishspeed - current
        if add > 0.0:
            v += min(CS_ACCELERATE * dt * wishspeed, add) * wishdir
    return v


def cs_time(v0: float, wish: float, done) -> float:
    """Seconds of CS ticks from `v0` until `done(v)`."""
    v, t = v0, 0.0
    while not done(v) and t < 5.0:
        v = cs_ground(v, wish)
        t += CS_TICK
    return t


# -- hAssault, stepped for real ----------------------------------------------


def _world():
    return physics.flat_world(ssize=256, floor=0, ceil=60)


def _body(world, vel_x: float = 0.0) -> physics.PlayerState:
    p = physics.PlayerState(x=128.0, y=128.0, z=0.0, on_ground=True)
    p.vel_x = vel_x
    # Settle it on the floor so the first measured step is a ground step.
    physics.step(world, p, physics.MoveInput(dt=DT), DT)
    p.x, p.y, p.vel_x, p.vel_y = 128.0, 128.0, vel_x, 0.0
    return p


def ha_time(vel_x: float, forward: float, done, crouch: bool = False) -> float:
    """Seconds of 250 Hz steps, facing +x, until `done(player)`."""
    world = _world()
    p = _body(world, vel_x)
    t = 0.0
    while not done(p) and t < 5.0:
        physics.step(
            world, p, physics.MoveInput(forward=forward, crouch=crouch, dt=DT), DT
        )
        t += DT
    return t


def ha_run_speed() -> float:
    return physics.MOVE_SPEED


def ha_jump() -> tuple[float, float]:
    """Apex above the floor and time in the air for a standing jump."""
    world = _world()
    p = _body(world)
    physics.step(world, p, physics.MoveInput(jump=True, dt=DT), DT)
    apex, t = p.z, DT
    while not p.on_ground and t < 5.0:
        physics.step(world, p, physics.MoveInput(dt=DT), DT)
        apex = max(apex, p.z)
        t += DT
    return apex, t


# -- the report ---------------------------------------------------------------


@dataclass
class Row:
    what: str
    ours: str
    cs: str
    note: str = ""


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms"


def movement_rows() -> list[Row]:
    height = physics.STANDING_HEIGHT
    run = ha_run_speed()
    accurate = run * weapons.ACCURATE_SPEED_SCALE
    cs_accurate = CS_RUN * CS_ACCURATE_SCALE

    def hps(v: float, h: float) -> str:
        return f"{v / h:.2f} heights/s"

    rows = [
        Row("Run speed", hps(run, height), hps(CS_RUN, CS_HEIGHT)),
        Row(
            "Walk speed",
            f"×{physics.WALK_SPEED_SCALE:.2f} of run",
            f"×{CS_WALK / CS_RUN:.2f} of run",
        ),
        Row(
            "Crouch speed",
            f"×{physics.CROUCH_SPEED_SCALE:.2f} of run",
            f"×{CS_DUCK_SCALE:.2f} of run",
        ),
        Row(
            "Accurate below",
            f"×{weapons.ACCURATE_SPEED_SCALE:.2f} of run",
            f"×{CS_ACCURATE_SCALE:.2f} of max",
        ),
    ]

    ours = ha_time(0.0, 1.0, lambda p: p.vel_x >= 0.95 * run)
    cs = cs_time(0.0, CS_RUN, lambda v: v >= 0.95 * CS_RUN)
    rows.append(Row("Rest → 95% run", _ms(ours), _ms(cs)))

    ours = ha_time(0.0, 1.0, lambda p: p.vel_x > accurate)
    cs = cs_time(0.0, CS_RUN, lambda v: v > cs_accurate)
    rows.append(
        Row(
            "Rest → inaccurate",
            _ms(ours),
            _ms(cs),
            "How long a peek is accurate after the key goes down",
        )
    )

    ours = ha_time(run, 0.0, lambda p: p.vel_x <= accurate)
    cs = cs_time(CS_RUN, 0.0, lambda v: v <= cs_accurate)
    rows.append(Row("Release → accurate", _ms(ours), _ms(cs), "No counter-strafe"))

    ours = ha_time(run, -1.0, lambda p: p.vel_x <= accurate)
    cs = cs_time(CS_RUN, -CS_RUN, lambda v: v <= cs_accurate)
    rows.append(
        Row(
            "**Counter-strafe → accurate**",
            f"**{_ms(ours)}**",
            f"**{_ms(cs)}**",
            "The opposite key, from full run",
        )
    )

    ours = ha_time(run, -1.0, lambda p: p.vel_x <= 0.0)
    cs = cs_time(CS_RUN, -CS_RUN, lambda v: v <= 0.0)
    rows.append(Row("Counter-strafe → stopped", _ms(ours), _ms(cs)))

    ours = ha_time(run, 0.0, lambda p: p.vel_x <= 0.01 * run)
    cs = cs_time(CS_RUN, 0.0, lambda v: v <= 0.01 * CS_RUN)
    rows.append(Row("Release → stopped (1%)", _ms(ours), _ms(cs)))

    crouch_speed = run * physics.CROUCH_SPEED_SCALE
    ours = ha_time(run, 1.0, lambda p: p.crouch >= 1.0, crouch=True)
    rows.append(
        Row(
            "Stand → full crouch",
            _ms(ours),
            "—",
            "`CROUCH_TRANSITION`. CS's duck time is not one cvar (it slows with duck spam), so no figure is given",
        )
    )
    ours = ha_time(run, 1.0, lambda p: p.vel_x <= crouch_speed * 1.05, crouch=True)
    rows.append(
        Row(
            "Run → crouch speed",
            _ms(ours),
            "—",
            "Holding forward and crouch; the speed cap drops once the crouch is past halfway",
        )
    )

    apex, air = ha_jump()
    cs_apex = CS_JUMP_IMPULSE**2 / (2 * CS_GRAVITY)
    cs_air = 2 * CS_JUMP_IMPULSE / CS_GRAVITY
    rows.append(
        Row(
            "Jump apex",
            f"{apex / height:.2f} heights",
            f"{cs_apex / CS_HEIGHT:.2f} heights",
            "Feet above the floor, standing jump",
        )
    )
    rows.append(
        Row(
            "Jump air time",
            _ms(air),
            _ms(cs_air),
            "Ours ramps gravity with time in the air (`GRAVITY_RAMP`), so the fall is quicker than the rise",
        )
    )
    return rows


def _deg(rad: float) -> str:
    return f"{math.degrees(rad):.2f}°"


def weapon_rows() -> list[list[str]]:
    run = physics.MOVE_SPEED
    walk = run * physics.WALK_SPEED_SCALE
    out = []
    for w in weapons.WEAPONS:
        if w.slot == "melee":
            continue
        rest = weapons.shot_cone(w, weapons.ShotContext())
        first = _deg(rest)
        if w.zoom_levels:
            # A scoped weapon's first shot is the scoped one; the hip cone is
            # what it costs not to scope.
            scoped = weapons.shot_cone(w, weapons.ShotContext(scoped=1))
            first = f"{_deg(scoped)} scoped ({_deg(rest)} hip)"
        follow = weapons.shot_cone(w, weapons.ShotContext(since_last_shot=w.interval))
        walking = weapons.shot_cone(w, weapons.ShotContext(speed=walk))
        running = weapons.shot_cone(w, weapons.ShotContext(speed=run))
        air = weapons.shot_cone(w, weapons.ShotContext(airborne=True, speed=run))
        # First-shot accuracy comes back once `recovery_time` has passed; a
        # pattern restarts after `spray_reset`. A tap rhythm slower than the
        # larger of the two gets a fresh first shot every time.
        tap = max(w.recovery_time, w.spray_reset if w.spray else 0.0)
        out.append(
            [
                w.name,
                f"{w.rpm:.0f}",
                first,
                _deg(follow),
                _ms(tap),
                _deg(walking),
                _deg(running),
                _deg(air),
            ]
        )
    return out


def render() -> str:
    lines = [
        "# hAssault movement and first-shot feel vs Counter-Strike",
        "",
        "Generated by `tools/hassault_feel_report.py` from the server's own "
        "`physics.step` and `weapons.shot_cone`, at 250 Hz. CS figures are "
        "Source's ground movement stepped at 64 tick with stock cvars. "
        "**Measurement only: nothing here has been tuned.**",
        "",
        "## Movement",
        "",
        "| Measure | hAssault | CS (derived) | Note |",
        "| --- | --- | --- | --- |",
    ]
    for r in movement_rows():
        lines.append(f"| {r.what} | {r.ours} | {r.cs} | {r.note} |")
    lines += [
        "",
        "## First-shot accuracy",
        "",
        "Cones are the half-angle `shot_cone` returns, standing unless stated. "
        "*Follow-up* is a shot at the weapon's full cyclic rate after another. "
        "*Fresh first shot every* is the slowest tap rhythm that still gets "
        "first-shot accuracy and a restarted pattern each time.",
        "",
        "| Weapon | RPM | First shot | Follow-up | Fresh first shot every | Walking | Running | Airborne |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in weapon_rows():
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", help="also write the markdown here")
    args = ap.parse_args()
    text = render()
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


if __name__ == "__main__":
    main()
