"""Latency and throughput measurement for the peer fabric.

The fabric carries hassault at 20 Hz, collab ops, peer chat, remote agent turns
and share traffic over **one signed-envelope link per peer**, and until now the
only timing anywhere was `monitor.PeerMonitor`'s single latest RTT sample. A
single sample cannot answer the question that actually matters -- *what did a
20 Hz stream's p99 do while something bulky ran* -- because that distribution is
bimodal, and a mean over a bimodal blocked-pump distribution hides precisely the
bug this module exists to find. So every number here is a percentile.

Three design constraints, each of which is a wrong measurement if dropped:

- **Observation must not be what it observes.** The hub hook is a module-level
  `_probe` checked with one `if _probe is not None`. Not a decorator, not a
  context manager, not `logging` -- each of those is itself measurable at 20 Hz.
  Samples land in preallocated `array('d')` rings; percentile reduction happens
  at snapshot time, never per sample.
- **Cross-node wire time is not measurable without clock sync**, and this module
  does not pretend otherwise. What is left after subtracting the local phases from
  the round trip is reported as `wire_residual_ms`, which is what it honestly is.
- **`local` mode, not loopback, is the zero-wire baseline.** `LoopbackLink.send`
  does `decode(encode(env))` inline on the *sender*, so it charges deserialization
  to the wrong side and adds a queue and a task switch. `local` runs the crypto
  and serialization in a straight loop with no link at all.

`BENCH_ECHO` is declared here rather than in `protocol.py`: a module owns its own
wire vocabulary (the `hassault/fabric.py` convention), so adding a feature never
edits the fabric core.
"""

from __future__ import annotations

import asyncio
import logging
import time
from array import array
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.modules.network import protocol
from backend.modules.network.models import PeerEnvelope

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession

logger = logging.getLogger(__name__)

#: Echo request/reply. The handler replies immediately with the same `data`, so a
#: round trip exercises sign, wire, verify and dispatch with a trivial handler --
#: everything except the cost of doing real work.
BENCH_ECHO = "bench_echo"

#: Samples retained per (phase, msg_type) before the ring wraps. 4096 doubles is
#: 32 KB per series -- small enough to leave on, large enough that a p99 over a
#: sustained run is computed from real tail samples rather than a handful.
RING_CAPACITY = 4096

#: Payload sizes for `sweep`, in bytes. The top of this range is where JSON
#: escaping and base64 cost stops being noise, which is the number the stream
#: tunnel's feasibility rests on.
SWEEP_SIZES = (64, 1024, 16 * 1024, 256 * 1024, 1024 * 1024)


# ---- sample storage ----------------------------------------------------------


class Ring:
    """A fixed-capacity ring of float samples (milliseconds).

    Preallocated so recording a sample allocates nothing: an append that grows a
    list is a heap allocation on the pump's critical path, and at 20 Hz across
    several peers that is a cost large enough to appear in its own measurement.
    """

    __slots__ = ("_buf", "_full", "_i")

    def __init__(self, capacity: int = RING_CAPACITY) -> None:
        self._buf = array("d", bytes(8 * capacity))
        self._i = 0
        self._full = False

    def add(self, value: float) -> None:
        self._buf[self._i] = value
        self._i += 1
        if self._i >= len(self._buf):
            self._i = 0
            self._full = True

    def values(self) -> list[float]:
        if self._full:
            return list(self._buf[self._i :]) + list(self._buf[: self._i])
        return list(self._buf[: self._i])

    def __len__(self) -> int:
        return len(self._buf) if self._full else self._i


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted list. `q` in [0, 1].

    Interpolated rather than nearest-rank so a p99 over a few hundred samples
    moves smoothly instead of snapping between two observations.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


@dataclass(frozen=True)
class PhaseStats:
    """Percentiles for one timed phase. Deliberately no mean -- see the module
    docstring: a mean over a bimodal blocked-pump distribution hides the bug."""

    phase: str
    msg_type: str
    count: int
    p50_ms: float
    p90_ms: float
    p99_ms: float
    max_ms: float

    @classmethod
    def of(cls, phase: str, msg_type: str, values: list[float]) -> PhaseStats:
        ordered = sorted(values)
        return cls(
            phase=phase,
            msg_type=msg_type,
            count=len(ordered),
            p50_ms=round(percentile(ordered, 0.50), 4),
            p90_ms=round(percentile(ordered, 0.90), 4),
            p99_ms=round(percentile(ordered, 0.99), 4),
            max_ms=round(ordered[-1] if ordered else 0.0, 4),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "msgType": self.msg_type,
            "count": self.count,
            "p50Ms": self.p50_ms,
            "p90Ms": self.p90_ms,
            "p99Ms": self.p99_ms,
            "maxMs": self.max_ms,
        }


class BenchProbe:
    """Collects phase timings from the hub. Installed only while a run is in
    flight; `hub.set_probe(None)` afterwards restores the null check to its
    permanent state, which is the state production runs in."""

    def __init__(self, capacity: int = RING_CAPACITY) -> None:
        self._rings: dict[tuple[str, str], Ring] = {}
        self._capacity = capacity

    def record_ns(self, phase: str, msg_type: str, elapsed_ns: int) -> None:
        key = (phase, msg_type)
        ring = self._rings.get(key)
        if ring is None:
            ring = Ring(self._capacity)
            self._rings[key] = ring
        ring.add(elapsed_ns / 1_000_000)

    def stats(self) -> list[PhaseStats]:
        return [
            PhaseStats.of(phase, msg_type, ring.values())
            for (phase, msg_type), ring in sorted(self._rings.items())
            if len(ring)
        ]

    def clear(self) -> None:
        self._rings.clear()


# ---- results -----------------------------------------------------------------


@dataclass
class BenchResult:
    """One bench run.

    `transport` is the bench's own label, never `PeerInfo.transport`:
    `LoopbackLink.transport_name` is `"direct"`, so reading the peer's field would
    file every loopback run under the wrong transport.
    """

    mode: str
    transport: str
    node_id: str | None = None
    payload_bytes: int = 0
    iterations: int = 0
    duration_s: float = 0.0
    errors: int = 0
    rtt: PhaseStats | None = None
    phases: list[PhaseStats] = field(default_factory=list)
    #: Round trip minus every local phase we can account for. Named for what it
    #: is: the unexplained remainder, not a measured wire time.
    wire_residual_ms: float | None = None
    #: `sustained` only: latency of the low-rate stream competing with bulk.
    victim: PhaseStats | None = None
    bytes_sent: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "transport": self.transport,
            "nodeId": self.node_id,
            "payloadBytes": self.payload_bytes,
            "iterations": self.iterations,
            "durationS": round(self.duration_s, 4),
            "errors": self.errors,
            "rtt": self.rtt.to_dict() if self.rtt else None,
            "phases": [p.to_dict() for p in self.phases],
            "wireResidualMs": self.wire_residual_ms,
            "victim": self.victim.to_dict() if self.victim else None,
            "bytesSent": self.bytes_sent,
            "note": self.note,
        }


# ---- the echo handler --------------------------------------------------------


async def handle_echo(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """Reply with the same payload.

    Deliberately trivial: an echo that did real work would measure the work rather
    than the fabric, which is the whole point of having it.
    """
    await hub.send_to(session.info.node_id, BENCH_ECHO, env.data, re=env.msg_id)


def register(hub: PeerHub) -> None:
    hub.register_handler(BENCH_ECHO, handle_echo)


def _payload(size_bytes: int) -> dict[str, Any]:
    """A payload whose serialized size is approximately `size_bytes`.

    ASCII on purpose: padding with non-ASCII would measure JSON escaping rather
    than size, and escaping deserves its own experiment rather than quietly
    contaminating this one.
    """
    return {"pad": "a" * max(0, size_bytes), "sent_ns": time.perf_counter_ns()}


# ---- modes -------------------------------------------------------------------


def run_local(iterations: int = 2000, payload_bytes: int = 64) -> BenchResult:
    """CPU-only baseline: construct, sign, serialize, deserialize, verify, with no
    link at all. This is the floor every other mode is measured against."""
    from backend.modules.network import identity

    me = identity.load_identity()
    probe = BenchProbe()
    data = _payload(payload_bytes)

    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        env = PeerEnvelope(type=BENCH_ECHO, src=me.node_id, data=data)
        t1 = time.perf_counter_ns()
        protocol.sign_envelope(env, me)
        t2 = time.perf_counter_ns()
        raw = protocol.encode(env)
        t3 = time.perf_counter_ns()
        decoded = protocol.decode(raw)
        t4 = time.perf_counter_ns()
        protocol.verify_envelope(decoded, me.public_key)
        t5 = time.perf_counter_ns()

        probe.record_ns("construct", BENCH_ECHO, t1 - t0)
        probe.record_ns("sign", BENCH_ECHO, t2 - t1)
        probe.record_ns("serialize", BENCH_ECHO, t3 - t2)
        probe.record_ns("deserialize", BENCH_ECHO, t4 - t3)
        probe.record_ns("verify", BENCH_ECHO, t5 - t4)

    sample = protocol.sign_envelope(
        PeerEnvelope(type=BENCH_ECHO, src=me.node_id, data=data), me
    )
    return BenchResult(
        mode="local",
        transport="none",
        payload_bytes=len(protocol.encode(sample).encode("utf-8")),
        iterations=iterations,
        phases=probe.stats(),
    )


async def run_echo(
    hub: PeerHub,
    node_id: str,
    *,
    count: int = 100,
    payload_bytes: int = 64,
    transport: str = "unknown",
    timeout: float = 10.0,
) -> BenchResult:
    """Sequential round trips against a peer's echo handler.

    Sequential on purpose: concurrent requests would measure the link's
    parallelism, and the first thing worth knowing is the latency of one message
    with nothing in its way.
    """
    from backend.modules.network import hub as hub_mod

    probe = BenchProbe()
    hub_mod.set_probe(probe)
    rtts: list[float] = []
    errors = 0
    data = _payload(payload_bytes)
    wire_bytes = 0

    started = time.perf_counter()
    try:
        for _ in range(count):
            t0 = time.perf_counter_ns()
            try:
                env = await hub.request(node_id, BENCH_ECHO, data, timeout=timeout)
            except Exception:  # noqa: BLE001 - a failed round trip is a datapoint
                errors += 1
                continue
            rtts.append((time.perf_counter_ns() - t0) / 1_000_000)
            if not wire_bytes:
                wire_bytes = len(protocol.encode(env).encode("utf-8"))
    finally:
        hub_mod.set_probe(None)

    phases = probe.stats()
    rtt = PhaseStats.of("rtt", BENCH_ECHO, rtts) if rtts else None
    return BenchResult(
        mode="echo",
        transport=transport,
        node_id=node_id,
        payload_bytes=wire_bytes,
        iterations=count,
        duration_s=time.perf_counter() - started,
        errors=errors,
        rtt=rtt,
        phases=phases,
        wire_residual_ms=_residual(rtt, phases),
        bytes_sent=wire_bytes * len(rtts),
    )


def _residual(rtt: PhaseStats | None, phases: list[PhaseStats]) -> float | None:
    """Round-trip p50 minus the local phases this node can account for.

    Only our own phases are subtracted -- the peer's sign, serialize and verify are
    still inside the remainder. That is exactly why this is a *residual* and not a
    wire time, and why the field is named for the weaker claim.
    """
    if rtt is None:
        return None
    local = sum(p.p50_ms for p in phases if p.phase in {"verify", "handler"})
    return round(max(0.0, rtt.p50_ms - local), 4)


async def run_sweep(
    hub: PeerHub,
    node_id: str,
    *,
    count: int = 40,
    sizes: tuple[int, ...] = SWEEP_SIZES,
    transport: str = "unknown",
) -> list[BenchResult]:
    """Echo at a range of payload sizes.

    The shape of this curve -- and especially whether it stays linear at 256 KB and
    1 MB -- is what says whether pushing bulk bytes through signed JSON envelopes
    is viable at all, which every later phase rests on.
    """
    results = []
    for size in sizes:
        results.append(
            await run_echo(
                hub,
                node_id,
                count=count,
                payload_bytes=size,
                transport=transport,
                timeout=30.0,
            )
        )
    return results


async def run_sustained(
    hub: PeerHub,
    node_id: str,
    *,
    duration_s: float = 5.0,
    bulk_bytes: int = 64 * 1024,
    victim_interval_s: float = 0.05,
    transport: str = "unknown",
) -> BenchResult:
    """Saturate the link with bulk echoes while a 20 Hz "victim" stream measures
    what that did to an interactive latency.

    This is the head-of-line-blocking test and the most important mode in the
    file. The interesting output is not throughput -- it is what the victim's p99
    did while bulk ran. A fabric that moves 40 MB/s while making hassault
    unplayable has not passed anything.
    """
    from backend.modules.network import hub as hub_mod

    probe = BenchProbe()
    hub_mod.set_probe(probe)
    victim_rtts: list[float] = []
    bulk_data = _payload(bulk_bytes)
    small = _payload(64)
    errors = 0
    sent = 0
    stop = asyncio.Event()

    async def bulk_loop() -> None:
        nonlocal errors, sent
        while not stop.is_set():
            try:
                await hub.request(node_id, BENCH_ECHO, bulk_data, timeout=30.0)
            except Exception:  # noqa: BLE001 - a failed bulk send is a datapoint
                errors += 1
            else:
                sent += bulk_bytes

    async def victim_loop() -> None:
        nonlocal errors
        while not stop.is_set():
            t0 = time.perf_counter_ns()
            try:
                await hub.request(node_id, BENCH_ECHO, small, timeout=30.0)
            except Exception:  # noqa: BLE001
                errors += 1
            else:
                victim_rtts.append((time.perf_counter_ns() - t0) / 1_000_000)
            await asyncio.sleep(victim_interval_s)

    started = time.perf_counter()
    tasks = [asyncio.create_task(bulk_loop()), asyncio.create_task(victim_loop())]
    try:
        await asyncio.sleep(duration_s)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hub_mod.set_probe(None)

    elapsed = time.perf_counter() - started
    throughput = round(sent / elapsed / 1_048_576, 2) if elapsed > 0 else 0.0
    return BenchResult(
        mode="sustained",
        transport=transport,
        node_id=node_id,
        payload_bytes=bulk_bytes,
        duration_s=elapsed,
        errors=errors,
        phases=probe.stats(),
        victim=(
            PhaseStats.of("victim_rtt", BENCH_ECHO, victim_rtts)
            if victim_rtts
            else None
        ),
        bytes_sent=sent,
        note=f"{throughput} MiB/s bulk",
    )
