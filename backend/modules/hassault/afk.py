"""The AFK timer: what counts as somebody playing, and how long they may not.

A player who stops sending meaningful input is warned, and then removed from
the match. Removed means **out of the room**: no mode here has a spectator
seat to move them to, and a body that stands in a doorway taking a slot (and,
in defuse, a teammate's round) is the problem this exists to solve.

### Server-authoritative, and keep-alives do not count

The clock is accrued by the room's own tick (`MatchRoom._step_afk`) and reset
only by `is_activity`, which reads the **commands the simulation consumed**.
Nothing a client sends outside a command — a `ping`, an `rtt`, an empty input
batch, a respawn request — touches it. Inside a command only *intent* counts:
a stream of well-formed frames with every key up and the view still is exactly
what an idle client looks like on the wire, so it is idle. What counts:

- movement (`forward` / `strafe`), `jump`, a crouch *toggle* (not a crouch
  held: a weighted key is not a player);
- aim, once the view has travelled `AIM_TRAVEL` radians since the last reset —
  enough that sensor noise and float drift on a resting mouse are not a person;
- `fire`, `alt_fire`, `reload`, a weapon *switch*, a throw, `use`, `drop`,
  `buy`, `sell`;
- a chat line that was actually posted (`chat.post` calls `note_activity`).

Tactical pings do not count: a ping key on a macro is a keep-alive with a
marker on it.

### When the clock does not run

Bots never. A dead player waiting to respawn does not accrue (there is nothing
to do), and neither does anybody while the mode says the clock is stopped —
defuse's freeze time and the gap between rounds (`GameMode.afk_clock_runs`).
Paused, not reset: dying does not forgive the minute you stood still first.

### Tuning

`WARN_AFTER` and `KICK_AFTER` are the one place the thresholds live, and the
game server can override them without a code change through `HASSAULT_AFK_WARN`
and `HASSAULT_AFK_KICK` (seconds; `HASSAULT_AFK_KICK=0` turns the timer off).
A test or a room that wants different numbers replaces `room.afk`.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .match import Command, MatchPlayer

#: Seconds of counted idle before the warning.
WARN_AFTER = 90.0
#: Seconds of counted idle before removal.
KICK_AFTER = 120.0
#: Radians of accumulated view travel that count as aiming (~1.1 degrees).
AIM_TRAVEL = 0.02

#: Who the warning and the removal notice are from, in the chat box.
SENDER = "SERVER"


@dataclass(slots=True, frozen=True)
class AfkPolicy:
    warn_after: float = WARN_AFTER
    kick_after: float = KICK_AFTER

    @property
    def enabled(self) -> bool:
        return self.kick_after > 0

    @classmethod
    def from_env(cls) -> AfkPolicy:
        """The defaults, overridden by `HASSAULT_AFK_WARN` / `HASSAULT_AFK_KICK`.

        A warning later than the kick is clamped to it, so a bad pair of numbers
        still warns before it removes rather than not warning at all.
        """
        kick = _env_seconds("HASSAULT_AFK_KICK", KICK_AFTER)
        warn = _env_seconds("HASSAULT_AFK_WARN", WARN_AFTER)
        return cls(warn_after=min(warn, kick), kick_after=kick)


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, value) if math.isfinite(value) else default


def _angle_delta(a: float, b: float) -> float:
    """Smallest absolute difference between two angles, so a yaw that wraps
    from just under 2π to just over 0 is a small turn and not a full circle."""
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def is_activity(player: MatchPlayer, command: Command) -> bool:
    """Whether this consumed command is a person playing. Updates the player's
    aim and crouch baselines as a side effect, so call it once per command."""
    crouch_toggled = command.crouch != player.afk_crouch
    player.afk_crouch = command.crouch

    aimed = False
    ref = player.afk_aim_ref
    if ref is not None:
        player.afk_aim_travel += _angle_delta(command.yaw, ref[0]) + abs(
            command.pitch - ref[1]
        )
        if player.afk_aim_travel >= AIM_TRAVEL:
            aimed = True
            player.afk_aim_travel = 0.0
    player.afk_aim_ref = (command.yaw, command.pitch)

    return (
        aimed
        or crouch_toggled
        or command.forward != 0.0
        or command.strafe != 0.0
        or command.jump
        or command.fire
        or command.alt_fire
        or command.reload
        or (command.weapon >= 0 and command.weapon != player.weapon)
        or command.throw
        or command.use
        or command.drop
        or command.buy >= 0
        or command.sell >= 0
    )


def note_activity(player: MatchPlayer) -> None:
    """Reset the clock. A warned player who comes back is simply un-warned —
    the countdown leaves their HUD with the next snapshot."""
    player.afk_idle = 0.0
    player.afk_warned = False


def warning_text(seconds: float) -> str:
    return (
        f"You are AFK. Move, aim or shoot within {max(1, math.ceil(seconds))}s "
        "or you will be removed from the match."
    )


def kicked_text(name: str) -> str:
    return f"{name} was removed from the match for being AFK."
