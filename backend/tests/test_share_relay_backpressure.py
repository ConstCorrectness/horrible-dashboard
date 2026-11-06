"""The relay's memory bound, tested against real aiortc rather than argued about.

This file exists because the failure it guards is invisible until it is fatal:
every queue on aiortc's receive path is unbounded and nothing drops frames, so a
relay that is leaking looks *exactly* like a relay that is working — perfect
picture, correct viewer count, right up until the kernel kills the process and
takes every token in the registry with it.

So these are not unit tests of a helper. They drive `Room` with a real
`MediaRelay` and assert the two properties that keep it alive:

1. an ingest with **no viewers** is still being drained, and
2. a viewer that cannot keep up **skips frames** instead of accumulating them.

`aiortc` is imported at module scope here (unlike in `fanout`, which is lazy on
purpose) because there is nothing to test without it — the whole point is the
behaviour of the real library.
"""

from __future__ import annotations

import asyncio

import pytest

aiortc = pytest.importorskip("aiortc", reason="the relay's media path needs aiortc")

from aiortc.contrib.media import MediaRelay  # noqa: E402
from aiortc.mediastreams import MediaStreamTrack  # noqa: E402

from backend.share_relay.fanout import Room, _buffered_for  # noqa: E402


class CountingTrack(MediaStreamTrack):
    """A source that yields a numbered object per `recv()` and counts the pulls.

    Deliberately not a real `VideoFrame`: nothing under test decodes or encodes,
    and a genuine frame would drag a codec into a test about queue discipline.
    What matters is only that `recv()` is awaited, and how often.
    """

    kind = "video"

    def __init__(self, limit: int = 200) -> None:
        super().__init__()
        self.pulled = 0
        self._limit = limit

    async def recv(self) -> object:
        if self.pulled >= self._limit:
            # Park rather than end: an ended track tears the relay's reader down,
            # which would make "is it still draining?" unanswerable.
            await asyncio.Event().wait()
        self.pulled += 1
        # Yield to the loop so a consumer can interleave; without this the source
        # starves everything else and the test measures nothing.
        await asyncio.sleep(0)
        return object()


async def _settle(times: int = 50) -> None:
    """Let the relay's reader task run without pinning the test to a clock."""
    for _ in range(times):
        await asyncio.sleep(0)


def test_video_is_unbuffered_and_audio_is_not() -> None:
    """The asymmetry is the design, so it is pinned rather than left to a comment.

    Flipping video to buffered restores the leak and flipping audio to unbuffered
    makes the stream drop samples audibly; neither shows up in any other test.
    """
    assert _buffered_for("video") is False
    assert _buffered_for("audio") is True


@pytest.mark.anyio
async def test_ingest_is_drained_with_no_viewers() -> None:
    """Rule 1: publishing starts the pull, watching does not.

    The regression this catches is the original code's assumption that unpulled
    frames are discarded. They are not — they queue — so a host who starts
    sharing before anyone opens the link was filling memory at line rate, which
    is the shape of the OOM that killed the deployed relay 2m21s into a single
    share with no viewer ceiling in sight.
    """
    room = Room("tok")
    room._relay = MediaRelay()
    track = CountingTrack()

    room._tracks[track.kind] = track
    room._start_pump(track)
    await _settle()

    assert track.pulled > 0, "nothing is pulling the ingest; frames are piling up"
    room._stop_pumps()


@pytest.mark.anyio
async def test_a_slow_viewer_skips_frames_instead_of_queueing() -> None:
    """Rule 2: falling behind costs fidelity, never memory.

    A viewer proxy is subscribed exactly as `Room.subscribe` does it, then read
    far slower than the source produces. Buffered, the backlog would be every
    frame it missed; unbuffered, it is one.
    """
    relay = MediaRelay()
    track = CountingTrack()
    proxy = relay.subscribe(track, buffered=_buffered_for(track.kind))

    # One read to register the proxy and start the relay's reader.
    await proxy.recv()
    await _settle(200)

    # The source has run well ahead of this consumer by now...
    assert track.pulled > 10, "the source never got ahead; the test proves nothing"
    # ...and the consumer is holding a single frame, not the backlog.
    assert proxy._queue is None, "a video proxy must not own a queue at all"
    assert proxy._frame is not None

    # It still yields the *newest* frame rather than stalling.
    latest = await proxy.recv()
    assert latest is not None


@pytest.mark.anyio
async def test_buffered_mode_is_the_leak_this_avoids() -> None:
    """The control case: the same consumer, buffered, keeps every missed frame.

    Asserting the failure mode is what makes the fix meaningful. If aiortc ever
    starts bounding its queues this test goes red, which is the signal to
    reconsider the whole approach rather than a bug to paper over.
    """
    relay = MediaRelay()
    track = CountingTrack()
    proxy = relay.subscribe(track, buffered=True)

    await proxy.recv()
    await _settle(200)

    assert proxy._queue is not None
    assert proxy._queue.qsize() > 10, (
        "expected an unbounded backlog; aiortc may have changed its queue policy"
    )


@pytest.mark.anyio
async def test_bundle_policy_is_max_compat_to_preserve_primary_candidates(monkeypatch) -> None:
    """The relay must configure MAX_COMPAT so aiortc's BUNDLE pass merges slave
    m-lines onto media[0]'s transport without overwriting media[0]'s candidates."""
    configs = []
    orig_pc = aiortc.RTCPeerConnection

    def intercept_pc(config=None):
        configs.append(config)
        return orig_pc(config)

    monkeypatch.setattr(aiortc, "RTCPeerConnection", intercept_pc)
    room = Room("tok")

    from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack

    pub_pc = orig_pc(
        aiortc.RTCConfiguration(bundlePolicy=aiortc.RTCBundlePolicy.MAX_COMPAT)
    )
    pub_pc.addTrack(VideoStreamTrack())
    pub_pc.addTrack(AudioStreamTrack())
    pub_pc.addTrack(AudioStreamTrack())
    await pub_pc.setLocalDescription(await pub_pc.createOffer())
    await room.publish(pub_pc.localDescription.sdp)
    assert configs[-1].bundlePolicy == aiortc.RTCBundlePolicy.MAX_COMPAT

    # All three transceivers must be bundled onto a single ICE transport, and
    # its remote candidates must come from media[0] (the primary BUNDLE mid).
    from aiortc import sdp

    parsed_offer = sdp.SessionDescription.parse(pub_pc.localDescription.sdp)
    primary_ports = {c.port for c in parsed_offer.media[0].ice_candidates}
    transports = list(room._publisher._RTCPeerConnection__iceTransports)
    assert len(transports) == 1
    remote_ports = {c.port for c in transports[0]._connection.remote_candidates}
    assert remote_ports == primary_ports

    sub_pc = orig_pc()
    sub_pc.addTransceiver("video", direction="recvonly")
    sub_pc.addTransceiver("audio", direction="recvonly")
    sub_offer = await sub_pc.createOffer()
    await room.subscribe(sub_offer.sdp)
    assert configs[-1].bundlePolicy == aiortc.RTCBundlePolicy.MAX_COMPAT

    await room.close()
    await pub_pc.close()
    await sub_pc.close()


@pytest.mark.anyio
async def test_duplicate_track_of_same_kind_is_drained_and_ignored() -> None:
    """A publisher sending duplicate tracks (e.g. 2 audio tracks) ignores the duplicate without leaking."""
    room = Room("tok")

    from aiortc.mediastreams import VideoStreamTrack

    pub_pc = aiortc.RTCPeerConnection()
    pub_pc.addTrack(VideoStreamTrack())
    pub_pc.addTrack(VideoStreamTrack())
    offer = await pub_pc.createOffer()
    await room.publish(offer.sdp)
    await _settle(10)

    assert room._tracks.get("video") is not None
    # Ensure side task exists for the duplicate track drain
    assert len(room._side_tasks) == 1

    await room.close()
    await pub_pc.close()
