"""A byte stream over the peer fabric: a peer's loopback service, as a local port.

The problem this solves: `llama-server` binds `127.0.0.1`, and so does
`rpc-server`. A friend cannot reach either, and exposing them would mean putting
an unauthenticated inference endpoint on a network -- which upstream explicitly
warns against for `rpc-server`. Tunnelling them over the fabric solves both at
once, because the fabric link is already authenticated, signed and trusted.

**A raw byte tunnel, not an HTTP proxy.** For `rpc-server` there is no choice; it
is raw TCP. For the chat endpoint an HTTP proxy is tempting -- per-request
accounting, clean cancellation -- but it would mean reimplementing chunked
transfer and, worse, preserving SSE chunk boundaries, when `_openai_chat_stream`
parses `data:` lines straight off httpx's byte stream. A raw tunnel gets SSE for
free, because SSE *is* bytes on a socket: httpx against
`http://127.0.0.1:<tunnel_port>` is byte-identical to httpx against a local
server. Accounting comes back cheaply by counting connections and bytes at both
ends.

Three limits, and the third exists because of a measurement:

- **Frames are capped at 64 KB.** A 1 MB frame occupies the receiving pump for
  19 ms -- most of a 50 ms budget at 20 Hz -- and it cannot be interrupted.
- **Credit-based flow control**, so a fast lender cannot outrun a slow borrower
  and fill its memory. The producer is a remote peer, so an unbounded buffer here
  is a remote memory-exhaustion lever.
- **A rate limit below saturation.** This is the one that is easy to leave out.
  Saturating the fabric with 64 KB frames moves ~57 MiB/s but degrades a
  concurrent 20 Hz stream's latency **6×** (0.29 ms → 1.80 ms), because the cost
  is signing and verifying the bytes, not scheduling -- so smaller frames do not
  fix it and neither does a dispatch mode. If a tunnel may share a link with a
  game, it has to stay *below* saturation rather than merely be polite about
  frame size.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

# Wire vocabulary. Declared here, not in `protocol.py` -- a module owns its own
# types, so adding a feature never edits the fabric core.
STREAM_OPEN = "stream_open"
STREAM_DATA = "stream_data"
STREAM_CREDIT = "stream_credit"
STREAM_CLOSE = "stream_close"

#: Max payload bytes per frame, before base64. See the module docstring: a 1 MB
#: frame is 19 ms of uninterruptible pump occupancy on the far side.
FRAME_BYTES = 64 * 1024

#: How many bytes a sender may have outstanding before it must wait for credit.
INITIAL_CREDIT = 256 * 1024

#: Default ceiling on one tunnel's throughput. Deliberately well below the
#: ~57 MiB/s the fabric can actually push, because that rate costs a concurrent
#: interactive stream 6× its latency. Ample for chat SSE (tokens are kilobytes)
#: and for embedding batches, which are latency-tolerant by nature.
DEFAULT_RATE_BYTES_S = 16 * 1024 * 1024

#: How long a borrower waits for the lender to connect to its own service.
OPEN_TIMEOUT_S = 20.0


class TunnelClosed(Exception):
    """The stream ended: the lease was revoked, the peer went away, or either
    side closed its socket."""


class RateLimiter:
    """A token bucket over bytes. Shared by every stream in one tunnel, so two
    concurrent connections cannot together exceed the ceiling."""

    def __init__(self, rate_bytes_s: float) -> None:
        self.rate = max(1.0, rate_bytes_s)
        # One second of burst: enough that a bursty sender is not shaped into
        # jitter, small enough that it cannot saturate for long.
        self._capacity = self.rate
        self._tokens = self.rate
        self._last = time.monotonic()

    async def take(self, count: int) -> None:
        # A single frame larger than the bucket would deadlock waiting for tokens
        # it can never accumulate, so it is clamped instead.
        want = min(float(count), self._capacity)
        while True:
            now = time.monotonic()
            self._tokens = min(
                self._capacity, self._tokens + (now - self._last) * self.rate
            )
            self._last = now
            if self._tokens >= want:
                self._tokens -= want
                return
            await asyncio.sleep((want - self._tokens) / self.rate)


class Stream:
    """One tunnelled TCP connection, from either end's point of view."""

    def __init__(
        self,
        stream_id: str,
        node_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        limiter: RateLimiter,
    ) -> None:
        self.stream_id = stream_id
        self.node_id = node_id
        self.reader = reader
        self.writer = writer
        self.limiter = limiter
        self.credit = INITIAL_CREDIT
        self.credit_available = asyncio.Event()
        self.credit_available.set()
        self.closed = asyncio.Event()
        self.bytes_up = 0
        self.bytes_down = 0

    def add_credit(self, count: int) -> None:
        self.credit += count
        if self.credit > 0:
            self.credit_available.set()

    async def spend_credit(self, count: int) -> None:
        while self.credit <= 0:
            self.credit_available.clear()
            if self.closed.is_set():
                raise TunnelClosed
            await self.credit_available.wait()
        self.credit -= count

    def close(self) -> None:
        self.closed.set()
        # Wake anyone parked on credit so they see `closed` and unwind, rather
        # than waiting for a grant that will never come.
        self.credit_available.set()
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001 - already-closed sockets are fine
            pass


class LocalTunnel:
    """The borrower's end: a `127.0.0.1` port that behaves like the peer's service.

    Nothing above this needs to know a fabric exists. `httpx` against
    `http://127.0.0.1:{port}` is byte-identical to `httpx` against a local server,
    which is exactly what lets a borrowed model reuse the `openai` dialect with no
    new provider code.
    """

    def __init__(
        self,
        manager: TunnelManager,
        node_id: str,
        service: str,
        lease_id: str,
        server: asyncio.Server,
        limiter: RateLimiter,
    ) -> None:
        self._manager = manager
        self.node_id = node_id
        self.service = service
        self.lease_id = lease_id
        self._server = server
        self.limiter = limiter
        self.connections = 0

    @property
    def port(self) -> int:
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def close(self) -> None:
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        self._manager.drop_tunnel(self)


class TunnelManager:
    """Owns every stream on this node, in both directions."""

    def __init__(self) -> None:
        self._streams: dict[str, Stream] = {}
        self._tunnels: list[LocalTunnel] = []
        self._services: dict[str, Callable[[], tuple[str, int] | None]] = {}
        self._authorize: Callable[[str, str, str], tuple[bool, str]] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- registration ----------------------------------------------------------

    def register_service(
        self, name: str, resolver: Callable[[], tuple[str, int] | None]
    ) -> None:
        """Make a local `(host, port)` reachable by lease holders under `name`.

        The resolver is called per connection rather than stored, because a
        service's port is not stable: `llama-server` takes an ephemeral one when
        8080 is occupied, which is exactly the case a cached port gets wrong.
        """
        self._services[name] = resolver

    def set_authorizer(self, fn: Callable[[str, str, str], tuple[bool, str]]) -> None:
        """Install the lease check: `(node_id, service, lease_id) -> (ok, reason)`.

        Kept as an injected callable so this module has no opinion about leases --
        it is a byte pipe, and the policy lives in `lease.py`.
        """
        self._authorize = fn

    def drop_tunnel(self, tunnel: LocalTunnel) -> None:
        if tunnel in self._tunnels:
            self._tunnels.remove(tunnel)

    def _track(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ---- borrower side ---------------------------------------------------------

    async def open_tunnel(
        self,
        hub: PeerHub,
        node_id: str,
        service: str,
        lease_id: str,
        *,
        rate_bytes_s: float = DEFAULT_RATE_BYTES_S,
    ) -> LocalTunnel:
        """Start a local listener whose connections are proxied to `node_id`."""
        limiter = RateLimiter(rate_bytes_s)
        tunnel_ref: dict[str, LocalTunnel] = {}

        async def on_client(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            tunnel = tunnel_ref.get("t")
            if tunnel is None:
                writer.close()
                return
            tunnel.connections += 1
            await self._start_borrower_stream(hub, tunnel, reader, writer)

        server = await asyncio.start_server(on_client, "127.0.0.1", 0)
        tunnel = LocalTunnel(self, node_id, service, lease_id, server, limiter)
        tunnel_ref["t"] = tunnel
        self._tunnels.append(tunnel)
        return tunnel

    async def _start_borrower_stream(
        self,
        hub: PeerHub,
        tunnel: LocalTunnel,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        stream_id = uuid.uuid4().hex
        try:
            reply = await hub.request(
                tunnel.node_id,
                STREAM_OPEN,
                {
                    "stream_id": stream_id,
                    "service": tunnel.service,
                    "lease_id": tunnel.lease_id,
                },
                timeout=OPEN_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("tunnel open to %s failed: %s", tunnel.node_id, exc)
            writer.close()
            return

        if not (reply.data or {}).get("ok"):
            reason = str((reply.data or {}).get("error") or "refused")
            logger.info("tunnel open refused by %s: %s", tunnel.node_id, reason)
            writer.close()
            return

        stream = Stream(stream_id, tunnel.node_id, reader, writer, tunnel.limiter)
        self._streams[stream_id] = stream
        await self._pump(hub, stream)

    # ---- lender side -----------------------------------------------------------

    async def handle_open(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        """A lease holder wants a connection to one of our local services."""
        data = env.data or {}
        stream_id = str(data.get("stream_id") or "")
        service = str(data.get("service") or "")
        lease_id = str(data.get("lease_id") or "")
        node_id = session.info.node_id

        async def refuse(reason: str) -> None:
            await hub.send_to(
                node_id, STREAM_OPEN, {"ok": False, "error": reason}, re=env.msg_id
            )

        # Trust first, unconditionally, like every other actuating handler on the
        # fabric: knowing a lease id is not the same as being a friend.
        if not session.info.trusted:
            await refuse("not a trusted peer")
            return
        if not stream_id or not service:
            await refuse("malformed open")
            return
        if self._authorize is None:
            await refuse("compute lending is not enabled here")
            return
        ok, reason = self._authorize(node_id, service, lease_id)
        if not ok:
            await refuse(reason)
            return

        resolver = self._services.get(service)
        target = resolver() if resolver is not None else None
        if target is None:
            await refuse(f"service {service!r} is not available here")
            return

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(*target), timeout=OPEN_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001
            await refuse(f"could not reach the local service: {exc}")
            return

        stream = Stream(
            stream_id, node_id, reader, writer, RateLimiter(DEFAULT_RATE_BYTES_S)
        )
        self._streams[stream_id] = stream
        await hub.send_to(node_id, STREAM_OPEN, {"ok": True}, re=env.msg_id)
        self._track(self._pump(hub, stream))

    # ---- shared pumping --------------------------------------------------------

    async def _pump(self, hub: PeerHub, stream: Stream) -> None:
        """Read the local socket, frame it, and send it to the peer.

        Symmetric: once a stream exists neither end cares which one opened it.
        """
        try:
            while not stream.closed.is_set():
                chunk = await stream.reader.read(FRAME_BYTES)
                if not chunk:
                    break
                await stream.limiter.take(len(chunk))
                await stream.spend_credit(len(chunk))
                stream.bytes_up += len(chunk)
                await hub.send_to(
                    stream.node_id,
                    STREAM_DATA,
                    {
                        "stream_id": stream.stream_id,
                        "b64": base64.b64encode(chunk).decode("ascii"),
                    },
                )
        except (TunnelClosed, ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("tunnel pump failed for %s", stream.stream_id)
        finally:
            await self._teardown(hub, stream, "eof")

    async def handle_data(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        """Write a frame to the local socket and return the credit it consumed.

        Registered `serial`: reordering these corrupts the stream, which is the
        whole reason `serial` exists rather than just using `detach`.
        """
        data = env.data or {}
        stream = self._streams.get(str(data.get("stream_id") or ""))
        if stream is None or stream.closed.is_set():
            return
        try:
            payload = base64.b64decode(str(data.get("b64") or ""), validate=True)
        except Exception:  # noqa: BLE001
            logger.warning("tunnel: undecodable frame from %s", session.info.node_id)
            await self._teardown(hub, stream, "bad frame")
            return

        try:
            stream.writer.write(payload)
            await stream.writer.drain()
        except Exception:  # noqa: BLE001 - the local service went away
            await self._teardown(hub, stream, "local socket closed")
            return

        stream.bytes_down += len(payload)
        # Credit is returned only after the bytes are *drained*, not on receipt:
        # returning it earlier would let the sender outrun a slow local service
        # and rebuild exactly the unbounded buffer this prevents.
        try:
            await hub.send_to(
                stream.node_id,
                STREAM_CREDIT,
                {"stream_id": stream.stream_id, "bytes": len(payload)},
            )
        except Exception:  # noqa: BLE001
            await self._teardown(hub, stream, "peer gone")

    async def handle_credit(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        data = env.data or {}
        stream = self._streams.get(str(data.get("stream_id") or ""))
        if stream is None:
            return
        try:
            granted = int(data.get("bytes") or 0)
        except (TypeError, ValueError):
            return
        if granted > 0:
            stream.add_credit(granted)

    async def handle_close(
        self, hub: PeerHub, session: PeerSession, env: PeerEnvelope
    ) -> None:
        data = env.data or {}
        stream = self._streams.get(str(data.get("stream_id") or ""))
        if stream is not None:
            stream.close()
            self._streams.pop(stream.stream_id, None)

    async def _teardown(self, hub: PeerHub, stream: Stream, reason: str) -> None:
        if self._streams.pop(stream.stream_id, None) is None:
            return
        stream.close()
        try:
            await hub.send_to(
                stream.node_id,
                STREAM_CLOSE,
                {"stream_id": stream.stream_id, "reason": reason},
            )
        except Exception:  # noqa: BLE001 - the peer may already be gone
            pass

    # ---- lease revocation ------------------------------------------------------

    def close_lease_streams(self, lease_id: str, node_id: str) -> int:
        """Kill every stream belonging to one lease.

        Called on revocation. Closing the sockets is what makes a mid-turn
        revocation *visible*: the borrower's httpx raises inside an already-200
        response, which the provider layer turns into a named error rather than a
        silent truncation.
        """
        killed = 0
        for stream in list(self._streams.values()):
            if stream.node_id == node_id:
                stream.close()
                self._streams.pop(stream.stream_id, None)
                killed += 1
        return killed

    def stream_count(self) -> int:
        return len(self._streams)


#: Process-global, like `peer_hub` itself.
tunnels = TunnelManager()


def register(hub: PeerHub, manager: TunnelManager | None = None) -> None:
    """Bind a manager's handlers to a hub.

    `manager` is injectable because **one manager belongs to one node**. Stream
    ids are unique per node, not globally, so two hubs sharing a manager would
    have the second end of a stream overwrite the first under the same id. That
    cannot happen across real processes, but it does in any in-process test with
    two hubs -- and a test that quietly proxies a stream back to itself proves
    nothing about the code that runs in production.
    """
    mgr = manager if manager is not None else tunnels
    # `detach`: handle_open awaits a TCP connect, and inline that would block the
    # pump the reply has to arrive on.
    hub.register_handler(STREAM_OPEN, mgr.handle_open, mode="detach")
    # `serial`: frames must arrive in order or the stream is corrupt.
    hub.register_handler(STREAM_DATA, mgr.handle_data, mode="serial")
    # Both are a counter bump and a dict pop; inline is correct and cheapest.
    hub.register_handler(STREAM_CREDIT, mgr.handle_credit)
    hub.register_handler(STREAM_CLOSE, mgr.handle_close)
