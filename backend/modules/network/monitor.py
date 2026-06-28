"""Peer Monitor: live link health for the distributed peer fabric.

A periodic heartbeat pings every connected peer to measure round-trip time and
samples the per-session byte/message counters the hub maintains. Each tick is
broadcast to the browser as a `peer_metrics` event on the `/ws` `network` channel,
which the Peer Monitor panel renders. Read-only — it never mutates peer state
beyond stamping each session's `rtt_ms`.

See docs/modules/network.mdx (Peer Monitor).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from backend.modules.network import protocol
from backend.modules.network.models import PeerMetrics

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub

logger = logging.getLogger(__name__)

PING_TIMEOUT_S = 5.0


class PeerMonitor:
    """Samples peer link metrics on an interval and streams them to the browser."""

    def __init__(self, hub: PeerHub, interval: float = 5.0) -> None:
        self._hub = hub
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("peer monitor loop crashed")

    async def _tick(self) -> None:
        await self._measure_rtts()
        self._hub.emit(
            "peer_metrics",
            {"metrics": [m.model_dump() for m in self.snapshot()]},
        )

    async def _measure_rtts(self) -> None:
        """Ping each peer concurrently, stamping the session RTT (or None on miss)."""
        sessions = list(self._hub.peers.items())

        async def ping(node_id: str) -> None:
            session = self._hub.peers.get(node_id)
            if session is None:
                return
            t0 = time.perf_counter()
            try:
                await self._hub.request(
                    node_id, protocol.PING, {}, timeout=PING_TIMEOUT_S
                )
                session.rtt_ms = round((time.perf_counter() - t0) * 1000, 1)
            except Exception:
                session.rtt_ms = None

        if sessions:
            await asyncio.gather(*(ping(nid) for nid, _ in sessions))

    def snapshot(self) -> list[PeerMetrics]:
        """Current metrics for every connected peer."""
        out: list[PeerMetrics] = []
        for session in self._hub.peers.values():
            info = session.info
            out.append(
                PeerMetrics(
                    node_id=info.node_id,
                    node_name=info.node_name,
                    transport=info.transport,
                    status=info.status,
                    rtt_ms=session.rtt_ms,
                    bytes_in=session.bytes_in,
                    bytes_out=session.bytes_out,
                    msgs_in=session.msgs_in,
                    msgs_out=session.msgs_out,
                    last_seen=info.last_seen,
                )
            )
        return out


# Process-global monitor bound to the singleton hub (started by the app lifespan).
from backend.modules.network.hub import peer_hub  # noqa: E402

peer_monitor = PeerMonitor(peer_hub)
