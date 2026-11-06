"""The relay's room table, without a media stack.

A room closes for more reasons than anyone asks it to: the WHIP DELETE, a revoke,
and -- the one that bit -- its own publisher's connection failing, which closes it
from inside aiortc's state handler. These pin what every one of those exits must
do, and none of them needs `aiortc` installed: an empty room closes without it.
"""

from __future__ import annotations

import pytest

from backend.share_relay.fanout import Rooms, ingest_bitrate_hints


@pytest.mark.anyio
async def test_a_room_that_closes_itself_is_forgotten() -> None:
    """The latched-`_closing` bug.

    A room that closed itself used to stay in the table with `_closing` set, so
    the host's next WHIP landed in a room whose `close()` was a no-op forever:
    its viewers could never be dropped, by a stop, a revoke, or anything else.
    """
    rooms = Rooms()
    room = rooms.get_or_create("tok")
    await room.close()  # as the publisher's failed-connection handler does
    assert rooms.get("tok") is None
    assert rooms.get_or_create("tok") is not room


@pytest.mark.anyio
async def test_every_close_is_reported_once() -> None:
    closed: list[str] = []

    async def on_close(token: str) -> None:
        closed.append(token)

    rooms = Rooms(on_close=on_close)
    rooms.get_or_create("a")
    room_b = rooms.get_or_create("b")

    await rooms.drop("a")
    await room_b.close()
    await room_b.close()  # re-entry, as a closing peer connection triggers

    assert closed == ["a", "b"]


@pytest.mark.anyio
async def test_a_stale_room_closing_does_not_evict_its_replacement() -> None:
    rooms = Rooms()
    old = rooms.get_or_create("tok")
    await rooms.drop("tok")
    new = rooms.get_or_create("tok")
    # The old room finishing a late close must not take the new one with it.
    await rooms._closed(old)
    assert rooms.get("tok") is new


@pytest.mark.anyio
async def test_a_failing_listener_does_not_fail_the_close() -> None:
    async def on_close(token: str) -> None:
        raise RuntimeError("nobody to tell")

    rooms = Rooms(on_close=on_close)
    rooms.get_or_create("tok")
    await rooms.drop("tok")  # must not raise
    assert rooms.get("tok") is None


_ANSWER = "\r\n".join(
    [
        "v=0",
        "m=video 9 UDP/TLS/RTP/SAVPF 97 98 99",
        "a=rtpmap:97 VP8/90000",
        "a=rtpmap:98 rtx/90000",
        "a=fmtp:98 apt=97",
        "a=rtpmap:99 H264/90000",
        "a=fmtp:99 packetization-mode=1;profile-level-id=42e01f",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "a=rtpmap:111 opus/48000/2",
        "a=fmtp:111 minptime=10",
        "",
    ]
)


def test_ingest_answer_tells_chrome_not_to_starve_the_upload(monkeypatch) -> None:
    """aiortc's REMB tracks what is arriving, so a still desktop pins the host's
    upload near zero and the next scroll arrives as a smear. The WHIP answer
    carries Chrome's bitrate floor on every video codec -- and only there."""
    monkeypatch.delenv("SHARE_RELAY_INGEST_MIN_KBPS", raising=False)
    out = ingest_bitrate_hints(_ANSWER)
    lines = out.split("\r\n")
    # VP8 had no fmtp line: one is added straight after its rtpmap.
    assert lines[lines.index("a=rtpmap:97 VP8/90000") + 1].startswith(
        "a=fmtp:97 x-google-min-bitrate=1000;"
    )
    # H.264 keeps its own parameters and gains the hint.
    assert any(
        line.startswith("a=fmtp:99 packetization-mode=1;profile-level-id=42e01f;x-google-min")
        for line in lines
    )
    # Not RTX, not audio; line endings preserved.
    assert "a=fmtp:98 apt=97" in lines
    assert "a=fmtp:111 minptime=10" in lines
    assert out.count("x-google-min-bitrate") == 2
    # Idempotent: a second pass adds nothing.
    assert ingest_bitrate_hints(out) == out


def test_ingest_floor_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_INGEST_MIN_KBPS", "600")
    assert "x-google-min-bitrate=600;" in ingest_bitrate_hints(_ANSWER)
