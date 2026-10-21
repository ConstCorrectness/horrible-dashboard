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


def test_install_patches_the_name_receivers_are_built_from() -> None:
    from aiortc import rtcrtpreceiver

    original = rtcrtpreceiver.JitterBuffer
    try:
        jitter.install()
        assert rtcrtpreceiver.JitterBuffer is jitter.buffer_class()
    finally:
        rtcrtpreceiver.JitterBuffer = original
