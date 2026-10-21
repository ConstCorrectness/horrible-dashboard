"""The newest frame of a room, held so a viewer is never waiting on the host.

`MediaRelay` hands a viewer's encoder a frame only when the host sends a *new*
one, and a screen share is mostly a still picture. Chrome encodes a desktop that
is not changing at close to zero frames per second, so two things went black:

- **A viewer who joined late** got nothing until something on the host's screen
  moved. Their peer connection said `connected`, the page waited for a first
  frame that was not coming, and then told them the host had probably stopped.
- **A viewer who lost a keyframe** stayed broken. Their browser sends a PLI, and
  aiortc's answer is to flag the *next* encode as a keyframe -- with no next
  frame, there is no keyframe, and the decoder has nothing to restart from.

Both are fixed by the same thing: the room keeps its newest decoded frame, a new
viewer starts on it immediately, and when the host goes quiet the relay repeats
it about once a second. Repeats are cheap for exactly the reason the problem
existed -- the picture is not changing -- and they are made **once per room**,
not once per viewer.

**Timestamps belong to the room, not to the host.** Every frame that enters the
holder is restamped from the room's own monotonic clock. Repeats need a fresh
timestamp anyway, and taking the host's RTP-derived ones for real frames would
let a repeat stamped "now" land after a real frame captured a moment earlier, or
jump backwards when a host re-publishes onto a fresh RTP clock. One clock for
everything makes each viewer's sequence strictly increasing by construction.

**A frame in the holder is never mutated after it is published.** Each viewer's
encoder reads `pts` in an executor thread while other viewers hold the same
object, so the stamp is written before `put` exposes the frame and repeats are
always copies.

`aiortc` and PyAV are imported lazily, as in `fanout`: the holder itself is plain
asyncio and is tested without a media stack.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Callable
from fractions import Fraction
from typing import Any

#: How long a still screen may go without a frame before the relay repeats the
#: last one. One second keeps PLI recovery and late joins under a second while
#: costing one frame copy per idle second per room.
IDLE_REPEAT_S = 1.0

#: RTP's video clock. Using it directly means aiortc's conversion is exact.
VIDEO_TIME_BASE = Fraction(1, 90000)


class HolderClosed(Exception):
    """The room this holder belonged to has closed; no frame is coming."""


class FrameHolder:
    """The newest video frame of one room, and a way to wait for the next.

    `seq` counts every `put`, real or repeated, so a consumer can ask "anything
    newer than what I last took?" without comparing frames. `first_at` and
    `last_at` track **real** frames only -- they answer whether the host is
    actually sending, which repeats must not fake.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._epoch = clock()
        self._last_pts = -1
        self._changed = asyncio.Event()
        self.frame: Any = None
        self.seq = 0
        self.first_at: float | None = None
        self.last_at: float | None = None
        self.closed = False

    def put(self, frame: Any, *, repeat: bool = False) -> None:
        """Publish `frame` as the newest, waking everyone waiting on it."""
        if self.closed:
            return
        now = self._clock()
        # Never equal to the previous stamp, even from two puts in one tick.
        pts = max(int((now - self._epoch) / VIDEO_TIME_BASE), self._last_pts + 1)
        self._last_pts = pts
        _stamp(frame, pts)
        self.frame = frame
        self.seq += 1
        if not repeat:
            if self.first_at is None:
                self.first_at = now
            self.last_at = now
        self._wake()

    def reset_live(self) -> None:
        """Forget that the host was sending, keeping the last picture.

        Called when a new publisher takes over: whether the room is live is a
        question about *this* publisher, but viewers already watching keep the
        last frame on screen rather than going black during the hand-over.
        """
        self.first_at = None
        self.last_at = None

    def close(self) -> None:
        self.closed = True
        self.frame = None
        self._wake()

    async def wait_newer(self, seq: int, timeout: float | None = None) -> bool:
        """Wait until a frame newer than `seq` exists.

        Returns False on timeout. Raises `HolderClosed` once the room is gone, so
        a consumer cannot mistake "the room closed" for "the screen is still".
        """
        while self.seq <= seq:
            if self.closed:
                raise HolderClosed
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), timeout)
            except TimeoutError:
                return False
        if self.closed:
            raise HolderClosed
        return True

    def _wake(self) -> None:
        # Replace, then set: a waiter holds the old event, which fires, and the
        # next waiter gets a fresh one that has not.
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()


async def repeat_while_idle(
    holder: FrameHolder,
    should_repeat: Callable[[], bool],
    idle_s: float = IDLE_REPEAT_S,
) -> None:
    """Re-publish the newest frame whenever the host has been quiet for `idle_s`.

    `should_repeat` lets the room skip the copy when nobody is watching. Runs
    until cancelled or until the holder closes.
    """
    try:
        while True:
            seq = holder.seq
            if await holder.wait_newer(seq, idle_s):
                continue
            frame = holder.frame
            if frame is not None and should_repeat():
                holder.put(copy_frame(frame), repeat=True)
    except HolderClosed:
        return


def _stamp(frame: Any, pts: int) -> None:
    """Restamp a real video frame. Anything else (a test's stand-in) is left alone."""
    video_frame = _video_frame_class()
    if video_frame is not None and isinstance(frame, video_frame):
        frame.pts = pts
        frame.time_base = VIDEO_TIME_BASE


def copy_frame(frame: Any) -> Any:
    """A new frame object with the same picture.

    Not `frame.reformat()`: PyAV returns the *same object* when nothing about the
    format changes, and a repeat that aliases the frame other viewers are encoding
    is exactly the shared mutation this module promises not to do.
    """
    video_frame = _video_frame_class()
    if video_frame is None or not isinstance(frame, video_frame):
        return frame
    if frame.format.name != "yuv420p":
        # A format change allocates a new frame already.
        return frame.reformat(format="yuv420p")
    return video_frame.from_ndarray(frame.to_ndarray(), format="yuv420p")


@functools.cache
def _video_frame_class() -> Any:
    try:
        from av import VideoFrame  # noqa: PLC0415 - lazy by design, see docstring
    except ImportError:  # pragma: no cover - depends on the environment
        return None
    return VideoFrame


@functools.cache
def _held_track_class() -> Any:
    """Built on first use so importing this module never needs aiortc."""
    from aiortc.mediastreams import (  # noqa: PLC0415 - lazy by design
        MediaStreamError,
        MediaStreamTrack,
    )

    class HeldVideoTrack(MediaStreamTrack):
        """One viewer's video: the room's newest frame, never a backlog.

        The first `recv()` returns whatever the room is holding, so a viewer who
        joins a still screen has a picture -- and their encoder a keyframe -- in
        one encode. After that it waits for anything newer, which `repeat_while_idle`
        guarantees within about a second.
        """

        kind = "video"

        def __init__(self, holder: FrameHolder) -> None:
            super().__init__()
            self._holder = holder
            self._seen = 0

        async def recv(self) -> Any:
            if self.readyState != "live":
                raise MediaStreamError
            try:
                await self._holder.wait_newer(self._seen)
            except HolderClosed:
                self.stop()
                raise MediaStreamError from None
            self._seen = self._holder.seq
            return self._holder.frame

    return HeldVideoTrack


def held_track(holder: FrameHolder) -> Any:
    """A per-viewer video track fed from `holder`."""
    return _held_track_class()(holder)
