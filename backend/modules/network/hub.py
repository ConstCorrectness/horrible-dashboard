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
from typing import Any

from backend.modules.network import identity, protocol, trust
from backend.modules.network.identity import fingerprint
from backend.modules.network.models import (
    NodeIdentity,
    PeerEnvelope,
    PeerInfo,
    PeersSnapshot,
)
from backend.modules.network.transport.base import LinkClosed, PeerLink, Transport

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 30.0

# A handler runs for one inbound envelope of its type. Replies (envelopes with `re`
# set) are resolved against pending futures before handlers are consulted.
Handler = Callable[["PeerHub", "PeerSession", PeerEnvelope], Awaitable[None]]


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

    async def send(self, env: PeerEnvelope) -> None:
        async with self._send_lock:
            self.bytes_out += len(env.model_dump_json())
            self.msgs_out += 1
            await self.link.send(env)


class PeerHub:
    def __init__(self, signer: identity.Identity | None = None) -> None:
        self.peers: dict[str, PeerSession] = {}
        self.transports: list[Transport] = []
        self._seen = protocol.SeenGuard()
        self._subscribers: set[Callable[[str, dict[str, Any]], None]] = set()
        self._handlers: dict[str, Handler] = {}
        self._started = False
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

    def register_handler(self, msg_type: str, handler: Handler) -> None:
        self._handlers[msg_type] = handler

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
        return ["agent", "collab", "hassault"]

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
        caps = env.data.get("capabilities")
        return PeerInfo(
            node_id=env.src,
            node_name=str(env.data.get("node_name", env.src)),
            public_key=public_key,
            transport=link.transport_name,  # type: ignore[arg-type]
            address=link.address,
            status="connected",
            trusted=trusted,
            last_seen=time.time(),
            capabilities=caps if isinstance(caps, list) else [],
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
        session.info.status = "disconnected"
        session.closed.set()
        self._emit("peer_update", {"peer": session.info.model_dump()})
        logger.info("peer disconnected: %s", node_id)

    async def _dispatch(self, session: PeerSession, env: PeerEnvelope) -> None:
        session.bytes_in += len(env.model_dump_json())
        session.msgs_in += 1
        # Loop/replay guard, then signature check against the established peer key.
        if not self._seen.check(env.msg_id):
            return
        if not protocol.verify_envelope(env, session.info.public_key):
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
        if handler is not None:
            await handler(self, session, env)

    def _apply_presence(self, session: PeerSession, env: PeerEnvelope) -> None:
        name = env.data.get("node_name")
        if isinstance(name, str):
            session.info.node_name = name
        caps = env.data.get("capabilities")
        if isinstance(caps, list):
            session.info.capabilities = caps
        self._emit("peer_update", {"peer": session.info.model_dump()})

    # ---- outbound -----------------------------------------------------------------

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
