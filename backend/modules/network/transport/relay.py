"""Relay transport: reach peers through a rendezvous broker for discovery and NAT
traversal when a direct dial isn't possible.

One outbound WebSocket to the broker carries traffic for *all* relayed peers; this
transport demultiplexes by `src` into a virtual `RelayLink` per peer, so the hub's
one-link-per-peer model is preserved. Envelopes stay end-to-end signed, so the
broker can route but never forge them. Addressing is by `node_id` (the broker maps
ids to connections); `dst` is filled in by the link before send.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from backend.modules.network import protocol
from backend.modules.network.models import PeerEnvelope
from backend.modules.network.transport.base import LinkClosed, PeerLink, Transport

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub

logger = logging.getLogger(__name__)


class RelayLink(PeerLink):
    """A virtual link to one peer multiplexed over the shared broker connection."""

    transport_name = "relay"

    def __init__(self, transport: RelayTransport, peer_node_id: str) -> None:
        self._transport = transport
        self.peer_node_id = peer_node_id
        self.address = f"relay:{peer_node_id}"
        self._inbox: asyncio.Queue[PeerEnvelope] = asyncio.Queue()
        self._closed = asyncio.Event()
        #: Why the link closed, surfaced to whoever was dialing ("not on the relay").
        self.close_reason = ""

    async def send(self, env: PeerEnvelope) -> None:
        # Stamp the routing dst so the broker can forward (excluded from the sig).
        # This is why `send` takes an envelope rather than a pre-encoded string:
        # the relay legitimately rewrites a routing header, and a pre-encoded
        # frame could only be patched by editing JSON text.
        if env.dst is None:
            env = env.model_copy(update={"dst": self.peer_node_id})
        raw = protocol.encode(env)
        self.last_sent_bytes = len(raw.encode("utf-8"))
        await self._transport._send_raw(raw)

    async def recv(self) -> PeerEnvelope:
        getter = asyncio.ensure_future(self._inbox.get())
        closed = asyncio.ensure_future(self._closed.wait())
        done, pending = await asyncio.wait(
            {getter, closed}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if getter in done:
            return getter.result()
        raise LinkClosed(self.close_reason or "relay link closed")

    async def close(self) -> None:
        self._closed.set()
        self._transport._drop_link(self.peer_node_id)

    def _deliver(self, env: PeerEnvelope) -> None:
        self._inbox.put_nowait(env)


class RelayTransport(Transport):
    """One authenticated, self-healing connection to a relay broker.

    `start` never blocks on the broker and never raises: it launches a supervisor
    that connects, proves this node's identity, pumps frames, and reconnects with
    backoff when the connection drops. This is the default transport off the LAN now
    (the broker is hosted by the game server), so a broker restart must not take the
    node's reachability with it, and a broker that is down at boot must not stop the
    rest of the fabric from starting.
    """

    name = "relay"

    #: Reconnect backoff, doubling from the first value up to the second.
    BACKOFF_S = (1.0, 60.0)
    #: A relayed frame can be as large as anything a direct link carries. The
    #: `websockets` default of 1 MiB would close the *shared* connection — every
    #: relayed friend at once — on one large message.
    MAX_FRAME = 16 * 1024 * 1024

    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: Any = None
        self._hub: PeerHub | None = None
        self._links: dict[str, RelayLink] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self.registered = asyncio.Event()

    @property
    def url(self) -> str:
        return self._url

    async def start(self, hub: PeerHub) -> None:
        self._hub = hub
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._supervisor = None
        await self._close_ws()

    async def dial(self, address: str) -> PeerLink:
        """`address` is the target's node id (a `relay:` prefix is accepted)."""
        node_id = address.removeprefix("relay:")
        if not self.registered.is_set():
            raise LinkClosed("not connected to the relay")
        link = RelayLink(self, node_id)
        self._links[node_id] = link
        return link

    async def _send_raw(self, raw: str) -> None:
        if self._ws is None or not self.registered.is_set():
            raise LinkClosed
        try:
            await self._ws.send(raw)
        except ConnectionClosed as exc:
            raise LinkClosed from exc

    def _drop_link(self, node_id: str) -> None:
        self._links.pop(node_id, None)

    async def _close_ws(self) -> None:
        self.registered.clear()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        # Every relayed link rode this connection; none of them survive it.
        for link in list(self._links.values()):
            link._closed.set()
        self._links.clear()

    async def _supervise(self) -> None:
        delay = self.BACKOFF_S[0]
        while True:
            try:
                await self._connect_and_pump()
                delay = self.BACKOFF_S[0]
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info("relay %s unavailable: %s", self._url, exc)
            finally:
                await self._close_ws()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.BACKOFF_S[1])

    async def _connect_and_pump(self) -> None:
        from backend.modules.network.relay_broker import challenge_bytes

        assert self._hub is not None
        self._ws = await ws_connect(self._url, max_size=self.MAX_FRAME)
        first = json.loads(await asyncio.wait_for(self._ws.recv(), 15))
        challenge = first.get("challenge") if isinstance(first, dict) else None
        if not isinstance(challenge, str):
            raise ConnectionError("relay sent no challenge")
        signer = self._hub.signer
        await self._ws.send(
            json.dumps(
                {
                    "register": signer.node_id,
                    "public_key": signer.public_key,
                    "sig": signer.sign(challenge_bytes(challenge)),
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(self._ws.recv(), 15))
        if not isinstance(ack, dict) or ack.get("registered") != signer.node_id:
            raise ConnectionError(f"relay refused registration: {ack}")
        self.registered.set()
        logger.info("relay transport registered at %s", self._url)
        await self._read_loop()

    async def _read_loop(self) -> None:
        assert self._hub is not None
        try:
            while True:
                raw = await self._ws.recv()
                text = raw if isinstance(raw, str) else raw.decode()
                try:
                    env = protocol.decode(text)
                except Exception:
                    self._control(text)
                    continue
                link = self._links.get(env.src)
                if link is None:
                    # First contact from a new peer: spin up a virtual link and run
                    # the acceptor handshake against it.
                    link = RelayLink(self, env.src)
                    self._links[env.src] = link
                    link._deliver(env)
                    asyncio.ensure_future(self._hub.accept_link(link))
                else:
                    link._deliver(env)
        except (ConnectionClosed, LinkClosed):
            pass

    def _control(self, text: str) -> None:
        """A broker frame that is not an envelope."""
        try:
            msg = json.loads(text)
        except ValueError:
            return
        dst = msg.get("undeliverable") if isinstance(msg, dict) else None
        if isinstance(dst, str):
            # The node is not on the relay. Fail its link now, so a dial surfaces
            # "offline" at once rather than after the handshake timeout.
            link = self._links.pop(dst, None)
            if link is not None:
                link.close_reason = f"{dst} is not online (not connected to the relay)"
                link._closed.set()
