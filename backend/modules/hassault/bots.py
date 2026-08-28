"""Bot players.

A bot is not a second kind of entity. It is a `MatchPlayer` whose input happens
to be produced on this machine instead of arriving over a socket: `BotBrain.think`
returns a `Command`, the room enqueues it through the same `enqueue` a browser's
input goes through, and it is validated, budgeted and simulated by exactly the
same code. That is the whole design, and it buys two things worth having — a bot
cannot do anything a player could not (no wall-clipping, no infinite ammo, no
firing faster than time passes), and there is only ever one simulation to debug.

The AI is deliberately reactive rather than planned. There is no navmesh: an
AssaultCube map is a heightfield of columns, and building a graph over it is a
larger piece of work than the rest of this file. Instead a bot steers toward
something it wants and probes ahead with the *movement code's own* `can_stand`
before committing — so what it believes it can walk through is precisely what it
can walk through. It gets lost in complicated geometry sometimes. It also, when
it rounds a corner and shoots you, feels like an opponent, which is the point.

Aim is the part that has to be wrong on purpose. A bot can compute the exact
angle to your head every tick; a bot that *uses* it is not difficult, it is
unplayable. So aim is a rate-limited turn toward the target plus a wandering
error term, and the skill levels are mostly settings for those two numbers.

See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.modules.hassault import physics, weapons
from backend.modules.hassault.weapons import PLAYER_EYE_HEIGHT

if TYPE_CHECKING:
    from backend.modules.hassault.match import Command, MatchPlayer, MatchRoom


@dataclass(frozen=True, slots=True)
class Skill:
    """One difficulty setting.

    `turn_rate` and `aim_error` are what a human actually experiences as
    difficulty; the rest mostly decide how often a bot notices you at all.
    """

    name: str
    """Radians per second the view may turn. A human flick is ~10 rad/s."""
    turn_rate: float
    """Steady-state aim error in radians. At 40 cubes, 0.05 rad is ~2 cubes."""
    aim_error: float
    """Seconds between spotting a target and being able to shoot at it."""
    reaction: float
    view_range: float
    """Half-angle of the cone the bot notices targets in."""
    fov: float
    """How close the aim must be before the trigger is pulled."""
    fire_angle: float
    """Chance per second of a dodge jump while engaging."""
    jumpiness: float
    """How readily the bot crouches to steady a long shot, 0..1.

    Not a separate behaviour so much as an expression of the same trade a player
    makes: crouching narrows its own hitbox and braces its recoil, at the cost of
    most of its speed. So a bot only does it at range, where standing still is
    survivable — and a better bot does it more often.
    """
    poise: float = 0.0


SKILLS: dict[str, Skill] = {
    "easy": Skill(
        name="easy",
        turn_rate=2.2,
        aim_error=0.10,
        reaction=0.65,
        view_range=70.0,
        fov=1.15,
        fire_angle=0.13,
        jumpiness=0.1,
        poise=0.15,
    ),
    "normal": Skill(
        name="normal",
        turn_rate=4.2,
        aim_error=0.045,
        reaction=0.35,
        view_range=110.0,
        fov=1.4,
        fire_angle=0.06,
        jumpiness=0.3,
        poise=0.45,
    ),
    "hard": Skill(
        name="hard",
        turn_rate=7.5,
        aim_error=0.017,
        reaction=0.16,
        view_range=170.0,
        fov=1.6,
        fire_angle=0.028,
        jumpiness=0.6,
        poise=0.8,
    ),
}
DEFAULT_SKILL = "normal"

# Names, so a scoreboard reads like a match rather than like a test fixture.
BOT_NAMES = (
    "Rook",
    "Vex",
    "Marlow",
    "Kestrel",
    "Dobbs",
    "Iona",
    "Sarge",
    "Pike",
    "Nomad",
    "Quill",
    "Bishop",
    "Ferro",
)

# How far ahead the bot checks it can walk. A little over its own diameter (2.2),
# so it commits to a heading only when there is room to actually take a step.
PROBE_AHEAD = 2.6

# Headings tried, in order, when the one it wants is blocked. Symmetric pairs so
# a bot in a corridor does not develop a permanent preference for turning left.
AVOID_OFFSETS = (0.0, 0.45, -0.45, 0.95, -0.95, 1.7, -1.7, math.pi)

# Distances the bot tries to keep from whatever it is shooting at.
CLOSE_RANGE = 11.0
LONG_RANGE = 42.0

# Below this displacement over `STUCK_WINDOW` seconds, a bot that is trying to
# move is considered wedged and picks a new heading.
STUCK_WINDOW = 1.0
STUCK_DISTANCE = 1.5


class BotBrain:
    """One bot's mind. Ticked once per server tick by `MatchRoom._think`."""

    def __init__(self, skill: str = DEFAULT_SKILL, seed: int | None = None) -> None:
        self.skill = SKILLS.get(skill, SKILLS[DEFAULT_SKILL])
        self.rng = random.Random(seed)
        self.target_id: str | None = None
        self.seen_at = 0.0
        self.retarget_in = 0.0
        # Wandering aim error, in radians, re-drawn slowly rather than every tick:
        # per-tick noise averages out to a perfect shot at 20 Hz.
        self.error_yaw = 0.0
        self.error_pitch = 0.0
        self.error_in = 0.0
        self.strafe_dir = 1.0
        self.strafe_in = 0.0
        self.roam: tuple[float, float] | None = None
        self.roam_in = 0.0
        self.switch_in = 0.0
        self.stuck_in = STUCK_WINDOW
        self.last_pos = (0.0, 0.0)
        self.moved = 0.0
        self.avoid_bias = 0.0

    # -- perception ---------------------------------------------------------

    def _visible(self, room: MatchRoom, me: MatchPlayer, other: MatchPlayer) -> bool:
        """Whether `other` is in front of `me`, in range, and not behind a wall.

        Uses the same `raycast_world` weapons use, so a bot never shoots at
        something it could not have hit — and never fails to see something it
        could.
        """
        dx = other.state.x - me.state.x
        dy = other.state.y - me.state.y
        distance = math.hypot(dx, dy)
        if distance > self.skill.view_range:
            return False
        if distance > 0.1:
            bearing = math.atan2(dy, dx)
            if abs(_wrap(bearing - me.state.yaw)) > self.skill.fov:
                return False
        eye = weapons.eye_position(me.state.x, me.state.y, me.state.z)
        target = weapons.eye_position(other.state.x, other.state.y, other.state.z)
        vx, vy, vz = target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]
        length = math.sqrt(vx * vx + vy * vy + vz * vz)
        if length < 1e-6:
            return True
        reach = weapons.raycast_world(
            room.world, eye, (vx / length, vy / length, vz / length), length
        )
        # Half a cube of slack: the ray ends *at* the target, and a heightfield
        # cell it grazes on the way should not count as cover.
        if reach < length - 0.5:
            return False
        # A smoke is cover too. Asked here rather than only in the renderer,
        # because a cloud that only humans have to respect is not a mechanic —
        # it is a handicap: a bot would keep shooting straight through the one
        # thing a player threw to stop being shot.
        return not room.smoked(eye, target)

    def _acquire(
        self, room: MatchRoom, me: MatchPlayer, dt: float
    ) -> MatchPlayer | None:
        current = room.players.get(self.target_id or "")
        if current is not None and (
            not current.alive
            or current.team == me.team
            or not self._visible(room, me, current)
        ):
            current = None
            self.target_id = None

        self.retarget_in -= dt
        if current is not None and self.retarget_in > 0:
            return current

        self.retarget_in = 0.3
        best: MatchPlayer | None = None
        best_distance = math.inf
        for other in room.players.values():
            if other.id == me.id or other.team == me.team or not other.alive:
                continue
            if not self._visible(room, me, other):
                continue
            distance = math.hypot(
                other.state.x - me.state.x, other.state.y - me.state.y
            )
            if distance < best_distance:
                best, best_distance = other, distance

        if best is None:
            return current
        if best.id != self.target_id:
            self.target_id = best.id
            # The reaction delay is charged from the moment of acquisition, so a
            # bot that walks into the open is shootable before it shoots.
            self.seen_at = 0.0
        return best

    # -- steering -----------------------------------------------------------

    def _clear_ahead(self, room: MatchRoom, me: MatchPlayer, heading: float) -> bool:
        return physics.can_stand(
            room.world,
            me.state.x + math.cos(heading) * PROBE_AHEAD,
            me.state.y + math.sin(heading) * PROBE_AHEAD,
            me.state.z,
        )

    def _steer(self, room: MatchRoom, me: MatchPlayer, wanted: float) -> float:
        """The nearest heading to `wanted` the bot can actually walk.

        `avoid_bias` makes it try the same side first for a moment after an
        avoidance, so it commits to going *round* an obstacle rather than
        oscillating in front of it.
        """
        offsets = AVOID_OFFSETS
        if self.avoid_bias < 0:
            offsets = tuple(-o for o in AVOID_OFFSETS)
        for offset in offsets:
            heading = wanted + offset
            if self._clear_ahead(room, me, heading):
                if offset:
                    self.avoid_bias = 1.0 if offset > 0 else -1.0
                return heading
        return wanted + math.pi

    def _pick_roam(self, room: MatchRoom, me: MatchPlayer) -> None:
        """Somewhere to go when there is nobody to fight.

        Spawn points, because they are the only positions in the file that are
        known to be standable and are spread across the playable area — a random
        cell is usually inside a wall.

        **Enemy** spawns first. A bot that wanders uniformly is nearly useless on
        a 256-cube map: four of them will happily spend a minute never meeting,
        which is what "add some bots" is supposed to prevent. Heading for the
        other side's spawns funnels both teams into the middle, and it is map
        knowledge rather than player knowledge — the bot is not tracking anyone,
        it is walking towards where the enemy comes from.
        """
        enemy = 1 - me.team
        options = [
            (s.x + 0.5, s.y + 0.5)
            for s in room.spawns
            if getattr(s, "attr2", None) == enemy
            and math.hypot(s.x - me.state.x, s.y - me.state.y) > 8
        ]
        if not options:
            # Deathmatch spawns (AC marks them `attr2 == 100`) and maps with no
            # team spawns at all both land here.
            options = [
                (s.x + 0.5, s.y + 0.5)
                for s in room.spawns
                if math.hypot(s.x - me.state.x, s.y - me.state.y) > 12
            ]
        if options:
            self.roam = self.rng.choice(options)
        else:
            angle = self.rng.random() * math.tau
            span = 20.0
            self.roam = (
                me.state.x + math.cos(angle) * span,
                me.state.y + math.sin(angle) * span,
            )
        self.roam_in = 12.0

    # -- weapons ------------------------------------------------------------

    @staticmethod
    def _has_rounds(me: MatchPlayer, index: int) -> bool:
        weapon = weapons.weapon_at(index)
        if weapon.mag <= 0:
            return True  # the knife never runs out
        reserve = me.reserve.get(index, 0)
        return me.ammo.get(index, 0) > 0 or reserve != 0

    def _choose_weapon(self, me: MatchPlayer, distance: float, dt: float) -> int:
        """Pick a weapon for the range, but not more often than every couple of
        seconds — a bot that reconsiders every tick spends the match switching.

        The dry-weapon case skips the timer entirely. Waiting two seconds to
        notice you are holding an empty rifle is exactly the moment you die, and
        it is also how a bot ends a long match standing still pulling a trigger
        that does nothing.
        """
        dry = not self._has_rounds(me, me.weapon)
        self.switch_in -= dt
        if self.switch_in > 0 and not dry:
            return -1
        if distance > 70:
            wanted = weapons.WEAPON_BY_ID["sniper"]
        elif distance < 16:
            wanted = weapons.WEAPON_BY_ID["shotgun"]
        else:
            wanted = weapons.WEAPON_BY_ID["assault"]
        index = weapons.WEAPONS.index(wanted)
        if not self._has_rounds(me, index):
            # Second choice is whatever still has rounds, nearest slot first, so
            # a bot out of rifle ammo falls back to the sidearm rather than to
            # the knife.
            index = next(
                (
                    i
                    for i in sorted(
                        range(len(weapons.WEAPONS)),
                        key=lambda i: -weapons.WEAPONS[i].range,
                    )
                    if self._has_rounds(me, i)
                ),
                me.weapon,
            )
        if index == me.weapon:
            return -1
        self.switch_in = 2.5
        return index

    # -- the tick -----------------------------------------------------------

    def think(self, room: MatchRoom, me: MatchPlayer, dt: float) -> Command | None:
        from backend.modules.hassault.match import Command

        if not me.alive:
            # Nothing to say while dead. The room respawns on its own clock.
            self.target_id = None
            return None
        dt = max(1e-3, min(dt, physics.MAX_STEP_DT))

        target = self._acquire(room, me, dt)
        yaw, pitch = me.state.yaw, me.state.pitch
        fire = False
        reload_now = False
        switch = -1
        distance = math.inf

        self.error_in -= dt
        if self.error_in <= 0:
            self.error_in = 0.25 + self.rng.random() * 0.35
            self.error_yaw = self.rng.gauss(0.0, self.skill.aim_error)
            self.error_pitch = self.rng.gauss(0.0, self.skill.aim_error * 0.6)

        if target is not None:
            self.seen_at += dt
            dx = target.state.x - me.state.x
            dy = target.state.y - me.state.y
            distance = math.hypot(dx, dy)
            # Aim at the chest rather than the feet or the head: the head is a
            # 1-cube band, and a bot that aims for it with any error at all
            # mostly shoots over people.
            dz = (target.state.z + PLAYER_EYE_HEIGHT * 0.75) - (
                me.state.z + PLAYER_EYE_HEIGHT
            )
            wanted_yaw = math.atan2(dy, dx) + self.error_yaw
            wanted_pitch = math.atan2(dz, max(distance, 0.001)) + self.error_pitch
            limit = self.skill.turn_rate * dt
            yaw = me.state.yaw + _clamp(_wrap(wanted_yaw - me.state.yaw), -limit, limit)
            pitch = me.state.pitch + _clamp(
                wanted_pitch - me.state.pitch, -limit, limit
            )
            pitch = _clamp(pitch, -1.5, 1.5)

            switch = self._choose_weapon(me, distance, dt)
            weapon = weapons.weapon_at(switch if switch >= 0 else me.weapon)
            # Measured against where the bot *thinks* it is aiming, error and
            # all — so it pulls the trigger with confidence and then misses by
            # `error_yaw`. Subtracting the error back out here would produce the
            # opposite and much worse behaviour: a bot that simply holds fire
            # until it is perfectly on target, and so never misses.
            aim_off = abs(_wrap(wanted_yaw - yaw))
            if me.ammo.get(me.weapon, 0) <= 0 and weapon.mag > 0:
                reload_now = True
            elif (
                self.seen_at >= self.skill.reaction
                and aim_off <= self.skill.fire_angle
                and distance <= weapon.range * 0.85
            ):
                fire = True
        else:
            self.seen_at = 0.0
            # Reload in the quiet, which is when a human does it too.
            weapon = weapons.weapon_at(me.weapon)
            if weapon.mag > 0 and me.ammo.get(me.weapon, 0) < weapon.mag * 0.4:
                reload_now = True

        # -- where to walk --------------------------------------------------
        self.strafe_in -= dt
        if self.strafe_in <= 0:
            self.strafe_in = 0.7 + self.rng.random() * 1.1
            self.strafe_dir = -self.strafe_dir

        if target is not None:
            bearing = math.atan2(
                target.state.y - me.state.y, target.state.x - me.state.x
            )
            if distance < CLOSE_RANGE:
                wanted_heading = bearing + math.pi + self.strafe_dir * 0.6
            elif distance > LONG_RANGE:
                wanted_heading = bearing + self.strafe_dir * 0.35
            else:
                wanted_heading = bearing + self.strafe_dir * 1.15
        else:
            self.roam_in -= dt
            if self.roam is None or self.roam_in <= 0:
                self._pick_roam(room, me)
            assert self.roam is not None
            if math.hypot(self.roam[0] - me.state.x, self.roam[1] - me.state.y) < 4:
                self._pick_roam(room, me)
            wanted_heading = math.atan2(
                self.roam[1] - me.state.y, self.roam[0] - me.state.x
            )

        heading = self._steer(room, me, wanted_heading)
        self.avoid_bias *= 0.9

        # -- stuck detection ------------------------------------------------
        self.stuck_in -= dt
        self.moved += math.hypot(
            me.state.x - self.last_pos[0], me.state.y - self.last_pos[1]
        )
        self.last_pos = (me.state.x, me.state.y)
        jump = False
        if self.stuck_in <= 0:
            if self.moved < STUCK_DISTANCE:
                # Wedged on geometry the probe was happy with — a lip, a corner,
                # a doorway it keeps clipping. Turn hard and try a hop.
                heading += self.rng.choice((-1.0, 1.0)) * (1.2 + self.rng.random())
                self.roam = None
                jump = True
            self.stuck_in = STUCK_WINDOW
            self.moved = 0.0

        if target is not None and self.rng.random() < self.skill.jumpiness * dt:
            jump = True

        # -- crouch ---------------------------------------------------------
        #
        # Only at range, and never while jumping. This is the same trade a player
        # makes rather than a bot-only trick: a narrower hitbox and a braced shot
        # for most of its speed, which is survivable at forty cubes and suicide at
        # ten. Producing it here means it goes through `enqueue` like everything
        # else — a bot crouches by pressing the key, not by editing its own state.
        crouch = (
            target is not None
            and not jump
            and distance > LONG_RANGE * 0.6
            and self.rng.random() < self.skill.poise
        )

        # -- look where you are going ---------------------------------------
        #
        # With a target, `yaw` was already turned toward it above and the body
        # walks in whatever direction the strafe pattern asked for. With *no*
        # target there was nothing turning `yaw` at all, so a roaming bot kept
        # the yaw it spawned with for the whole match: it slid to its roam point
        # sideways or backwards, facing one fixed direction forever. That reads
        # from the outside as a model that cannot rotate rather than as an aim
        # bug, because every bot spawns level and most spawn facing the same way.
        #
        # So an untargeted bot turns toward where it is walking, rate-limited by
        # the same `turn_rate` its aim is — a bot that snapped instantly to each
        # new roam point would be the other tell — and levels its pitch off, so
        # one that lost a target while looking down does not roam staring at the
        # floor.
        #
        # After `heading` is final, deliberately: the stuck detector turns it
        # hard, and turning to face a heading the bot then abandons is what
        # produces the twitch.
        if target is None:
            limit = self.skill.turn_rate * dt
            yaw = yaw + _clamp(_wrap(heading - yaw), -limit, limit)
            pitch = pitch - _clamp(pitch, -limit, limit)

        # Movement is expressed in the player's own frame, so a bot that is aiming
        # one way and walking another — which is most of a firefight — needs the
        # heading rotated into it.
        relative = _wrap(heading - yaw)
        forward = math.cos(relative)
        strafe = math.sin(relative)

        # -- scope ----------------------------------------------------------
        #
        # A bot that picked a scoped weapon picked it *for the range*, so it uses
        # the scope for the same reason a player would. Without this the hip-fire
        # penalty would land entirely on the bots — `_choose_weapon` hands them
        # the sniper past `LONG_RANGE` and they would then fire it through a cone
        # twenty-seven times too wide, which reads as the bots getting worse
        # rather than as the scope being a mechanic.
        #
        # The second step is kept for genuinely long shots. It costs a bot
        # nothing to be at 4× — it has no screen to lose peripheral vision on —
        # so tying it to distance keeps the bot's zoom a consequence of the same
        # judgement a player makes instead of a free maximum.
        held = weapons.weapon_at(me.weapon)
        scoped = 0
        if held.zoom_levels and target is not None:
            scoped = 2 if distance > LONG_RANGE * 1.5 else 1

        me.bot_seq += 1
        return Command(
            seq=me.bot_seq,
            forward=_clamp(forward, -1.0, 1.0),
            strafe=_clamp(strafe, -1.0, 1.0),
            jump=jump,
            crouch=crouch,
            yaw=yaw,
            pitch=pitch,
            dt=dt,
            fire=fire,
            reload=reload_now,
            weapon=switch,
            scoped=scoped,
            # No rewind: a bot's input is produced here, on this tick, so the
            # world it "saw" is the world as it is.
            view_t=None,
        )


def _wrap(angle: float) -> float:
    """An angle folded into (-π, π]. Without this a bot turning past π takes the
    long way round — the same bug entity interpolation has on the client."""
    return (angle + math.pi) % math.tau - math.pi


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def add_bots(
    room: MatchRoom,
    count: int = 1,
    skill: str = DEFAULT_SKILL,
    team: int | None = None,
) -> list[MatchPlayer]:
    """Put `count` bots into a room, named and teamed.

    Team defaults to balancing, exactly as a human joining does: with one human
    on team 0, the first bot lands on team 1 and the second back on team 0 — an
    enemy and an ally. Pass `team` to stack them all one way, which is what you
    want for "give me three to shoot at".
    """
    from backend.modules.hassault.match import MAX_PLAYERS

    taken = {p.name for p in room.players.values()}
    out: list[MatchPlayer] = []
    for _ in range(max(0, count)):
        if len(room.players) >= MAX_PLAYERS:
            break
        name = next(
            (f"[bot] {n}" for n in BOT_NAMES if f"[bot] {n}" not in taken),
            f"[bot] {len(room.players)}",
        )
        taken.add(name)
        brain = BotBrain(skill=skill, seed=room.rng.randrange(1 << 30))
        out.append(room.add(name, None, brain=brain, team=team))
    return out
