"""Development spawner: every user is routed to one already-running backend.

**No isolation** — all users share one process, its settings and its shell. It
exists so the hub, its sign-in and the frontend can be exercised without Docker
(`HUB_SPAWNER=static`, pointing at a `pnpm dev` backend), and the hub logs a warning
at startup when it is selected.
"""

from __future__ import annotations

from backend.hub.spawner.base import Instance
from backend.hub.store import User


class StaticSpawner:
    def __init__(self, upstream: str, token: str = "") -> None:
        self._instance = Instance(base_url=upstream.rstrip("/"), token=token)

    async def ensure(self, user: User) -> Instance:
        return self._instance

    async def stop(self, user_id: str) -> None:
        return None

    async def close(self) -> None:
        return None
