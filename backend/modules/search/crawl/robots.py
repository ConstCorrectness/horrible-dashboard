"""Politeness: robots.txt, per-host rate limiting, and the crawler's user agent.

A crawler that ignores these is a crawler that gets the user's IP banned from the
sites they most wanted indexed, so this is a correctness concern rather than an
etiquette one.

`urllib.robotparser` is stdlib, so honouring robots.txt costs no dependency. Its
`Crawl-delay` is treated as a floor the configured delay can only be raised to,
never lowered from — a site asking for 10s gets 10s.

The limiter serializes **per host**: one in-flight request to any given origin, with
a minimum interval between them. Global concurrency is separate and larger, so a crawl
spanning several hosts still makes progress while any one of them is being polite.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

# Identifies the crawler honestly, and points at the project so an operator who sees
# it in their logs can find out what it is. A crawler that lies about who it is has
# no business honouring robots.txt in the first place.
USER_AGENT = (
    "horrible-dashboard-crawler/1.0 "
    "(+https://github.com/horriblecpp/horrible-dashboard; personal index)"
)
# The token robots.txt rules are matched against.
ROBOTS_AGENT = "horrible-dashboard-crawler"

_ROBOTS_TIMEOUT_S = 10.0
_ROBOTS_MAX_BYTES = 512_000


class HostLimiter:
    """One in-flight request per host, with a minimum gap between them.

    Scoped to a single crawl run rather than process-global: two concurrent crawls of
    different seeds are rare, and sharing state between them would mean a lock whose
    contention is harder to reason about than the occasional doubled request rate.
    """

    def __init__(self, min_interval_s: float = 1.0) -> None:
        self._min_interval = max(0.0, min_interval_s)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}

    def note_crawl_delay(self, host: str, delay: float | None) -> None:
        """Record a site's own `Crawl-delay`. Only ever raises the interval."""
        if delay and delay > 0:
            self._host_delay[host] = max(self._host_delay.get(host, 0.0), float(delay))

    def _interval(self, host: str) -> float:
        return max(self._min_interval, self._host_delay.get(host, 0.0))

    async def acquire(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        await lock.acquire()
        wait = self._interval(host) - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)

    def release(self, host: str) -> None:
        self._last[host] = time.monotonic()
        lock = self._locks.get(host)
        if lock is not None and lock.locked():
            lock.release()


class RobotsCache:
    """robots.txt per host, fetched once per crawl.

    **Unreachable robots.txt is treated as "allowed".** That is the documented
    convention (a 404 means no rules), and the alternative — refusing to crawl a site
    whose robots.txt 500s — would make the crawler fail closed on transient errors.
    A 401/403 on robots.txt itself is the one case that means "stay out", and is
    honoured as such.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def _load(self, scheme: str, host: str) -> RobotFileParser | None:
        import httpx

        url = f"{scheme}://{host}/robots.txt"
        try:
            async with httpx.AsyncClient(
                timeout=_ROBOTS_TIMEOUT_S, follow_redirects=True
            ) as client:
                res = await client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            logger.debug("robots.txt unreachable for %s (%s) — allowing", host, exc)
            return None

        if res.status_code in (401, 403):
            parser = RobotFileParser()
            parser.disallow_all = True
            return parser
        if res.status_code >= 400:
            return None

        parser = RobotFileParser()
        parser.parse(res.text[:_ROBOTS_MAX_BYTES].splitlines())
        return parser

    async def get(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        host = parts.netloc
        if not host:
            return None
        if host not in self._parsers:
            self._parsers[host] = await self._load(parts.scheme or "https", host)
        return self._parsers[host]

    async def allowed(self, url: str) -> bool:
        parser = await self.get(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(ROBOTS_AGENT, url)
        except Exception:  # noqa: BLE001 — a malformed robots.txt shouldn't stop a crawl
            return True

    async def crawl_delay(self, url: str) -> float | None:
        parser = await self.get(url)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(ROBOTS_AGENT)
        except Exception:  # noqa: BLE001
            return None
        return float(delay) if delay else None
