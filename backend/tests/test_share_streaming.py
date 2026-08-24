"""Phase 6: RTMP restreaming, and the one thing that must never leak.

A stream key is a password that does not expire. The tests that matter here are
the ones asserting it does **not** appear: not in an API response, not in a log
line, not in a status payload. Everything else about this feature failing is an
inconvenience; the key escaping is the incident.
"""

from __future__ import annotations

import logging

import pytest

from backend.modules.share import streaming
from backend.share_relay.restream import FPS, build_args

KEY = "live_1234567_SUPERSECRETKEY"


@pytest.fixture
def configured(monkeypatch):
    """A node with a Twitch key stored, without touching the real secret store."""
    stored: dict = {}

    def fake_load():
        return stored

    def fake_save(data):
        stored.clear()
        stored.update(data)

    monkeypatch.setattr(streaming, "_load", fake_load)
    monkeypatch.setattr(streaming, "_save", fake_save)
    fake_save({"twitch": {"key": KEY, "ingest": "rtmp://live.twitch.tv/app"}})
    return stored


# ---------------------------------------------------------------------------
# The key does not escape
# ---------------------------------------------------------------------------


def test_configured_destinations_names_ids_never_keys(configured) -> None:
    listed = streaming.configured_destinations()
    assert listed == ["twitch"]
    assert KEY not in str(listed)


def test_status_carries_no_key(configured) -> None:
    # This object is serialized straight to the browser by the connectors route.
    status = streaming._status()
    assert KEY not in repr(status)
    assert status.connected is True
    assert status.scopes == ["twitch"]


def test_redact_keeps_the_useful_half_and_drops_the_key() -> None:
    url = f"rtmp://live.twitch.tv/app/{KEY}"
    safe = streaming.redact(url)
    assert KEY not in safe
    # The host and app survive, because that is what makes a log line diagnostic.
    assert safe == "rtmp://live.twitch.tv/app/<key>"


def test_redact_survives_a_malformed_url() -> None:
    assert "secret" not in streaming.redact("garbage-with-no-slashes-secret")


def test_target_url_is_the_only_thing_that_returns_the_key(configured) -> None:
    assert streaming.target_url("twitch") == f"rtmp://live.twitch.tv/app/{KEY}"
    assert streaming.target_url("youtube") is None
    assert streaming.target_url("nonsense") is None


def test_starting_a_restream_logs_the_redacted_url(configured, monkeypatch, caplog):
    """The log line is the likeliest accidental disclosure: it ends up in bug
    reports, screenshots and pasted stack traces."""
    import backend.modules.share.link as link

    monkeypatch.setattr(link, "relay_base", lambda: "https://relay.example.com")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"live": True}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            # The relay does need the real target -- that is the point of the
            # node holding the key rather than the browser.
            assert KEY in json["target"]
            return FakeResponse()

    monkeypatch.setattr(link.httpx, "AsyncClient", FakeClient)

    import asyncio

    with caplog.at_level(logging.INFO):
        label = asyncio.run(link.start_restream("tok", "twitch"))

    assert label == "Twitch"
    assert KEY not in caplog.text
    assert "<key>" in caplog.text


def test_an_unconfigured_destination_says_where_to_find_the_key(
    configured, monkeypatch
):
    import asyncio

    import backend.modules.share.link as link

    monkeypatch.setattr(link, "relay_base", lambda: "https://relay.example.com")
    with pytest.raises(link.LinkError) as exc:
        asyncio.run(link.start_restream("tok", "youtube"))
    assert "YouTube Studio" in str(exc.value)
    assert KEY not in str(exc.value)


# ---------------------------------------------------------------------------
# The ffmpeg command line
# ---------------------------------------------------------------------------


def test_the_input_format_is_fully_specified() -> None:
    """Raw video on a pipe has no header. A missing or wrong `-s`/`-pix_fmt`/`-r`
    does not fail — it produces a skewed, miscoloured or wrong-speed picture,
    which reads as a broken encoder rather than a wrong argument."""
    args = build_args("rtmp://x/app/k", 1280, 720)
    assert "-f" in args and "rawvideo" in args
    assert args[args.index("-s") + 1] == "1280x720"
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-r") + 1] == str(FPS)


def test_keyframes_are_forced_at_least_every_two_seconds() -> None:
    # Every platform requires this, and without it a viewer joining mid-stream
    # waits for the next natural keyframe -- on a static screen share, forever.
    args = build_args("rtmp://x/app/k", 1280, 720)
    assert args[args.index("-g") + 1] == str(FPS * 2)


def test_zerolatency_is_set() -> None:
    # Without it x264 buffers frames looking ahead and the broadcast lags the
    # room by seconds, which is invisible in a test and obvious on a stream.
    args = build_args("rtmp://x/app/k", 640, 480)
    assert args[args.index("-tune") + 1] == "zerolatency"


def test_the_target_is_last_and_the_container_is_flv() -> None:
    args = build_args("rtmp://x/app/k", 640, 480)
    assert args[-1] == "rtmp://x/app/k"
    assert args[args.index("-f", args.index("-c:v")) + 1] == "flv"
