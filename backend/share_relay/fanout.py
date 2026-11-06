"""One ingested track, N viewers — the SFU half of the relay.

`MediaRelay` subscribes many `RTCPeerConnection`s to a single incoming track, so
the host uploads **one** copy instead of one per viewer. That is the bandwidth
win, and it is the reason a public link scales further than the fabric mesh does.

It is **not** a passthrough. aiortc decodes every incoming stream in a
`decoder_worker` thread, so what a relayed track yields is *decoded* frames. Left
to itself, every outgoing sender then holds its own `Encoder` and re-encodes them
-- **one decode plus N encodes**, which on the two-core Fly machine gave fifteen
viewers about 3 fps each. So video does not go through per-viewer encoders any
more: the room encodes once (`encoder.RoomEncoder`) and every viewer's sender
only packetizes the shared packets. The cost is one decode plus **one** encode
per room, plus a cheap packetize per viewer. Anything that wants the decoded
frames (the RTMP restream) is still nearly free to add.

That is the *CPU* story. The **memory** story is separate, was the thing that
actually killed a deployed relay, and is the reason this file is careful about
one boolean:

**Every queue on aiortc's receive path is unbounded, and nothing drops frames.**
`decoder_worker` decodes each inbound frame and `put_nowait`s it onto
`RemoteStreamTrack._queue` whether or not anybody is reading, and
`MediaRelay.subscribe()` defaults to `buffered=True`, which gives each consumer
its own unbounded `asyncio.Queue` fed the same way. So a decoded frame is only
ever freed when somebody pulls it, and *nothing anywhere applies backpressure to
the host*. A 1080p frame is about 3 MB of YUV420; at the capture's 15 fps that is
roughly 45 MB/s of pure accumulation from any consumer that is slow or absent.
The observed failure was an `oom_killed=true` exit 2m21s into a **single** share
on a 4 GB machine, which is that arithmetic almost exactly.

Two rules follow, and both are invisible if broken — the picture stays perfect
right up until the process dies:

1. **The ingest is drained from the moment it is published**, by `_pump`, and not
   only once a viewer turns up. Nothing pulls `RemoteStreamTrack._queue` until
   some proxy of the source calls `recv()`, so a host who mints a link and starts
   sharing before anyone opens it is not idling — they are filling memory at line
   rate.
2. **Video subscribers are unbuffered**, so a slow encoder skips frames instead of
   growing a queue. For live video that is not a compromise, it is the correct
   semantics: a frame that arrives late is worthless, and the alternative is
   latency that climbs forever and then an OOM. Re-encoding makes this free of
   the usual hazard — dropping *decoded* input cannot break a decoder downstream,
   because each viewer's encoder produces its own keyframes on PLI.

Audio stays buffered; see `_buffered_for`.

**Viewers do not read the relay directly for video.** The video pump writes each
decoded frame into the room's `FrameHolder`; the room's encoder reads that,
newest frame only -- which is rule 2 with one slot for the whole room -- and
fans its packets out to the viewers. The holder also
repeats the last frame when the host's still screen stops producing new ones,
because a viewer whose encoder is never handed a frame sees black: see
`frames.py` for the two ways that happened. The RTMP restream still takes a
plain unbuffered relay proxy (`proxy`), since ffmpeg times its input by count.

**A room is live once a frame has arrived, not once SDP was exchanged.** The
WHIP answer proves the relay agreed to receive; it says nothing about whether the
host's ICE ever reached it. Announcing `live` on the answer sent every viewer to
a connected peer connection with nothing behind it -- a black rectangle that
blamed the host. `on_live` fires from the pump on the first decoded frame.

`aiortc` is imported **lazily**, the same rule every optional extra in this repo
follows. The relay is deployed as its own image where it is always present, but
the node's test suite imports this module to check the token and room logic, and
a hard import would make `uv run pytest` require a native media stack nobody
needs locally.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from backend.share_relay import jitter
from backend.share_relay.encoder import RoomAudio, RoomEncoder
from backend.share_relay.frames import FrameHolder, repeat_while_idle

logger = logging.getLogger(__name__)

#: Told when a room has finished closing. A room closes for more reasons than
#: anyone asks it to -- a host whose network dropped closes it from inside
#: aiortc's own state handler -- so this is the one place every exit passes.
OnClose = Callable[["Room"], Awaitable[None]]

#: Told when a publisher's first video frame decodes -- the moment the room is
#: worth connecting to. Fires once per publish.
OnLive = Callable[["Room"], Awaitable[None]]

#: How long to wait for one peer connection to shut down before abandoning it.
#:
#: `RTCPeerConnection.close()` waits on the DTLS transport, and a peer that never
#: finished its handshake — a viewer who closed the tab mid-negotiation, a host
#: whose network dropped during the offer — never completes that wait. Without a
#: bound, tearing such a room down hangs the request that asked for it, so
#: revoking a link would hang forever exactly when someone urgently wanted it
#: revoked.
CLOSE_TIMEOUT_S = 3.0

#: How long a viewer may sit `disconnected` before its seat is reclaimed. ICE
#: often repairs a blip by itself, but a viewer who closed the lid holds a seat
#: until consent freshness gives up (~30s) -- long enough to turn a reconnecting
#: audience away from a full stream.
VIEWER_DISCONNECT_GRACE_S = 10.0


async def _close_pc(pc: Any, label: str) -> None:
    """Best-effort close. Never raises, never blocks longer than the timeout."""
    try:
        await asyncio.wait_for(pc.close(), CLOSE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("relay %s: close timed out; abandoning the connection", label)
    except Exception:  # pragma: no cover - teardown is best effort by definition
        logger.debug("relay %s: close failed", label, exc_info=True)


def _buffered_for(kind: str) -> bool:
    """Whether a subscriber of this track kind gets a queue or a latest-frame slot.

    Video: **no**. A late video frame has no value, so a slow encoder should skip
    to the newest one. Unbuffered costs exactly one frame of memory per viewer no
    matter how far behind they fall.

    Audio: **yes**, and the asymmetry is deliberate. Dropping audio frames is
    *audible* — samples vanish rather than arriving late — while a dropped video
    frame just makes the motion coarser. Audio can afford the queue because it is
    three orders of magnitude smaller (a decoded Opus frame is a few KB against a
    1080p frame's ~3 MB) and because Opus encoding is cheap enough that the
    consumer does not fall behind in the first place. If that ever stops being
    true the answer is to bound the audio queue, not to start dropping samples.
    """
    return kind == "audio"


def _aiortc() -> Any:
    """Import aiortc or explain what to install. Never a bare ImportError."""
    try:
        import aiortc  # noqa: PLC0415 — lazy by design, see the module docstring
        import aiortc.contrib.media  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "The share relay needs aiortc. Install it with `uv sync --extra webrtc`."
        ) from exc
    # Before any receiver exists: without it the newest frame of a still screen
    # is never decoded. See `jitter.py`.
    jitter.install()
    return aiortc


class Room:
    """The live media for one token: one publisher, many subscribers.

    A room exists only while a host is actually sending. The token outlives it —
    a link can be minted before the stream starts and stays valid after it stops —
    so a room is created on the WHIP offer and torn down on the WHIP delete, and
    `Registry` is what knows whether the *link* is still good.
    """

    def __init__(
        self,
        token: str,
        on_close: OnClose | None = None,
        on_live: OnLive | None = None,
    ) -> None:
        self.token = token
        self._on_close = on_close
        self._on_live = on_live
        #: The newest video frame, shared by every viewer. Room-scoped rather
        #: than publish-scoped, so viewers keep the last picture across a host
        #: re-publishing instead of being dropped and redialling.
        self._frames = FrameHolder()
        #: Encodes the held frame once for every viewer. Room-scoped like the
        #: holder, so viewers survive a host re-publishing. See `encoder.py`.
        self._encoder = RoomEncoder(self._frames)
        #: The same for audio: one Opus encode, every viewer sends its packets.
        self._audio = RoomAudio()
        #: Re-publishes the held frame while the host's screen is still.
        self._repeater: Any = None
        #: Fire-and-forget callbacks, held so they cannot be collected mid-flight.
        self._side_tasks: set[Any] = set()
        self._relay: Any = None
        self._publisher: Any = None
        #: The tracks the host is sending, by kind. A screen share is one video
        #: track plus at most one audio track; a second of either kind replaces
        #: the first rather than accumulating, because a renegotiation that added
        #: tracks forever is a slow leak nobody would notice until the machine died.
        #:
        #: These are the **source** tracks, not proxies of them. Subscribing every
        #: viewer to the source directly makes them siblings under one
        #: `MediaRelay.__run_track` loop; subscribing a proxy *of a proxy* (which
        #: is what this used to do) builds a chain whose middle link is only
        #: drained once somebody is watching the end of it.
        self._tracks: dict[str, Any] = {}
        #: One task per track, pulling the ingest so it can never pile up. See
        #: rule 1 in the module docstring -- without this a share with no viewers
        #: accumulates decoded frames at line rate.
        self._pumps: dict[str, Any] = {}
        self._subscribers: set[Any] = set()
        #: WHEP resource ids, so a viewer can give its seat back (`drop_viewer`).
        self._viewer_ids: dict[str, Any] = {}
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
            # Live again only when *this* publisher's first frame arrives.
            self._frames.reset_live()
            self._relay = MediaRelay()
            # `MAX_COMPAT`, deliberately — NOT `BALANCED` and NOT `MAX_BUNDLE`.
            # In aiortc's `setRemoteDescription`, pre-bundled transceivers share
            # `dtlsTransport` inside the m-line loop (before `a=group:BUNDLE` is
            # applied at line 989), causing `iceCandidates[iceTransport] = media`
            # to overwrite `media[0]`'s ICE candidates with the last m-line's
            # (which is empty under browser `max-bundle`, or a slave port the
            # browser closes upon BUNDLE answer). Under `BALANCED`, a second
            # track of the same kind also sets `_bundled = True` on the wrong
            # transport, so line 1005 (`not transceiver._bundled`) leaves it on a
            # stopped transport. `MAX_COMPAT` keeps `_bundled = False` during the
            # loop and lets aiortc's BUNDLE pass merge every slave m-line onto
            # `media[0]`'s primary transport cleanly.
            config = aiortc.RTCConfiguration(
                iceServers=ice_servers or [],
                bundlePolicy=aiortc.RTCBundlePolicy.MAX_COMPAT,
            )
            pc = aiortc.RTCPeerConnection(config)
            self._publisher = pc

            @pc.on("track")
            def _on_track(track: Any) -> None:
                # Hold the source and start pulling it *now*. Frames nobody pulls
                # are not dropped -- that was the assumption this code was written
                # on and it is wrong. They queue, unbounded, in
                # `RemoteStreamTrack._queue`, so an unwatched share is not idle:
                # it is the fastest way to fill the machine.
                if track.kind in self._tracks:
                    logger.warning(
                        "relay %s: duplicate %s track received; draining and ignoring",
                        self.token[:8],
                        track.kind,
                    )

                    async def _drain_extra() -> None:
                        try:
                            while True:
                                await track.recv()
                        except Exception:
                            pass

                    task = asyncio.ensure_future(_drain_extra())
                    self._side_tasks.add(task)
                    task.add_done_callback(self._side_tasks.discard)
                    return

                self._tracks[track.kind] = track
                self._start_pump(track)
                logger.info("relay %s: %s track published", self.token[:8], track.kind)

                @track.on("ended")
                async def _on_ended() -> None:
                    if self._tracks.get(track.kind) is track:
                        self._tracks.pop(track.kind, None)
                        self._stop_pump(track.kind)

            @pc.on("connectionstatechange")
            async def _on_state() -> None:
                if pc.connectionState in ("failed", "closed"):
                    await self.close()

            await pc.setRemoteDescription(
                aiortc.RTCSessionDescription(sdp=offer_sdp, type="offer")
            )
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return ingest_bitrate_hints(pc.localDescription.sdp)

    # -- keeping the ingest drained --------------------------------------------

    def _start_pump(self, track: Any) -> None:
        """Pull `track` forever, discarding what comes out.

        This looks like it does nothing, and that is roughly true -- the *point*
        is the side effect. `MediaRelay` only starts reading a source once some
        proxy of it calls `recv()`, and once started it keeps reading whether or
        not anyone consumes. So one `recv()` loop here both starts that reader and
        guarantees it never stops, which is what bounds the receiver's queue.

        The pump's own proxy is unbuffered, so the frames it discards cost one
        slot rather than a queue. The task is held in `self._pumps` because a
        bare `ensure_future` may be garbage collected mid-flight -- the classic
        asyncio disappearing-task bug, which here would silently restore the leak.
        """
        if self._relay is None or track.kind in self._pumps:
            return

        # Buffered for audio (see `_buffered_for`) -- safe here because this loop
        # never stops draining, so the queue cannot grow.
        proxy = self._relay.subscribe(track, buffered=_buffered_for(track.kind))
        video = track.kind == "video"
        if video:
            self._start_repeater()

        async def _drain() -> None:
            try:
                while True:
                    frame = await proxy.recv()
                    if video:
                        self._frame_arrived(frame)
                    else:
                        self._audio_arrived(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A source that ends raises `MediaStreamError`; that is this
                # loop's normal exit, not something to shout about.
                logger.debug("relay %s: ingest pump ended", self.token[:8])

        self._pumps[track.kind] = asyncio.ensure_future(_drain())

    def _stop_pump(self, kind: str) -> None:
        task = self._pumps.pop(kind, None)
        if task is not None:
            task.cancel()

    def _stop_pumps(self) -> None:
        for kind in list(self._pumps):
            self._stop_pump(kind)
        self._stop_repeater()

    def _start_repeater(self) -> None:
        if self._repeater is not None:
            return
        # Only worth a frame copy when somebody is encoding it.
        self._repeater = asyncio.ensure_future(
            repeat_while_idle(self._frames, lambda: bool(self._subscribers))
        )

    def _stop_repeater(self) -> None:
        if self._repeater is not None:
            self._repeater.cancel()
            self._repeater = None

    def _audio_arrived(self, frame: Any) -> None:
        try:
            self._audio.put(frame)
        except Exception:
            # Never let one odd frame end the pump: a stopped pump is the
            # unbounded-queue leak this whole file exists to prevent.
            logger.debug("relay %s: audio encode failed", self.token[:8], exc_info=True)

    def _frame_arrived(self, frame: Any) -> None:
        """One decoded video frame from the publisher: hold it, and go live on the first."""
        first = self._frames.first_at is None
        self._frames.put(frame)
        if first:
            self._went_live()

    def _went_live(self) -> None:
        logger.info("relay %s: first frame decoded", self.token[:8])
        if self._on_live is None:
            return
        task = asyncio.ensure_future(self._tell_live())
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    async def _tell_live(self) -> None:
        try:
            if self._on_live is not None:
                await self._on_live(self)
        except Exception:
            # A courtesy to viewers, exactly like `on_close`.
            logger.warning("relay %s: on_live failed", self.token[:8], exc_info=True)

    # -- subscriber side -------------------------------------------------------

    async def subscribe(
        self,
        offer_sdp: str,
        ice_servers: list[Any] | None = None,
        viewer_id: str | None = None,
    ) -> str:
        """Take a viewer's WHEP offer, return the answer SDP.

        `viewer_id` names the WHEP resource, so `drop_viewer` can end exactly
        this connection when the page says it is leaving.
        """
        aiortc = _aiortc()
        config = aiortc.RTCConfiguration(
            iceServers=ice_servers or [],
            bundlePolicy=aiortc.RTCBundlePolicy.MAX_COMPAT,
        )
        pc = aiortc.RTCPeerConnection(config)
        self._subscribers.add(pc)
        if viewer_id is not None:
            self._viewer_ids[viewer_id] = pc
        tracks: list[Any] = []
        grace: list[Any] = []

        async def _drop() -> None:
            if pc not in self._subscribers:
                return
            self._subscribers.discard(pc)
            for vid, known in list(self._viewer_ids.items()):
                if known is pc:
                    del self._viewer_ids[vid]
            for track in tracks:
                track.stop()
            await _close_pc(pc, f"{self.token[:8]} viewer")

        async def _reclaim_if_still_down() -> None:
            await asyncio.sleep(VIEWER_DISCONNECT_GRACE_S)
            if pc.connectionState == "disconnected":
                logger.info("relay %s: reclaiming a stalled viewer", self.token[:8])
                await _drop()

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            for task in grace:
                task.cancel()
            grace.clear()
            if pc.connectionState in ("failed", "closed"):
                await _drop()
            elif pc.connectionState == "disconnected":
                task = asyncio.ensure_future(_reclaim_if_still_down())
                grace.append(task)
                self._side_tasks.add(task)
                task.add_done_callback(self._side_tasks.discard)

        # Video is the room's shared encode: one `PacketTrack` per viewer, all
        # fed the same packets. See `encoder.py` for why this is the whole
        # capacity story, and for the keyframe rules sharing packets imposes.
        #
        # Audio is shared the same way (`RoomAudio`): one Opus encode per room.
        # Per-viewer Opus encodes in the default executor were what throttled
        # the shared video encode at 25 viewers.
        for kind, track in list(self._tracks.items()):
            if kind == "video":
                video = self._encoder.subscribe()
                tracks.append(video)
                sender = pc.addTrack(video)
                # aiortc ignores its own keyframe flag for pre-encoded packets,
                # so a viewer's PLI has to reach the encoder that makes them.
                sender._send_keyframe = self._encoder.request_keyframe
                _pin_codec(aiortc, pc, sender, "video/vp8")
            elif kind == "audio":
                audio = self._audio.subscribe()
                tracks.append(audio)
                _pin_codec(aiortc, pc, pc.addTrack(audio), "audio/opus")

        try:
            await pc.setRemoteDescription(
                aiortc.RTCSessionDescription(sdp=offer_sdp, type="offer")
            )
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception:
            await _drop()
            raise
        return pc.localDescription.sdp

    async def drop_viewer(self, viewer_id: str) -> bool:
        """End one viewer's connection, freeing its seat now rather than in 30s."""
        pc = self._viewer_ids.pop(viewer_id, None)
        if pc is None:
            return False
        # Closing fires `connectionstatechange` -> `closed`, which runs the same
        # cleanup as any other exit; discarding first keeps the seat count
        # honest even if that close times out.
        self._subscribers.discard(pc)
        await _close_pc(pc, f"{self.token[:8]} viewer")
        return True
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
        return self._relay.subscribe(track, buffered=_buffered_for(kind))

    @property
    def viewers(self) -> int:
        return len(self._subscribers)

    def encoder_stats(self) -> dict[str, Any]:
        return self._encoder.stats()

    @property
    def live(self) -> bool:
        """A publisher is connected *and* its video has produced a frame.

        Tracks alone exist from the moment the SDP is applied, long before -- or
        without ever -- a packet crossing the host's ICE path.
        """
        return bool(self._tracks) and self._frames.first_at is not None

    @property
    def publishing(self) -> bool:
        """A publisher has negotiated, whether or not media has arrived yet."""
        return bool(self._tracks)

    @property
    def last_frame_age(self) -> float | None:
        """Seconds since the host's last real frame; None before the first.

        Informational: a still screen legitimately sends few frames, so a large
        age is not by itself a fault.
        """
        last = self._frames.last_at
        return None if last is None else max(0.0, time.monotonic() - last)

    # -- teardown --------------------------------------------------------------

    async def _close_publisher(self) -> None:
        # Pumps first: each holds a proxy of a track that is about to go away, and
        # cancelling them is the only thing that lets the relay forget the source.
        self._stop_pumps()
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
        # After the pumps stop, so nothing puts into a closed holder; this ends
        # every viewer's video track with `MediaStreamError`.
        self._frames.close()
        self._encoder.stop()
        self._audio.stop()
        subscribers = list(self._subscribers)
        self._subscribers.clear()
        self._viewer_ids.clear()
        await asyncio.gather(
            *(_close_pc(pc, f"{self.token[:8]} viewer") for pc in subscribers)
        )
        if self._on_close is not None:
            try:
                await self._on_close(self)
            except Exception:
                # Telling people the stream stopped is a courtesy; it must not
                # turn a clean teardown into a failed one.
                logger.warning(
                    "relay %s: on_close failed", self.token[:8], exc_info=True
                )


#: Floor and starting point for the host's upload, in kbps. See
#: `ingest_bitrate_hints`.
DEFAULT_INGEST_MIN_KBPS = 1000
DEFAULT_INGEST_START_KBPS = 2500
DEFAULT_INGEST_MAX_KBPS = 8000

_VIDEO_CODECS = ("VP8", "VP9", "H264", "AV1")


def ingest_bitrate_hints(sdp: str) -> str:
    """Tell the host's Chrome not to starve the upload, via the WHIP answer.

    aiortc estimates the host's bandwidth by REMB alone, and its estimate tracks
    about 1.5x whatever is *currently* arriving. A desktop that is mostly still
    sends very little, so the estimate settles low -- measured at 53 kbps with
    the host's encoder reporting `qualityLimitationReason: bandwidth` -- and when
    the screen then changes (a scroll, a new window) the host has no budget for
    it. Every viewer gets the smear, however well the relay encodes.

    `x-google-min-bitrate` / `x-google-start-bitrate` on the video codecs' fmtp
    is how an SFU tells Chrome where its estimate may not drop below and where to
    start. Chrome reads them from the remote description; other browsers ignore
    unknown fmtp parameters. Only the *answer we return* is rewritten: aiortc
    has already applied its own copy.
    """
    min_kbps = _env_kbps("SHARE_RELAY_INGEST_MIN_KBPS", DEFAULT_INGEST_MIN_KBPS)
    start_kbps = max(min_kbps, DEFAULT_INGEST_START_KBPS)
    max_kbps = max(start_kbps, DEFAULT_INGEST_MAX_KBPS)
    hint = (
        f"x-google-min-bitrate={min_kbps};x-google-start-bitrate={start_kbps};"
        f"x-google-max-bitrate={max_kbps}"
    )
    eol = "\r\n" if "\r\n" in sdp else "\n"
    lines = sdp.split(eol)
    out: list[str] = []
    video = False
    codec_pts: set[str] = set()
    has_fmtp: set[str] = set()
    for line in lines:
        if line.startswith("m="):
            video = line.startswith("m=video")
            codec_pts = set()
        elif video and line.startswith("a=rtpmap:"):
            pt, _, rest = line[len("a=rtpmap:") :].partition(" ")
            if rest.split("/")[0].upper() in _VIDEO_CODECS:
                codec_pts.add(pt)
        elif video and line.startswith("a=fmtp:"):
            pt, _, params = line[len("a=fmtp:") :].partition(" ")
            if pt in codec_pts and "x-google-min-bitrate" not in params:
                line = f"a=fmtp:{pt} {params};{hint}" if params else f"a=fmtp:{pt} {hint}"
                has_fmtp.add(pt)
        out.append(line)
    # Codecs with no fmtp line at all (aiortc writes none for VP8) get one,
    # placed right after their rtpmap.
    final: list[str] = []
    video = False
    for line in out:
        final.append(line)
        if line.startswith("m="):
            video = line.startswith("m=video")
        elif video and line.startswith("a=rtpmap:"):
            pt, _, rest = line[len("a=rtpmap:") :].partition(" ")
            if rest.split("/")[0].upper() in _VIDEO_CODECS and pt not in has_fmtp:
                if not any(o.startswith(f"a=fmtp:{pt} ") for o in out):
                    final.append(f"a=fmtp:{pt} {hint}")
    return eol.join(final)


def _env_kbps(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(100, int(raw)) if raw else default
    except ValueError:
        return default


def _pin_codec(aiortc: Any, pc: Any, sender: Any, mime: str) -> None:
    """Negotiate this sender onto the one codec the room encodes in.

    VP8 and Opus: every browser offers both. A sender left free to pick H.264
    would hand VP8 packets to aiortc's H.264 packetizer, which splits them as NAL
    units -- a connected viewer with a garbage picture and no error anywhere.
    """
    kind = mime.split("/")[0]
    caps = aiortc.RTCRtpSender.getCapabilities(kind).codecs
    keep = (mime, f"{kind}/rtx")
    prefs = [c for c in caps if c.mimeType.lower() in keep]
    for transceiver in pc.getTransceivers():
        if transceiver.sender is sender:
            transceiver.setCodecPreferences(prefs)
            return


class Rooms:
    """Every live room on this process, keyed by token.

    `on_close` hears about every room that closes, however it closed. The table
    also forgets a room the moment it closes, and that is not tidiness: a room
    that closed *itself* (its publisher's connection failed) used to stay here
    with `_closing` latched, so the host's next WHIP landed in a room whose
    `close()` was a no-op forever and whose viewers could never be dropped.
    """

    def __init__(
        self,
        on_close: Callable[[str], Awaitable[None]] | None = None,
        on_live: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._rooms: dict[str, Room] = {}
        self._on_close = on_close
        self._on_live = on_live

    async def _closed(self, room: Room) -> None:
        # Only if it is still *this* room: a fresh one may already hold the token.
        if self._rooms.get(room.token) is room:
            del self._rooms[room.token]
        if self._on_close is not None:
            await self._on_close(room.token)

    async def _live(self, room: Room) -> None:
        if self._rooms.get(room.token) is room and self._on_live is not None:
            await self._on_live(room.token)

    def get_or_create(self, token: str) -> Room:
        room = self._rooms.get(token)
        if room is None:
            room = Room(token, on_close=self._closed, on_live=self._live)
            self._rooms[token] = room
        return room

    def get(self, token: str) -> Room | None:
        return self._rooms.get(token)

    def all(self) -> list[Room]:
        return list(self._rooms.values())

    async def drop(self, token: str) -> None:
        room = self._rooms.pop(token, None)
        if room is not None:
            await room.close()

    async def close_all(self) -> None:
        for token in list(self._rooms):
            await self.drop(token)

    def __len__(self) -> int:
        return len(self._rooms)
