"""Bomb defuse: rounds, a plant, a defuse, and a side swap at half time.

One team carries a bomb to a site and plants it; the other stops them, or defuses
it once it is down. A round ends when the bomb goes off, is defused, one side is
eliminated, or the clock runs out — and the match ends when a side wins enough
rounds.

## The phase machine is a pure function, and that is not aesthetics

`advance` takes a `RoundState` and returns a new one. Everything that touches
`MatchRoom` — respawning bodies, resetting items, emitting effects — lives in the
`tick` wrapper around it. That split is what makes the machine portable to a
shared conformance fixture, the way `physics-vectors.json` binds the three
physics ports: three clients have to agree about *when a round ends*, and the
cheapest way to keep them agreeing is for the rule to be a function with no world
in it.

## Which clock, and why it is three

- **The round clock and the bomb's fuse run on the room's clock**, ticked with
  `elapsed`. Same argument `_step_grenades` makes: a planted bomb belongs to
  nobody, so pacing it by the planter's command stream would freeze the fuse the
  moment they disconnect.
- **Plant and defuse progress runs on the player's own simulated time**, accrued
  from `command.dt` in `on_command`. It is bounded by the same replenishing
  budget movement is, so a client cannot plant faster than time passes. On the
  room clock instead, a stuttering client would plant *more slowly* than a smooth
  one and nothing would say why.
- **Respawn timers stay on the wall clock**, which is `_respawn_due`'s business
  and unchanged: a dead player sends no commands, so simulated time would stop
  for exactly the person waiting on it.

## Things that are silently wrong if you get them the other way round

- **Interrupting a plant resets it, never pauses it.** Release the key, leave the
  site, die, fire, or switch weapons and the progress is gone. Pausing gives you
  the classic "walk away, walk back, it finishes instantly".
- **A round reset must clear `room.history`.** Every player has just teleported,
  and a shot in the first quarter-second of the new round would otherwise be
  rewound against last round's positions — registering a hit on a body that was
  somewhere else. `PositionHistory.clear` already exists; it simply has to be
  called.
- **It must also clear `nades` and `zones`.** A fire zone from last round burns
  people in the new one, and a grenade in flight detonates on a fresh spawn.
- **`outfit` runs after `reset_loadout`, never instead of it.** `reset_loadout`
  hands out every weapon with full magazines, which is what deathmatch wants and
  what an economy has to undo — in that order, or the grant silently wins.
- **Attacker and defender are derived from `(team, half)`, never stored.** Stored,
  the half-time swap needs two updates and one of them gets forgotten.

## The swap changes roles, not teams

Counter-Strike moves the players: your team changes sides, so you inherit the
other side's spawns and the other half of the map. Here the swap flips one
boolean and `attackers` is derived from it — nobody's `team` changes, nobody
moves spawn, and `wins` stays indexed by the people who earned it, so no score
has to be reversed either.

That is a real deviation and it is only sound because of how the bundled maps are
built: the sites are placed *neutrally*, equidistant from both sides, precisely so
that attacking is not easier from one end. On a map with a defender-favouring
site, keeping your spawn across the swap would mean one team attacks the hard
site twice. If such a map is ever added, that is the moment to move players
instead — and `maplint` is where the rule would go.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from .. import grenades, weapons
from .base import GameMode, Goal
from .objectives import Site

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import Command, MatchPlayer, MatchRoom

#: Rounds a side must win to take the match.
ROUNDS_TO_WIN = 5

#: The round after which the sides swap.
#:
#: **`ROUNDS_TO_WIN - 1`, and the minus one is the whole point.** Set equal, a
#: side can take every round of the first half and win the match without the
#: other side ever having played the attacking role — which makes the map's
#: balance the entire result. Deriving it keeps the two from drifting, and it is
#: the structure CS uses for the same reason: you can lead the first half at
#: most `HALF_AT` to nothing, which is one short of winning, so the swap always
#: happens.
HALF_AT = ROUNDS_TO_WIN - 1

#: Buy time at the top of a round, in seconds.
#:
#: Damage is off during it (`damage_scale` returns 0), so the round cannot be
#: decided before it starts. Movement is deliberately *not* frozen: doing that
#: would mean a physics hook, and every mode paying for a branch that only one of
#: them uses. Walking to your side of the map during the buy is not an exploit.
FREEZE_TIME = 8.0

#: How long the attackers have to plant, in seconds.
ROUND_TIME = 105.0

#: How long the bomb burns before it goes off.
FUSE_TIME = 35.0

#: The pause between a round ending and the next one starting.
POST_TIME = 5.0

#: Seconds of held `use` to plant, and to defuse.
#:
#: Defusing is deliberately longer than planting: the attacker plants under
#: pressure with the round on the line, while the defender chooses their moment.
#: Equal times make the last seconds of a round a coin flip rather than a read.
PLANT_TIME = 3.2
DEFUSE_TIME_STANDARD = 10.0
DEFUSE_TIME_KIT = 5.0
DEFUSE_TIME = DEFUSE_TIME_STANDARD
DEFUSER_PRICE = 400

#: Seconds a player's own drop ignores them.
#:
#: A drop lands where you are standing, so without this it is picked straight
#: back up on the next tick and the key appears to do nothing at all. On the
#: wall clock, like a respawn: it is a property of the thing on the floor, not of
#: any one player's simulated time.
DROP_REPICK_DELAY = 1.0

# ---------------------------------------------------------------------------
# The economy
# ---------------------------------------------------------------------------
#
# Every number here is **served** in `welcome_state().config`, the `/weapons` and
# `/items` precedent. A second copy of a price in a client is a buy menu that
# disagrees with the server about what you can afford — and the way that fails is
# a purchase the menu offered and the server refused, with the money still there
# and nothing saying why.


#: What everybody starts a half with.
#:
#: Enough for a rifle *or* armour and a grenade, not both. The first round being
#: a real decision rather than a formality is most of what an economy is for.
START_MONEY = 800

#: Hard ceiling, so a long half cannot make the last rounds free.
MAX_MONEY = 16000

#: Paid to the killer, per kill. Flat rather than per weapon: a weapon-scaled
#: reward makes the cheap guns a *worse* buy the better you are with them, which
#: is backwards.
KILL_REWARD = 300

#: Charged to the killer for a team kill, and the reason it is a charge rather
#: than nothing: friendly fire here is partial, so a teammate in a doorway is
#: already a cost, and this is what stops "shoot through them" being free.
TEAMKILL_PENALTY = 300

#: The round rewards.
WIN_REWARD = 3000
#: A loss pays too, or a side that loses once loses the match: they buy nothing,
#: lose again, and the scoreline stops being about play.
LOSS_REWARD = 1400
#: Added per consecutive loss, up to `MAX_LOSS_STREAK` of them.
LOSS_STREAK_BONUS = 500
MAX_LOSS_STREAK = 4

#: Paid to the planter and the defuser, on top of the round reward.
#:
#: Paid **even on a losing round** — planting and then being wiped is still the
#: play that nearly worked, and an economy that only pays the winner punishes the
#: side already losing.
PLANT_REWARD = 300
DEFUSE_REWARD = 300


@dataclass(slots=True, frozen=True)
class BuyItem:
    """One thing you can buy.

    Not raw `WEAPONS` indices, so armour and grenades ride the same integer as a
    rifle and `Command.buy` stays one number.
    """

    id: str
    name: str
    #: `weapon`, `armour` or `nade`.
    kind: str
    #: Index into `weapons.WEAPONS` or `grenades.GRENADES`; unused for armour.
    slot: int
    price: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "slot": self.slot,
            "price": self.price,
        }


#: The catalogue, in the order a menu lists it.
#:
#: The knife and the pistol are absent because you always have them — an entry
#: for something you cannot not own would be a row that never does anything.
CATALOG: tuple[BuyItem, ...] = (
    BuyItem("assault", "Assault Rifle", "weapon", 2, 2700),
    BuyItem("shotgun", "Shotgun", "weapon", 3, 1800),
    BuyItem("sniper", "Sniper Rifle", "weapon", 4, 4750),
    BuyItem("armour", "Armour", "armour", -1, 1000),
    BuyItem("he", "HE Grenade", "nade", 0, 300),
    BuyItem("flash", "Flashbang", "nade", 1, 200),
    BuyItem("smoke", "Smoke Grenade", "nade", 2, 300),
    BuyItem("molotov", "Incendiary", "nade", 3, 600),
    BuyItem("defuser", "Defusal Kit", "kit", -1, DEFUSER_PRICE),
)

#: What you are given at the start of every round regardless of money.
#:
#: The knife and the pistol, and the pistol has unlimited reserve already, so
#: being broke is a bad round rather than an unplayable one.
FREE_SLOTS = (0, 1)

#: Phases, in the order they occur.
WARMUP = "warmup"
FREEZE = "freeze"
LIVE = "live"
POST = "post"
OVER = "over"


@dataclass(slots=True, frozen=True)
class Bomb:
    """Where the bomb is in its own little lifecycle.

    `state` is `"carried"`, `"dropped"`, `"planted"` or `"defused"`. A carrier who
    *dies* never leaves it on the floor — it passes to another living attacker,
    because a bomb lying where somebody fell on a map this size is a hunt rather
    than a round. `"dropped"` is only ever a choice (`Command.drop`, to hand it to
    a teammate better placed to plant), and `x`/`y`/`z` are then where it lies.
    """

    state: str = "carried"
    carrier: str = ""
    site: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    #: Seconds of fuse left once planted.
    fuse: float = 0.0


@dataclass(slots=True, frozen=True)
class RoundState:
    """Everything the phase machine needs, and nothing about the world.

    Frozen, so `advance` cannot mutate its input — which is what lets a
    conformance fixture replay a case and compare the whole structure rather than
    trusting the function to have left its argument alone.
    """

    phase: str = WARMUP
    #: Seconds left in the current phase.
    remaining: float = 0.0
    #: 1-based. `0` while in warmup, before the first round has begun.
    round: int = 0
    #: Rounds won, by team index.
    wins: tuple[int, int] = (0, 0)
    #: Which team is attacking *right now*, derived from the half when a round
    #: starts and then carried, so a swap mid-round is impossible by construction.
    attackers: int = 0
    #: Whether the sides have swapped yet.
    swapped: bool = False
    bomb: Bomb = field(default_factory=Bomb)
    #: The team that won the round that just ended, or -1.
    last_winner: int = -1


@dataclass(slots=True, frozen=True)
class DroppedKit:
    """A defusal kit dropped on the ground by a fallen defender."""

    id: str
    x: float
    y: float
    z: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
        }


@dataclass(slots=True, frozen=True)
class DroppedWeapon:
    """A bought gun somebody put down with `Command.drop`.

    It keeps its magazine and reserve, so handing a teammate a half-spent rifle
    hands them exactly that — a drop that refilled itself would be a free reload
    for two players standing next to each other.
    """

    id: str
    slot: int
    ammo: int
    reserve: int
    x: float
    y: float
    z: float
    dropped_by: str
    #: `time.monotonic()`, for `DROP_REPICK_DELAY`.
    dropped_at: float

    def to_dict(self) -> dict[str, Any]:
        # No ammunition: how many rounds are left in a gun on the floor is the
        # business of whoever picks it up, and it arrives in their own envelope.
        return {
            "id": self.id,
            "slot": self.slot,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "z": round(self.z, 2),
        }


def _standing_on(player: "MatchPlayer", x: float, y: float, z: float) -> bool:
    """Whether a body's feet are close enough to take something lying at `x,y,z`.

    One reach for everything a player can drop — kits, guns, the bomb — so no two
    of them can be taken from different distances.
    """
    dx = player.state.x - x
    dy = player.state.y - y
    dz = player.state.z - z
    return dx * dx + dy * dy <= 1.8 * 1.8 and -1.25 <= dz <= 4.5


def _catalog_index(kind: str, slot: int) -> int:
    """The catalogue row that sells this weapon or grenade slot, or `-1`."""
    return next(
        (
            i
            for i, item in enumerate(CATALOG)
            if item.kind == kind and item.slot == slot
        ),
        -1,
    )


@dataclass(slots=True, frozen=True)
class Facts:
    """What the world says this tick, as three numbers and two flags.

    The whole of the world's influence on the phase machine, which is what keeps
    the machine pure. Anything the machine needs must arrive through here.
    """

    attackers_alive: int = 1
    defenders_alive: int = 1
    #: The bomb was planted this tick, and on which site.
    planted_on: str = ""
    #: The bomb was defused this tick.
    defused: bool = False
    defuse_info: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class Emit:
    """Something the wrapper has to do in the world.

    Returned rather than performed, because performing it is exactly the part
    that needs a `MatchRoom` and would make the machine unportable.
    """

    kind: str
    team: int = -1
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _round_over(state: RoundState, winner: int, emits: list[Emit]) -> RoundState:
    wins = list(state.wins)
    if 0 <= winner < len(wins):
        wins[winner] += 1
    emits.append(Emit("round_end", team=winner))
    done = max(wins) >= ROUNDS_TO_WIN
    if done:
        emits.append(Emit("match_over", team=wins.index(max(wins))))
    return replace(
        state,
        phase=OVER if done else POST,
        remaining=0.0 if done else POST_TIME,
        wins=(wins[0], wins[1]),
        last_winner=winner,
        bomb=Bomb(
            state="defused" if state.bomb.state == "defused" else state.bomb.state
        ),
    )


def advance(
    state: RoundState, dt: float, facts: Facts
) -> tuple[RoundState, list[Emit]]:
    """One tick of the round clock. Pure: no world, no randomness, no time.

    `dt` is the room's elapsed seconds. `facts` is everything the world
    contributes. The returned emits are what the caller must then do.
    """
    emits: list[Emit] = []
    if state.phase == OVER:
        return state, emits

    if state.phase == WARMUP:
        # Warmup ends the moment both sides have somebody in them; there is no
        # timer, because a room that fills over a minute should not spend that
        # minute counting down and then start with one player.
        if facts.attackers_alive and facts.defenders_alive:
            return _begin_round(state, 1, emits), emits
        return state, emits

    remaining = state.remaining - dt
    bomb = state.bomb

    if state.phase == FREEZE:
        if remaining > 0:
            return replace(state, remaining=remaining), emits
        emits.append(Emit("round_live", team=state.attackers))
        return replace(state, phase=LIVE, remaining=ROUND_TIME), emits

    if state.phase == POST:
        if remaining > 0:
            return replace(state, remaining=remaining), emits
        return _begin_round(state, state.round + 1, emits), emits

    # LIVE.
    if facts.defused:
        emits.append(Emit("bomb_defused", extra=facts.defuse_info))
        planted = replace(bomb, state="defused", fuse=0.0)
        return _round_over(
            replace(state, bomb=planted), 1 - state.attackers, emits
        ), emits

    if facts.planted_on and bomb.state != "planted":
        emits.append(
            Emit("bomb_planted", team=state.attackers, detail=facts.planted_on)
        )
        bomb = replace(bomb, state="planted", site=facts.planted_on, fuse=FUSE_TIME)
        # **The round clock stops mattering the moment the bomb is down.** From
        # here it is the fuse, which is what makes a plant with four seconds left
        # a winning play rather than a wasted one.
        return replace(state, remaining=ROUND_TIME, bomb=bomb), emits

    if bomb.state == "planted":
        fuse = bomb.fuse - dt
        if fuse <= 0:
            emits.append(Emit("bomb_exploded"))
            return _round_over(
                replace(state, bomb=replace(bomb, fuse=0.0)), state.attackers, emits
            ), emits
        return replace(state, remaining=remaining, bomb=replace(bomb, fuse=fuse)), emits

    # Elimination, and only while the bomb is not planted: wiping the attackers
    # after a plant does not win the round, it just means nobody is left to stop
    # the defuse. That asymmetry is the whole reason to plant early.
    if not facts.attackers_alive:
        emits.append(Emit("eliminated", team=state.attackers))
        return _round_over(state, 1 - state.attackers, emits), emits
    if not facts.defenders_alive:
        emits.append(Emit("eliminated", team=1 - state.attackers))
        return _round_over(state, state.attackers, emits), emits

    if remaining <= 0:
        # Time out with no bomb down is a defender win: the attackers had a job
        # and did not do it.
        emits.append(Emit("time_out"))
        return _round_over(state, 1 - state.attackers, emits), emits

    return replace(state, remaining=remaining), emits


def _begin_round(state: RoundState, number: int, emits: list[Emit]) -> RoundState:
    """Open a round, swapping sides first if this is the one after half time."""
    swapped = state.swapped
    if not swapped and number > HALF_AT:
        swapped = True
        emits.append(Emit("half"))
    # Derived from the half, never stored across it: team 0 attacks the first
    # half and defends the second. Storing the roles instead means the swap has
    # two things to update and one of them gets forgotten.
    attackers = 1 if swapped else 0
    emits.append(Emit("round_start", team=attackers, detail=str(number)))
    return replace(
        state,
        phase=FREEZE,
        remaining=FREEZE_TIME,
        round=number,
        attackers=attackers,
        swapped=swapped,
        bomb=Bomb(),
        last_winner=-1,
    )


class Defuse(GameMode):
    id = "defuse"
    name = "Bomb Defuse"
    score_label = "Rounds"
    teams = True

    def __init__(self) -> None:
        self.state = RoundState()
        self.sites: list[Site] = []
        # Set by `on_command` when an action completes, drained by `tick`.
        #
        # A one-tick mailbox rather than a direct call into the phase machine,
        # because `advance` is pure and takes the world as `Facts`: a plant that
        # reached into it would be the one path that could change a round from
        # outside the function that owns rounds.
        self._planted_this_tick = ""
        self._defused_this_tick = False
        #: Consecutive rounds lost, per team, for the loss bonus. Per team and
        #: not per player: it is the *side* that has been losing, and a player
        #: who joined two rounds ago is on the same footing as one who did not.
        self._loss_streak = [0, 0]
        self.dropped_kits: list[DroppedKit] = []
        self.dropped_weapons: list[DroppedWeapon] = []
        self._drop_seq = 0
        #: Who put the bomb down, and when — `Bomb` has no room for either and
        #: the wire has no use for them, but `DROP_REPICK_DELAY` does.
        self._bomb_dropped_by = ""
        self._bomb_dropped_at = 0.0
        self._defuse_info: dict[str, Any] = {}
        self._last_defuse_accolades: dict[str, Any] = {}

    # -- lifecycle ----------------------------------------------------------

    def attach(self, room: MatchRoom) -> None:
        self.sites = list(room.objectives.sites)

    def reset(self, room: MatchRoom) -> None:
        self.state = RoundState()
        room.scores[:] = [0, 0]
        self.dropped_kits.clear()
        self.dropped_weapons.clear()
        self._defuse_info.clear()
        self._last_defuse_accolades.clear()
        self._reset_round(room)

    def _seed_money(self, player: MatchPlayer) -> None:
        player.money = START_MONEY

    def on_join(self, room: MatchRoom, player: MatchPlayer) -> None:
        """A mid-round joiner waits for the next one.

        Dropping them in alive would hand one side a body the other did not have
        to shoot, which decides the round on when somebody's browser finished
        loading.
        """
        # A joiner buys in at the starting purse rather than at whatever the
        # room has accumulated: arriving in round nine with nothing is a player
        # who cannot participate, and arriving with the round's average is a
        # reward for having missed it.
        self._seed_money(player)
        if self.state.phase in (LIVE, FREEZE) and self.state.round:
            player.alive = False
            player.health = 0.0

    # -- the round ----------------------------------------------------------

    def _alive(self, room: MatchRoom, team: int) -> int:
        return sum(1 for p in room.players.values() if p.team == team and p.alive)

    def tick(self, room: MatchRoom, elapsed: float, now: float) -> None:
        state = self.state

        # Check dropped defusal kit pickups by living defenders
        for kit in list(self.dropped_kits):
            for player in room.players.values():
                if not player.alive or player.team == state.attackers:
                    continue
                if "defuser" in player.owned_extras:
                    continue
                if _standing_on(player, kit.x, kit.y, kit.z):
                    player.owned_extras.add("defuser")
                    self.dropped_kits.remove(kit)
                    room._emit(
                        {
                            "kind": "kit_pickup",
                            "player": player.id,
                            "name": player.name,
                            "id": kit.id,
                        }
                    )
                    break

        self._collect_drops(room, now)
        # **Re-read, never reuse the copy from the top of the tick.** A bomb
        # picked up just now lives only in `self.state`; advancing the stale copy
        # would write the pickup back out, leaving the bomb on the floor with a
        # teammate standing on it.
        state = self.state

        facts = Facts(
            attackers_alive=self._alive(room, state.attackers),
            defenders_alive=self._alive(room, 1 - state.attackers),
            planted_on=self._planted_this_tick,
            defused=self._defused_this_tick,
            defuse_info=dict(self._defuse_info),
        )
        self._planted_this_tick = ""
        self._defused_this_tick = False
        self._defuse_info.clear()

        # A planted bomb tracks the carrier's last position, which is where it
        # was put down — not where they are now.
        new_state, emits = advance(state, elapsed, facts)
        self.state = new_state
        for emit in emits:
            self._perform(room, emit)

        room.scores[:] = list(new_state.wins)

    def _perform(self, room: MatchRoom, emit: Emit) -> None:
        room._emit(
            {
                "kind": emit.kind,
                **({"team": emit.team} if emit.team >= 0 else {}),
                **({"detail": emit.detail} if emit.detail else {}),
                **emit.extra,
            }
        )
        if emit.kind == "round_end":
            self._pay_round(room, emit.team)
        elif emit.kind == "half":
            # Both sides start the second half level, or the first half's economy
            # decides a match whose sides have just been swapped.
            for player in room.players.values():
                player.money = START_MONEY
            self._loss_streak = [0, 0]
        elif emit.kind == "round_start":
            self._reset_round(room)

    def _pay_round(self, room: MatchRoom, winner: int) -> None:
        """The round's wages, and the loss streak that keeps a match alive.

        A loss pays as well as a win, and pays *more* the longer the losing side
        has been losing. Without it a side that loses once buys nothing, loses
        again, and the scoreline stops being about play — which is the failure
        mode an economy is most likely to introduce and the one it is least
        obvious it has.
        """
        for team in (0, 1):
            if team == winner:
                self._loss_streak[team] = 0
                reward = WIN_REWARD
            else:
                streak = min(self._loss_streak[team], MAX_LOSS_STREAK)
                reward = LOSS_REWARD + LOSS_STREAK_BONUS * streak
                self._loss_streak[team] = min(
                    self._loss_streak[team] + 1, MAX_LOSS_STREAK
                )
            for player in room.players.values():
                if player.team == team:
                    player.money = min(MAX_MONEY, player.money + reward)

    def _reset_round(self, room: MatchRoom) -> None:
        """Everything that has to go back to the start, and nothing that does not.

        Money, kills and deaths persist: they are the match, not the round.
        """
        room.items.reset()
        room.nades.clear()
        room.zones.clear()
        # Every body has just teleported. Without this, a shot in the first
        # quarter-second of the round is rewound against last round's positions
        # and registers on somebody who was somewhere else — silently, and only
        # for the first few ticks, which makes it near-impossible to reproduce.
        room.history.clear()
        for player in room.players.values():
            # What you bought lasts one round. Cleared *before* the respawn, so
            # `outfit` — which runs inside it — sees the round you are entering
            # rather than the one you just left. After it, every player would
            # spawn with the previous round's kit and lose it a frame later.
            player.owned.clear()
            player.owned_nades.clear()
            player.owned_extras.clear()
            player.purchased.clear()
            room.respawn(player)
            player.action_progress = 0.0
            player.action_kind = ""
        self.dropped_kits.clear()
        self.dropped_weapons.clear()
        self._defuse_info.clear()
        self._last_defuse_accolades.clear()
        self._give_bomb(room)

    def _give_bomb(self, room: MatchRoom) -> None:
        """Hand the bomb to an attacker, deterministically.

        The room's own seeded RNG, so a replayed match hands it to the same
        person — the same argument `MatchRoom.rng` already makes about shotgun
        patterns.
        """
        attackers = [
            p
            for p in room.players.values()
            if p.team == self.state.attackers and p.alive
        ]
        if not attackers:
            return
        holder = room.rng.choice(attackers)
        self.state = replace(self.state, bomb=Bomb(state="carried", carrier=holder.id))

    def _collect_drops(self, room: MatchRoom, now: float) -> None:
        """Hand a dropped bomb or gun to the first living body standing on it.

        The bomb only to an attacker — a defender walking over it leaves it where
        it is, since taking it off the map would end the round without a shot.
        A gun to anyone who does not already own one of that kind, and **never
        into `purchased`**: it was somebody else's money, and refunding it would
        let a teammate buy once and have it sold back twice.
        """
        bomb = self.state.bomb
        if bomb.state == "dropped":
            for player in room.players.values():
                if not player.alive or player.team != self.state.attackers:
                    continue
                if (
                    player.id == self._bomb_dropped_by
                    and now - self._bomb_dropped_at < DROP_REPICK_DELAY
                ):
                    continue
                if not _standing_on(player, bomb.x, bomb.y, bomb.z):
                    continue
                self.state = replace(
                    self.state, bomb=Bomb(state="carried", carrier=player.id)
                )
                room._emit(
                    {
                        "kind": "bomb_pickup",
                        "team": player.team,
                        "by": player.id,
                        "byName": player.name,
                    }
                )
                break

        for drop in list(self.dropped_weapons):
            for player in room.players.values():
                if not player.alive or drop.slot in player.owned:
                    continue
                if (
                    player.id == drop.dropped_by
                    and now - drop.dropped_at < DROP_REPICK_DELAY
                ):
                    continue
                if not _standing_on(player, drop.x, drop.y, drop.z):
                    continue
                self.dropped_weapons.remove(drop)
                player.owned.add(drop.slot)
                player.ammo[drop.slot] = drop.ammo
                player.reserve[drop.slot] = drop.reserve
                # Into your hands only if they held nothing worth keeping there.
                # Swapping somebody off a rifle because they brushed past a
                # shotgun would be a pickup that costs a fight.
                if player.weapon in FREE_SLOTS:
                    self._hold(player, drop.slot)
                break

    # -- simulation ---------------------------------------------------------

    def may_respawn(self, room: MatchRoom, player: MatchPlayer, now: float) -> bool:
        """Nobody comes back inside a round. Warmup is a warmup, so they do."""
        return self.state.phase == WARMUP

    def damage_scale(
        self, room: MatchRoom, attacker: MatchPlayer, victim: MatchPlayer
    ) -> float:
        """Partial friendly fire, and none at all during freeze time.

        The partial figure is why `damage_scale` returns a float rather than a
        bool: a teammate you spray through is a real cost, and a teammate you
        cannot hurt at all makes a doorway a place to stand.
        """
        if self.state.phase == FREEZE:
            return 0.0
        if attacker.team == victim.team:
            return 0.35
        return 1.0

    def on_kill(
        self,
        room: MatchRoom,
        victim: MatchPlayer,
        attacker: MatchPlayer,
        head: bool,
        weapon: Any,
    ) -> None:
        """Rounds are the score, so a kill scores nothing.

        No `super()`, for the same reason CTF has none: the base adds one to the
        killer's team score, which under a scoreboard labelled "Rounds" would
        make the number mean two things at once.
        """
        if attacker.id != victim.id:
            if attacker.team == victim.team:
                # Friendly fire is partial here, so a teammate in a doorway is
                # already a cost; this is what stops shooting through them being
                # free. Floored at zero — a debt would follow somebody into a
                # round they had nothing to do with.
                attacker.money = max(0, attacker.money - TEAMKILL_PENALTY)
            else:
                attacker.money = min(MAX_MONEY, attacker.money + KILL_REWARD)

    def on_death(self, room: MatchRoom, player: MatchPlayer) -> None:
        """Hand over bomb if carrier died, and drop defuse kit if defender had one."""
        if self.state.bomb.carrier == player.id:
            self._give_bomb(room)
        if player.team != self.state.attackers and "defuser" in player.owned_extras:
            player.owned_extras.discard("defuser")
            kit = DroppedKit(
                id=f"kit_{player.id}_{self.state.round}_{int(time.time() * 1000)}",
                x=player.state.x,
                y=player.state.y,
                z=player.state.z,
            )
            self.dropped_kits.append(kit)
            room._emit(
                {
                    "kind": "kit_drop",
                    "player": player.id,
                    "x": round(kit.x, 2),
                    "y": round(kit.y, 2),
                    "z": round(kit.z, 2),
                }
            )

    def on_command(
        self, room: MatchRoom, player: MatchPlayer, command: Command, now: float
    ) -> None:
        """Plant, defuse and buy.

        Buying first, and not gated on `LIVE` like the rest: the whole point of
        it is that it happens in the freeze. Selling before buying, so one frame
        can trade a rifle for a sniper on the strength of the refund.
        """
        if command.sell >= 0:
            self._sell(room, player, command.sell)
        if command.buy >= 0:
            self._buy(room, player, command.buy)
        if command.drop:
            self._drop(room, player, now)

        if self.state.phase != LIVE or not player.alive:
            self._clear_action(player)
            return

        kind = self._action_for(room, player)
        if not kind or not command.use or command.fire or command.weapon >= 0:
            # Firing or switching cancels, and so does letting go. **Reset, not
            # pause** — otherwise walking away and back finishes it instantly.
            self._clear_action(player)
            return

        if player.action_kind != kind:
            player.action_kind = kind
            player.action_progress = 0.0
        if kind == "plant":
            span = PLANT_TIME
        else:
            span = (
                DEFUSE_TIME_KIT
                if "defuser" in player.owned_extras
                else DEFUSE_TIME_STANDARD
            )
        player.action_progress = min(1.0, player.action_progress + command.dt / span)
        if player.action_progress < 1.0:
            return

        self._clear_action(player)
        if kind == "plant":
            site = room.objectives.site_at(
                player.state.x, player.state.y, player.state.z
            )
            if site is None:
                return
            player.objectives += 1
            # Paid here rather than at round end, and paid **even if this round
            # is then lost**: planting and being wiped is still the play that
            # nearly worked, and an economy that only pays the winner punishes
            # the side already losing. Paying it now also avoids having to
            # remember who the planter was, since they may not be alive later.
            player.money = min(MAX_MONEY, player.money + PLANT_REWARD)
            self._planted_this_tick = site.id
            self.state = replace(
                self.state,
                bomb=replace(
                    self.state.bomb,
                    carrier="",
                    x=player.state.x,
                    y=player.state.y,
                    z=player.state.z,
                ),
            )
        else:
            player.objectives += 1
            player.money = min(MAX_MONEY, player.money + DEFUSE_REWARD)

            # Clutch & ninja defuse detection
            alive_attackers = sum(
                1
                for p in room.players.values()
                if p.team == self.state.attackers and p.alive
            )
            is_ninja = alive_attackers >= 2
            fuse_left = max(0.0, self.state.bomb.fuse)
            is_clutch = fuse_left < 1.0
            clutch_time = round(fuse_left, 2)
            has_kit = "defuser" in player.owned_extras

            self._defused_this_tick = True
            accolades = {
                "by": player.id,
                "byName": player.name,
                "ninja": is_ninja,
                "clutch": is_clutch,
                "clutchTime": clutch_time,
                "hasKit": has_kit,
            }
            self._defuse_info = dict(accolades)
            self._last_defuse_accolades = dict(accolades)

    def _buy(self, room: MatchRoom, player: MatchPlayer, index: int) -> None:
        """Spend, if every one of the reasons not to is absent.

        Checked here and not trusted from the client for the usual reason — a
        client that decided whether it could afford something would have infinite
        money — but also because these are the checks a *menu* cannot make: what
        phase the room is in, and what this player already owns.

        **A purchase that would give nothing spends nothing.** That is the
        `pickups.apply` shape and it is the same argument: taking armour at full
        armour, or a second rifle you already hold, should not quietly cost you
        the round's money. Every branch below returns without touching `money`.
        """
        if index >= len(CATALOG):
            return
        # The buy window. A dead player may still buy — they are buying for the
        # *next* round, and freeze time is exactly when that is decided — so
        # `alive` is deliberately not checked.
        if self.state.phase != FREEZE:
            return
        item = CATALOG[index]
        if item.id == "defuser" and player.team == self.state.attackers:
            return
        if self._owns(player, item):
            return
        if player.money < item.price:
            return
        player.money -= item.price
        player.purchased.add(index)
        if item.kind == "weapon":
            player.owned.add(item.slot)
            # Handed over now rather than at the next spawn: the buy happens
            # during the freeze of the round it is for, and a rifle that only
            # arrived next round would be a menu that lies about what it sold.
            spec = weapons.weapon_at(item.slot)
            player.ammo[item.slot] = spec.mag
            player.reserve[item.slot] = spec.reserve
            player.weapon = item.slot
        elif item.kind == "nade":
            player.owned_nades.add(item.slot)
            player.nades.counts[item.slot] = 1
        elif item.kind == "armour":
            player.owned_extras.add(item.id)
            player.armour = weapons.MAX_ARMOUR
        elif item.kind == "kit":
            player.owned_extras.add(item.id)
        # No public effect. What somebody bought is revealed by the gun in their
        # hands, which `PlayerRow.weapon` already broadcasts — an fx would be
        # telling the other side what to expect before they could see it.

    def _owns(self, player: MatchPlayer, item: BuyItem) -> bool:
        if item.kind == "weapon":
            return item.slot in player.owned
        if item.kind == "nade":
            return item.slot in player.owned_nades
        if item.kind == "armour":
            return item.id in player.owned_extras or player.armour >= weapons.MAX_ARMOUR
        if item.id == "defuser":
            return item.id in player.owned_extras or player.team == self.state.attackers
        return item.id in player.owned_extras

    def _sellable(self, player: MatchPlayer, index: int) -> bool:
        """Whether the purchase at `index` can still be undone.

        Paid for, and **still exactly what was sold**: a gun still in your
        hands, a grenade not yet thrown, armour that has not absorbed anything.
        Refunding a spent grenade or a dented vest would make the freeze a
        window in which utility is free.
        """
        if index not in player.purchased or index >= len(CATALOG):
            return False
        item = CATALOG[index]
        if item.kind == "weapon":
            return item.slot in player.owned
        if item.kind == "nade":
            return player.nades.counts.get(item.slot, 0) > 0
        if item.kind == "armour":
            return (
                item.id in player.owned_extras and player.armour >= weapons.MAX_ARMOUR
            )
        return item.id in player.owned_extras

    def _sell(self, room: MatchRoom, player: MatchPlayer, index: int) -> None:
        """Undo a purchase made in this freeze, for everything it cost.

        The mirror of `_buy`, and gated the same way — the freeze, and nothing
        the menu decided. A full refund because the window is short and the
        mistake it corrects is a misclick on a number row; a partial one would
        make the buy menu a place you are afraid to press keys.
        """
        if self.state.phase != FREEZE or not self._sellable(player, index):
            return
        item = CATALOG[index]
        player.purchased.discard(index)
        player.money = min(MAX_MONEY, player.money + item.price)
        if item.kind == "weapon":
            player.owned.discard(item.slot)
            player.ammo[item.slot] = 0
            player.reserve[item.slot] = 0
            if player.weapon == item.slot:
                self._hold(player, 1)
        elif item.kind == "nade":
            player.owned_nades.discard(item.slot)
            player.nades.counts[item.slot] = 0
        elif item.kind == "armour":
            player.owned_extras.discard(item.id)
            player.armour = 0.0
        else:
            player.owned_extras.discard(item.id)

    def _drop(self, room: MatchRoom, player: MatchPlayer, now: float) -> None:
        """Put down the bomb if you carry it, and otherwise the gun in your hands.

        **The bomb first**, because that is what a carrier means by it: the
        drop exists so the player holding the bomb can hand it to one better
        placed, and a carrier who wanted to give away a rifle instead can do
        that the moment the bomb is gone.

        Only a bought gun — the knife and the pistol are `FREE_SLOTS`, and
        dropping something everybody is always given would put a pile of them on
        the floor for no one. A dead player drops nothing: what they held has
        already stayed with them, the rule `test_what_you_bought_survives_dying`
        pins.
        """
        if not player.alive:
            return
        bomb = self.state.bomb
        if bomb.state == "carried" and bomb.carrier == player.id:
            self._bomb_dropped_by = player.id
            self._bomb_dropped_at = now
            self.state = replace(
                self.state,
                bomb=Bomb(
                    state="dropped",
                    x=player.state.x,
                    y=player.state.y,
                    z=player.state.z,
                ),
            )
            self._clear_action(player)
            room._emit(
                {
                    "kind": "bomb_drop",
                    "team": player.team,
                    "by": player.id,
                    "byName": player.name,
                }
            )
            return

        slot = player.weapon
        if slot in FREE_SLOTS or slot not in player.owned:
            return
        self._drop_seq += 1
        self.dropped_weapons.append(
            DroppedWeapon(
                id=f"gun{self.state.round}-{self._drop_seq}",
                slot=slot,
                ammo=player.ammo.get(slot, 0),
                reserve=player.reserve.get(slot, 0),
                x=player.state.x,
                y=player.state.y,
                z=player.state.z,
                dropped_by=player.id,
                dropped_at=now,
            )
        )
        player.owned.discard(slot)
        # Out of your hands is out of `purchased`. Otherwise buy, drop for a
        # teammate, and sell the empty slot back: one rifle, two refunds' worth.
        player.purchased.discard(_catalog_index("weapon", slot))
        player.ammo[slot] = 0
        player.reserve[slot] = 0
        self._hold(player, 1)

    def _hold(self, player: MatchPlayer, slot: int) -> None:
        """Switch hands server-side, cancelling whatever the old weapon was doing.

        A reload or a spray index carried across the swap would finish the
        pistol's reload on the rifle, or start the rifle three shots into its
        pattern.
        """
        player.weapon = slot
        player.reload_until = -999.0
        player.reloading_empty = False
        player.spray_index = 0

    def _clear_action(self, player: MatchPlayer) -> None:
        player.action_progress = 0.0
        player.action_kind = ""

    def _action_for(self, room: MatchRoom, player: MatchPlayer) -> str:
        """What holding `use` means for this player, here, right now."""
        bomb = self.state.bomb
        attacking = player.team == self.state.attackers
        if attacking and bomb.state == "carried" and bomb.carrier == player.id:
            site = room.objectives.site_at(
                player.state.x, player.state.y, player.state.z
            )
            return "plant" if site is not None else ""
        if not attacking and bomb.state == "planted":
            dx, dy = player.state.x - bomb.x, player.state.y - bomb.y
            if dx * dx + dy * dy <= 4.0 and abs(player.state.z - bomb.z) <= 3.0:
                return "defuse"
        return ""

    def outfit(self, room: MatchRoom, player: MatchPlayer) -> None:
        """Take back what `reset_loadout` just gave, and hand over what was bought.

        **Order is the whole contract.** `reset_loadout` grants every weapon with
        full magazines, which is what deathmatch wants and what an economy has to
        undo; this runs immediately after it, and reversing the two would let the
        grant silently win with no symptom but an economy that does nothing.

        Not a parameter on `reset_loadout` for the same reason: that method is
        deathmatch's, and changing it would change deathmatch.
        """
        for slot in range(len(weapons.WEAPONS)):
            if slot in FREE_SLOTS or slot in player.owned:
                continue
            # Zero rather than absent: `ammo.get(slot, 0)` reads the same either
            # way, but a missing key would make a weapon that was never bought
            # indistinguishable from one whose table entry has gone.
            player.ammo[slot] = 0
            player.reserve[slot] = 0
        player.nades.counts = {
            i: (1 if i in player.owned_nades else 0)
            for i in range(len(grenades.GRENADES))
        }
        player.armour = weapons.MAX_ARMOUR if "armour" in player.owned_extras else 0.0
        # Hold something you actually have. Defaulting to the rifle leaves a
        # player who could not afford one holding an empty gun and wondering why
        # the trigger does nothing.
        if player.weapon not in FREE_SLOTS and player.weapon not in player.owned:
            player.weapon = 1

    # -- wire ---------------------------------------------------------------

    def welcome_state(self, room: MatchRoom) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scoreLabel": self.score_label,
            "v": self.version,
            "teams": self.teams,
            "config": {
                "roundsToWin": ROUNDS_TO_WIN,
                "halfAt": HALF_AT,
                "freezeTime": FREEZE_TIME,
                "roundTime": ROUND_TIME,
                "fuseTime": FUSE_TIME,
                "postTime": POST_TIME,
                "plantTime": PLANT_TIME,
                "defuseTime": DEFUSE_TIME_STANDARD,
                "defuseTimeStandard": DEFUSE_TIME_STANDARD,
                "defuseTimeKit": DEFUSE_TIME_KIT,
                # The economy, served whole. A client with its own copy of a
                # price is a buy menu that disagrees with the server about what
                # you can afford, and the way that fails is a purchase the menu
                # offered and the server refused — money still there, nothing
                # saying why.
                "startMoney": START_MONEY,
                "maxMoney": MAX_MONEY,
                "killReward": KILL_REWARD,
                "winReward": WIN_REWARD,
                "lossReward": LOSS_REWARD,
                "lossStreakBonus": LOSS_STREAK_BONUS,
                "plantReward": PLANT_REWARD,
                "defuseReward": DEFUSE_REWARD,
            },
            #: What can be bought, in the order a menu lists it. The index into
            #: this list *is* `Command.buy`, so a client never invents an id.
            "catalog": [item.to_dict() for item in CATALOG],
            "sites": [s.to_dict() for s in self.sites],
            **(self.shared_state(room) or {}),
        }

    def shared_state(self, room: MatchRoom) -> dict[str, Any]:
        state = self.state
        bomb: dict[str, Any] = {"state": state.bomb.state}
        if state.bomb.state == "carried":
            # Public, like CTF's carrier: on a map this size the holder is the
            # thing both teams are already tracking, and hiding the name while
            # the body is visible would be incoherent rather than secret.
            bomb["carrier"] = state.bomb.carrier
        elif state.bomb.state == "planted":
            bomb.update(
                {
                    "site": state.bomb.site,
                    "x": round(state.bomb.x, 2),
                    "y": round(state.bomb.y, 2),
                    "z": round(state.bomb.z, 2),
                    "fuseIn": round(state.bomb.fuse, 1),
                }
            )
        elif state.bomb.state == "dropped":
            # Public for the carrier's reason: it is lying in plain sight.
            bomb.update(
                {
                    "x": round(state.bomb.x, 2),
                    "y": round(state.bomb.y, 2),
                    "z": round(state.bomb.z, 2),
                }
            )
        elif state.bomb.state == "defused":
            bomb.update(
                {
                    "site": state.bomb.site,
                    "x": round(state.bomb.x, 2),
                    "y": round(state.bomb.y, 2),
                    "z": round(state.bomb.z, 2),
                    "fuseIn": 0.0,
                    **self._last_defuse_accolades,
                }
            )
        return {
            "phase": state.phase,
            "phaseIn": round(max(0.0, state.remaining), 1),
            "round": state.round,
            "attackers": state.attackers,
            "swapped": state.swapped,
            "bomb": bomb,
            "kits": [k.to_dict() for k in self.dropped_kits],
            "drops": [d.to_dict() for d in self.dropped_weapons],
        }

    def private_state(self, room: MatchRoom, player: MatchPlayer) -> dict[str, Any]:
        has_kit = "defuser" in player.owned_extras
        return {
            "attacking": player.team == self.state.attackers,
            "carrying": self.state.bomb.carrier == player.id,
            "progress": round(player.action_progress, 3),
            "progressKind": player.action_kind,
            # **Per recipient, and this is the field that makes that matter.**
            # In `shared_state` it would be every player's purse, world-readable,
            # with nothing raising, warning or breaking the snapshot template to
            # say so. `private_view` is the only half of a snapshot rebuilt per
            # player, so this is the only place it can go.
            "money": player.money,
            "canBuy": self.state.phase == FREEZE,
            "hasKit": has_kit,
            "defuseTime": DEFUSE_TIME_KIT if has_kit else DEFUSE_TIME_STANDARD,
            # Indices into the served catalogue, so a menu can grey out what is
            # already owned without keeping its own idea of what that means.
            "bought": sorted(
                index for index, item in enumerate(CATALOG) if self._owns(player, item)
            ),
            # What pressing an owned row would sell back. A list of its own and
            # not derived from `bought`, because owning is not having paid: a
            # gun picked up off a teammate is owned and is not refundable, and a
            # menu that guessed would offer a sale the server refuses.
            "sellable": (
                sorted(i for i in player.purchased if self._sellable(player, i))
                if self.state.phase == FREEZE
                else []
            ),
        }

    # -- results ------------------------------------------------------------

    def outcome_for(self, room: MatchRoom, player: MatchPlayer) -> tuple[bool, bool]:
        mine = room.scores[player.team] if player.team < len(room.scores) else 0
        theirs = max(
            (s for i, s in enumerate(room.scores) if i != player.team), default=0
        )
        if mine <= theirs:
            return (False, False)

        def worth(p: MatchPlayer) -> int:
            return p.objectives * 3 + p.kills

        best = max(
            (worth(p) for p in room.players.values() if p.team == player.team),
            default=0,
        )
        return (True, worth(player) >= best)

    def rounds_won(self, room: MatchRoom, player: MatchPlayer) -> int:
        """Rounds their side took — which here *is* what `scores` counts.

        Read off the room rather than off `RoundState`, so a half-time swap needs
        no second update: `scores` is reversed with the teams, so a player who
        changed side keeps the rounds they won on the old one.
        """
        return room.scores[player.team] if player.team < len(room.scores) else 0

    # -- bots ---------------------------------------------------------------

    def bot_goal(self, room: MatchRoom, me: MatchPlayer) -> Goal | None:
        state = self.state
        bomb = state.bomb
        attacking = me.team == state.attackers

        if bomb.state == "planted":
            # Both sides converge on it: one to defuse, one to stop the defuse.
            return Goal(
                x=bomb.x,
                y=bomb.y,
                z=bomb.z,
                use=not attacking,
                radius=1.8 if not attacking else 4.0,
            )
        if attacking and bomb.state == "dropped":
            # A bomb on the floor is the attackers' whole round until somebody
            # picks it up, and no bot drops one — this is them fetching a human's.
            return Goal(x=bomb.x, y=bomb.y, z=bomb.z, radius=1.0)
        if not self.sites:
            return None
        if attacking:
            site = self.sites[hash(me.id) % len(self.sites)]
            return Goal(
                x=site.x,
                y=site.y,
                z=site.z,
                # Only the carrier can plant, and holding `use` elsewhere is
                # harmless — but sending it only from the carrier keeps the wire
                # honest about what the bot is trying to do.
                use=bomb.carrier == me.id,
                radius=site.radius * 0.6,
            )
        site = self.sites[hash(me.id) % len(self.sites)]
        return Goal(x=site.x, y=site.y, z=site.z, radius=site.radius)
