"""The contract a game mode implements, and the do-nothing mode.

`MatchRoom` used to encode "the only mode is deathmatch" in exactly five places,
and this package exists to be those five places rather than to be a framework:

===============================  ==========================================
What                             Where it lived
===============================  ==========================================
``scores[attacker.team] += 1``   ``_apply_damage``
``other.team == player.team``    ``_fire``, ``_detonate``, ``_burn``
unconditional timed respawn      ``_respawn_due``
room-relative ``won``/``mvp``    ``result_for``
no clock at all                  absent from ``simulate``
===============================  ==========================================

Everything else in the simulation — physics, weapons, noise, radar, grenades,
pickups, prediction, the peer fabric, the time budget — is genuinely
mode-invariant, and no hook is added for it. The rest of the surface here is
wire (three methods) and lifecycle plumbing that was already a method body
somewhere.

**Every hook has a working default**, which is the property that makes this safe
to land: a `MatchRoom` built without a mode gets `Deathmatch()` and behaves
exactly as it did before, so every existing test keeps passing without being
touched, and a new mode overrides only what it actually changes.

## Where mode state is allowed to go on the wire

Three methods, mapping onto the three places the snapshot already has:

- `welcome_state` rides once with the welcome, like item placements. Static
  configuration belongs here — timings, prices, site positions.
- `shared_state` is public and per tick. Everyone sees it.
- `private_state` is per recipient and goes inside `you`.

**Putting per-recipient data in `shared_state` does not raise, does not warn,
and does not break the snapshot template's fragmentation** — it simply sends
every player's money to every player. The split (`_ACK_HOLE` / `_YOU_HOLE` in
`match.py`) only guards `ack` and `you`. That is the most dangerous mistake
available in this package and there is a test for it.

Relatedly: **never echo a client-supplied string into any of these blobs.** A
sentinel collision is caught and falls back to `send_json`, so the failure is not
a crash — it is the bandwidth optimisation silently switching itself off. Site
ids and phase names are server-chosen enums for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..match import Command, MatchPlayer, MatchRoom


@dataclass(slots=True)
class Goal:
    """Somewhere a bot should go, and whether to hold the action key there.

    The whole of the objective interface bots get. Deliberately a *point* and not
    a path: there is no navmesh, and every goal a mode produces is a position
    `maplint` has already proved standable and connected, which is the same class
    of position `bots._pick_roam` already steers to.

    `expires_in` is not optional politeness. Bots stick on concave geometry, and
    a goal with no timeout parks an entire team against a wall for a whole round
    — which reads as "the mode is broken" rather than as "the pathing is dumb".
    """

    x: float
    y: float
    z: float
    #: Hold `use` once inside `radius`.
    use: bool = False
    radius: float = 2.0
    expires_in: float = 12.0


class GameMode:
    """A mode's hooks. Every one of them does nothing by default."""

    #: Stable id. Used as the registry key, in `CreateMatchRequest`, and to key
    #: `find_or_create` — two rooms on the same map in different modes are two
    #: rooms.
    id: str = "none"
    name: str = "None"

    #: Whether this mode is scored by side.
    #:
    #: A class attribute rather than something each mode happens to set in
    #: `__init__`, because `catalog()` reads it off an instance to answer the
    #: lobby — and `getattr(mode, "teams", False)` on a mode that only declared
    #: it inside `welcome_state` reported every objective mode as a free-for-all.
    #: The welcome said one thing and the catalog another, both plausibly, and
    #: the only place it showed was a lobby offering one scoreboard for a game
    #: with two sides.
    teams: bool = False

    #: What the number in `scores` counts, for the HUD to label its own display
    #: with. Served rather than duplicated per client, the `plane_order`
    #: precedent: a client that hardcodes "KILLS" is a client that lies as soon
    #: as a mode counts something else.
    score_label: str = "Kills"

    #: Bumped when the shape of the wire blobs changes. The native client
    #: compares it against its own constant and reports a mismatch once —
    #: without it an old build joins, renders none of the mode, and says nothing,
    #: because serde's `#[serde(default)]` swallows unknown keys silently.
    version: int = 1

    # -- lifecycle ----------------------------------------------------------

    def attach(self, room: MatchRoom) -> None:
        """Seed from the room's world, spawns and objectives."""

    def reset(self, room: MatchRoom) -> None:
        """Back to the start, as `server.restart` means it."""

    def tick(self, room: MatchRoom, elapsed: float, now: float) -> None:
        """Advance the mode's own clock.

        Called from `simulate` **after** grenades and **before**
        `history.record`, so a round that ends this tick has already seen every
        consequence of the commands in it.

        `now` is `time.monotonic()` — the room's clock, not any player's
        simulated time. That distinction is load-bearing and is the same one
        `_step_grenades` makes: a round timer and a bomb fuse belong to nobody,
        so pacing them by a player's command stream would freeze them when that
        player disconnects. Anything accrued by a *living player who is sending
        commands* — a plant or defuse progress bar — must instead be spent from
        `command.dt` in `on_command`, or a stuttering client plants more slowly
        than a smooth one and nothing says so.
        """

    def on_join(self, room: MatchRoom, player: MatchPlayer) -> None:
        """Somebody arrived, possibly mid-round."""

    def on_leave(self, room: MatchRoom, player: MatchPlayer) -> None:
        """Somebody left. Drop anything keyed by them."""

    # -- simulation ---------------------------------------------------------

    def damage_scale(
        self, room: MatchRoom, attacker: MatchPlayer, victim: MatchPlayer
    ) -> float:
        """How much of a hit between these two actually lands, 0.0 to 1.0.

        A float and not a bool because the three modes want three answers:
        free-for-all wants 1.0 on everyone, team modes want 0.0 on a teammate,
        and a defuse mode wants CS-style partial friendly fire. A bool would need
        a second hook beside it.

        Called from **all three** damage sites — bullets, the HE blast and a fire
        zone. Fixing two of the three is the regression this refactor most
        invites, so there is a test per site.

        `_fire` uses it as a legality filter (`> 0`) when deciding who is even a
        candidate, because `weapons.resolve_shot` is pure geometry and expects
        the caller to have filtered; `_apply_damage` multiplies by it. One hook,
        two call shapes.
        """
        return 0.0 if attacker.team == victim.team else 1.0

    def may_respawn(self, room: MatchRoom, player: MatchPlayer, now: float) -> bool:
        """Whether a dead player whose timer has expired comes back now."""
        return True

    def outfit(self, room: MatchRoom, player: MatchPlayer) -> None:
        """Adjust a loadout immediately after `reset_loadout`.

        Separate from `reset_loadout` rather than a parameter on it, because that
        method is what deathmatch wants and changing it would change deathmatch.
        A mode with an economy strips what was just granted and re-grants what
        was bought; the default does nothing, so deathmatch is untouched.
        """

    def on_kill(
        self,
        room: MatchRoom,
        victim: MatchPlayer,
        attacker: MatchPlayer,
        head: bool,
        weapon: Any,
    ) -> None:
        """Score it. The one place `room.scores` is mutated by a kill.

        Note `attacker.kills` is incremented by `_apply_damage` regardless, and
        deliberately so even for a team kill: `kills` is a *stat*, and a mode
        that does not want a team kill to count decides that here, in what it
        does to `scores`.
        """
        if 0 <= attacker.team < len(room.scores):
            room.scores[attacker.team] += 1

    def on_command(
        self, room: MatchRoom, player: MatchPlayer, command: Command, now: float
    ) -> None:
        """The objective half of a command: `use`, and `buy`.

        Called from `_handle_combat`, so it runs once per consumed command with
        that command's own `dt` already spent from the player's budget.
        """

    # -- wire ---------------------------------------------------------------

    def welcome_state(self, room: MatchRoom) -> dict[str, Any] | None:
        """Static configuration plus the current public state, sent once."""
        return None

    def shared_state(self, room: MatchRoom) -> dict[str, Any] | None:
        """Public state, every tick. Visible to everyone — see the module docs."""
        return None

    def private_state(
        self, room: MatchRoom, player: MatchPlayer
    ) -> dict[str, Any] | None:
        """This player's own state. Goes inside `you`."""
        return None

    # -- results ------------------------------------------------------------

    def outcome_for(self, room: MatchRoom, player: MatchPlayer) -> tuple[bool, bool]:
        """`(won, mvp)` for the debrief card."""
        return (False, False)

    def rounds_won(self, room: MatchRoom, player: MatchPlayer) -> int:
        """How many rounds this player's side took. Zero unless there are rounds.

        A hook rather than `room.scores[player.team]` read directly at the call
        site, because `scores` means whatever the mode counts: in deathmatch it
        is kills, so reading it there would pay a per-round XP bonus once per
        frag. A mode with no rounds says so by returning nothing, which is the
        honest answer rather than a number that happens to exist.
        """
        return 0

    # -- bots ---------------------------------------------------------------

    def bot_goal(self, room: MatchRoom, me: MatchPlayer) -> Goal | None:
        """Where this bot should be, if the mode has an opinion."""
        return None
