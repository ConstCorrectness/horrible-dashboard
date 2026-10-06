"""What the hub needs from whatever runs the per-user instances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.hub.store import User


@dataclass(frozen=True)
class Instance:
    """Where one user's backend answers, and the token it demands."""

    base_url: str
    token: str


class InstanceSpawner(Protocol):
    async def ensure(self, user: User) -> Instance:
        """Return the user's instance, creating or starting it if needed. Returns as
        soon as it has been asked to run — readiness is the hub's to probe."""
        ...

    async def running(self, user: User) -> Instance | None:
        """The user's instance if it is running right now, else None. Must never
        start anything: the idle reaper and parked tabs ask this."""
        ...

    async def stop(self, user_id: str) -> None:
        """Stop (not delete) the user's instance; its volume survives."""
        ...

    async def close(self) -> None: ...
