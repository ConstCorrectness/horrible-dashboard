"""The relay decodes a still screen's last frame instead of sitting on it.

aiortc releases a video frame only when the next frame's first packet arrives.
For a screen share that is still, there is no next frame -- found by publishing a
canvas painted once: one keyframe sent and received, nothing ever decoded.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiortc", reason="the relay's media path needs aiortc")

from aiortc.jitterbuffer import JitterBuffer  # noqa: E402
from aiortc.rtp import RtpPacket  # noqa: E402

from backend.share_relay import jitter  # noqa: E402


def packet(seq: int, ts: int, marker: bool = False, data: bytes = b"x") -> RtpPacket:
    p = RtpPacket(sequence_number=seq, timestamp=ts, marker=int(marker))
    p._data = data
    return p


def video_buffer():
    return jitter.buffer_class()(capacity=128, is_video=True)


def test_aiortc_holds_the_last_frame_until_another_arrives() -> None:
    """The control case. If this starts returning a frame, aiortc fixed it."""
    buf = JitterBuffer(capacity=128, is_video=True)
    buf.add(packet(1, 1000, data=b"a"))
    _, frame = buf.add(packet(2, 1000, marker=True, data=b"b"))
    assert frame is None


def test_a_marked_frame_is_released_on_its_last_packet() -> None:
    buf = video_buffer()
    assert buf.add(packet(1, 1000, data=b"a"))[1] is None
    _, frame = buf.add(packet(2, 1000, marker=True, data=b"b"))
    assert frame is not None
    assert frame.data == b"ab"
    assert frame.timestamp == 1000


def test_the_next_frame_is_not_mixed_with_the_released_one() -> None:
    buf = video_buffer()
    buf.add(packet(1, 1000, marker=True, data=b"a"))
    _, frame = buf.add(packet(2, 2000, marker=True, data=b"b"))
    assert frame is not None
    assert (frame.data, frame.timestamp) == (b"b", 2000)


def test_a_frame_with_a_gap_is_not_released_early() -> None:
    buf = video_buffer()
    buf.add(packet(1, 1000, data=b"a"))
    # Packet 2 lost; the marker alone must not hand the decoder a broken frame.
    assert buf.add(packet(3, 1000, marker=True, data=b"c"))[1] is None


def test_incomplete_older_frame_does_not_wedge_static_screen_and_gates_on_keyframe() -> None:
    """When frame 1 loses a packet and frame 2 completes, the buffer must not
    wedge on frame 1 forever, and must discard orphaned delta frames (requesting
    PLI) until a keyframe arrives."""
    vp8_key = b"\x10\x02\x00\x9d\x01\x2a" + b"K" * 20
    vp8_delta = b"\x31\x02\x00" + b"D" * 20

    buf = video_buffer()
    # Frame 1 (ts=1000): packet 1 arrives, packet 2 is lost.
    assert buf.add(packet(1, 1000, data=vp8_key[:10])) == (False, None)
    # Frame 2 (ts=2000): delta frame arrives completely (seq=3, marker=True).
    # Must evict incomplete frame 1, refuse the orphaned delta frame, and ask for PLI.
    pli, frame = buf.add(packet(3, 2000, marker=True, data=vp8_delta))
    assert pli is True
    assert frame is None

    # Frame 3 (ts=3000): another delta frame arrives before the host's keyframe.
    # Still gated: rejected, and PLI requested again.
    pli, frame = buf.add(packet(4, 3000, marker=True, data=vp8_delta))
    assert pli is True
    assert frame is None

    # Frame 4 (ts=4000): host responds to PLI with a multi-packet VP8 keyframe.
    assert buf.add(packet(5, 4000, data=vp8_key[:12]))[1] is None
    pli, frame = buf.add(packet(6, 4000, marker=True, data=vp8_key[12:]))
    assert frame is not None
    assert frame.data == vp8_key
    assert frame.timestamp == 4000

    # Subsequent delta frames now flow normally again.
    pli, frame = buf.add(packet(7, 5000, marker=True, data=vp8_delta))
    assert pli is False
    assert frame is not None
    assert frame.data == vp8_delta


def test_smart_remove_across_leading_gap_drops_entire_partial_frame() -> None:
    """aiortc's `smart_remove` broke when `_origin` was `None` (a lost first
    packet of a frame), leaving packet #2..N of that frame at `_origin` to be
    assembled as a decapitated frame."""
    vp8_key = b"\x10\x02\x00\x9d\x01\x2a" + b"K" * 20
    buf = jitter.buffer_class()(capacity=8, is_video=True)
    # Seed origin at seq=1, then lose seq=2 (first packet of ts=2000) while
    # receiving seq=3,4 (tail of ts=2000).
    buf.add(packet(1, 1000, marker=True, data=vp8_key))
    buf.add(packet(3, 2000, data=b"middle"))
    buf.add(packet(4, 2000, marker=True, data=b"tail"))
    # Overflow capacity=8 with seq=10 (ts=3000), forcing smart_remove over the
    # leading None at seq=2.
    pli, frame = buf.add(packet(10, 3000, marker=True, data=vp8_key))
    assert pli is True
    # Must release the clean keyframe at ts=3000, never the decapitated ts=2000 tail.
    assert frame is not None
    assert frame.timestamp == 3000
    assert frame.data == vp8_key


def test_install_patches_the_name_receivers_are_built_from() -> None:
    from aiortc import rtcrtpreceiver

    original = rtcrtpreceiver.JitterBuffer
    try:
        jitter.install()
        assert rtcrtpreceiver.JitterBuffer is jitter.buffer_class()
    finally:
        rtcrtpreceiver.JitterBuffer = original
