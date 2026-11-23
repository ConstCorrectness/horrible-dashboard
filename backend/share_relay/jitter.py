"""Release a video frame when its last packet arrives, not when the next frame does.

aiortc's `JitterBuffer` decides a frame is complete only when a packet with a
*different* timestamp shows up behind it -- it never reads the RTP marker bit,
which is the sender saying "that was the last packet of this frame". For camera
video at 30 fps the difference is one frame of latency and nobody notices.

For a screen share it is the whole picture. Chrome sends a still desktop at close
to zero frames per second, so the newest frame the host sent sits in the relay's
buffer, undecoded, until the screen next changes. Viewers watch the state of the
screen one change ago -- and a host who shares a screen that is still from the
start never gets a single frame decoded, so the room never goes live. Found by
publishing a canvas that was painted once: one keyframe encoded, sent and
received, and nothing decoded, for as long as anyone cared to wait.

The fix is the marker bit. `MarkerAwareJitterBuffer` completes a video frame on
its marked packet when every packet back to the buffer's origin is present and
shares its timestamp; anything less (a gap, an older frame still incomplete)
falls through to aiortc's own rule, so loss handling and PLI are unchanged.

Installed by patching the name `aiortc.rtcrtpreceiver` constructs its buffers
from, because that is where `RTCRtpReceiver` looks it up. Audio buffers are built
from the same name and are untouched: the override only acts on video.
"""

from __future__ import annotations

import functools
from typing import Any


def is_video_keyframe(payload: bytes) -> bool:
    """Whether an assembled encoded video frame (VP8 or H.264 Annex-B) is a keyframe.

    aiortc's VP8 depacketizer strips the RTP VP8 payload descriptor before storing
    `packet._data` in `JitterBuffer`, so an assembled VP8 `JitterFrame.data` begins
    with the 3-byte VP8 frame tag (bit 0 of byte 0 is 0 for keyframe, 1 for
    interframe) followed on keyframes by the 3-byte start code `0x9d 0x01 0x2a`.
    For H.264, `h264_depayload` emits Annex-B start codes (`00 00 00 01 <nal>`),
    where NAL type 5 (IDR) or 7 (SPS) marks a keyframe.
    """
    if not payload:
        return False
    if payload.startswith(b"\x00\x00\x00\x01") or payload.startswith(b"\x00\x00\x01"):
        i = 0
        n = len(payload)
        while i < n - 4:
            if payload[i : i + 4] == b"\x00\x00\x00\x01":
                if (payload[i + 4] & 0x1F) in (5, 7):
                    return True
                i += 5
            elif payload[i : i + 3] == b"\x00\x00\x01":
                if (payload[i + 3] & 0x1F) in (5, 7):
                    return True
                i += 4
            else:
                i += 1
        return False
    if (payload[0] & 0x01) == 0:
        if len(payload) >= 6 and payload[3:6] != b"\x9d\x01\x2a":
            return False
        return True
    return False


@functools.cache
def _buffer_class() -> Any:
    from aiortc.jitterbuffer import JitterBuffer, JitterFrame  # noqa: PLC0415 - lazy
    from aiortc.utils import uint16_add  # noqa: PLC0415 - lazy

    class MarkerAwareJitterBuffer(JitterBuffer):
        def __init__(
            self, capacity: int, prefetch: int = 0, is_video: bool = False
        ) -> None:
            super().__init__(capacity=capacity, prefetch=prefetch, is_video=is_video)
            #: Set whenever an incomplete frame is dropped so orphaned delta
            #: frames are never handed to the decoder before a keyframe arrives.
            self._need_keyframe = False

        def add(self, packet: Any) -> tuple[bool, Any]:
            pli_flag, frame = super().add(packet)
            if pli_flag and self._is_video:
                self._need_keyframe = True
            if frame is None and self._is_video and packet.marker:
                if self._evict_incomplete_older_frames(packet):
                    pli_flag = True
                    self._need_keyframe = True
                frame = self._take_marked_frame()
            if self._is_video and self._need_keyframe:
                if frame is not None:
                    if is_video_keyframe(frame.data):
                        self._need_keyframe = False
                    else:
                        # Never decode an orphaned P-frame after packet loss:
                        # it corrupts FrameHolder with macroblock smearing that
                        # repeat_while_idle would then broadcast every second.
                        frame = None
                        pli_flag = True
                elif packet.marker:
                    # Keep asking on frame boundaries until a whole keyframe lands.
                    pli_flag = True
            return pli_flag, frame

        def smart_remove(self, count: int) -> bool:
            """Remove at least `count` slots and finish the partial frame at `_origin`.

            Fixes aiortc's `smart_remove`, where leading `None` slots left
            `timestamp = None` and caused `timestamp != packet.timestamp` to break
            in the middle of the frame whose first packet was lost, leaving a
            decapitated half-frame at `_origin`.
            """
            if self._is_video:
                self._need_keyframe = True
            timestamp = None
            for i in range(self._capacity):
                pos = self._origin % self._capacity
                packet = self._packets[pos]
                if packet is not None:
                    if (
                        i >= count
                        and timestamp is not None
                        and timestamp != packet.timestamp
                    ):
                        break
                    timestamp = packet.timestamp
                self._packets[pos] = None
                self._origin = uint16_add(self._origin, 1)
                if i == self._capacity - 1:
                    return True
            return False

        def _evict_incomplete_older_frames(self, marked_packet: Any) -> bool:
            """If an older frame ahead of `marked_packet` has a missing packet,
            evict it so a static screen never wedges `_origin` behind a dead gap."""
            if self._origin is None:
                return False
            delta = uint16_add(marked_packet.sequence_number, -self._origin)
            if delta <= 0 or delta >= self._capacity:
                return False
            # Walk back from `marked_packet` across contiguous packets of its frame.
            frame_start = delta
            while frame_start > 0:
                prev = self._packets[(self._origin + frame_start - 1) % self._capacity]
                if prev is None or prev.timestamp != marked_packet.timestamp:
                    break
                frame_start -= 1
            if frame_start == 0:
                return False
            # Confirm the prefix belongs to an older frame (or leading gap), not
            # a mid-frame gap inside `marked_packet`'s own timestamp.
            has_gap = False
            for offset in range(frame_start):
                pkt = self._packets[(self._origin + offset) % self._capacity]
                if pkt is None:
                    has_gap = True
                elif pkt.timestamp == marked_packet.timestamp:
                    # The gap is inside `marked_packet`'s own frame; wait for NACK
                    # or a newer timestamp rather than releasing a partial frame.
                    return False
            if not has_gap:
                return False
            self.remove(frame_start)
            return True

        def _take_marked_frame(self) -> Any:
            """The frame at the origin, if it is whole and ends in a marker."""
            if self._origin is None:
                return None
            packets = []
            timestamp = None
            for count in range(self.capacity):
                packet = self._packets[(self._origin + count) % self._capacity]
                if packet is None:
                    return None  # a gap: not whole yet
                if timestamp is None:
                    timestamp = packet.timestamp
                elif packet.timestamp != timestamp:
                    return None  # an older frame is still incomplete
                packets.append(packet)
                if packet.marker:
                    self.remove(count + 1)
                    return JitterFrame(
                        data=b"".join(p._data for p in packets), timestamp=timestamp
                    )
            return None

    return MarkerAwareJitterBuffer


def install() -> None:
    """Make every aiortc receiver built from now on use the marker-aware buffer."""
    from aiortc import rtcrtpreceiver  # noqa: PLC0415 - lazy

    rtcrtpreceiver.JitterBuffer = _buffer_class()


def buffer_class() -> Any:
    """The buffer class itself, for tests."""
    return _buffer_class()
