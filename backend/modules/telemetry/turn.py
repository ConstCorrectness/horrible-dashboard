"""Which agent turn — and which round of it — the current task is inside.

`IoEvent` records *what* went over the wire; this is what records **why**. The two
together are what let a stepper put a provider request next to the context that
produced it, instead of leaving the reader to match them up by timestamp.

A contextvar rather than a parameter because the recording happens several layers
below the loop that knows the answer: httpx's response hook, the streaming tee, the
inbound middleware. Threading a turn id through all of them would mean teaching
code that has no business knowing about agents what an agent turn is.

`enter`/`leave` bracket a whole turn and `mark_round` re-stamps the round as the
loop advances. Only `enter` yields a token: `mark_round` discards its own, and
`leave` restores whatever was in effect before the turn began — which is what makes
a delegated sub-turn restore its parent's stamp on the way out instead of clearing
it.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

# (turn_id, round). None outside an agent turn, which is most I/O this node does —
# nothing here should imply that every request belongs to some agent.
_current: ContextVar[tuple[str, int] | None] = ContextVar(
    "telemetry_current_turn", default=None
)


def enter(turn_id: str) -> Token[tuple[str, int] | None]:
    """Begin stamping I/O with `turn_id`, starting at round 0."""
    return _current.set((turn_id, 0))


def mark_round(turn_id: str, round_no: int) -> None:
    """Advance the round stamp. Cheap enough to call every round."""
    _current.set((turn_id, round_no))


def leave(token: Token[tuple[str, int] | None]) -> None:
    """Restore the stamp that was in effect before `enter`."""
    _current.reset(token)


def current() -> tuple[str, int] | None:
    return _current.get()
