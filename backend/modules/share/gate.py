"""The one place a guest's rights are decided.

Friendship grants **reachability, not authority** — the rule the fabric already
applies to `agent.ask_peer`. Being trusted enough to reach this node says nothing
about what you may do inside a session here, so every guest action passes through
`require()` before it touches anything.

Two invariants worth defending:

1. **One gate, not two.** `terminal` and `agent` grants do not carry their own
   permission logic — they hand off to `backend/modules/agent/permissions.py`, so
   a guest can never exceed what the host's own agent rules allow. A second,
   laxer implementation of "may this run?" is exactly where the gap appears.
2. **A public viewer holds no grant at all.** Relay viewers are not participants
   and never appear in a session's roster, so there is no rung for them to sit on
   — `require()` cannot be reached on their behalf because they have no node id
   on the fabric. Interactivity requires an authenticated person.
"""

from __future__ import annotations

import logging

from backend.modules.share.models import GRANT_LADDER, GrantLevel, Participant

logger = logging.getLogger(__name__)


def rung(level: GrantLevel) -> int:
    """Position on the ladder. Unknown values sit at the bottom rather than
    raising: a peer on a newer build could name a rung this one has never heard
    of, and the safe reading of "something I do not understand" is `view`."""
    try:
        return GRANT_LADDER.index(level)
    except ValueError:
        logger.warning("unknown grant level %r, treating as 'view'", level)
        return 0


def allows(held: GrantLevel, needed: GrantLevel) -> bool:
    """Whether a participant holding `held` may do something needing `needed`."""
    return rung(held) >= rung(needed)


def require(
    participant: Participant | None, needed: GrantLevel
) -> tuple[bool, str | None]:
    """Gate one action. Returns `(ok, reason)`; `reason` is safe to show a guest.

    A missing participant is not an error worth explaining in detail — somebody
    who is not in the session asking what they may do in it gets the same answer
    as somebody who is in it with too low a rung.
    """
    if participant is None:
        return False, "not a participant in this session"
    if participant.role == "host":
        return True, None
    if not allows(participant.grant, needed):
        return False, f"needs the {needed!r} grant; you have {participant.grant!r}"
    return True, None
