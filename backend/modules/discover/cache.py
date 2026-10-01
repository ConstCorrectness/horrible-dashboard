"""TTL cache with in-flight coalescing and a last-good fallback.

Three behaviours, each answering a real failure of browsing a rate-limited API:

- **TTL.** Switching between two sections and back, or re-opening a repo, is the
  same request; a fresh-enough answer is served without leaving the node.
- **Coalescing.** Two identical requests in flight share one upstream call. A pane
  mounting while the agent asks the same thing, or a double-fired effect, would
  otherwise spend two of GitHub's ten anonymous searches a minute on one answer.
- **Last good.** When upstream fails (rate limit, outage), the last successful
  answer for that key is still worth showing — marked stale by the caller, with the
  failure beside it. Kept separately from the TTL entries and never expires on its
  own; only the LRU bound evicts it.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

MAX_ENTRIES = 300


class Cache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._fresh: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._last_good: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._max = max_entries

    def clear(self) -> None:
        self._fresh.clear()
        self._last_good.clear()
        self._inflight.clear()

    def _put(
        self, table: OrderedDict[str, tuple[float, Any]], key: str, value: Any
    ) -> None:
        table[key] = (time.time(), value)
        table.move_to_end(key)
        while len(table) > self._max:
            table.popitem(last=False)

    def get_fresh(self, key: str, ttl: float) -> tuple[float, Any] | None:
        hit = self._fresh.get(key)
        if hit is None:
            return None
        if time.time() - hit[0] > ttl:
            self._fresh.pop(key, None)
            return None
        self._fresh.move_to_end(key)
        return hit

    def last_good(self, key: str) -> tuple[float, Any] | None:
        return self._last_good.get(key)

    async def fetch(
        self,
        key: str,
        ttl: float,
        loader: Callable[[], Awaitable[Any]],
        *,
        fresh: bool = False,
        cacheable: Callable[[Any], bool] = lambda _v: True,
    ) -> tuple[float, Any]:
        """`(stored_at, value)` — from the cache when fresh, else one shared load.

        `cacheable` decides whether a loaded value is a success worth keeping; a
        failure is returned to every waiter but never stored, so a transient error
        can't outlive itself.
        """
        if not fresh and (hit := self.get_fresh(key, ttl)) is not None:
            return hit
        if (pending := self._inflight.get(key)) is not None:
            return await asyncio.shield(pending)

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await loader()
        except BaseException as exc:
            future.set_exception(exc)
            # Retrieve it so a future nobody else awaited doesn't log "never retrieved".
            future.exception()
            raise
        else:
            now = time.time()
            if cacheable(value):
                self._put(self._fresh, key, value)
                self._put(self._last_good, key, value)
            future.set_result((now, value))
            return now, value
        finally:
            self._inflight.pop(key, None)
