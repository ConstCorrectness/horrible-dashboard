"""One ingested track, N viewers — the SFU half of the relay.

`MediaRelay` subscribes many `RTCPeerConnection`s to a single incoming track, so
the host uploads **one** copy instead of one per viewer. That is the bandwidth
win, and it is the reason a public link scales further than the fabric mesh does.

It is **not** a passthrough, and assuming it is will size a machine wrong.
aiortc decodes every incoming stream in a `decoder_worker` thread, so what a
relayed track yields is *decoded* frames, and every outgoing sender holds its own
`Encoder` and re-encodes them. The real cost is therefore **one decode plus N
encodes**, which is where the viewer ceiling comes from — the CPU, not the
network. Anything that wants the frames (the RTMP restream in Phase 6) is
consequently nearly free to add: the decode has already happened.

`aiortc` is imported **lazily**, the same rule every optional extra in this repo
follows. The relay is deployed as its own image where it is always present, but
the node's test suite imports this module to check the token and room logic, and
a hard import would make `uv run pytest` require a native media stack nobody
needs locally.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: How long to wait for one peer connection to shut down before abandoning it.
#:
#: `RTCPeerConnection.close()` waits on the DTLS transport, and a peer that never
#: finished its handshake — a viewer who closed the tab mid-negotiation, a host
#: whose network dropped during the offer — never completes that wait. Without a
#: bound, tearing such a room down hangs the request that asked for it, so
#: revoking a link would hang forever exactly when someone urgently wanted it
#: revoked.
CLOSE_TIMEOUT_S = 3.0


async def _close_pc(pc: Any, label: str) -> None:
    """Best-effort close. Never raises, never blocks longer than the timeout."""
    try:
        await asyncio.wait_for(pc.close(), CLOSE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("relay %s: close timed out; abandoning the connection", label)
    except Exception:  # pragma: no cover - teardown is best effort by definition
        logger.debug("relay %s: close failed", label, exc_info=True)


def _aiortc() -> Any:
    """Import aiortc or explain what to install. Never a bare ImportError."""
    try:
        import aiortc  # noqa: PLC0415 — lazy by design, see the module docstring
        import aiortc.contrib.media  # noqa: PLC0415

        return aiortc
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "The share relay needs aiortc. Install it with `uv sync --extra webrtc`."
        ) from exc


class Room:
    """The live media for one token: one publisher, many subscribers.

    A room exists only while a host is actually sending. The token outlives it —
    a link can be minted before the stream starts and stays valid after it stops —
    so a room is created on the WHIP offer and torn down on the WHIP delete, and
    `Registry` is what knows whether the *link* is still good.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self._relay: Any = None
        self._publisher: Any = None
        #: The tracks the host is sending, by kind. A screen share is one video
        #: track plus at most one audio track; a second of either kind replaces
        #: the first rather than accumulating, because a renegotiation that added
        #: tracks forever is a slow leak nobody would notice until the machine died.
        self._tracks: dict[str, Any] = {}
        self._subscribers: set[Any] = set()
        self._lock = asyncio.Lock()
        # Closing a peer connection fires its own `connectionstatechange`, whose
        # handler closes the room — so a plain teardown re-enters itself and pays
        # every timeout twice. The flag is the base case.
        self._closing = False

    # -- publisher side --------------------------------------------------------

    async def publish(
        self, offer_sdp: str, ice_servers: list[Any] | None = None
    ) -> str:
        """Take the host's WHIP offer, return the answer SDP."""
        aiortc = _aiortc()
        from aiortc.contrib.media import MediaRelay  # noqa: PLC0415

        async with self._lock:
            await self._close_publisher()
            self._relay = MediaRelay()
            config = aiortc.RTCConfiguration(iceServers=ice_servers or [])
            pc = aiortc.RTCPeerConnection(config)
            self._publisher = pc

            @pc.on("track")
            def _on_track(track: Any) -> None:
                # Subscribe through the relay immediately. A track that nobody has
                # subscribed to is not pulled, so its frames are simply dropped —
                # which shows up later as "the first viewer sees nothing until the
                # second one joins", a genuinely baffling bug.
                self._tracks[track.kind] = self._relay.subscribe(track)
                logger.info("relay %s: %s track published", self.token[:8], track.kind)

                @track.on("ended")
                async def _on_ended() -> None:
                    self._tracks.pop(track.kind, None)

            @pc.on("connectionstatechange")
            async def _on_state() -> None:
                if pc.connectionState in ("failed", "closed"):
                    await self.close()

            await pc.setRemoteDescription(
                aiortc.RTCSessionDescription(sdp=offer_sdp, type="offer")
            )
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return pc.localDescription.sdp

    # -- subscriber side -------------------------------------------------------

    async def subscribe(
        self, offer_sdp: str, ice_servers: list[Any] | None = None
    ) -> str:
        """Take a viewer's WHEP offer, return the answer SDP."""
        aiortc = _aiortc()
        config = aiortc.RTCConfiguration(iceServers=ice_servers or [])
        pc = aiortc.RTCPeerConnection(config)
        self._subscribers.add(pc)

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            if pc.connectionState in ("failed", "closed"):
                self._subscribers.discard(pc)
                await _close_pc(pc, f"{self.token[:8]} viewer")

        # Every subscriber gets its own `relay.subscribe()` proxy of the same
        # source track. Adding the *source* track to two peer connections is the
        # classic aiortc mistake: the second consumer steals frames from the first
        # and both stutter.
        for track in list(self._tracks.values()):
            pc.addTrack(self._relay.subscribe(track) if self._relay else track)

        await pc.setRemoteDescription(
            aiortc.RTCSessionDescription(sdp=offer_sdp, type="offer")
        )
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription.sdp

    def proxy(self, kind: str = "video") -> Any:
        """A fresh relay proxy of one of the published tracks, or None.

        For consumers that are not peer connections -- the RTMP restream. It must
        be a *new* `relay.subscribe()` proxy and never the source track: two
        consumers pulling the same source steal frames from each other, so a
        restream sharing the viewers' track would make both stutter.
        """
        track = self._tracks.get(kind)
        if track is None or self._relay is None:
            return None
        return self._relay.subscribe(track)

    @property
    def viewers(self) -> int:
        return len(self._subscribers)

    @property
    def live(self) -> bool:
        return bool(self._tracks)

    # -- teardown --------------------------------------------------------------

    async def _close_publisher(self) -> None:
        if self._publisher is not None:
            await _close_pc(self._publisher, f"{self.token[:8]} publisher")
            self._publisher = None
        self._tracks.clear()

    async def close(self) -> None:
        """Drop the publisher and every viewer.

        Viewers are closed too, deliberately. Leaving them attached to a dead
        room gives each of them a peer connection that stays 'connected' and
        renders a frozen final frame — the same failure the host-side stop path
        avoids by sending an explicit `bye`.

        Closed **concurrently** and each under its own timeout, so a room with
        twenty viewers does not spend twenty timeouts in a row shutting down.
        """
        if self._closing:
            return
        self._closing = True
        await self._close_publisher()
        subscribers = list(self._subscribers)
        self._subscribers.clear()
        await asyncio.gather(
            *(_close_pc(pc, f"{self.token[:8]} viewer") for pc in subscribers)
        )


class Rooms:
    """Every live room on this process, keyed by token."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def get_or_create(self, token: str) -> Room:
        room = self._rooms.get(token)
        if room is None:
            room = Room(token)
            self._rooms[token] = room
        return room

    def get(self, token: str) -> Room | None:
        return self._rooms.get(token)

    async def drop(self, token: str) -> None:
        room = self._rooms.pop(token, None)
        if room is not None:
            await room.close()

    async def close_all(self) -> None:
        for token in list(self._rooms):
            await self.drop(token)

    def __len__(self) -> int:
        return len(self._rooms)
