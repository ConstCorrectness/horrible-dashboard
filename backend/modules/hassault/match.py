"""The authoritative match server.

The server owns every player's position. Clients predict locally so movement
feels instant, but what the server simulates is what happened; a client that
disagrees is corrected. This is the standard prediction/reconciliation shape, and
the three pieces that make it work are:

**Every input carries a sequence number.** The client keeps each command it sent
until the server acknowledges it. Snapshots carry `ack` — the last command the
server consumed *from that client* — so the client knows exactly which of its own
predictions are still unconfirmed and can replay just those.

**The server advances a player only on that player's own commands**, each with
the `dt` the client measured, rather than ticking everyone by wall-clock. A
client whose frames are 8 ms and one whose frames are 33 ms then travel the same
distance per second, and — much more importantly — the server integrates exactly
the same sequence of steps the client predicted with, so a correct prediction
reconciles to zero error instead of to a small permanent jitter.

**Client-supplied `dt` is spent from a replenishing budget.** Trusting the client
for `dt` is trusting it for speed, so each player earns simulated time at real
time (plus a jitter allowance) into a small reservoir. Bursts after a stall are
absorbed; a client that simply claims time faster than it passes runs the
reservoir dry and is throttled to real time. It is a cap on the exploit, not a
lie detector — this is a game you host for friends, not a public ladder.

Combat rides the same rails. Firing is a **flag on a movement command**, not a
message of its own, so a shot arrives with the exact view angles and sequence
number of the frame it happened on; and the rate limiter is spent from that same
budget, so nobody's rifle fires faster than time passes. Hit registration itself
lives in `weapons.py`, which also explains the rewind. Bots (`bots.py`) enqueue
commands through the identical path — they are players whose input happens to be
generated here, which is what stops "bots" becoming a second simulation.

See docs/modules/hassault.mdx.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.modules.hassault import assets, noise, physics, weapons
from backend.modules.hassault.cgz import CgzError
from backend.modules.hassault.noise import Noise
from backend.modules.hassault.physics import MoveInput, PlayerState, World
from backend.modules.hassault.weapons import (
    MAX_HEALTH,
    RESPAWN_DELAY,
    SPAWN_PROTECT,
    PositionHistory,
)

if TYPE_CHECKING:
    from backend.modules.hassault.bots import BotBrain
    from backend.modules.ws import WsConnection

logger = logging.getLogger(__name__)

CHANNEL = "hassault"

# Snapshots per second. 20 Hz with ~100 ms of client-side interpolation delay is
# the classic Source-engine setting and holds up: remote players are rendered
# from two snapshots that have both already arrived, so ordinary jitter never
# shows. Raising this costs bandwidth linearly and buys very little.
SNAPSHOT_HZ = 20
TICK_INTERVAL = 1.0 / SNAPSHOT_HZ

# Simulated seconds a player may bank against real time. Roughly four frames:
# enough to absorb a stutter or a burst of coalesced commands, far too little to
# be worth exploiting.
BUDGET_CEILING = 0.25
# Jitter allowance on the earn rate. A client's clock and ours disagree slightly
# and its `dt` measurements are noisy; without a little headroom an honest client
# would be throttled by rounding.
BUDGET_EARN_RATE = 1.1

# Commands held for a player who is behind. Beyond this the oldest are dropped:
# an unbounded queue turns a lagging client into unbounded memory, and stale
# movement commands are worthless anyway by the time they would be simulated.
MAX_QUEUED_COMMANDS = 64

# No commands for this long and a player is shown as stale rather than silently
# standing still — the distinction matters when you are wondering whether to
# shoot at someone.
STALE_AFTER = 2.0

MAX_PLAYERS = 16
MAX_NAME_LEN = 24

# Effects carried in one snapshot. A tick's worth of shots from sixteen players
# is a handful; a number far past that is a client repeating itself, and the
# packet is not the place to find out.
MAX_FX_PER_TICK = 64

# Hitmarkers held for one player between snapshots. Eight pellets of a shotgun on
# two bodies is the realistic worst case.
MAX_PENDING_HITS = 24

# Noises produced in one tick, before the room stops recording them. Sixteen
# players cannot make many more than this in 50 ms, and the cap is what stops a
# pathological case turning into a per-recipient audibility sweep of unbounded size.
MAX_NOISE_PER_TICK = 48

# How long an empty room is kept before it is retired. A room opened for a friend
# who has not clicked the invite yet is empty and must survive; a room everyone
# has left is ~590 KB of map planes and should not.
EMPTY_GRACE = 60.0


@dataclass(slots=True)
class Command:
    """One client input frame.

    The combat fields ride here rather than in messages of their own so that a
    shot carries the sequence number and view angles of the exact frame it was
    fired on, and so the fabric — which forwards commands verbatim — needed no
    changes at all to carry weapons across the peer wire.
    """

    seq: int
    forward: float
    strafe: float
    jump: bool
    yaw: float
    pitch: float
    dt: float
    crouch: bool = False
    fire: bool = False
    reload: bool = False
    """Weapon slot to switch to, or `-1` for no change."""
    weapon: int = -1
    """Server-clock ms the client was *rendering* when it fired. See `weapons.py`
    — this is what the shot is rewound to, clamped."""
    view_t: float | None = None
    """Zoom step the client says it was scoped to: 0 for none, 1-based into the
    weapon's `zoom_levels`.

    Client-owned like the view angles, and for the same reason — the scope only
    changes what the player can see and how far the mouse moves them, both of
    which already live on their machine. What the server does with it is decide
    the shot's cone, so it is clamped against the weapon actually held rather
    than believed. See `weapons.clamp_zoom`.
    """
    scoped: int = 0


@dataclass(slots=True)
class MatchPlayer:
    id: str
    name: str
    team: int
    state: PlayerState
    conn: Any = None
    queue: deque[Command] = field(default_factory=deque)
    # Last command sequence the simulation has actually consumed. Sent back as
    # `ack` so the client knows what to replay.
    ack: int = 0
    # Highest sequence ever seen, used to drop duplicates and reorders. Distinct
    # from `ack`, which only moves when a command is simulated.
    high_seq: int = 0
    budget: float = 0.0
    last_command_at: float = field(default_factory=time.monotonic)
    joined_at: float = field(default_factory=time.monotonic)
    rtt_ms: float = 0.0

    # -- combat -------------------------------------------------------------
    health: float = MAX_HEALTH
    alive: bool = True
    weapon: int = weapons.DEFAULT_WEAPON
    ammo: dict[int, int] = field(default_factory=dict)
    reserve: dict[int, int] = field(default_factory=dict)
    kills: int = 0
    deaths: int = 0
    # Simulated seconds this player has been advanced by. Fire rate and reloads
    # are measured on this clock, not the wall clock: commands arrive in batches,
    # and gating on real time would silently halve a fast weapon's rate. It is
    # bounded by the same budget as movement, so it cannot outrun real time.
    sim_time: float = 0.0
    last_fire_at: float = -999.0
    reload_until: float = -999.0
    """Wall clock, unlike the two above: a dead player stops sending commands, so
    a respawn measured on their simulated time would never come."""
    respawn_at: float = 0.0
    protected_until: float = 0.0
    # Hitmarker feedback, drained into that player's own snapshot envelope.
    pending_hits: list[dict[str, Any]] = field(default_factory=list)
    """Cubes travelled since the last footstep. A footstep every `STRIDE_DISTANCE`
    of ground covered, rather than on a timer: a player who is barely moving is
    barely audible, which is what makes creeping forward a real option even
    standing up."""
    stride: float = 0.0
    """Health the last landing cost, drained into that player's own envelope so
    the HUD can say why the number dropped."""
    last_fall: float = 0.0
    # Set for bot players. Also what distinguishes them from a human whose socket
    # happens to be `None` (which is every player in the unit tests).
    brain: BotBrain | None = None
    bot_seq: int = 0

    @property
    def is_bot(self) -> bool:
        return self.brain is not None

    def reset_loadout(self) -> None:
        """Full health and full magazines. Called on spawn and on respawn."""
        self.health = MAX_HEALTH
        self.alive = True
        self.weapon = weapons.DEFAULT_WEAPON
        self.ammo = {i: w.mag for i, w in enumerate(weapons.WEAPONS)}
        self.reserve = {i: w.reserve for i, w in enumerate(weapons.WEAPONS)}
        self.reload_until = -999.0
        self.last_fire_at = -999.0
        self.protected_until = time.monotonic() + SPAWN_PROTECT

    @property
    def protected(self) -> bool:
        return time.monotonic() < self.protected_until

    def snapshot(self, now: float) -> dict[str, Any]:
        """The wire form. Rounded hard — a millimetre of a cube is not a thing
        anyone can see, and the digits are most of the packet."""
        return {
            "id": self.id,
            "name": self.name,
            "team": self.team,
            "x": round(self.state.x, 3),
            "y": round(self.state.y, 3),
            "z": round(self.state.z, 3),
            "yaw": round(self.state.yaw, 3),
            "pitch": round(self.state.pitch, 3),
            "ground": self.state.on_ground,
            "stale": (now - self.last_command_at) > STALE_AFTER,
            "rtt": round(self.rtt_ms),
            # Health is public: a wounded enemy is exactly the information that
            # makes a firefight a decision rather than a coin toss, and every
            # shooter since Quake has shown it on the hit feedback anyway.
            "hp": round(self.health),
            "alive": self.alive,
            "weapon": self.weapon,
            "kills": self.kills,
            "deaths": self.deaths,
            "bot": self.is_bot,
            # Public, because it changes both what you see and what you can hit:
            # the avatar is drawn to this height and a shot is rewound against it.
            "crouch": round(self.state.crouch, 2),
        }

    def private_view(self, now: float) -> dict[str, Any]:
        """The half of a player's state only they get to see, and the flush point
        for their hitmarkers.

        Ammo is per-recipient rather than in the shared rows because it is nobody
        else's business how many rounds are left in your magazine — and because it
        would be sixteen extra numbers in every packet for everyone.
        """
        hits = self.pending_hits
        self.pending_hits = []
        fell = self.last_fall
        self.last_fall = 0.0
        weapon = weapons.weapon_at(self.weapon)
        return {
            "hp": round(self.health),
            # What the client's prediction rebases on. Momentum made this
            # necessary: replaying unacknowledged commands on top of the client's
            # own velocity would run the replay on the very number the correction
            # exists to fix. Private rather than in the shared rows because it is
            # nobody else's business and would be sixteen more numbers per packet.
            #
            # `sinceLanded` is a duration, not a timestamp: the two simulated
            # clocks are unrelated, so only "how long ago" transfers.
            "move": {
                "vel": [
                    round(self.state.vel_x, 3),
                    round(self.state.vel_y, 3),
                    round(self.state.vel_z, 3),
                ],
                "air": round(self.state.time_in_air, 3),
                "crouch": round(self.state.crouch, 3),
                "crouchedInAir": self.state.crouched_in_air,
                "sinceLanded": round(max(0.0, self.state.t - self.state.landed_at), 3),
            },
            "fell": round(fell),
            "alive": self.alive,
            "weapon": self.weapon,
            "ammo": self.ammo.get(self.weapon, 0),
            "reserve": self.reserve.get(self.weapon, 0),
            "reloading": self.sim_time < self.reload_until,
            "reloadIn": max(0.0, round(self.reload_until - self.sim_time, 2)),
            "respawnIn": (
                0.0 if self.alive else max(0.0, round(self.respawn_at - now, 2))
            ),
            "protected": self.protected,
            "kills": self.kills,
            "deaths": self.deaths,
            "mag": weapon.mag,
            "hits": hits,
        }


class MatchRoom:
    """One match on one map. Owns a tick task for as long as anyone is in it."""

    def __init__(self, room_id: str, map_name: str, world: World, spawns: list) -> None:
        """Takes a world and its spawns rather than a parsed map, so a test can
        build a room without AssaultCube content — which this repo cannot ship."""
        self.id = room_id
        self.map_name = map_name
        self.world = world
        self.spawns = spawns
        self.players: dict[str, MatchPlayer] = {}
        self.tick = 0
        self.created_at = time.time()
        # When the room last had a *human* in it. A room created for an invite is
        # empty until the invitee arrives, so "empty" alone cannot mean "retire" —
        # and a room holding only bots is empty in every sense that matters.
        self.empty_since: float | None = time.monotonic()
        # Where everyone was, for rewinding shots. See `weapons.py`.
        self.history = PositionHistory()
        # Kills per team, index by team number.
        self.scores: list[int] = [0, 0]
        # Effects produced this tick — shots and kills — flushed with the next
        # snapshot rather than sent as they happen. A rifle at 700 rpm would
        # otherwise be its own message stream; batching makes combat cost the
        # tick rate, not the fire rate.
        self.fx: list[dict[str, Any]] = []
        # Noises made this tick, filtered per recipient when the envelopes are
        # built. Deliberately *not* broadcast like `fx`: a shared list carrying
        # every footstep's position would put the location of an enemy two rooms
        # away in the packet, which is a wall hack made of sound. See `noise.py`.
        self.noises: list[Noise] = []
        # Seeded per room rather than per shot: reproducible if you know the room
        # and the shot count, which is worth nothing to a cheat and worth a lot
        # when a test needs a shotgun to pattern the same way twice.
        self.rng = random.Random(room_id)

    # -- membership ---------------------------------------------------------

    @property
    def humans(self) -> list[MatchPlayer]:
        return [p for p in self.players.values() if not p.is_bot]

    def _balanced_team(self) -> int:
        cla = sum(1 for p in self.players.values() if p.team == 0)
        rvsf = len(self.players) - cla
        return 0 if cla <= rvsf else 1

    def _spawn_state(self, team: int) -> PlayerState:
        """A spawn for `team`, falling back to any spawn, then to the middle.

        Not every map has spawns for both teams (and a few community maps have
        none at all), so each fallback is a real case rather than paranoia.
        """
        options = [e for e in self.spawns if e.attr2 == team] or self.spawns
        if not options:
            mid = self.world.ssize / 2
            return PlayerState(x=mid, y=mid, z=self.world.floor_at(int(mid), int(mid)))
        return physics.spawn_at(self.world, self.rng.choice(options))

    def add(
        self,
        name: str,
        conn: Any,
        brain: BotBrain | None = None,
        team: int | None = None,
    ) -> MatchPlayer:
        chosen = self._balanced_team() if team is None else (1 if team else 0)
        player = MatchPlayer(
            id=uuid.uuid4().hex[:12],
            name=name,
            team=chosen,
            state=self._spawn_state(chosen),
            conn=conn,
            brain=brain,
        )
        player.reset_loadout()
        self.players[player.id] = player
        if not player.is_bot:
            self.empty_since = None
        return player

    def remove(self, player_id: str) -> MatchPlayer | None:
        gone = self.players.pop(player_id, None)
        if not self.humans:
            self.empty_since = time.monotonic()
        return gone

    def remove_bots(self, count: int | None = None) -> int:
        """Drop bots, newest first — the undo of "add three more" is those three,
        not the ones who have been playing since the match opened.

        Ordered by insertion rather than by `joined_at`: `players` is a dict and
        dicts are ordered, whereas `time.monotonic()` on Windows has a ~16 ms
        granularity that makes two bots added in the same breath indistinguishable.
        """
        bots = [p for p in reversed(list(self.players.values())) if p.is_bot]
        chosen = bots if count is None else bots[: max(0, count)]
        for bot in chosen:
            self.players.pop(bot.id, None)
        if not self.humans:
            self.empty_since = time.monotonic()
        return len(chosen)

    def respawn(self, player: MatchPlayer) -> None:
        player.state = self._spawn_state(player.team)
        player.reset_loadout()
        # Drop queued commands: they were predicted against the old position, and
        # simulating them after a teleport walks the player away from the spawn.
        player.queue.clear()

    # -- simulation ---------------------------------------------------------

    def enqueue(self, player: MatchPlayer, command: Command) -> None:
        # Duplicates and reorders are normal on a lossy link; the sequence number
        # is what makes them cheap to ignore.
        if command.seq <= player.high_seq:
            return
        player.high_seq = command.seq
        player.last_command_at = time.monotonic()
        player.queue.append(command)
        while len(player.queue) > MAX_QUEUED_COMMANDS:
            player.queue.popleft()

    def simulate(self, elapsed: float) -> None:
        """Drain each player's queue, spending from their time budget."""
        now = time.monotonic()
        now_ms = time.time() * 1000.0
        self._respawn_due(now)
        self._think(elapsed)

        for player in self.players.values():
            player.budget = min(
                BUDGET_CEILING, player.budget + elapsed * BUDGET_EARN_RATE
            )
            while player.queue:
                command = player.queue[0]
                dt = min(max(command.dt, 0.0), physics.MAX_STEP_DT)
                if dt > player.budget:
                    # Out of credit: leave the command queued and let it run next
                    # tick. Throttling rather than dropping keeps an honest but
                    # stuttering client's movement continuous.
                    break
                player.queue.popleft()
                player.budget -= dt
                player.sim_time += dt
                # View angles are cosmetic on the server but they steer movement,
                # so they are applied before the step, not after.
                player.state.yaw = command.yaw
                player.state.pitch = command.pitch
                if player.alive:
                    before = (player.state.x, player.state.y)
                    was_airborne = not player.state.on_ground
                    physics.step(
                        self.world,
                        player.state,
                        MoveInput(
                            forward=command.forward,
                            strafe=command.strafe,
                            jump=command.jump,
                            crouch=command.crouch,
                            yaw=command.yaw,
                            pitch=command.pitch,
                            dt=dt,
                            seq=command.seq,
                        ),
                        dt,
                    )
                    self._movement_consequences(player, before, was_airborne, now)
                    self._handle_combat(player, command, now, now_ms)
                # The ack advances even for a dead player's commands: their client
                # is still predicting and still needs to know what was consumed,
                # and a frozen ack makes it replay an ever-growing tail.
                player.ack = command.seq

        self.history.record(
            now_ms,
            {
                p.id: (p.state.x, p.state.y, p.state.z)
                for p in self.players.values()
                if p.alive
            },
            # Heights too, so a shooter who aimed at a standing head is not told
            # they missed because the target crouched in the meantime.
            {
                p.id: physics.body_height(p.state)
                for p in self.players.values()
                if p.alive
            },
        )

    # -- movement consequences ----------------------------------------------

    def _movement_consequences(
        self,
        player: MatchPlayer,
        before: tuple[float, float],
        was_airborne: bool,
        now: float,
    ) -> None:
        """Noise and fall damage — what moving costs you besides time.

        Driven from the step that produced it rather than from a timer, so both are
        a function of the same simulated motion the position came from. That is
        also what makes them replay-safe: `fall_speed` is an output of one step,
        and a command simulated once produces its landing once.
        """
        state = player.state

        # Footsteps, by distance covered rather than by time. Crouching makes none
        # at all — which is the whole reason the crouch speed penalty is a trade.
        travelled = math.hypot(state.x - before[0], state.y - before[1])
        if state.on_ground and state.crouch <= 0.5:
            player.stride += travelled
            if player.stride >= noise.STRIDE_DISTANCE:
                player.stride = 0.0
                self._noise(player, "step", noise.STRIDE_LOUDNESS)
        else:
            # Airborne or crouched: no stride accumulates. Reset rather than bank
            # it, or a player could crouch-walk a long way and then pay for all of
            # it with one loud step on standing up.
            player.stride = 0.0

        if was_airborne and state.on_ground:
            # A landing is louder the harder it was, and a hard one hurts. Both
            # are the price of the shoot-jump: vertical travel is available, and
            # it announces you and costs health.
            impact = state.fall_speed
            loudness = noise.LAND_LOUDNESS * min(
                1.0, 0.45 + impact / (physics.JUMP_SPEED * 2)
            )
            self._noise(player, "land", loudness)
            damage = physics.fall_damage(impact)
            if damage > 0:
                self._fall_damage(player, damage, now)
        elif not was_airborne and not state.on_ground and state.vel_z > 0:
            self._noise(player, "jump", noise.JUMP_LOUDNESS)

    def _noise(
        self, player: MatchPlayer, kind: str, loudness: float, weapon: str = ""
    ) -> None:
        if len(self.noises) >= MAX_NOISE_PER_TICK:
            return
        self.noises.append(
            Noise(
                kind=kind,
                source=player.id,
                x=player.state.x,
                y=player.state.y,
                z=player.state.z,
                loudness=loudness,
                weapon=weapon,
            )
        )

    def _fall_damage(self, player: MatchPlayer, amount: float, now: float) -> None:
        """Damage with nobody to credit it to.

        Kept apart from `_apply_damage` because that one needs an attacker for the
        killfeed, the hitmarker and the score — and a fall has none of those. A
        death here is a death with no kill, which is exactly right.
        """
        player.health -= amount
        player.last_fall = amount
        if player.health > 0:
            self._noise(player, "hurt", noise.HURT_LOUDNESS)
            return
        player.health = 0
        player.alive = False
        player.deaths += 1
        player.respawn_at = now + RESPAWN_DELAY
        player.queue.clear()
        self._noise(player, "die", noise.DIE_LOUDNESS)
        self._emit(
            {
                "kind": "kill",
                "victim": player.id,
                "victimName": player.name,
                # No killer: an empty id is what the feed reads as "the map did it".
                "killer": "",
                "killerName": "",
                "weapon": "fall",
                "head": False,
            }
        )

    def _think(self, elapsed: float) -> None:
        """Let every bot produce this tick's input.

        Bots enqueue through `enqueue` like anyone else, so they are validated,
        budgeted and simulated by the same code as a browser — there is exactly
        one simulation, and a bot cannot do anything a player could not.
        """
        for player in self.players.values():
            if player.brain is None:
                continue
            try:
                command = player.brain.think(self, player, elapsed)
            except Exception:
                logger.exception("hassault bot %s failed to think", player.name)
                continue
            if command is not None:
                self.enqueue(player, command)

    def _respawn_due(self, now: float) -> None:
        for player in self.players.values():
            if not player.alive and now >= player.respawn_at:
                self.respawn(player)
                self._emit({"kind": "spawn", "id": player.id})

    # -- combat -------------------------------------------------------------

    def _handle_combat(
        self, player: MatchPlayer, command: Command, now: float, now_ms: float
    ) -> None:
        if command.weapon >= 0 and command.weapon != player.weapon:
            player.weapon = max(0, min(len(weapons.WEAPONS) - 1, command.weapon))
            # Switching cancels a reload rather than queueing behind it: that is
            # what every player expects the switch to be *for*.
            player.reload_until = -999.0
        # Every frame, not only on the next trigger pull. Resolving it lazily
        # deadlocks anyone who reloads and then waits — including every bot, which
        # stops firing precisely because it is empty.
        self._finish_reload(player)
        if command.reload:
            self._begin_reload(player)
        if command.fire:
            self._fire(player, command, now, now_ms)

    def _begin_reload(self, player: MatchPlayer) -> None:
        weapon = weapons.weapon_at(player.weapon)
        if weapon.mag <= 0 or player.sim_time < player.reload_until:
            return
        if player.ammo.get(player.weapon, 0) >= weapon.mag:
            return
        if player.reserve.get(player.weapon, 0) == 0:
            return
        player.reload_until = player.sim_time + weapon.reload_time

    def _finish_reload(self, player: MatchPlayer) -> None:
        """Move rounds from the reserve into the magazine, if a reload has run
        its course.

        Driven from `_handle_combat`, so it lands on the first command after the
        reload's simulated time is up rather than on a timer of its own — a
        player's ammo only advances when their own input does, which is the same
        rule the rest of the simulation follows.
        """
        weapon = weapons.weapon_at(player.weapon)
        if weapon.mag <= 0 or player.reload_until <= -900:
            return
        if player.sim_time < player.reload_until:
            return
        player.reload_until = -999.0
        have = player.ammo.get(player.weapon, 0)
        want = weapon.mag - have
        pool = player.reserve.get(player.weapon, -1)
        if pool < 0:
            player.ammo[player.weapon] = weapon.mag
            return
        taken = min(want, pool)
        player.ammo[player.weapon] = have + taken
        player.reserve[player.weapon] = pool - taken

    def _fire(
        self, player: MatchPlayer, command: Command, now: float, now_ms: float
    ) -> None:
        weapon = weapons.weapon_at(player.weapon)
        if player.sim_time < player.reload_until:
            return
        if player.sim_time - player.last_fire_at < weapon.interval:
            return
        if weapon.mag > 0:
            if player.ammo.get(player.weapon, 0) <= 0:
                # Out: start the reload rather than doing nothing, so holding the
                # trigger through an empty magazine behaves the way it does in
                # every other shooter.
                self._begin_reload(player)
                return
            player.ammo[player.weapon] -= 1
        player.last_fire_at = player.sim_time
        # Shooting forfeits spawn protection. Otherwise it is not protection, it
        # is a three-second licence.
        player.protected_until = 0.0

        crouching = player.state.crouch > 0.5
        # From the *current* eye, which crouching lowers — a crouched player whose
        # shots left from standing height would be firing through their own cover.
        origin = weapons.eye_position(
            player.state.x,
            player.state.y,
            player.state.z,
            physics.eye_height(player.state),
        )
        direction = weapons.aim_vector(command.yaw, command.pitch)

        rewind_to = self.history.clamp(command.view_t, now_ms)
        rewound = self.history.rewind(rewind_to)
        rewound_heights = self.history.rewind_heights(rewind_to)
        targets: dict[str, tuple[float, float, float]] = {}
        heights: dict[str, float] = {}
        for other in self.players.values():
            if other.id == player.id or not other.alive or other.protected:
                continue
            # Friendly fire is off. A four-player match on a map with team spawns
            # is otherwise decided by who turns around first.
            if other.team == player.team:
                continue
            live = (other.state.x, other.state.y, other.state.z)
            targets[other.id] = (
                rewound.get(other.id, live) if rewound is not None else live
            )
            heights[other.id] = rewound_heights.get(
                other.id, physics.body_height(other.state)
            )

        result = weapons.resolve_shot(
            self.world,
            weapon,
            origin,
            direction,
            targets,
            self.rng,
            spread=weapons.effective_spread(weapon, command.scoped),
            rewound_ms=max(0.0, now_ms - rewind_to),
            heights=heights,
        )

        # Recoil shoves the shooter, opposite their aim — AC's `attackphysics`, and
        # the whole of shoot-jumping. Applied here, after the step, which is the
        # order the client's `Predictor` replays it in.
        kick = weapons.kick_vector(weapon, command.yaw, command.pitch, crouching)
        if kick != (0.0, 0.0, 0.0):
            physics.apply_impulse(player.state, *kick)

        # Firing is the loudest thing you can do, and it is the reason a silenced
        # approach ends the moment you take the shot.
        # The weapon rides along so the listener hears *which* gun: a shot's
        # loudness already comes from the weapon, and its voice does too.
        self._noise(player, "shot", noise.shot_loudness(weapon), weapon.id)

        for hit in result.hits:
            victim = self.players.get(hit.victim)
            if victim is None or not victim.alive:
                continue
            self._apply_damage(victim, player, hit.damage, hit.head, weapon, now)

        self._emit(
            {
                "kind": "shot",
                "id": player.id,
                "weapon": player.weapon,
                "origin": [round(v, 2) for v in result.origin],
                "ends": [[round(v, 2) for v in end] for end in result.endpoints],
                "hit": bool(result.hits),
            }
        )

    def _apply_damage(
        self,
        victim: MatchPlayer,
        attacker: MatchPlayer,
        amount: float,
        head: bool,
        weapon: weapons.Weapon,
        now: float,
    ) -> None:
        victim.health -= amount
        killed = victim.health <= 0
        if len(attacker.pending_hits) < MAX_PENDING_HITS:
            attacker.pending_hits.append(
                {
                    "victim": victim.id,
                    "damage": round(amount),
                    "head": head,
                    "killed": killed,
                }
            )
        if not killed:
            return

        victim.health = 0
        victim.alive = False
        victim.deaths += 1
        victim.respawn_at = now + RESPAWN_DELAY
        victim.queue.clear()
        attacker.kills += 1
        if 0 <= attacker.team < len(self.scores):
            self.scores[attacker.team] += 1
        self._emit(
            {
                "kind": "kill",
                "victim": victim.id,
                "victimName": victim.name,
                "killer": attacker.id,
                "killerName": attacker.name,
                "weapon": weapon.id,
                "head": head,
            }
        )

    def _emit(self, effect: dict[str, Any]) -> None:
        if len(self.fx) < MAX_FX_PER_TICK:
            self.fx.append(effect)

    # -- wire ---------------------------------------------------------------

    def snapshot_for(self, player: MatchPlayer, now: float, rows: list[dict]) -> dict:
        """This player's copy of the tick.

        Note `private_view` **drains** their hitmarkers, so this is the flush
        point and must be called once per player per tick.
        """
        you = player.private_view(time.monotonic())
        # Resolved per recipient, here rather than in the shared rows, because
        # audibility is the whole mechanic: a shared list of noises with positions
        # in it would hand every client the location of everyone it cannot hear.
        you["noise"] = noise.envelope(
            self.world,
            weapons.eye_position(
                player.state.x,
                player.state.y,
                player.state.z,
                physics.eye_height(player.state),
            ),
            player.id,
            self.noises,
        )
        return {
            "channel": CHANNEL,
            "event": "snapshot",
            "data": {
                "room": self.id,
                "tick": self.tick,
                # Server clock in ms, so a client can measure one-way drift and
                # order snapshots without trusting arrival order.
                "t": round(now * 1000),
                "ack": player.ack,
                "players": rows,
                "you": you,
                "scores": self.scores,
                # Copied, not aliased: `_broadcast` clears `self.fx` once everyone
                # has been sent their copy, and handing out a reference to a list
                # we are about to empty is the kind of thing that works until
                # someone makes the send path yield before serialising.
                "fx": list(self.fx),
            },
        }

    def state_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "room": self.id,
            "map": self.map_name,
            "tick": self.tick,
            "snapshotHz": SNAPSHOT_HZ,
            "players": [p.snapshot(now) for p in self.players.values()],
            "scores": self.scores,
        }


class MatchServer:
    """Process-global registry of rooms, and the tick loop that drives them."""

    def __init__(self) -> None:
        self.rooms: dict[str, MatchRoom] = {}
        # Which room each connection's player is in, so a socket closing can be
        # cleaned up without searching every room.
        self.membership: dict[int, tuple[str, str]] = {}
        self._task: asyncio.Task[None] | None = None

    # -- rooms --------------------------------------------------------------

    def create(self, map_name: str, room_id: str | None = None) -> MatchRoom:
        cgz = assets.load_map(map_name)
        if cgz is None:
            raise LookupError(f"no map named {map_name!r}")
        rid = room_id or uuid.uuid4().hex[:8]
        room = MatchRoom(rid, map_name, World.from_map(cgz), cgz.spawns())
        self.rooms[rid] = room
        # Start ticking even though the room is empty: the tick loop is also what
        # retires it, so a room opened for an invite nobody accepts would
        # otherwise hold its map planes until some unrelated match began.
        try:
            self.ensure_running()
        except RuntimeError:
            # No running loop (a synchronous caller in a test). The next join
            # starts the loop anyway.
            pass
        return room

    def get(self, room_id: str) -> MatchRoom | None:
        return self.rooms.get(room_id)

    def find_or_create(self, map_name: str) -> MatchRoom:
        """The first room on `map_name` with space, else a new one.

        "Join a map" is what a player actually wants; explicit room ids exist for
        the friends-list invite path, which hands one over.
        """
        for room in self.rooms.values():
            if room.map_name == map_name and len(room.players) < MAX_PLAYERS:
                return room
        return self.create(map_name)

    def listing(self) -> list[dict[str, Any]]:
        return [
            {
                "id": room.id,
                "map": room.map_name,
                "players": len(room.players),
                "bots": sum(1 for p in room.players.values() if p.is_bot),
                "maxPlayers": MAX_PLAYERS,
                "createdAt": room.created_at,
            }
            for room in self.rooms.values()
        ]

    # -- lifecycle ----------------------------------------------------------

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Fixed-rate loop: simulate, broadcast, retire empty rooms.

        Sleeps the remainder of the interval rather than a flat `TICK_INTERVAL`,
        so the tick rate does not silently sag under load.
        """
        last = time.monotonic()
        try:
            while self.rooms:
                started = time.monotonic()
                elapsed = started - last
                last = started
                for room in list(self.rooms.values()):
                    # Bots alone do not keep a room alive, and there is nobody to
                    # simulate for: a match with no humans in it is a screensaver.
                    if not room.humans:
                        if (started - (room.empty_since or started)) > EMPTY_GRACE:
                            self.rooms.pop(room.id, None)
                        continue
                    room.tick += 1
                    room.simulate(elapsed)
                    await self._broadcast(room)
                await asyncio.sleep(
                    max(0.0, TICK_INTERVAL - (time.monotonic() - started))
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("hassault match loop failed")

    async def _broadcast(self, room: MatchRoom) -> None:
        now = time.time()
        mono = time.monotonic()
        # One shared list of rows, but a per-player envelope: `ack`, `you` and the
        # hitmarkers differ per recipient, and `ack` is the field prediction
        # depends on.
        rows = [p.snapshot(mono) for p in room.players.values()]
        for player in list(room.players.values()):
            conn = player.conn
            if conn is None:
                continue
            try:
                await conn.send_json(room.snapshot_for(player, now, rows))
            except Exception:
                # A dead socket is the /ws loop's problem; dropping the player
                # here would race its own disconnect handling.
                pass
        # Cleared after everyone has been sent this tick's copy, not as they are
        # produced — a bot has no socket and would otherwise consume the effects
        # nobody ever saw.
        room.fx.clear()
        # Same for noises, and for a second reason: every recipient's envelope is
        # built from this one list, so it cannot be emptied until the last of them
        # has had their audibility resolved against it.
        room.noises.clear()

    async def broadcast_event(
        self, room: MatchRoom, event: str, data: dict[str, Any], exclude: str = ""
    ) -> None:
        message = {"channel": CHANNEL, "event": event, "data": data}
        for player in list(room.players.values()):
            if player.id == exclude or player.conn is None:
                continue
            try:
                await player.conn.send_json(message)
            except Exception:
                pass

    # -- membership ---------------------------------------------------------

    async def join(
        self, conn: WsConnection, map_name: str, name: str, room_id: str | None = None
    ) -> tuple[MatchRoom, MatchPlayer]:
        await self.leave(conn)
        if room_id:
            room = self.get(room_id)
            if room is None:
                raise LookupError(f"no match {room_id!r}")
        else:
            room = self.find_or_create(map_name)
        if len(room.players) >= MAX_PLAYERS:
            raise ValueError("that match is full")
        player = room.add(name[:MAX_NAME_LEN] or "player", conn)
        self.membership[id(conn)] = (room.id, player.id)
        self.ensure_running()
        await self.broadcast_event(
            room,
            "joined",
            {"room": room.id, "player": player.snapshot(time.monotonic())},
            player.id,
        )
        return room, player

    async def leave(self, conn: WsConnection) -> None:
        entry = self.membership.pop(id(conn), None)
        if entry is None:
            return
        room_id, player_id = entry
        room = self.rooms.get(room_id)
        if room is None:
            return
        room.remove(player_id)
        await self.broadcast_event(
            room, "left", {"room": room.id, "playerId": player_id}
        )

    def player_for(self, conn: WsConnection) -> tuple[MatchRoom, MatchPlayer] | None:
        entry = self.membership.get(id(conn))
        if entry is None:
            return None
        room = self.rooms.get(entry[0])
        if room is None:
            return None
        player = room.players.get(entry[1])
        return (room, player) if player else None

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.rooms.clear()
        self.membership.clear()


match_server = MatchServer()


def map_error(exc: Exception) -> str:
    """A message worth showing a player, for the map-loading failures."""
    if isinstance(exc, CgzError):
        return f"that map cannot be read: {exc}"
    return str(exc)
