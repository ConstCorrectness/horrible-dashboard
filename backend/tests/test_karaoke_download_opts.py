"""The yt-dlp options a karaoke download is started with.

Everything here fails against YouTube without raising anything useful, so it is
pinned: without a JS runtime yt-dlp (which enables only deno by default) gets a
withheld format list and 403s; YouTube serves most songs as separate video and audio
streams, so a watchable file needs ffmpeg to join them; and without ffmpeg the video
path must fall back to audio rather than fail with "Requested format is not
available".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules.karaoke import downloader


def test_every_download_enables_the_common_js_runtimes(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path)
    assert {"deno", "node", "bun"} <= set(opts["js_runtimes"])
    assert all(isinstance(cfg, dict) for cfg in opts["js_runtimes"].values())


def test_with_ffmpeg_video_joins_h264_video_to_m4a_audio(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path, ffmpeg="C:/tools/ffmpeg.exe")
    assert opts["format"] == downloader.VIDEO_FORMAT
    first = opts["format"].split("/")[0]
    # H.264 decodes in every browser and WebView; AV1/VP9 depend on hardware.
    assert first.startswith("bv*[vcodec^=avc1]")
    assert "[height<=720]" in first
    assert first.endswith("+ba[ext=m4a]")
    assert opts["merge_output_format"] == "mp4"
    assert opts["ffmpeg_location"] == "C:/tools/ffmpeg.exe"


def test_without_ffmpeg_nothing_is_merged_and_audio_is_the_fallback(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path, ffmpeg=None)
    assert opts["format"] == downloader.NO_FFMPEG_FORMAT
    # A `+` asks yt-dlp to merge, which is exactly what it cannot do here.
    assert "+" not in opts["format"]
    assert "merge_output_format" not in opts
    assert "ffmpeg_location" not in opts
    alternatives = opts["format"].split("/")
    assert alternatives[0] == "b[ext=mp4]"
    assert alternatives[-1].startswith("ba")


def test_audio_only_ignores_ffmpeg(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path, audio_only=True, ffmpeg="ffmpeg")
    assert opts["format"] == downloader.AUDIO_FORMAT
    assert opts["format"].startswith("ba")
    assert "merge_output_format" not in opts


def test_the_download_looks_ffmpeg_up_per_call(tmp_path: Path, monkeypatch):
    """Installing ffmpeg must change the next download without a backend restart, and
    an audio-only request must not merge even when ffmpeg is present."""
    captured: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            captured.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, _url, download):
            return {"ext": "mp4"}

    monkeypatch.setattr(
        downloader, "_ytdlp", lambda: SimpleNamespace(YoutubeDL=FakeYDL)
    )
    monkeypatch.setattr(downloader.store, "songs_dir", lambda: tmp_path)

    monkeypatch.setattr(downloader.transpose, "ffmpeg_path", lambda: None)
    downloader._download_blocking("a", "https://youtu.be/aaaaaaaaaaa")
    monkeypatch.setattr(downloader.transpose, "ffmpeg_path", lambda: "/bin/ffmpeg")
    downloader._download_blocking("b", "https://youtu.be/aaaaaaaaaaa")
    downloader._download_blocking("c", "https://youtu.be/aaaaaaaaaaa", audio_only=True)

    assert [o["format"] for o in captured] == [
        downloader.NO_FFMPEG_FORMAT,
        downloader.VIDEO_FORMAT,
        downloader.AUDIO_FORMAT,
    ]
    assert captured[1]["ffmpeg_location"] == "/bin/ffmpeg"


@pytest.mark.parametrize(
    ("info", "name", "expected"),
    [
        ({"vcodec": "none", "acodec": "mp4a.40.2"}, "x.m4a", True),
        ({"vcodec": "avc1.4d401f", "acodec": "mp4a.40.2"}, "x.mp4", False),
        ({}, "x.m4a", True),
        ({}, "x.mp4", False),
    ],
)
def test_an_audio_only_result_is_recognised(tmp_path: Path, info, name, expected):
    assert downloader.got_audio_only(info, tmp_path / name) is expected


def test_the_filename_is_the_row_id(tmp_path: Path):
    opts = downloader.download_opts("abc123", tmp_path, audio_only=True)
    assert opts["outtmpl"] == str(tmp_path / "abc123.%(ext)s")


def test_the_runtime_table_is_not_shared_between_downloads(tmp_path: Path):
    """yt-dlp mutates `js_runtimes` in place (`_clean_js_runtimes` pops entries), so
    each download must get its own copy of the module-level table."""
    first = downloader.download_opts("a", tmp_path)
    first["js_runtimes"].pop("deno")
    assert "deno" in downloader.download_opts("b", tmp_path)["js_runtimes"]
