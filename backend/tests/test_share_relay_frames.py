"""The relay's frame holder: why a still screen no longer means a black viewer.

Driven against real aiortc and PyAV, like the backpressure tests, because the
failure is a property of the real library: a `MediaRelay` proxy hands a viewer's
encoder a frame only when the host sends a *new* one. The control case below
pins that, so the fix stays meaningful.
"""

from __future__ import annotations

import asyncio

import pytest

aiortc = pytest.importorskip("aiortc", reason="the relay's media path needs aiortc")

from aiortc.contrib.media import MediaRelay  # noqa: E402
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack  # noqa: E402
from av import VideoFrame  # noqa: E402

from backend.share_relay.fanout import Room  # noqa: E402
from backend.share_relay.frames import (  # noqa: E402
    FrameHolder,
    HolderClosed,
    copy_frame,
    held_track,
    repeat_while_idle,
)


def frame() -> VideoFrame:
    return VideoFrame(64, 48, "yuv420p")


class StillScreen(MediaStreamTrack):
    """A host whose screen changes a few times and then stops changing.

    That is what Chrome sends for a desktop nobody is touching: close to nothing.
    Parks rather than ending, because an ended track tears the relay down and the
    question here is what a viewer sees while the host is still connected.
    """

    kind = "video"

    def __init__(self, frames: int = 3) -> None:
        super().__init__()
        self.left = frames

    async def recv(self) -> VideoFrame:
        if self.left <= 0:
            await asyncio.Event().wait()
        self.left -= 1
        await asyncio.sleep(0)
        return frame()


async def settle(times: int = 50) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def publishing_room(source: StillScreen) -> Room:
    room = Room("tok")
    room._relay = MediaRelay()
    room._tracks[source.kind] = source
    room._start_pump(source)
    return room


# --- the failure, and the fix -----------------------------------------------


@pytest.mark.anyio
async def test_a_relay_proxy_gives_a_late_viewer_nothing_on_a_still_screen() -> None:
    """The control case: what every viewer used to be subscribed to.

    If this ever starts passing a frame, aiortc changed and the holder may no
    longer be needed -- that is the signal to look, not a bug to paper over.
    """
    source = StillScreen()
    room = publishing_room(source)
    try:
        await settle()
        assert source.left == 0, "the host's frames were never pulled"
        late = room._relay.subscribe(source, buffered=False)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(late.recv(), 0.3)
    finally:
        room._stop_pumps()


@pytest.mark.anyio
async def test_a_late_viewer_gets_the_held_frame_at_once() -> None:
    source = StillScreen()
    room = publishing_room(source)
    try:
        await settle()
        viewer = held_track(room._frames)
        got = await asyncio.wait_for(viewer.recv(), 1.5)
        assert isinstance(got, VideoFrame)
    finally:
        room._stop_pumps()


@pytest.mark.anyio
async def test_a_still_screen_keeps_producing_frames_for_a_watcher() -> None:
    """PLI recovery needs a *next* frame to make a keyframe of; this is it."""
    holder = FrameHolder()
    holder.put(frame())
    repeater = asyncio.ensure_future(
        repeat_while_idle(holder, lambda: True, idle_s=0.02)
    )
    try:
        viewer = held_track(holder)
        first = await asyncio.wait_for(viewer.recv(), 1)
        second = await asyncio.wait_for(viewer.recv(), 1)
        third = await asyncio.wait_for(viewer.recv(), 1)
        assert first.pts < second.pts < third.pts
        # Repeats are copies: the frame other viewers' encoders are reading is
        # never restamped underneath them.
        assert second is not first and third is not second
    finally:
        repeater.cancel()


@pytest.mark.anyio
async def test_nothing_is_copied_when_nobody_is_watching() -> None:
    holder = FrameHolder()
    holder.put(frame())
    seq = holder.seq
    repeater = asyncio.ensure_future(
        repeat_while_idle(holder, lambda: False, idle_s=0.01)
    )
    try:
        await asyncio.sleep(0.08)
        assert holder.seq == seq
    finally:
        repeater.cancel()


@pytest.mark.anyio
async def test_repeats_do_not_count_as_the_host_sending() -> None:
    holder = FrameHolder()
    holder.put(frame())
    last = holder.last_at
    holder.put(copy_frame(holder.frame), repeat=True)
    assert holder.last_at == last
    assert holder.seq == 2


def test_copy_frame_is_a_new_object_with_the_same_picture() -> None:
    original = frame()
    original.planes[0].update(bytes([7]) * original.planes[0].buffer_size)
    copied = copy_frame(original)
    assert copied is not original
    assert (copied.to_ndarray() == original.to_ndarray()).all()


# --- the holder itself --------------------------------------------------------


@pytest.mark.anyio
async def test_timestamps_strictly_increase_even_within_one_clock_tick() -> None:
    holder = FrameHolder(clock=lambda: 5.0)
    stamps = []
    for _ in range(3):
        f = frame()
        holder.put(f)
        stamps.append(f.pts)
    assert stamps == sorted(set(stamps))


@pytest.mark.anyio
async def test_wait_newer_times_out_and_then_sees_a_put() -> None:
    holder = FrameHolder()
    assert await holder.wait_newer(0, timeout=0.01) is False
    holder.put(frame())
    assert await holder.wait_newer(0, timeout=0.01) is True


@pytest.mark.anyio
async def test_a_closed_holder_ends_its_viewers() -> None:
    holder = FrameHolder()
    viewer = held_track(holder)
    pending = asyncio.ensure_future(viewer.recv())
    await settle()
    holder.close()
    with pytest.raises(MediaStreamError):
        await pending
    with pytest.raises(HolderClosed):
        await holder.wait_newer(0)


# --- liveness -----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_room_is_live_only_once_a_frame_has_arrived() -> None:
    told: list[str] = []

    async def on_live(room: Room) -> None:
        told.append(room.token)

    room = Room("tok", on_live=on_live)
    room._tracks["video"] = object()  # negotiated, nothing decoded yet
    assert room.publishing is True
    assert room.live is False
    assert room.last_frame_age is None

    room._frame_arrived(frame())
    room._frame_arrived(frame())
    await settle()
    assert room.live is True
    assert told == ["tok"], "on_live must fire once per publish, not per frame"


@pytest.mark.anyio
async def test_republishing_keeps_the_picture_but_not_the_live_flag() -> None:
    """A viewer keeps watching the last frame through a host's re-publish rather
    than being dropped; the room is live again only on the new publisher's frame."""
    room = Room("tok")
    room._tracks["video"] = object()
    room._frame_arrived(frame())
    viewer = held_track(room._frames)
    await viewer.recv()

    room._frames.reset_live()
    assert room.live is False
    assert room._frames.frame is not None

    room._frame_arrived(frame())
    assert room.live is True
    assert isinstance(await asyncio.wait_for(viewer.recv(), 1), VideoFrame)


@pytest.mark.anyio
async def test_closing_the_room_ends_every_viewer() -> None:
    room = Room("tok")
    room._frame_arrived(frame())
    viewer = held_track(room._frames)
    await viewer.recv()
    await room.close()
    with pytest.raises(MediaStreamError):
        await viewer.recv()
