"""What each guest actually did, kept where the host can see it.

A grant ladder nobody can audit is a grant ladder nobody should trust. The pane
lets a host hand a friend `terminal`, and the only honest way to offer that is to
show, afterwards and in order, exactly what was run.

Three decisions worth stating:

- **Denials are recorded too, and are the more interesting half.** A guest whose
  action was refused leaves no other trace anywhere; an audit log that only shows
  successes would be silent in precisely the case a host wants to know about.
- **Bounded and in-memory.** A session is a live thing, not a compliance record,
  and a log that grew without limit would be a memory leak on a long call. It
  holds the last `LIMIT` entries and dies with the process. Say that plainly in
  the docs rather than implying this is durable.
- **Keyed by node, labelled by person.** The node id is what the fabric
  authenticated; the display name is a label a peer supplied and is never
  identity. Same rule the roster and hassault's invites follow.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

#: How many entries one session keeps. Enough to scroll back through a working
#: session; small enough that it can never be the thing that fills memory.
LIMIT = 250

Outcome = Literal["allowed", "denied", "asked", "failed"]


@dataclass
class Entry:
    """One thing a guest tried."""

    ts: float
    #: The fabric-authenticated node. The identity.
    node_id: str
    #: A label the peer supplied. Never an identity.
    name: str
    action: str
    #: The rung the action required, for a host reading back why it was refused.
    needs: str
    outcome: Outcome
    #: Why, when it was not allowed. Safe to show the guest as well as the host.
    reason: str = ""
    #: Small, action-specific context — a command, a pane id. Never a payload.
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLog:
    """The last `LIMIT` actions in this session, oldest first."""

    def __init__(self, limit: int = LIMIT) -> None:
        self._entries: deque[Entry] = deque(maxlen=limit)

    def record(
        self,
        *,
        node_id: str,
        name: str,
        action: str,
        needs: str,
        outcome: Outcome,
        reason: str = "",
        detail: dict[str, Any] | None = None,
    ) -> Entry:
        entry = Entry(
            ts=time.time(),
            node_id=node_id,
            name=name,
            action=action,
            needs=needs,
            outcome=outcome,
            reason=reason,
            detail=detail or {},
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[Entry]:
        return list(self._entries)

    def clear(self) -> None:
        """Drop everything. Called when a session ends -- the log belongs to the
        session, and carrying one session's actions into the next would attribute
        them to whoever is in the room now."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
