"""In-process transport for tests: two `PeerHub`s talk through paired queues with
no sockets. `connect_pair(hub_a, hub_b)` wires a dialer link into A and hands the
accepting link to B's `accept_link`, exercising the real handshake end to end."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from backend.modules.network.models import PeerEnvelope
from backend.modules.network.transport.base import LinkClosed, PeerLink, Transport

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub


class LoopbackLink(PeerLink):
    """One end of an in-process pipe. `send` enqueues onto the partner's inbox."""

    transport_name = "direct"

    def __init__(self, inbox: asyncio.Queue[PeerEnvelope]) -> None:
        self._inbox = inbox
        self._outbox: asyncio.Queue[PeerEnvelope] | None = None
        self._closed = asyncio.Event()

    def _attach(self, outbox: asyncio.Queue[PeerEnvelope]) -> None:
        self._outbox = outbox

    async def send(self, env: PeerEnvelope) -> None:
        if self._closed.is_set() or self._outbox is None:
            raise LinkClosed
        # Round-trip through JSON so tests exercise real (de)serialization.
        from backend.modules.network import protocol

        await self._outbox.put(protocol.decode(protocol.encode(env)))

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


def make_pair() -> tuple[LoopbackLink, LoopbackLink]:
    """Two cross-wired links (dialer, acceptor)."""
    q_a: asyncio.Queue[PeerEnvelope] = asyncio.Queue()
    q_b: asyncio.Queue[PeerEnvelope] = asyncio.Queue()
    dialer = LoopbackLink(q_a)
    acceptor = LoopbackLink(q_b)
    dialer._attach(q_b)
    acceptor._attach(q_a)
    return dialer, acceptor


class InProcessTransport(Transport):
    """Test transport. `dial` is unused for the loopback path (tests call
    `connect_pair`); it exists so a hub can be constructed with a transport list."""

    name = "loopback"

    async def start(self, hub: PeerHub) -> None:
        return None

    async def dial(self, address: str) -> PeerLink:
        raise NotImplementedError("loopback transport dials via connect_pair")


async def connect_pair(
    hub_a: PeerHub, hub_b: PeerHub, *, token: str | None = None
) -> None:
    """Open a loopback link between two hubs and run the handshake: A dials, B
    accepts. Returns once both sides have registered the peer (or raised)."""
    dialer, acceptor = make_pair()
    accept = asyncio.ensure_future(hub_b.accept_link(acceptor))
    await hub_a.handshake_dial(dialer, token=token)
    await accept
