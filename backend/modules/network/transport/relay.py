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

    async def send(self, env: PeerEnvelope) -> None:
        # Stamp the routing dst so the broker can forward (excluded from the sig).
        if env.dst is None:
            env = env.model_copy(update={"dst": self.peer_node_id})
        await self._transport._send_raw(protocol.encode(env))

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
        raise LinkClosed

    async def close(self) -> None:
        self._closed.set()
        self._transport._drop_link(self.peer_node_id)

    def _deliver(self, env: PeerEnvelope) -> None:
        self._inbox.put_nowait(env)


class RelayTransport(Transport):
    name = "relay"

    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: Any = None
        self._hub: PeerHub | None = None
        self._links: dict[str, RelayLink] = {}
        self._reader: asyncio.Task[None] | None = None

    async def start(self, hub: PeerHub) -> None:
        self._hub = hub
        self._ws = await ws_connect(self._url)
        await self._ws.send(json.dumps({"register": hub.signer.node_id}))
        self._reader = asyncio.ensure_future(self._read_loop())
        logger.info("relay transport connected to %s", self._url)

    async def stop(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def dial(self, address: str) -> PeerLink:
        # For relay, `address` is the target peer's node_id.
        link = RelayLink(self, address)
        self._links[address] = link
        return link

    async def _send_raw(self, raw: str) -> None:
        if self._ws is None:
            raise LinkClosed
        try:
            await self._ws.send(raw)
        except ConnectionClosed as exc:
            raise LinkClosed from exc

    def _drop_link(self, node_id: str) -> None:
        self._links.pop(node_id, None)

    async def _read_loop(self) -> None:
        assert self._hub is not None
        try:
            while True:
                raw = await self._ws.recv()
                text = raw if isinstance(raw, str) else raw.decode()
                try:
                    env = protocol.decode(text)
                except Exception:
                    continue  # broker control frames (e.g. registration ack)
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
        except Exception:
            logger.exception("relay read loop failed")
