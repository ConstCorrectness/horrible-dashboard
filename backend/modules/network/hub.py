"""`PeerHub`: the process-global peer fabric.

One hub per backend node (constructed at import, like `visualizer_manager`), shared
by all of that node's browser tabs. It owns the peer registry, runs the handshake,
pumps inbound envelopes, and offers a request/reply primitive (`request`) that
mirrors the orchestrator's `conn.pending[callId]` future pattern.

Inbound message types beyond the handshake/presence core are handled by callables
registered through `register_handler`, so the agent and collab slices plug in without
editing this module.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from backend.modules.network import capabilities, identity, protocol, trust
from backend.modules.network.identity import fingerprint
from backend.modules.network.models import (
    NodeIdentity,
    PeerEnvelope,
    PeerInfo,
    PeersSnapshot,
)
from backend.modules.network.transport.base import LinkClosed, PeerLink, Transport

if TYPE_CHECKING:
    from backend.modules.network.bench import BenchProbe

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 30.0

#: How a handler is run relative to the session's receive pump.
#:
#: - ``inline``  — awaited on the pump. Correct only for handlers that are a dict
#:   write and a send; anything slower delays *every other message type* sharing
#:   the link (measured: a 20 Hz stream's p50 went 0.29 ms → 1.81 ms behind bulk
#:   traffic, and behind a 500 ms handler it stalls outright).
#: - ``detach``  — run as its own task. **Mandatory for any handler that calls
#:   `hub.request`**, because that reply is delivered by this very pump: awaiting
#:   it inline deadlocks the session against itself.
#: - ``serial``  — queued to a per-(session, type) FIFO worker. For handlers that
#:   must not block the pump *and* must not be reordered, which `detach` cannot
#:   promise since tasks race.
DispatchMode = Literal["inline", "detach", "serial"]

#: Depth of a `serial` handler's queue before the session is torn down. Bounded
#: deliberately: the producer is a remote peer, so an unbounded queue is a remote
#: memory-exhaustion lever.
SERIAL_QUEUE_MAXSIZE = 256

#: Minimum gap between presence broadcasts. Capability attrs are live (open match
#: counts, loaded models), so without this a filling match would announce to every
#: peer on every join.
PRESENCE_DEBOUNCE_S = 5.0

# The bench probe, installed only while a measurement run is in flight (see
# bench.py). It is a module-level global read behind `if _probe is not None`
# rather than a hook object, a decorator or a context manager, because at 20 Hz
# across several peers each of those costs enough to show up in its own
# measurement. In production this is always None and the cost is a null check.
_probe: BenchProbe | None = None


def set_probe(probe: BenchProbe | None) -> None:
    """Install or clear the bench probe. Clearing is not optional -- a probe left
    installed keeps allocating rings for traffic nobody is measuring."""
    global _probe
    _probe = probe


# A handler runs for one inbound envelope of its type. Replies (envelopes with `re`
# set) are resolved against pending futures before handlers are consulted.
Handler = Callable[["PeerHub", "PeerSession", PeerEnvelope], Awaitable[None]]


def _wire_bytes(reported: int | None, env: PeerEnvelope) -> int:
    """The frame's size in bytes: what the transport measured, or a fallback.

    The hub used to serialize every envelope a second time in each direction
    purely to length it, which at 256 KB cost as much as the transport's own
    encode. Transports already hold the encoded string, so they report it.

    The fallback encodes to UTF-8 before counting because the original did not:
    `len()` on a `str` counts characters, so any non-ASCII payload -- an accented
    node name, a chat message in any other script -- was silently under-counted
    in the observability panel.
    """
    if reported is not None:
        return reported
    return len(env.model_dump_json().encode("utf-8"))


class PeerSession:
    """One connected peer: its link, its info, a send lock, and the futures awaiting
    replies to requests this node sent it (keyed by the request's msg_id)."""

    def __init__(self, link: PeerLink, info: PeerInfo) -> None:
        self.link = link
        self.info = info
        self._send_lock = asyncio.Lock()
        self.pending: dict[str, asyncio.Future[PeerEnvelope]] = {}
        # Set when the session is torn down, so the inbound `/peer-ws` endpoint can
        # block on the link's lifetime.
        self.closed = asyncio.Event()
        # Live link metrics surfaced by the Peer Monitor (see monitor.py). Counts
        # post-handshake traffic; `rtt_ms` is filled by the monitor's heartbeat.
        self.bytes_in = 0
        self.bytes_out = 0
        self.msgs_in = 0
        self.msgs_out = 0
        self.rtt_ms: float | None = None
        # Per-message-type FIFO queues for `serial` handlers, created on first use
        # and torn down with the session. Keyed by type so two ordered streams
        # never queue behind each other.
        self.serial_queues: dict[str, asyncio.Queue[PeerEnvelope]] = {}
        self.serial_workers: dict[str, asyncio.Task[None]] = {}

    async def send(self, env: PeerEnvelope) -> None:
        async with self._send_lock:
            self.msgs_out += 1
            await self.link.send(env)
            self.bytes_out += _wire_bytes(self.link.last_sent_bytes, env)


class PeerHub:
    def __init__(self, signer: identity.Identity | None = None) -> None:
        self.peers: dict[str, PeerSession] = {}
        self.transports: list[Transport] = []
        self._seen = protocol.SeenGuard()
        self._subscribers: set[Callable[[str, dict[str, Any]], None]] = set()
        self._handlers: dict[str, Handler] = {}
        self._modes: dict[str, DispatchMode] = {}
        # Strong references to in-flight `detach` tasks. asyncio holds only weak
        # ones, so a task nobody references can be garbage-collected mid-await.
        self._detached: set[asyncio.Task[None]] = set()
        self._started = False
        # Presence debounce state (see announce_presence).
        self._last_presence = 0.0
        self._presence_pending: asyncio.Task[None] | None = None
        # The keypair this hub signs as. Bound lazily for the process-global
        # singleton (the app has one identity); passed explicitly in tests so two
        # hubs in one process stay distinct.
        self._signer = signer

    @property
    def signer(self) -> identity.Identity:
        if self._signer is None:
            self._signer = identity.load_identity()
        return self._signer

    # ---- lifecycle ----------------------------------------------------------------

    def set_transports(self, transports: list[Transport]) -> None:
        self.transports = transports

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for tp in self.transports:
            try:
                await tp.start(self)
            except Exception:  # a broken transport must not sink the others
                logger.exception("transport %s failed to start", tp.name)

    async def add_transport(self, transport: Transport) -> None:
        """Register an extra transport at runtime (e.g. the lobby's relay-fallback),
        starting it if the hub is already running. No-op if one of that name exists."""
        if any(t.name == transport.name for t in self.transports):
            return
        self.transports.append(transport)
        if self._started:
            try:
                await transport.start(self)
            except Exception:
                logger.exception("transport %s failed to start", transport.name)

    async def stop(self) -> None:
        for session in list(self.peers.values()):
            await session.link.close()
        self.peers.clear()
        for tp in self.transports:
            try:
                await tp.stop()
            except Exception:
                logger.exception("transport %s failed to stop", tp.name)
        self._started = False

    # ---- handler + subscriber registration ----------------------------------------

    def register_handler(
        self, msg_type: str, handler: Handler, *, mode: DispatchMode = "inline"
    ) -> None:
        """Register the handler for one inbound message type.

        `mode` defaults to `inline` so existing registrations keep their exact
        behaviour, but it is the thing to think about when adding one: a handler
        that calls `hub.request` **must** be `detach` (its reply arrives on the
        pump it would otherwise be blocking), and one that is both slow and
        order-sensitive must be `serial`. See `DispatchMode`.
        """
        self._handlers[msg_type] = handler
        self._modes[msg_type] = mode

    def subscribe(
        self, cb: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register a per-`/ws`-connection callback for peer/presence events. Returns
        an unsubscribe function."""
        self._subscribers.add(cb)
        return lambda: self._subscribers.discard(cb)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """Broadcast an event to every subscribed browser connection (e.g. the
        Peer Monitor's periodic `peer_metrics`). Public wrapper over `_emit`."""
        self._emit(event, data)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        for cb in list(self._subscribers):
            try:
                cb(event, data)
            except Exception:
                logger.exception("network subscriber failed")

    # ---- identity / snapshot ------------------------------------------------------

    def identity(self) -> NodeIdentity:
        me = self.signer
        return NodeIdentity(
            node_id=me.node_id,
            public_key=me.public_key,
            node_name=identity.node_name(),
            capabilities=self.capabilities(),
        )

    def capabilities(self) -> list[str]:
        # Advertised to every peer during the handshake, so a friend's UI can
        # offer only what this node can actually accept — a match invite to a
        # node with no `hassault` is a dead button.
        #
        # **This return type is frozen.** It feeds `CommonsProfile`, which is
        # signed and re-served by a federated index, plus the lobby wire and the
        # Kotlin client. The richer form lives beside it in `caps` — see
        # capabilities.py.
        return capabilities.ids()

    def list_peers(self) -> list[PeerInfo]:
        return [s.info for s in self.peers.values()]

    def snapshot(self) -> PeersSnapshot:
        return PeersSnapshot(self=self.identity(), peers=self.list_peers())

    # ---- handshake ----------------------------------------------------------------

    def _hello_data(
        self, nonce: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        me = self.identity()
        data = {
            "node_name": me.node_name,
            "public_key": me.public_key,
            "capabilities": me.capabilities,
            # Additive: a peer that does not understand `caps` reads
            # `capabilities` exactly as before, and one that does gets the live
            # detail. Never fold these together — see capabilities.py.
            "caps": capabilities.wire(),
            "nonce": nonce,
        }
        if extra:
            data.update(extra)
        return data

    async def _signed(
        self,
        msg_type: str,
        dst: str | None,
        data: dict[str, Any],
        re: str | None = None,
    ) -> PeerEnvelope:
        env = PeerEnvelope(
            type=msg_type, src=self.signer.node_id, dst=dst, data=data, re=re
        )
        return protocol.sign_envelope(env, self.signer)

    @staticmethod
    def _check_identity(env: PeerEnvelope) -> str | None:
        """Verify the envelope's signature against the public key it carries and that
        the key's fingerprint matches `src`. Returns the public key on success."""
        public_key = env.data.get("public_key")
        if not isinstance(public_key, str) or not public_key:
            return None
        if fingerprint(public_key) != env.src:
            return None
        if not protocol.verify_envelope(env, public_key):
            return None
        return public_key

    async def handshake_dial(
        self, link: PeerLink, *, token: str | None = None
    ) -> PeerSession:
        """Drive the dialer side of the handshake over an already-open link."""
        my_nonce = uuid.uuid4().hex
        await link.send(
            await self._signed(protocol.HELLO, None, self._hello_data(my_nonce))
        )

        ack = await link.recv()
        if ack.type != protocol.HELLO_ACK:
            await link.close()
            raise LinkClosed("expected hello_ack")
        public_key = self._check_identity(ack)
        if public_key is None or ack.data.get("echo") != my_nonce:
            await link.close()
            raise LinkClosed("peer identity verification failed")
        their_nonce = str(ack.data.get("nonce", ""))

        await link.send(
            await self._signed(
                protocol.AUTH, ack.src, {"echo": their_nonce, "token": token}
            )
        )
        result = await link.recv()
        if result.type != protocol.AUTH_RESULT or not result.data.get("ok"):
            await link.close()
            raise LinkClosed(str(result.data.get("reason") or "auth rejected"))

        info = self._peer_info(ack, public_key, link, trusted=True)
        # **Persist the trust, not just the session.** Pairing used to be
        # one-sided: the acceptor wrote a `known_peers` record (`trust.evaluate`
        # → `save_known_peer`) while the dialer marked trust in memory only. So
        # after A redeemed B's invite, only B remembered — and the next time A was
        # the one dialled, B's node had never heard of it and rejected the
        # handshake. Pairing is mutual by nature; both ends have now met an
        # identity they verified cryptographically in this very handshake.
        trust.save_known_peer(
            ack.src,
            {
                "trusted": True,
                "via": "dialed",
                "public_key": public_key,
                "address": link.address,
            },
        )
        return self._register(link, info)

    async def accept_link(self, link: PeerLink) -> PeerSession | None:
        """Drive the acceptor side of the handshake, then pump the link if admitted."""
        try:
            logger.info("Handshake started from %s", link.address)
            hello = await link.recv()
            logger.info("Handshake received %s from %s", hello.type, hello.src)
            if hello.type != protocol.HELLO:
                logger.warning("Handshake failed: expected HELLO, got %s", hello.type)
                await link.close()
                return None
            public_key = self._check_identity(hello)
            if public_key is None:
                logger.warning(
                    "Handshake failed: identity verification failed for %s", hello.src
                )
                await link.close()
                return None
            their_nonce = str(hello.data.get("nonce", ""))
            my_nonce = uuid.uuid4().hex
            await link.send(
                await self._signed(
                    protocol.HELLO_ACK,
                    hello.src,
                    self._hello_data(my_nonce, {"echo": their_nonce}),
                )
            )
            logger.info("Handshake sent HELLO_ACK to %s", hello.src)

            auth = await link.recv()
            logger.info("Handshake received %s from %s", auth.type, auth.src)
            if (
                auth.type != protocol.AUTH
                or auth.src != hello.src
                or auth.data.get("echo") != my_nonce
                or not protocol.verify_envelope(auth, public_key)
            ):
                logger.warning("Handshake failed: invalid AUTH from %s", hello.src)
                await link.close()
                return None

            ok, reason = trust.evaluate(hello.src, auth.data.get("token"))
            await link.send(
                await self._signed(
                    protocol.AUTH_RESULT, hello.src, {"ok": ok, "reason": reason}
                )
            )
            if not ok:
                logger.warning(
                    "Handshake failed: auth evaluation rejected for %s: %s",
                    hello.src,
                    reason,
                )
                await link.close()
                return None

            logger.info("Handshake complete for %s", hello.src)
            info = self._peer_info(hello, public_key, link, trusted=True)
            session = self._register(link, info)
            return session
        except LinkClosed:
            logger.info("Handshake aborted: link closed from %s", link.address)
            await link.close()
            return None
        except Exception:
            logger.exception("Handshake error from %s", link.address)
            await link.close()
            return None

    def _peer_info(
        self, env: PeerEnvelope, public_key: str, link: PeerLink, *, trusted: bool
    ) -> PeerInfo:
        raw_ids = env.data.get("capabilities")
        cap_ids = raw_ids if isinstance(raw_ids, list) else []
        return PeerInfo(
            node_id=env.src,
            node_name=str(env.data.get("node_name", env.src)),
            public_key=public_key,
            transport=link.transport_name,  # type: ignore[arg-type]
            address=link.address,
            status="connected",
            trusted=trusted,
            last_seen=time.time(),
            capabilities=cap_ids,
            caps=capabilities.from_wire(env.data.get("caps"), cap_ids),
        )

    def _register(self, link: PeerLink, info: PeerInfo) -> PeerSession:
        link.peer_node_id = info.node_id
        session = PeerSession(link, info)
        self.peers[info.node_id] = session
        asyncio.ensure_future(self._pump(session))
        self._emit("peer_update", {"peer": info.model_dump()})
        logger.info("peer connected: %s (%s)", info.node_name, info.node_id)
        return session

    # ---- inbound pump + dispatch --------------------------------------------------

    async def _pump(self, session: PeerSession) -> None:
        try:
            while True:
                env = await session.link.recv()
                await self._dispatch(session, env)
        except LinkClosed:
            pass
        except Exception:
            logger.exception("peer pump error for %s", session.info.node_id)
        finally:
            self._drop(session)

    def _drop(self, session: PeerSession) -> None:
        node_id = session.info.node_id
        if self.peers.get(node_id) is session:
            del self.peers[node_id]
        for fut in session.pending.values():
            if not fut.done():
                fut.cancel()
        # Serial workers outlive their queue otherwise: each is parked on
        # `queue.get()` forever, holding the session and its handler alive for the
        # life of the process.
        for worker in session.serial_workers.values():
            worker.cancel()
        session.serial_workers.clear()
        session.serial_queues.clear()
        session.info.status = "disconnected"
        session.closed.set()
        self._emit("peer_update", {"peer": session.info.model_dump()})
        logger.info("peer disconnected: %s", node_id)

    async def _dispatch(self, session: PeerSession, env: PeerEnvelope) -> None:
        session.bytes_in += _wire_bytes(session.link.last_recv_bytes, env)
        session.msgs_in += 1
        # Loop/replay guard, then signature check against the established peer key.
        if not self._seen.check(env.msg_id):
            return
        if _probe is None:
            verified = protocol.verify_envelope(env, session.info.public_key)
        else:
            t0 = time.perf_counter_ns()
            verified = protocol.verify_envelope(env, session.info.public_key)
            _probe.record_ns("verify", env.type, time.perf_counter_ns() - t0)
        if not verified:
            logger.warning("dropping unsigned/forged envelope from %s", env.src)
            return
        session.info.last_seen = time.time()

        # A reply to one of our requests resolves the pending future first.
        if env.re is not None:
            fut = session.pending.pop(env.re, None)
            if fut is not None and not fut.done():
                fut.set_result(env)
            return

        if env.type == protocol.PING:
            await session.send(
                await self._signed(protocol.PONG, env.src, {}, re=env.msg_id)
            )
            return
        if env.type == protocol.PRESENCE:
            self._apply_presence(session, env)
            return

        handler = self._handlers.get(env.type)
        if handler is None:
            return
        mode = self._modes.get(env.type, "inline")
        if mode == "inline":
            # Awaited on the pump: this handler's duration is the head-of-line
            # delay every other message type on this link pays.
            await self._invoke(handler, session, env)
        elif mode == "detach":
            task = asyncio.create_task(self._invoke(handler, session, env))
            self._detached.add(task)
            task.add_done_callback(self._detached.discard)
        else:
            self._enqueue_serial(handler, session, env)

    async def _invoke(
        self, handler: Handler, session: PeerSession, env: PeerEnvelope
    ) -> None:
        """Run one handler, timing it and containing its failures.

        A detached or queued handler no longer unwinds into `_pump`'s
        `except Exception`, so without this its errors would vanish silently --
        the failure mode of moving work off a pump, and a worse one than the
        blocking it cures.
        """
        t0 = time.perf_counter_ns() if _probe is not None else 0
        try:
            await handler(self, session, env)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("peer handler %s failed", env.type)
        finally:
            if _probe is not None:
                _probe.record_ns("handler", env.type, time.perf_counter_ns() - t0)

    def _enqueue_serial(
        self, handler: Handler, session: PeerSession, env: PeerEnvelope
    ) -> None:
        """Hand an envelope to this type's FIFO worker, starting it on first use.

        A full queue **tears the session down** rather than dropping the envelope.
        That looks harsh, but `serial` exists for streams whose order carries
        meaning, and such a stream with a hole in it has no correct continuation
        the transport can invent -- a dropped collab op is a silently corrupted
        document. Closing forces the feature layer's existing reconnect-and-resync
        path, which is a defined recovery. The blast radius is one peer, and a
        peer can only do this to itself.
        """
        queue = session.serial_queues.get(env.type)
        if queue is None:
            queue = asyncio.Queue(maxsize=SERIAL_QUEUE_MAXSIZE)
            session.serial_queues[env.type] = queue
            session.serial_workers[env.type] = asyncio.create_task(
                self._serial_worker(handler, session, queue, env.type)
            )
        try:
            queue.put_nowait(env)
        except asyncio.QueueFull:
            logger.error(
                "serial queue for %s overflowed from peer %s (%d deep); closing the "
                "session so the feature layer resyncs rather than losing ordered ops",
                env.type,
                session.info.node_id,
                SERIAL_QUEUE_MAXSIZE,
            )
            task = asyncio.create_task(session.link.close())
            self._detached.add(task)
            task.add_done_callback(self._detached.discard)

    async def _serial_worker(
        self,
        handler: Handler,
        session: PeerSession,
        queue: asyncio.Queue[PeerEnvelope],
        msg_type: str,
    ) -> None:
        """Drain one type's queue in arrival order, off the pump.

        The ordering guarantee is the whole point: `detach` would also unblock the
        pump, but its tasks race for the send lock, so two collab ops could apply
        backwards.
        """
        try:
            while True:
                env = await queue.get()
                try:
                    await self._invoke(handler, session, env)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("serial worker for %s died", msg_type)

    def _apply_presence(self, session: PeerSession, env: PeerEnvelope) -> None:
        name = env.data.get("node_name")
        if isinstance(name, str):
            session.info.node_name = name
        raw_ids = env.data.get("capabilities")
        if isinstance(raw_ids, list):
            session.info.capabilities = raw_ids
            session.info.caps = capabilities.from_wire(env.data.get("caps"), raw_ids)
        self._emit("peer_update", {"peer": session.info.model_dump()})

    # ---- outbound -----------------------------------------------------------------

    async def announce_presence(self) -> None:
        """Broadcast this node's current capabilities to every connected peer.

        Called when a registered provider's answer changes -- a match opening, a
        model finishing its load. Debounced: a hassault match filling up would
        otherwise broadcast to every peer on every join, and presence is worth
        far less than the traffic that would cost.
        """
        now = time.monotonic()
        if now - self._last_presence < PRESENCE_DEBOUNCE_S:
            if self._presence_pending is None or self._presence_pending.done():
                delay = PRESENCE_DEBOUNCE_S - (now - self._last_presence)
                self._presence_pending = asyncio.create_task(
                    self._announce_after(delay)
                )
            return
        await self._announce_now()

    async def _announce_after(self, delay: float) -> None:
        # A trailing edge, not a dropped call: the last state within a burst is
        # the one worth having, and dropping it would leave peers believing a
        # match is still open after it filled.
        try:
            await asyncio.sleep(delay)
            await self._announce_now()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("deferred presence announce failed")

    async def _announce_now(self) -> None:
        self._last_presence = time.monotonic()
        data = self._hello_data(uuid.uuid4().hex)
        for session in list(self.peers.values()):
            try:
                await session.send(
                    await self._signed(protocol.PRESENCE, session.info.node_id, data)
                )
            except Exception:  # noqa: BLE001 - one dead peer must not stop the rest
                logger.debug("presence announce to %s failed", session.info.node_id)

    async def send_to(
        self, node_id: str, msg_type: str, data: dict[str, Any], re: str | None = None
    ) -> None:
        session = self.peers.get(node_id)
        if session is None:
            raise KeyError(f"no peer {node_id}")
        await session.send(await self._signed(msg_type, node_id, data, re=re))

    async def request(
        self,
        node_id: str,
        msg_type: str,
        data: dict[str, Any],
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> PeerEnvelope:
        """Send a message and await the peer's reply (an envelope whose `re` equals
        this message's `msg_id`). Raises `TimeoutError` if the peer doesn't reply."""
        session = self.peers.get(node_id)
        if session is None:
            raise KeyError(f"no peer {node_id}")
        env = await self._signed(msg_type, node_id, data)
        fut: asyncio.Future[PeerEnvelope] = asyncio.get_running_loop().create_future()
        session.pending[env.msg_id] = fut
        await session.send(env)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            session.pending.pop(env.msg_id, None)

    async def connect(
        self, address: str, transport: str = "direct", token: str | None = None
    ) -> PeerInfo:
        """Dial an address with the named transport and complete the handshake. A
        `token` (from an invite) is presented during auth for manual-mode peers."""
        tp = next((t for t in self.transports if t.name == transport), None)
        if tp is None:
            raise ValueError(f"transport {transport!r} not enabled")
        link = await tp.dial(address)
        session = await self.handshake_dial(link, token=token)
        return session.info

    async def disconnect(self, node_id: str) -> None:
        session = self.peers.get(node_id)
        if session is not None:
            await session.link.close()


# Process-global singleton (constructed at import, like visualizer_manager).
peer_hub = PeerHub()
