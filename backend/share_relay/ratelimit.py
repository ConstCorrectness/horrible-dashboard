"""A per-address limit on **wrong guesses** at the relay.

A watch code is 40 bits (`tokens.py`), which is only a safe size because guessing
is slow. This is what makes it slow: an address that names too many codes that do
not exist -- or supplies too many wrong passphrases -- is refused for a minute.

Only *misses* count. A viewer reloading a real link, or a busy room of people
opening the same URL behind one NAT, never approaches the limit; somebody walking
the code space hits it on their twentieth try.

Two rules that are quiet if wrong:

- **Only well-formed codes count as guesses.** A browser fetches `/favicon.ico`
  next to every page it opens, and counting that as a miss would lock out a
  viewer who simply reloaded twenty times.
- **`Fly-Client-IP` is trusted only on Fly.** Fly's proxy sets it and overwrites
  any inbound copy; anywhere else it is a header the caller wrote, and trusting
  it would let a guesser pick a fresh "address" per request.

In memory, like the registry: the relay runs on one machine (fly.share.toml), and
a restart that forgets every miss also forgets every code worth guessing.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Mapping

#: Misses per address per window before it is refused. At 20/min, finding one
#: live code among a handful in a 2^40 space takes longer than any link lives.
DEFAULT_MISS_LIMIT = 20
WINDOW_S = 60.0

#: How many addresses are tracked at once. A bound on memory, not a policy:
#: a flood of distinct addresses evicts the stalest rather than growing forever.
MAX_TRACKED = 10_000


def client_ip(headers: Mapping[str, str], peer: str | None) -> str:
    """The address to charge a miss to."""
    if os.environ.get("FLY_APP_NAME"):
        forwarded = (headers.get("fly-client-ip") or "").strip()
        if forwarded:
            return forwarded
    return peer or "unknown"


class MissLimiter:
    """A sliding window of misses per address."""

    def __init__(
        self, limit: int = DEFAULT_MISS_LIMIT, window_s: float = WINDOW_S
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._misses: dict[str, deque[float]] = {}

    def _live(self, key: str, now: float) -> deque[float] | None:
        misses = self._misses.get(key)
        if misses is None:
            return None
        cutoff = now - self.window_s
        while misses and misses[0] <= cutoff:
            misses.popleft()
        if not misses:
            del self._misses[key]
            return None
        return misses

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        misses = self._live(key, now)
        return misses is not None and len(misses) >= self.limit

    def miss(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        misses = self._live(key, now)
        if misses is None:
            if len(self._misses) >= MAX_TRACKED:
                self._evict(now)
            misses = self._misses[key] = deque()
        misses.append(now)

    def _evict(self, now: float) -> None:
        for key in list(self._misses):
            self._live(key, now)
        while len(self._misses) >= MAX_TRACKED:
            # Insertion order: the address that started missing longest ago.
            del self._misses[next(iter(self._misses))]
