"""The yt-dlp options a karaoke download is started with.

Two things here fail silently against YouTube rather than raising, so they are pinned:
without a JS runtime yt-dlp (which enables only deno by default) gets a withheld
format list and 403s, and the pre-muxed mp4 the video path asks for is often gone,
which is why room music downloads audio only.
"""

from __future__ import annotations

from pathlib import Path

from backend.modules.karaoke import downloader


def test_every_download_enables_the_common_js_runtimes(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path)
    assert {"deno", "node", "bun"} <= set(opts["js_runtimes"])
    assert all(isinstance(cfg, dict) for cfg in opts["js_runtimes"].values())


def test_audio_only_asks_for_a_single_audio_stream(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path, audio_only=True)
    assert opts["format"] == downloader.AUDIO_FORMAT
    assert opts["format"].startswith("ba")
    # No merge: a single stream must not require ffmpeg.
    assert "merge_output_format" not in opts


def test_the_video_path_is_unchanged(tmp_path: Path):
    opts = downloader.download_opts("song1", tmp_path)
    assert opts["format"] == "best[ext=mp4]/best"
    assert opts["merge_output_format"] == "mp4"


def test_the_filename_is_the_row_id(tmp_path: Path):
    opts = downloader.download_opts("abc123", tmp_path, audio_only=True)
    assert opts["outtmpl"] == str(tmp_path / "abc123.%(ext)s")


def test_the_runtime_table_is_not_shared_between_downloads(tmp_path: Path):
    """yt-dlp mutates `js_runtimes` in place (`_clean_js_runtimes` pops entries), so
    each download must get its own copy of the module-level table."""
    first = downloader.download_opts("a", tmp_path)
    first["js_runtimes"].pop("deno")
    assert "deno" in downloader.download_opts("b", tmp_path)["js_runtimes"]
