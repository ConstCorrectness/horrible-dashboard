"""WebRTC datachannel transport: peer links over an aiortc `RTCDataChannel`, with SDP
offer/answer signaling carried by the lobby connection.

This is the NAT-traversal transport the WebSocket links can't be: ICE (STUN, plus an
optional TURN relay) negotiates a media-grade path through cone NATs that a plain TCP
dial can't reach. aiortc gathers candidates **non-trickle** — the full SDP carries
them — so signaling is a single offer/answer round-trip over the lobby `signal` seam
(`lobby_server` forwards a `signal` frame to its `to` node, tagging `from`). Once the
channel opens it carries the same signed `PeerEnvelope` frames as every other
transport, so the hub's handshake, trust, and protocol are unchanged. The relay stays
the guaranteed fallback for paths ICE can't punch (symmetric NAT without a TURN server).

aiortc is an optional extra (`uv sync --extra webrtc`); the heavy import is guarded so
this module always loads, and `AIORTC_AVAILABLE` gates whether the transport is usable.
The link is duck-typed against the channel/peer-connection objects, so its unit tests
run without aiortc installed. See docs/architecture/distributed.mdx.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from backend.modules.network import protocol
from backend.modules.network.models import PeerEnvelope
from backend.modules.network.transport.base import LinkClosed, PeerLink, Transport
from backend.modules.settings.routes import get_value

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub

logger = logging.getLogger(__name__)

try:
    from aiortc import (
        RTCConfiguration,
        RTCIceServer,
        RTCPeerConnection,
        RTCSessionDescription,
    )

    AIORTC_AVAILABLE = True
except ImportError:  # the optional `webrtc` extra isn't installed
    AIORTC_AVAILABLE = False

# ICE negotiation (gather + connectivity checks) can take a few seconds; give a dial
# and the answerer's channel-open wait generous headroom before falling back.
NEGOTIATION_TIMEOUT_S = 20.0


class WebRtcLink(PeerLink):
    """A peer link over an aiortc data channel. `recv` drains an inbox fed by the
    channel's `message` handler; `close` and any failed/closed connection state tear
    the link down so the hub's pump can drop the session."""

    transport_name = "webrtc"

    def __init__(self, pc: Any, channel: Any, peer_node_id: str) -> None:
        self._pc = pc
        self._channel = channel
        self.peer_node_id = peer_node_id
        self.address = f"webrtc:{peer_node_id}"
        self._inbox: asyncio.Queue[PeerEnvelope] = asyncio.Queue()
        self._closed = asyncio.Event()

        @channel.on("message")
        def _on_message(message: Any) -> None:
            try:
                env = protocol.decode(
                    message if isinstance(message, str) else message.decode()
                )
            except Exception:
                return  # ignore anything that isn't a well-formed envelope
            self._inbox.put_nowait(env)

        @channel.on("close")
        def _on_close() -> None:
            self._closed.set()

        @pc.on("connectionstatechange")
        def _on_state() -> None:
            if pc.connectionState in ("failed", "closed"):
                self._closed.set()

    async def send(self, env: PeerEnvelope) -> None:
        if self._closed.is_set():
            raise LinkClosed
        try:
            self._channel.send(protocol.encode(env))
        except Exception as exc:  # channel torn down mid-send
            raise LinkClosed from exc

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
        try:
            await self._pc.close()
        except Exception:
            pass


class WebRtcTransport(Transport):
    """Establishes data-channel links, using a signaler (the lobby by default) to
    exchange SDP. `dial(node_id)` offers; an inbound offer is answered and the opened
    channel is handed to the hub's acceptor — so either end runs the normal handshake.

    Requires the signaler to be reachable (a configured `network.lobbyUrl`); SDP rides
    its `signal` frames, addressed by node_id. `signaler` is injectable for tests."""

    name = "webrtc"

    def __init__(self, signaler: Any = None) -> None:
        self._hub: PeerHub | None = None
        self._signaler = signaler
        # node_id -> future resolved with the answer SDP for an in-flight dial.
        self._pending_answers: dict[str, asyncio.Future[str]] = {}

    # ---- signaling seam -----------------------------------------------------------

    def _get_signaler(self) -> Any:
        if self._signaler is None:
            from backend.modules.network.lobby import lobby_client

            self._signaler = lobby_client
        return self._signaler

    async def _signal(self, to: str, payload: dict[str, Any]) -> None:
        await self._get_signaler().send_signal(to, payload)

    # ---- lifecycle ----------------------------------------------------------------

    async def start(self, hub: PeerHub) -> None:
        self._hub = hub
        self._get_signaler().set_signal_handler(self._on_signal)
        logger.info("webrtc transport ready (SDP signaling via the lobby)")

    async def stop(self) -> None:
        for fut in self._pending_answers.values():
            if not fut.done():
                fut.cancel()
        self._pending_answers.clear()

    # ---- ice config ---------------------------------------------------------------

    def _ice_servers(self) -> list[Any]:
        """STUN (the configured server) plus an optional TURN relay, from settings."""
        servers: list[Any] = []
        stun = str(
            get_value("network.stunServer", "stun.l.google.com:19302") or ""
        ).strip()
        if stun:
            servers.append(RTCIceServer(urls=[f"stun:{stun}"]))
        turn_url = str(get_value("network.turnUrl", "") or "").strip()
        if turn_url:
            servers.append(
                RTCIceServer(
                    urls=[turn_url],
                    username=str(get_value("network.turnUsername", "") or "") or None,
                    credential=str(get_value("network.turnCredential", "") or "")
                    or None,
                )
            )
        return servers

    def _new_pc(self) -> Any:
        return RTCPeerConnection(RTCConfiguration(iceServers=self._ice_servers()))

    # ---- dial (offerer) -----------------------------------------------------------

    async def dial(self, address: str) -> PeerLink:
        # `address` is the target peer's node_id; the signaler routes by id.
        peer_id = address
        pc = self._new_pc()
        channel = pc.createDataChannel("peer")
        opened = asyncio.Event()

        @channel.on("open")
        def _on_open() -> None:
            opened.set()

        answer_fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending_answers[peer_id] = answer_fut
        try:
            await pc.setLocalDescription(await pc.createOffer())
            await self._signal(
                peer_id, {"kind": "offer", "sdp": pc.localDescription.sdp}
            )
            answer_sdp = await asyncio.wait_for(answer_fut, NEGOTIATION_TIMEOUT_S)
        except BaseException:
            await pc.close()
            raise
        finally:
            self._pending_answers.pop(peer_id, None)

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )
        try:
            await asyncio.wait_for(opened.wait(), NEGOTIATION_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            await pc.close()
            raise LinkClosed("webrtc channel did not open")
        return WebRtcLink(pc, channel, peer_id)

    # ---- inbound (answerer) -------------------------------------------------------

    async def _on_signal(self, from_id: str, msg: dict[str, Any]) -> None:
        """Lobby `signal` callback. Kept cheap: answering an offer runs a full ICE
        negotiation, so it's scheduled rather than awaited (the lobby read loop must
        not block on it)."""
        kind = msg.get("kind")
        if kind == "offer":
            asyncio.ensure_future(
                self._answer_offer(from_id, str(msg.get("sdp") or ""))
            )
        elif kind == "answer":
            fut = self._pending_answers.get(from_id)
            if fut is not None and not fut.done():
                fut.set_result(str(msg.get("sdp") or ""))

    async def _answer_offer(self, from_id: str, offer_sdp: str) -> None:
        if self._hub is None:
            return
        pc = self._new_pc()
        loop = asyncio.get_running_loop()
        link_ready: asyncio.Future[WebRtcLink] = loop.create_future()

        @pc.on("datachannel")
        def _on_datachannel(channel: Any) -> None:
            # Build the link now so its message handler is registered before the
            # dialer's HELLO can arrive; resolve once the channel is actually open.
            link = WebRtcLink(pc, channel, from_id)

            def _ready() -> None:
                if not link_ready.done():
                    link_ready.set_result(link)

            if channel.readyState == "open":
                _ready()
            else:
                channel.on("open", _ready)

        try:
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=offer_sdp, type="offer")
            )
            await pc.setLocalDescription(await pc.createAnswer())
            await self._signal(
                from_id, {"kind": "answer", "sdp": pc.localDescription.sdp}
            )
            link = await asyncio.wait_for(link_ready, NEGOTIATION_TIMEOUT_S)
        except Exception as exc:
            logger.info("webrtc answer to %s failed: %s", from_id, exc)
            await pc.close()
            return
        await self._hub.accept_link(link)
