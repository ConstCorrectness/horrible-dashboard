"""Encode a room's video **once**, and hand every viewer the same packets.

The relay used to give each viewer a decoded-frame track, and aiortc gives every
`RTCRtpSender` its own `Vp8Encoder` -- so N viewers meant N full-resolution
software encodes of the *same* frame. Measured on two cores (the Fly machine):
fifteen viewers of a 1080p share each got about 3 fps of the host's 15, with the
CPU pinned. That is what "laggy" and "stuck connecting" were: the encoders
starved the event loop that ICE and DTLS run on.

So a room has one `RoomEncoder`. It takes the newest frame from the room's
`FrameHolder`, encodes it once, and pushes the resulting `av.Packet` to every
viewer's `PacketTrack`. aiortc's sender sees a packet rather than a frame and
takes its passthrough path -- `encoder.pack()`, which only packetizes. The cost
is one decode plus one encode per room, whatever the audience.

Sharing packets changes the rules frame-skipping relied on, and these are the
ones that matter:

- **A viewer must start on a keyframe.** A decoder handed a delta frame first
  shows nothing (or garbage) until the next keyframe. A new `PacketTrack`
  discards packets until one arrives, and subscribing asks for one.
- **A viewer may not skip encoded frames.** Dropping a *decoded* frame was free,
  because each viewer's encoder made its own reference chain. Dropping an
  *encoded* delta frame breaks every frame after it. So a viewer that falls
  behind drops everything up to the next keyframe and asks for one -- never a
  single frame from the middle.
- **Keyframe requests are the room's, not the viewer's.** aiortc ignores its
  own force-keyframe flag on the passthrough path, so `Room.subscribe` points
  each sender's PLI handler here. Requests are coalesced to at most one keyframe
  per `MIN_KEYFRAME_GAP_S`: fifteen viewers recovering from the same loss burst
  must not cost fifteen keyframes.
- **One bitrate for everyone.** A viewer's REMB no longer retunes anything,
  which is the point: aiortc's `Vp8Encoder` rebuilt its codec (and emitted a
  fresh keyframe) on every 10% bitrate swing, per viewer, and clamped the rate to
  1.5 Mbps -- unreadable for a desktop. Here the operator picks the rate.

Viewers are pinned to VP8 (`Room.subscribe` sets codec preferences), because the
packets are VP8 and a sender negotiated onto H.264 would packetize them as
nonsense.

Like everything media-related in this package, PyAV and aiortc are imported
lazily, so the room and token logic can be tested without a media stack.
"""

from __future__ import annotations

import asyncio
import collections
import functools
import logging
import os
import statistics
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from typing import Any

from backend.share_relay.frames import (
    VIDEO_TIME_BASE,
    FrameHolder,
    HolderClosed,
    copy_frame,
)

logger = logging.getLogger(__name__)

#: Default video bitrate. A desktop is mostly sharp edges on flat colour, and at
#: aiortc's 1.5 Mbps cap 1080p text smeared into mush. 3 Mbps is comfortable for
#: any home connection and fifteen of them is ~45 Mbps of relay egress.
DEFAULT_VIDEO_KBPS = 3000

#: Frames taller than this are scaled down once, before the single encode. A 4K
#: share costs four times the encode of a 1080p one and few viewers' windows can
#: show the difference.
DEFAULT_MAX_HEIGHT = 1080

#: A keyframe at least this often even if nobody asks. Bounds how long a viewer
#: whose PLI was lost (RTCP is unreliable too) stays broken.
KEYFRAME_INTERVAL_S = 8.0

#: Coalesce keyframe requests: no two forced keyframes closer than this.
MIN_KEYFRAME_GAP_S = 1.0

#: Encoded frames a viewer may have waiting before it is declared behind. At 15
#: fps this is two seconds; a sender only packetizes, so a healthy one never
#: gets close.
VIEWER_QUEUE_MAX = 30


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("%s=%r is not a number; using %d", name, raw, default)
        return default


def video_bitrate() -> int:
    """Bits per second, from `SHARE_RELAY_VIDEO_KBPS`."""
    return _env_int("SHARE_RELAY_VIDEO_KBPS", DEFAULT_VIDEO_KBPS, 200) * 1000


def max_height() -> int:
    """Tallest frame encoded as-is, from `SHARE_RELAY_MAX_HEIGHT`."""
    return _env_int("SHARE_RELAY_MAX_HEIGHT", DEFAULT_MAX_HEIGHT, 144)


def scaled_size(width: int, height: int, limit: int) -> tuple[int, int]:
    """`(width, height)` fitted under `limit` lines, both even (VP8/yuv420p need it)."""
    if height > limit:
        width = round(width * limit / height)
        height = limit
    return max(2, width - width % 2), max(2, height - height % 2)


class Encoded:
    """One encoded frame, shared read-only by every viewer that sends it."""

    __slots__ = ("key", "packet")

    def __init__(self, packet: Any, key: bool) -> None:
        self.packet = packet
        self.key = key


class Vp8:
    """The single libvpx encoder of a room. Runs in an executor thread.

    Not aiortc's `Vp8Encoder`: that one is built for one peer -- it clamps to
    1.5 Mbps and rebuilds itself whenever its viewer's bandwidth estimate moves.
    """

    def __init__(self, bitrate: int, limit: int) -> None:
        self.bitrate = bitrate
        self.limit = limit
        self._codec: Any = None
        self._size: tuple[int, int] | None = None

    def _open(self, width: int, height: int) -> Any:
        import av  # noqa: PLC0415 - lazy by design, see module docstring

        codec = av.CodecContext.create("libvpx", "w")
        codec.width = width
        codec.height = height
        codec.pix_fmt = "yuv420p"
        codec.time_base = VIDEO_TIME_BASE
        codec.bit_rate = self.bitrate
        # Keyframes are forced explicitly (on request and on the interval), so
        # the codec's own GOP never fires one at a moment nobody chose.
        codec.gop_size = 100_000
        codec.qmin = 2
        codec.qmax = 50
        codec.thread_count = min(4, os.cpu_count() or 1)
        codec.options = {
            "deadline": "realtime",
            "cpu-used": "8",
            "lag-in-frames": "0",
            # CBR, so a keyframe of a busy screen cannot burst past what the
            # viewers' links were sized for. ffmpeg sets rc_buf_sz from bufsize.
            "minrate": str(self.bitrate),
            "maxrate": str(self.bitrate),
            "bufsize": str(self.bitrate),
            "undershoot-pct": "100",
            "overshoot-pct": "15",
            # Screen content: no denoising (it eats text), skip static blocks.
            "noise-sensitivity": "0",
            "static-thresh": "1",
            "screen-content-mode": "1",
            "partitions": "0",
            "error-resilient": "1",
        }
        return codec

    def encode(self, frame: Any, key: bool) -> Encoded | None:
        import av  # noqa: PLC0415

        size = scaled_size(frame.width, frame.height, self.limit)
        if size != (frame.width, frame.height) or frame.format.name != "yuv420p":
            src = frame.reformat(width=size[0], height=size[1], format="yuv420p")
        elif key:
            # Setting `pict_type` writes to the frame; never to the shared one.
            src = copy_frame(frame)
        else:
            src = frame
        src.pts = frame.pts
        src.time_base = VIDEO_TIME_BASE

        if self._codec is None or self._size != size:
            self._codec = self._open(*size)
            self._size = size
            key = True
        src.pict_type = (
            av.video.frame.PictureType.I if key else av.video.frame.PictureType.NONE
        )

        packets = self._codec.encode(src)
        if not packets:
            return None
        if len(packets) == 1:
            packet = packets[0]
        else:  # pragma: no cover - lag-in-frames 0 yields one packet per frame
            packet = av.Packet(b"".join(bytes(p) for p in packets))
        is_key = any(p.is_keyframe for p in packets) or key
        packet.pts = frame.pts
        packet.time_base = VIDEO_TIME_BASE
        return Encoded(packet, is_key)


class RoomEncoder:
    """Encode the room's newest frame once per change; fan the packet out.

    Encodes only while somebody is subscribed: an unwatched share costs its
    decode and nothing more.
    """

    def __init__(
        self,
        holder: FrameHolder,
        *,
        bitrate: int | None = None,
        limit: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        encode: Callable[[Any, bool], Encoded | None] | None = None,
    ) -> None:
        self._holder = holder
        self._clock = clock
        self._vp8 = Vp8(bitrate or video_bitrate(), limit or max_height())
        self._encode = encode or self._vp8.encode
        self._outputs: set[Any] = set()
        self._task: asyncio.Task | None = None
        #: The encode's own thread. Not the loop's default executor: anything
        #: else queued there (aiortc uses it too) would sit in front of the one
        #: job every viewer is waiting on.
        self._pool: ThreadPoolExecutor | None = None
        self._kick = asyncio.Event()
        self._want_key = False
        self._last_key_at = -1e9
        #: Recent encode durations in ms, for `/health`.
        self.encode_ms: collections.deque[float] = collections.deque(maxlen=120)
        self.frames = 0
        self.keyframes = 0

    # -- viewers ---------------------------------------------------------------

    def subscribe(self) -> Any:
        """A new viewer's video track. Starts at a keyframe, asked for on its first pull."""
        track = _packet_track_class()(self)
        self._outputs.add(track)
        self.start()
        return track

    def unsubscribe(self, track: Any) -> None:
        self._outputs.discard(track)

    @property
    def viewers(self) -> int:
        return len(self._outputs)

    def request_keyframe(self) -> None:
        """Ask for a keyframe soon. Coalesced; safe to call from every viewer's PLI."""
        self._want_key = True
        self._kick.set()

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(1, thread_name_prefix="relay-vp8")
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        for track in list(self._outputs):
            track.stop()
        self._outputs.clear()

    def stats(self) -> dict[str, Any]:
        recent = list(self.encode_ms)
        return {
            "viewers": len(self._outputs),
            "frames": self.frames,
            "keyframes": self.keyframes,
            "encode_ms_p50": round(statistics.median(recent), 1) if recent else None,
            "encode_ms_max": round(max(recent), 1) if recent else None,
        }

    # -- the loop --------------------------------------------------------------

    def _key_due(self, now: float) -> float | None:
        """Seconds until a keyframe may be forced; None when none is wanted."""
        wanted = self._want_key or now - self._last_key_at >= KEYFRAME_INTERVAL_S
        if not wanted:
            return None
        return max(0.0, self._last_key_at + MIN_KEYFRAME_GAP_S - now)

    async def _wait(self, seen: int) -> None:
        """Until there is a newer frame, or a pending keyframe is allowed."""
        if self._holder.seq > seen:
            return
        # A keyframe is only worth waking for when somebody will receive it;
        # without this an unwatched room with one pending spins.
        due = self._key_due(self._clock()) if self._outputs else None
        if due == 0.0 and self._holder.frame is not None:
            return
        self._kick.clear()
        newer = asyncio.ensure_future(self._holder.wait_newer(seen))
        kick = asyncio.ensure_future(self._kick.wait())
        try:
            done, _ = await asyncio.wait(
                {newer, kick}, timeout=due, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            newer.cancel()
            kick.cancel()
        if newer in done and not newer.cancelled():
            newer.result()  # re-raises HolderClosed

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        seen = 0
        try:
            while True:
                await self._wait(seen)
                frame, seq = self._holder.frame, self._holder.seq
                if frame is None:
                    seen = seq
                    continue
                if not self._outputs:
                    # Nobody to send to: stay current without spending an encode.
                    seen = seq
                    continue
                now = self._clock()
                due = self._key_due(now)
                key = due == 0.0
                if seq == seen and not key:
                    continue  # woken for a keyframe that is not allowed yet
                started = time.perf_counter()
                try:
                    encoded = await loop.run_in_executor(
                        self._pool, self._encode, frame, key
                    )
                except Exception:
                    logger.warning("relay encoder: encode failed", exc_info=True)
                    seen = seq
                    await asyncio.sleep(0.1)
                    continue
                self.encode_ms.append((time.perf_counter() - started) * 1000)
                seen = seq
                if encoded is None:
                    continue
                self.frames += 1
                if encoded.key:
                    self.keyframes += 1
                    self._last_key_at = self._clock()
                    self._want_key = False
                for track in list(self._outputs):
                    track.push(encoded)
        except HolderClosed:
            pass
        finally:
            for track in list(self._outputs):
                track.stop()


@functools.cache
def _packet_track_class() -> Any:
    """Built on first use so importing this module never needs aiortc."""
    from aiortc.mediastreams import (  # noqa: PLC0415 - lazy by design
        MediaStreamError,
        MediaStreamTrack,
    )

    class PacketTrack(MediaStreamTrack):
        """One viewer's video: the room's encoded packets, from a keyframe on."""

        kind = "video"

        def __init__(self, encoder: RoomEncoder) -> None:
            super().__init__()
            self._encoder = encoder
            self._queue: collections.deque[Any] = collections.deque()
            self._ready = asyncio.Event()
            self._need_key = True
            #: Whether the sender has started pulling. Nothing is queued before
            #: that: a viewer still in its ICE/DTLS handshake would otherwise
            #: start on a backlog seconds old.
            self._pulling = False
            self.dropped = 0

        def push(self, encoded: Encoded) -> None:
            if self.readyState != "live" or not self._pulling:
                return
            if len(self._queue) >= VIEWER_QUEUE_MAX:
                # Behind. Never skip one delta frame -- the decoder downstream
                # would break -- so drop the lot and resume at a keyframe.
                self.dropped += len(self._queue)
                self._queue.clear()
                self._need_key = True
                self._encoder.request_keyframe()
            if self._need_key:
                if not encoded.key:
                    return
                self._need_key = False
            self._queue.append(encoded.packet)
            self._ready.set()

        async def recv(self) -> Any:
            if not self._pulling:
                # The sender is ready: this is the moment a keyframe is useful.
                self._pulling = True
                self._encoder.request_keyframe()
            while not self._queue:
                if self.readyState != "live":
                    raise MediaStreamError
                self._ready.clear()
                await self._ready.wait()
            return self._queue.popleft()

        def stop(self) -> None:
            super().stop()
            self._encoder.unsubscribe(self)
            self._queue.clear()
            # Wake a pending `recv()` so it sees the track ended.
            self._ready.set()

    return PacketTrack


# -- audio ---------------------------------------------------------------------

#: Opus runs at 48 kHz in 20 ms frames, which is what aiortc's packetizer and
#: every browser expect.
AUDIO_TIME_BASE = Fraction(1, 48000)

#: Encoded audio packets a viewer may have waiting (one second). Audio has no
#: keyframes, so a viewer that falls behind simply loses its oldest packets.
AUDIO_QUEUE_MAX = 50


class RoomAudio:
    """Encode the host's audio once and hand every viewer the same Opus packets.

    The same reasoning as `RoomEncoder`, and measured, not assumed: with video
    shared, twenty-five viewers ran at full frame rate on two cores -- until the
    publisher carried audio. Each viewer's sender then Opus-encoded the same
    samples in the default executor, about 1,250 jobs a second, and the single
    video encode queued behind them (median 15ms -> 83ms) until every viewer was
    down to 2 fps.

    Encodes for as long as the host sends audio, watched or not: Opus is cheap,
    and a continuous sample clock keeps the RTP timestamps honest across
    viewers coming and going.
    """

    def __init__(self) -> None:
        self._outputs: set[Any] = set()
        self._resampler: Any = None
        self._codec: Any = None
        self._samples = 0
        self.packets = 0

    def subscribe(self) -> Any:
        track = _audio_track_class()(self)
        self._outputs.add(track)
        return track

    def unsubscribe(self, track: Any) -> None:
        self._outputs.discard(track)

    def _open(self) -> None:
        import av  # noqa: PLC0415 - lazy by design
        from aiortc.codecs.opus import OpusEncoder  # noqa: PLC0415

        # aiortc's own encoder is built exactly for this (s16 stereo, 48 kHz,
        # 960-sample frames); borrowing its resampler and codec keeps the
        # packets byte-identical to what a per-viewer sender used to make.
        template = OpusEncoder()
        self._resampler = template.resampler
        self._codec = template.codec
        self._av = av

    def put(self, frame: Any) -> None:
        """One decoded audio frame from the host. Cheap enough for the loop thread."""
        if self._codec is None:
            self._open()
        if frame.format.name != "s16" or frame.layout.name not in ("mono", "stereo"):
            frame = frame.reformat(format="s16", layout="stereo")
        for chunk in self._resampler.resample(frame):
            for packet in self._codec.encode(chunk):
                packet.pts = self._samples
                packet.time_base = AUDIO_TIME_BASE
                self._samples += chunk.samples
                self.packets += 1
                for track in list(self._outputs):
                    track.push(packet)

    def stop(self) -> None:
        for track in list(self._outputs):
            track.stop()
        self._outputs.clear()


@functools.cache
def _audio_track_class() -> Any:
    from aiortc.mediastreams import (  # noqa: PLC0415 - lazy by design
        MediaStreamError,
        MediaStreamTrack,
    )

    class AudioPacketTrack(MediaStreamTrack):
        """One viewer's audio: the room's Opus packets, newest second only."""

        kind = "audio"

        def __init__(self, audio: RoomAudio) -> None:
            super().__init__()
            self._audio = audio
            self._queue: collections.deque[Any] = collections.deque(
                maxlen=AUDIO_QUEUE_MAX
            )
            self._ready = asyncio.Event()
            #: As for video: queue nothing until the sender starts pulling.
            self._pulling = False

        def push(self, packet: Any) -> None:
            if self.readyState != "live" or not self._pulling:
                return
            self._queue.append(packet)
            self._ready.set()

        async def recv(self) -> Any:
            self._pulling = True
            while not self._queue:
                if self.readyState != "live":
                    raise MediaStreamError
                self._ready.clear()
                await self._ready.wait()
            return self._queue.popleft()

        def stop(self) -> None:
            super().stop()
            self._audio.unsubscribe(self)
            self._queue.clear()
            self._ready.set()

    return AudioPacketTrack
