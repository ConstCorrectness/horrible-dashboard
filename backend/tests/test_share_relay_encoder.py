"""The room's shared encoder: one encode per frame, whatever the audience.

These pin the rules that sharing *encoded* packets imposes (see `encoder.py`):
a viewer starts on a keyframe, never skips a single delta frame, and keyframe
requests from many viewers coalesce into one. Most tests drive `RoomEncoder`
with a fake encode so they test the fan-out, not libvpx; one test runs the real
codec, and one runs a real publisher and real viewers through a `Room`.
"""

from __future__ import annotations

import asyncio
import time

import pytest

aiortc = pytest.importorskip("aiortc", reason="the relay's media path needs aiortc")

from backend.share_relay import encoder as enc  # noqa: E402
from backend.share_relay.encoder import (  # noqa: E402
    Encoded,
    RoomAudio,
    RoomEncoder,
    Vp8,
    scaled_size,
)
from backend.share_relay.frames import FrameHolder  # noqa: E402


class FakeCodec:
    """Counts encodes; a keyframe exactly when asked (or on the first frame)."""

    def __init__(self) -> None:
        self.calls = 0
        self.keys = 0

    def __call__(self, frame: object, key: bool) -> Encoded:
        self.calls += 1
        key = key or self.calls == 1
        self.keys += key
        return Encoded(packet=(self.calls, key), key=key)


async def _pulling(tracks: list) -> list[asyncio.Task]:
    """Start each track's sender-side pull, as aiortc's RTP loop would."""
    pulls = [asyncio.ensure_future(t.recv()) for t in tracks]
    await asyncio.sleep(0.01)
    return pulls


async def _drain(track, timeout: float = 0.5) -> list:
    got = []
    while True:
        try:
            got.append(await asyncio.wait_for(track.recv(), timeout))
        except TimeoutError:
            return got


@pytest.mark.anyio
async def test_fifteen_viewers_cost_one_encode_per_frame() -> None:
    holder = FrameHolder()
    codec = FakeCodec()
    room = RoomEncoder(holder, encode=codec)
    tracks = [room.subscribe() for _ in range(15)]
    pulls = await _pulling(tracks)
    try:
        for n in range(5):
            holder.put(object())
            await asyncio.sleep(0.02)
        # One encode per frame -- not fifteen. This is the whole capacity fix.
        assert codec.calls == 5
        firsts = await asyncio.gather(*pulls)
        assert all(packet == firsts[0] for packet in firsts)
        for track in tracks:
            # The rest arrived too, in order, and nobody got a private copy.
            assert [p[0] for p in await _drain(track, 0.05)] == [2, 3, 4, 5]
    finally:
        room.stop()


@pytest.mark.anyio
async def test_a_late_viewer_starts_on_a_keyframe() -> None:
    holder = FrameHolder()
    codec = FakeCodec()
    room = RoomEncoder(holder, encode=codec)
    early = room.subscribe()
    await _pulling([early])
    try:
        for _ in range(3):
            holder.put(object())
            await asyncio.sleep(0.02)
        keys_before = codec.keys
        late = room.subscribe()
        first = asyncio.ensure_future(late.recv())
        # Its first pull asks for a keyframe, and it is granted after the
        # coalescing gap even with the screen perfectly still.
        packet = await asyncio.wait_for(first, enc.MIN_KEYFRAME_GAP_S + 1.0)
        assert packet[1] is True
        assert codec.keys == keys_before + 1
    finally:
        room.stop()


@pytest.mark.anyio
async def test_a_viewer_that_falls_behind_resumes_at_a_keyframe() -> None:
    holder = FrameHolder()
    room = RoomEncoder(holder, encode=FakeCodec())
    track = room.subscribe()
    asked: list[bool] = []
    room.request_keyframe = lambda: asked.append(True)  # type: ignore[method-assign]
    track._pulling = True
    track.push(Encoded("k0", key=True))
    for n in range(enc.VIEWER_QUEUE_MAX):
        track.push(Encoded(f"d{n}", key=False))
    # Over the limit: the whole backlog goes, never one frame from the middle.
    assert len(track._queue) == 0
    assert asked == [True]
    track.push(Encoded("d-late", key=False))
    assert len(track._queue) == 0, (
        "a delta frame after the drop would corrupt the decoder"
    )
    track.push(Encoded("k1", key=True))
    assert list(track._queue) == ["k1"]
    track.stop()


@pytest.mark.anyio
async def test_a_pli_storm_costs_one_keyframe() -> None:
    holder = FrameHolder()
    codec = FakeCodec()
    room = RoomEncoder(holder, encode=codec)
    track = room.subscribe()
    await _pulling([track])
    try:
        holder.put(object())  # the first frame is a keyframe
        await asyncio.sleep(0.05)
        assert codec.keys == 1
        for _ in range(15):
            room.request_keyframe()
        deadline = time.monotonic() + enc.MIN_KEYFRAME_GAP_S * 1.6
        while time.monotonic() < deadline:
            holder.put(object())
            await asyncio.sleep(0.03)
        assert codec.keys == 2
    finally:
        room.stop()


@pytest.mark.anyio
async def test_an_unwatched_room_does_not_encode() -> None:
    holder = FrameHolder()
    codec = FakeCodec()
    room = RoomEncoder(holder, encode=codec)
    track = room.subscribe()
    track.stop()
    try:
        for _ in range(5):
            holder.put(object())
            await asyncio.sleep(0.01)
        assert codec.calls == 0
    finally:
        room.stop()


@pytest.mark.anyio
async def test_closing_the_holder_ends_every_viewer() -> None:
    from aiortc.mediastreams import MediaStreamError

    holder = FrameHolder()
    room = RoomEncoder(holder, encode=FakeCodec())
    track = room.subscribe()
    pending = asyncio.ensure_future(track.recv())
    await asyncio.sleep(0.01)
    holder.close()
    with pytest.raises(MediaStreamError):
        await asyncio.wait_for(pending, 1.0)
    room.stop()


def test_scaled_size_caps_height_and_keeps_dimensions_even() -> None:
    assert scaled_size(1920, 1080, 1080) == (1920, 1080)
    assert scaled_size(2560, 1440, 1080) == (1920, 1080)
    assert scaled_size(3840, 2160, 1080) == (1920, 1080)
    assert scaled_size(1001, 1301, 1080) == (830, 1080)


def test_real_vp8_encode_is_keyed_scaled_and_stamped() -> None:
    from av import VideoFrame

    from backend.share_relay.frames import VIDEO_TIME_BASE

    codec = Vp8(bitrate=2_000_000, limit=720)
    frames = []
    for n in range(4):
        frame = VideoFrame(1280, 1440, "yuv420p")
        frame.pts = 3000 * (n + 1)
        frame.time_base = VIDEO_TIME_BASE
        frames.append(frame)
    first = codec.encode(frames[0], key=False)
    assert first is not None and first.key, "a fresh codec must open on a keyframe"
    assert codec._size == (640, 720)
    assert first.packet.pts == 3000
    assert first.packet.time_base == VIDEO_TIME_BASE
    second = codec.encode(frames[1], key=False)
    assert second is not None and not second.key
    forced = codec.encode(frames[2], key=True)
    assert forced is not None and forced.key

    # Forcing a keyframe on a frame that needs no scaling must not write to the
    # frame itself: the room's holder shares it with the idle repeater.
    unscaled = Vp8(bitrate=2_000_000, limit=1080)
    held = VideoFrame(640, 480, "yuv420p")
    held.pts = 3000
    held.time_base = VIDEO_TIME_BASE
    before = int(held.pict_type)
    assert unscaled.encode(held, key=True).key
    assert int(held.pict_type) == before


@pytest.mark.anyio
async def test_shared_audio_encodes_once_for_every_viewer() -> None:
    from av import AudioFrame

    audio = RoomAudio()
    tracks = [audio.subscribe() for _ in range(5)]
    for track in tracks:
        track._pulling = True
    for n in range(10):
        frame = AudioFrame(format="s16", layout="stereo", samples=960)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.sample_rate = 48000
        frame.pts = n * 960
        audio.put(frame)
    assert audio.packets > 0
    sizes = {len(track._queue) for track in tracks}
    assert sizes == {audio.packets}, "every viewer gets every packet, encoded once"
    first = tracks[0]._queue[0]
    assert all(track._queue[0] is first for track in tracks)
    audio.stop()


# -- end to end ----------------------------------------------------------------


@pytest.mark.anyio
async def test_real_viewers_receive_the_shared_stream_as_vp8() -> None:
    """A real publisher, a real `Room`, three real viewers over loopback ICE.

    One viewer prefers H.264, which is the case codec pinning exists for: it
    must still be answered with VP8, or it would receive VP8 packets split as
    H.264 NAL units and decode nothing.
    """
    from aiortc import (
        RTCPeerConnection,
        RTCRtpReceiver,
        RTCSessionDescription,
    )
    from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack

    from backend.share_relay.fanout import Room

    room = Room("tok")
    pub = RTCPeerConnection()
    pub.addTrack(VideoStreamTrack())
    pub.addTrack(AudioStreamTrack())
    await pub.setLocalDescription(await pub.createOffer())
    answer = await room.publish(pub.localDescription.sdp)
    await pub.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

    viewers = []
    counts: list[dict[str, int]] = []
    try:
        deadline = time.monotonic() + 20
        while not room.live and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert room.live, "the publisher's first frame never decoded"

        for n in range(3):
            pc = RTCPeerConnection()
            video = pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")
            if n == 0:
                caps = RTCRtpReceiver.getCapabilities("video").codecs
                h264_first = sorted(caps, key=lambda c: c.mimeType != "video/H264")
                video.setCodecPreferences(h264_first)
            seen = {"video": 0, "audio": 0}
            counts.append(seen)

            def on_track(track, seen=seen) -> None:
                async def consume() -> None:
                    try:
                        while True:
                            await track.recv()
                            seen[track.kind] += 1
                    except Exception:
                        pass

                asyncio.ensure_future(consume())

            pc.on("track", on_track)
            await pc.setLocalDescription(await pc.createOffer())
            sdp = await room.subscribe(pc.localDescription.sdp, viewer_id=f"v{n}")
            assert "VP8" in sdp and "H264" not in sdp
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
            viewers.append(pc)

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not all(
            c["video"] >= 10 and c["audio"] >= 10 for c in counts
        ):
            await asyncio.sleep(0.2)
        assert all(c["video"] >= 10 for c in counts), counts
        assert all(c["audio"] >= 10 for c in counts), counts
        stats = room.encoder_stats()
        assert stats["viewers"] == 3
        # One encode per frame for three viewers: each decoded more frames than
        # a third of the encodes, which per-viewer encoding could not exceed.
        assert stats["frames"] < sum(c["video"] for c in counts)

        # Giving a seat back is immediate.
        assert await room.drop_viewer("v1") is True
        assert room.viewers == 2
        assert await room.drop_viewer("v1") is False
    finally:
        await room.close()
        for pc in viewers:
            await pc.close()
        await pub.close()
