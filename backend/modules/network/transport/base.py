"""Transport abstraction: one `PeerLink` per live connection, one `Transport` per
backend kind. The hub speaks only this interface, so direct-WS, relay, LAN, and the
in-process test transport are interchangeable."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from backend.modules.network.models import PeerEnvelope

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub


class PeerLink(ABC):
    """A single bidirectional connection to one peer. `recv` raises `LinkClosed`
    when the connection ends so the hub's read pump can tear the session down."""

    # Set once the handshake identifies the peer; None before then.
    peer_node_id: str | None = None
    # How this link was established, surfaced in PeerInfo.transport.
    transport_name: str = "direct"
    # The dialed address (ws url / relay route), for reconnect + display.
    address: str | None = None

    # Wire size of the most recent frame, in **bytes**, set by transports that
    # know it (all of them do -- they hold the encoded string). The hub reads
    # these for its per-session counters instead of serializing the envelope a
    # second time purely to measure it, which is what it used to do in both
    # directions. Optional rather than abstract so a transport that cannot answer
    # simply leaves them None and the hub falls back to counting.
    #
    # They are bytes and not `len(str)` on purpose: the old counter measured
    # characters, so every non-ASCII payload was under-counted -- a node name
    # with an accent in it made the observability panel quietly wrong.
    last_sent_bytes: int | None = None
    last_recv_bytes: int | None = None

    @abstractmethod
    async def send(self, env: PeerEnvelope) -> None: ...

    @abstractmethod
    async def recv(self) -> PeerEnvelope: ...

    @abstractmethod
    async def close(self) -> None: ...


class LinkClosed(Exception):
    """Raised by `PeerLink.recv` when the connection has ended."""


class Transport(ABC):
    """A way to reach peers. `start` begins listening/advertising; `dial` opens an
    outbound link; `stop` releases resources."""

    name: str = "base"

    @abstractmethod
    async def start(self, hub: PeerHub) -> None: ...

    @abstractmethod
    async def dial(self, address: str) -> PeerLink: ...

    async def stop(self) -> None:  # default: nothing to release
        return None
