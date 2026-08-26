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

from backend.modules.hassault import assets, grenades, noise, physics, weapons
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

#: Simulated seconds between throws. Long enough that a held key is one grenade
#: rather than the whole pouch, short enough that a smoke-then-flash entry is
#: still a thing you can do.
THROW_COOLDOWN = 0.9

#: Grenades allowed in the air per room, and zones alive at once. Both are the
#: same kind of bound as `MAX_FX_PER_TICK`: the packet carries all of them, and
#: eight players who all found the throw key should cost a bounded amount.
MAX_LIVE_GRENADES = 24
MAX_LIVE_ZONES = 16

#: How far a player paints an enemy for their team's radar. Generous — the point
#: of a radar is to share what somebody already saw, not to make spotting a skill
#: of its own — but finite, so the far side of a big map is still dark.
SPOT_RANGE = 90.0

#: Half-angle of the cone that counts as looking at somebody. Wider than a screen
#: (which is about 0.65 rad half-angle at 75° FOV) because peripheral awareness is
#: real and a radar that only paints what is dead centre is a radar nobody trusts.
SPOT_FOV = 1.35

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
    """Throw the grenade in `nade` this frame.

    A flag on a movement command for exactly the reason firing is one: a throw
    has to carry the yaw, pitch and velocity of the frame it left the hand on, and
    a separate message would arrive with none of them — the grenade would leave in
    a direction the player was no longer looking. It also meant the peer fabric,
    which forwards commands verbatim, carried grenades across the wire with no
    changes at all.
    """
    throw: bool = False
    """Which grenade slot the throw uses. `-1` is no selection."""
    nade: int = -1
    """Underhand: a short throw, for putting a smoke at your own feet."""
    lob: bool = False


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
    #: Damage this player has actually landed, in hit points. **Applied** damage,
    #: not rolled: a 90-damage sniper round into a body with 20 left counts 20.
    #: Overkill would make the number a description of the weapon rather than of
    #: the match, and it is the one stat a player can check against the health
    #: bars they watched go down.
    damage_dealt: float = 0.0
    #: Kills whose final hit landed in the head band. **Kills, not hits**, because
    #: the debrief divides this by `kills` to show a percentage, and counting hits
    #: there would print numbers over 100%.
    head_kills: int = 0
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
    #: What this player is carrying, per grenade slot.
    nades: grenades.Inventory = field(default_factory=grenades.Inventory)
    #: Simulated time of the last throw, so a held button does not empty the
    #: whole pouch in one frame. On `sim_time` like the fire rate, and for the
    #: same reason: commands arrive batched, and a wall clock would let a
    #: stuttering client throw faster than a smooth one.
    last_throw_at: float = -999.0
    #: How blind this player currently is, 0..1, and how fast it is fading.
    #: Per-player rather than a broadcast effect — see `grenades.flash_strength`.
    flash: float = 0.0
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
        self.nades.reset()
        self.last_throw_at = -999.0
        # Dying clears a flash. Being blinded through a respawn would be a
        # punishment for having already been punished.
        self.flash = 0.0
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
            # Private for the same reason ammo is: how much utility somebody has
            # left is exactly the thing you would like to know about them.
            "nades": self.nades.to_dict(),
            # How blind *you* are. Never in the shared rows — a client that was
            # told how blind everyone else is could draw them through the flash.
            "flash": round(self.flash, 3),
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
        # Grenades in the air, and the smoke/fire they leave behind. Both are
        # **public**: a grenade you can see on your screen and a cloud standing in
        # a doorway are visible to everyone, so unlike noise there is nothing to
        # resolve per recipient. What a flashbang did to you is private, and lives
        # in `MatchPlayer.flash` instead.
        self.nades: list[grenades.Grenade] = []
        self.zones: list[grenades.Zone] = []
        # Monotonic per room, so an id is stable for a client to interpolate a
        # grenade's arc against between snapshots.
        self._nade_seq = 0
        self._zone_seq = 0
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

    def result_for(self, player_id: str) -> dict[str, Any] | None:
        """How one player did, as the debrief needs it.

        Read from the simulation's own counters, which is the whole point: every
        number here was produced by a shot that was actually resolved against
        this world. The route used to invent all of them with `random.randint`,
        which made the card a screensaver.

        `won` and `mvp` are **relative to the room** rather than to a team score,
        because the only mode is deathmatch: you won if nobody outscored you, and
        you are the MVP if nobody equalled you either. Bots count — losing to one
        is losing, and a card that quietly excluded them would be flattering
        rather than true.
        """
        player = self.players.get(player_id)
        if player is None:
            return None
        others = [p for p in self.players.values() if p.id != player_id]
        best = max((p.kills for p in others), default=-1)
        return {
            "map": self.map_name,
            "room": self.id,
            "name": player.name,
            "kills": player.kills,
            "deaths": player.deaths,
            "headKills": player.head_kills,
            "damageDealt": round(player.damage_dealt),
            "won": player.kills >= best,
            "mvp": player.kills > best,
            "opponents": len(others),
            # Wall clock, so a summary can be told from a stale one. The room's
            # own clock starts when it opens, which is not when this player
            # joined it.
            "playedAt": time.time(),
        }

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
            # Overflow. Dropping the oldest is right — it is the most stale — but
            # dropping it *silently* is not: `ack` only moves when a command is
            # simulated, so a command discarded here would never be acknowledged
            # and the client would replay it on top of every correction for the
            # rest of the match. Its prediction then sits permanently ahead of
            # the server, which is exactly the rubber-banding this queue exists
            # to bound. Acknowledging it says "this one is not coming back".
            #
            # Monotonic by construction: the dropped sequence is lower than
            # anything still queued behind it.
            player.ack = max(player.ack, player.queue.popleft().seq)

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

        self._step_grenades(elapsed, now)

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

    # -- thrown utility -------------------------------------------------------

    def _step_grenades(self, elapsed: float, now: float) -> None:
        """Advance every grenade and zone, and resolve what that produced.

        Ticked on the **room's** clock rather than on any player's simulated time,
        unlike movement and fire rate. That is the right call here and the wrong
        one there: a grenade belongs to nobody once it is thrown, so pacing it by
        its thrower's command stream would freeze it mid-air the moment that
        player's connection stuttered — and stop the fuse with it.
        """
        dt = min(elapsed, physics.MAX_STEP_DT)

        # Detonations are collected and applied after the walk, not during it:
        # an HE that kills somebody mutates the player table, and a zone created
        # mid-iteration would be stepped in the same tick it was born.
        spent: list[grenades.Grenade] = []
        for nade in self.nades:
            impact = grenades.step_grenade(self.world, nade, dt)
            if nade.fuse <= 0 or (nade.spec.impact and impact):
                nade.detonated = True
                spent.append(nade)
        if spent:
            self.nades = [n for n in self.nades if not n.detonated]
            for nade in spent:
                self._detonate(nade, now)

        if self.zones:
            for zone in self.zones:
                zone.remaining -= dt
                if zone.damage_per_second > 0:
                    self._burn(zone, dt, now)
            self.zones = [z for z in self.zones if z.remaining > 0]

        # The flash fades on the room's clock too, for the same reason: a blinded
        # player who stops sending input must still recover.
        for player in self.players.values():
            if player.flash > 0:
                player.flash = max(0.0, player.flash - dt / grenades.FLASH_MAX)

    def _detonate(self, nade: grenades.Grenade, now: float) -> None:
        """What a grenade does when its fuse runs out.

        Four branches rather than one parameterised effect, because the four
        genuinely resolve differently — see the module docstring in
        `grenades.py`.
        """
        kind = nade.spec.kind
        self.noises.append(
            Noise(
                kind="explosion" if kind == "he" else f"nade_{kind}",
                source=nade.owner,
                x=nade.x,
                y=nade.y,
                z=nade.z,
                loudness=noise.EXPLOSION_LOUDNESS if kind == "he" else noise.LAND_LOUDNESS,
            )
        )
        self._emit(
            {
                "kind": "detonate",
                "nade": kind,
                "id": nade.id,
                "at": [round(nade.x, 2), round(nade.y, 2), round(nade.z, 2)],
                "radius": nade.spec.radius,
            }
        )

        if kind in ("smoke", "fire"):
            if len(self.zones) >= MAX_LIVE_ZONES:
                return
            self._zone_seq += 1
            self.zones.append(
                grenades.Zone(
                    id=f"{self.id}-z{self._zone_seq}",
                    kind=kind,
                    owner=nade.owner,
                    team=nade.team,
                    x=nade.x,
                    y=nade.y,
                    # Lifted off the floor by its own radius, so the cloud is a
                    # ball resting on the ground rather than one half-buried in
                    # it — half a smoke underground is half a smoke.
                    z=nade.z + nade.spec.radius * 0.45,
                    radius=nade.spec.radius,
                    remaining=nade.spec.duration,
                    duration=nade.spec.duration,
                    damage_per_second=nade.spec.damage_per_second,
                )
            )
            return

        thrower = self.players.get(nade.owner)
        if kind == "flash":
            for player in self.players.values():
                if not player.alive or player.protected:
                    continue
                strength = grenades.flash_strength(
                    self.world,
                    nade,
                    player.state.x,
                    player.state.y,
                    player.state.z + physics.eye_height(player.state),
                    player.state.yaw,
                    player.state.pitch,
                )
                # Your own flash blinds you. It is the whole reason a flashbang
                # is a skill rather than a free button, and exempting the thrower
                # would make throwing one into your own doorway strictly correct.
                if strength > player.flash:
                    player.flash = strength
            return

        # HE. Targets are enemies plus the thrower — friendly fire stays off, as
        # it is for bullets, but a grenade at your own feet is your own fault and
        # the game says so.
        targets: dict[str, tuple[float, float, float]] = {}
        for other in self.players.values():
            if not other.alive or other.protected:
                continue
            if other.id != nade.owner and other.team == nade.team:
                continue
            targets[other.id] = (
                other.state.x,
                other.state.y,
                # Centre of mass, not the feet: a grenade level with somebody's
                # ankles on the far side of a low wall would otherwise be traced
                # to a point the wall covers and do nothing.
                other.state.z + physics.body_height(other.state) * 0.5,
            )
        for hit in grenades.resolve_blast(self.world, nade, targets):
            victim = self.players.get(hit.victim)
            if victim is None or not victim.alive:
                continue
            if thrower is not None and victim.id != thrower.id:
                self._apply_damage(
                    victim, thrower, hit.damage, False, weapons.weapon_at(0), now
                )
            else:
                # Blowing yourself up has no killer to credit, exactly like a
                # fall. Routing it through `_apply_damage` would award you a kill
                # on yourself and put you on your own scoreboard line.
                self._fall_damage(victim, hit.damage, now)

    def _burn(self, zone: grenades.Zone, dt: float, now: float) -> None:
        """Damage over time from a fire, for anybody standing in it."""
        owner = self.players.get(zone.owner)
        for player in self.players.values():
            if not player.alive or player.protected:
                continue
            if player.id != zone.owner and player.team == zone.team:
                continue
            # Feet, not centre: fire is on the floor, and testing a body's middle
            # would let a player wade through the edge of one untouched.
            if not zone.contains(player.state.x, player.state.y, player.state.z + 0.5):
                continue
            amount = zone.damage_per_second * dt
            if owner is not None and player.id != owner.id:
                self._apply_damage(
                    player, owner, amount, False, weapons.weapon_at(0), now
                )
            else:
                self._fall_damage(player, amount, now)

    # -- what the radar is allowed to show ------------------------------------

    def spotted_by(self, viewer: MatchPlayer) -> list[str]:
        """Which enemies this player's team can currently see.

        The radar shows teammates unconditionally — that is a radio, and every
        team shooter works that way — but an enemy has to be **seen** by somebody
        on your side. Resolved here rather than in the browser because only the
        server holds the thing the answer depends on: the level's geometry, and
        the smoke standing in it.

        Three conditions, and each is a counter a player can actually use:

        - **Range.** Beyond `SPOT_RANGE` nobody is spotting anybody.
        - **Facing.** A teammate has to be looking roughly at them, so walking
          past somebody with your back turned does not paint them for the team.
        - **Sight.** A wall stops it, and so does a smoke — which is most of the
          reason to throw one, and would be worth nothing if the cloud blocked
          eyes but not the minimap.

        Note this is a **fairness** rule, not an anti-cheat one, and it is worth
        being honest about which: the shared rows already carry every player's
        position, because the renderer needs them the instant somebody steps into
        view. A modified client could always draw a full radar. What this buys is
        that the radar the game ships shows the same thing to everyone, and that
        a smoke does the same job on it that it does on screen.
        """
        seen: list[str] = []
        allies = [
            p
            for p in self.players.values()
            if p.team == viewer.team and p.alive
        ]
        for other in self.players.values():
            if other.team == viewer.team or not other.alive:
                continue
            target = weapons.eye_position(
                other.state.x,
                other.state.y,
                other.state.z,
                physics.eye_height(other.state),
            )
            for ally in allies:
                eye = weapons.eye_position(
                    ally.state.x,
                    ally.state.y,
                    ally.state.z,
                    physics.eye_height(ally.state),
                )
                dx = target[0] - eye[0]
                dy = target[1] - eye[1]
                dz = target[2] - eye[2]
                distance = math.sqrt(dx * dx + dy * dy + dz * dz)
                if distance > SPOT_RANGE:
                    continue
                if distance > 0.1:
                    bearing = math.atan2(dy, dx)
                    delta = abs(
                        (bearing - ally.state.yaw + math.pi) % (2 * math.pi) - math.pi
                    )
                    if delta > SPOT_FOV:
                        continue
                    direction = (dx / distance, dy / distance, dz / distance)
                    reach = weapons.raycast_world(self.world, eye, direction, distance)
                    if reach < distance - 0.5:
                        continue
                if self.smoked(eye, target):
                    continue
                seen.append(other.id)
                break
        return seen

    def smoked(self, a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
        """Whether a smoke stands between two points.

        Public because it is not only the renderer's business: the bots' vision
        and the radar both have to ask, or a cloud would be something only humans
        respect. See `grenades.sight_blocked_by`.
        """
        return grenades.sight_blocked_by(self.zones, a, b)

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
        if command.throw:
            self._throw(player, command)

    def _throw(self, player: MatchPlayer, command: Command) -> None:
        """Put a grenade in the air, if this player has one and is allowed to.

        The checks are all here rather than trusted from the client for the usual
        reason — a client that decides whether it has a grenade left has infinite
        grenades — but note the *rate* check in particular: `throw` is a flag on
        a movement command, so a client sending 120 commands a second would throw
        120 grenades in a second without it.
        """
        slot = command.nade
        spec = grenades.spec_at(slot)
        if spec is None:
            return
        if player.sim_time - player.last_throw_at < THROW_COOLDOWN:
            return
        if len(self.nades) >= MAX_LIVE_GRENADES:
            return
        if not player.nades.take(slot):
            return
        player.last_throw_at = player.sim_time
        # Throwing forfeits spawn protection, exactly as firing does: a grenade
        # from inside a shield is the same three-second licence.
        player.protected_until = 0.0

        state = player.state
        origin = grenades.throw_origin(
            state.x, state.y, state.z + physics.eye_height(state), command.yaw, command.pitch
        )
        velocity = grenades.throw_velocity(
            command.yaw,
            command.pitch,
            command.lob,
            (state.vel_x, state.vel_y, state.vel_z),
        )
        self._nade_seq += 1
        self.nades.append(
            grenades.Grenade(
                id=f"{self.id}-n{self._nade_seq}",
                spec=spec,
                owner=player.id,
                team=player.team,
                x=origin[0],
                y=origin[1],
                z=origin[2],
                vx=velocity[0],
                vy=velocity[1],
                vz=velocity[2],
                fuse=spec.fuse,
            )
        )
        self._noise(player, "throw", noise.JUMP_LOUDNESS * 0.8)

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
        # Before the subtraction: what actually landed is capped by what was
        # left, and reading it afterwards would count the overkill too.
        landed = min(amount, max(0.0, victim.health))
        attacker.damage_dealt += landed
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
        if head:
            attacker.head_kills += 1
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
        # Per recipient for the same reason the noise envelope is: it is a
        # different answer for each team, and a shared list would be one team's
        # information sitting in the other's packet.
        you["spotted"] = self.spotted_by(player)
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
                # Public, unlike the noise envelope above: a grenade in the air
                # and a cloud in a doorway are things everybody can see, and
                # withholding one somebody is looking at would be worse than
                # useless.
                "nades": [n.snapshot() for n in self.nades],
                "zones": [z.snapshot() for z in self.zones],
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
            "nades": [n.snapshot() for n in self.nades],
            "zones": [z.snapshot() for z in self.zones],
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

    async def leave(self, conn: WsConnection) -> dict[str, Any] | None:
        """Take this socket out of its room, returning how its player did.

        The result is read **before** the removal, which is the only order that
        works: `remove` drops the `MatchPlayer` and with it every counter the
        debrief is made of. Returned rather than recorded here because this layer
        has no idea who the account is — the channel does, and it is the caller.
        """
        entry = self.membership.pop(id(conn), None)
        if entry is None:
            return None
        room_id, player_id = entry
        room = self.rooms.get(room_id)
        if room is None:
            return None
        result = room.result_for(player_id)
        room.remove(player_id)
        await self.broadcast_event(
            room, "left", {"room": room.id, "playerId": player_id}
        )
        return result

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


# ---------------------------------------------------------------------------
# Wire validation
#
# **One validator, used by every caller.** This lived in `channel.py`, which made
# it reachable only by a browser on this node — and the moment a second entry
# point appeared (a peer over the fabric, then the game server hosting a rated
# match) the temptation was a second copy. A laxer second implementation of these
# clamps is precisely where a gap appears, and it appears in the one place nobody
# is looking: the path that is not the common one.
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    # NaN and infinities survive JSON and poison every downstream comparison, so
    # they are rejected here rather than at the first surprising position.
    return out if out == out and abs(out) != float("inf") else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_command(raw: Any) -> Command | None:
    if not isinstance(raw, dict):
        return None
    seq = raw.get("seq")
    if not isinstance(seq, int) or seq <= 0:
        return None
    weapon = raw.get("weapon")
    view_t = raw.get("viewT")
    return Command(
        seq=seq,
        # Clamped rather than trusted: the analogue axes are the obvious place to
        # ask for a value of 50 and move fifty times as fast.
        forward=_clamp(_num(raw.get("forward")), -1.0, 1.0),
        strafe=_clamp(_num(raw.get("strafe")), -1.0, 1.0),
        jump=bool(raw.get("jump")),
        crouch=bool(raw.get("crouch")),
        yaw=_num(raw.get("yaw")),
        pitch=_clamp(_num(raw.get("pitch")), -1.5708, 1.5708),
        dt=_clamp(_num(raw.get("dt")), 0.0, 0.25),
        fire=bool(raw.get("fire")),
        reload=bool(raw.get("reload")),
        # `-1` means "no change", so an absent or nonsensical slot leaves the
        # weapon alone rather than silently arming the knife.
        weapon=(
            int(_clamp(_num(weapon, -1.0), -1.0, float(len(weapons.WEAPONS) - 1)))
            if isinstance(weapon, (int, float))
            else -1
        ),
        # Left as `None` when absent: the shot is then judged live, which is the
        # right answer for a client that did not say what it was looking at.
        # Range-checking is `PositionHistory.clamp`'s job — it is the only place
        # that knows the current time, and it is the security boundary.
        view_t=_num(view_t) if isinstance(view_t, (int, float)) else None,
        # Floored to a non-negative step. The upper bound is *not* applied here:
        # it depends on the weapon this command turns out to be applied to, which
        # only the simulation knows — see `weapons.clamp_zoom`.
        scoped=max(0, int(_num(raw.get("scoped")))),
        throw=bool(raw.get("throw")),
        # `-1` for absent or out of range, which `grenades.spec_at` reads as "no
        # grenade" — the same shape as `weapon`, and for the same reason: a
        # nonsensical slot must do nothing rather than pick one.
        nade=(
            int(_clamp(_num(raw.get("nade"), -1.0), -1.0, float(len(grenades.GRENADES) - 1)))
            if isinstance(raw.get("nade"), (int, float))
            else -1
        ),
        lob=bool(raw.get("lob")),
    )
