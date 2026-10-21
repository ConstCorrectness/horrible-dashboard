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


@functools.cache
def _buffer_class() -> Any:
    from aiortc.jitterbuffer import JitterBuffer, JitterFrame  # noqa: PLC0415 - lazy

    class MarkerAwareJitterBuffer(JitterBuffer):
        def add(self, packet: Any) -> tuple[bool, Any]:
            pli_flag, frame = super().add(packet)
            if frame is None and self._is_video and packet.marker:
                frame = self._take_marked_frame()
            return pli_flag, frame

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
