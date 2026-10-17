"""Karaoke's use of yt-dlp: karaoke-biased search, and video into the songs directory.

The generic machinery (lazy import, flat search, JS runtimes, the shared download
pool) lives in `backend.modules.ytdl`, which the music library uses too. What stays
here is karaoke's own: the "karaoke" query bias, the video formats and their ffmpeg
merge, and writing the result into the `karaoke_songs` catalog.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path
from typing import Any

from backend.modules import ytdl
from backend.modules.karaoke import store, transpose
from backend.modules.karaoke.models import SearchResult

logger = logging.getLogger(__name__)

INSTALL_HINT = ytdl.INSTALL_HINT


def _ytdlp() -> Any | None:
    """The yt_dlp module, or None. A module-level seam so tests can fake it."""
    return ytdl.load()


def available() -> bool:
    """Whether downloads can run here. Kept as a bare bool for its callers; the
    three-state answer (and the install hint) is `extras.probe("yt-dlp")`."""
    return _ytdlp() is not None


async def search(
    query: str, limit: int = 20, karaoke_bias: bool = True
) -> tuple[list[SearchResult], str]:
    """Search YouTube. Returns (results, note) — `note` explains an empty list.

    `karaoke_bias` appends "karaoke" to the query unless the user already typed it.
    This is the single highest-value thing PiKaraoke does with search: nobody
    opening this pane wants the original recording, and typing the word every time
    is friction. It's a flag rather than a hardcode so the agent can search
    literally when a user explicitly asks for a non-karaoke video.
    """
    if not query.strip():
        return [], "Empty query."
    if not available():
        return [], INSTALL_HINT

    effective = query.strip()
    if karaoke_bias and "karaoke" not in effective.lower():
        effective = f"{effective} karaoke"

    try:
        entries = await asyncio.to_thread(
            ytdl.search_blocking, _ytdlp(), effective, limit
        )
    except Exception as exc:  # yt-dlp raises a wide variety of network errors
        logger.warning("karaoke search failed: %s", exc)
        return [], f"Search failed: {exc}"

    results = [
        SearchResult(
            video_id=hit.video_id,
            title=hit.title,
            url=hit.url,
            channel=hit.channel,
            duration=hit.duration,
            thumbnail=hit.thumbnail,
            downloaded=store.find_by_video_id(hit.video_id) is not None,
        )
        for hit in ytdl.to_hits(entries)
    ]
    note = "" if results else "No results."
    return results, note


parse_video_id = ytdl.parse_video_id
_split_title = ytdl.split_title

JS_RUNTIMES = ytdl.JS_RUNTIMES
AUDIO_FORMAT = ytdl.AUDIO_FORMAT

#: Karaoke video: separate video and audio streams, joined by ffmpeg. YouTube no
#: longer serves a single pre-muxed mp4 for most songs -- measured 2026-09-10 on
#: yt-dlp 2026.8.19 with a JS runtime, the format list had separate video-only and
#: audio-only streams and nothing combined, so the old `best[ext=mp4]/best` failed
#: with "Requested format is not available". H.264 first because it decodes in every
#: browser and WebView without a hardware-codec lottery (AV1 and VP9 do not); 720p is
#: plenty for lyrics on a TV and keeps a song to tens of megabytes. The single-file
#: choices only matter for a video that still has one.
#:
#: The final `+ba` pair is for a video whose only audio is Opus/WebM and which has no
#: combined file: every choice before it needs m4a audio or a single file, so such a
#: video used to fail outright. It must stay *last* -- nearly every video has separate
#: streams, so anywhere earlier it would win before H.264 + AAC was ever tried. It
#: still prefers H.264: measured 2026-09-12, an unconstrained `bv*` picked AV1. ffmpeg
#: copies Opus into the mp4 container (checked with ffprobe) and browsers play it.
VIDEO_FORMAT = (
    "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]"
    "/bv*[ext=mp4][height<=720]+ba[ext=m4a]"
    "/b[ext=mp4]/b"
    "/bv*[vcodec^=avc1][height<=720]+ba"
    "/bv*[height<=720]+ba"
)

#: Without ffmpeg nothing can be joined, so take a single pre-muxed file if the video
#: still has one and otherwise its audio. A song you can sing to with no picture beats
#: a download that fails; `download_song` notes the fallback on the row.
NO_FFMPEG_FORMAT = "b[ext=mp4]/b/ba[ext=m4a]/ba"

#: Shown inline on a library row that asked for video and got only audio.
AUDIO_ONLY_NOTE = "Audio only: downloading video needs ffmpeg on PATH."


def download_opts(
    song_id: str,
    target: Path,
    *,
    audio_only: bool = False,
    ffmpeg: str | None = None,
) -> dict[str, Any]:
    """The yt-dlp options for one download. Split out so tests can pin them.

    `ffmpeg` is the binary to merge with, or None when there is none, and it decides
    the video format. An audio-only download ignores it: one stream needs no merge.
    """
    opts = ytdl.base_opts(song_id, target)
    if audio_only:
        opts["format"] = AUDIO_FORMAT
    elif ffmpeg:
        opts["format"] = VIDEO_FORMAT
        opts["merge_output_format"] = "mp4"
        # The same binary pitch shift uses, rather than whatever yt-dlp finds itself.
        opts["ffmpeg_location"] = ffmpeg
    else:
        opts["format"] = NO_FFMPEG_FORMAT
    return opts


got_audio_only = ytdl.got_audio_only


def _download_blocking(
    song_id: str, url: str, *, audio_only: bool = False
) -> dict[str, Any]:
    """Fetch one video (or just its audio). Returns the info dict; raises on failure."""
    yt_dlp = _ytdlp()
    if yt_dlp is None:
        raise RuntimeError(INSTALL_HINT)
    # Looked up per download rather than at import, so installing ffmpeg takes effect
    # on the next song without restarting the backend.
    ffmpeg = None if audio_only else transpose.ffmpeg_path()
    opts = download_opts(
        song_id, store.songs_dir(), audio_only=audio_only, ffmpeg=ffmpeg
    )
    return ytdl.fetch_blocking(yt_dlp, url, opts)


async def download_song(song_id: str, url: str, *, audio_only: bool = False) -> None:
    """Download into `song_id`'s row, updating its status as it goes.

    Never raises: this runs detached, so a failure has to land in the row (where
    the UI shows it) rather than in an unretrieved task exception.
    """
    # Imported here, not at module scope: `session` imports `store`, and a
    # top-level import would close the cycle downloader → session → store →
    # downloader.
    from backend.modules.karaoke.session import publish_song, session

    async with ytdl.download_slots:
        song = store.update_song(song_id, status="downloading", error=None)
        if song:
            await publish_song(song)
        try:
            info = await asyncio.to_thread(
                functools.partial(
                    _download_blocking, song_id, url, audio_only=audio_only
                )
            )
        except Exception as exc:
            logger.warning("karaoke download failed for %s: %s", url, exc)
            failed = store.update_song(song_id, status="failed", error=str(exc)[:500])
            if failed:
                await publish_song(failed)
            # An entry may already be waiting on this file (it was queued while
            # the download ran). Tell the session so it drops it rather than
            # leaving the stage pointed at a song that will never arrive.
            await session.song_downloaded(song_id, ok=False)
            return

        path = ytdl.landed_file(info, store.songs_dir(), song_id)
        if path is None:
            failed = store.update_song(
                song_id, status="failed", error="Download produced no file"
            )
            if failed:
                await publish_song(failed)
            await session.song_downloaded(song_id, ok=False)
            return

        raw_title = str(info.get("title") or "")
        current = store.get_song(song_id) or {}
        artist, title = _split_title(raw_title)
        # A video download that could only get audio (no ffmpeg to join YouTube's
        # separate streams) is still a song you can sing to, so it lands as `ready`
        # -- with a note in `error`, which the library row shows inline without
        # disabling the song.
        fell_back = not audio_only and got_audio_only(info, path)
        if fell_back:
            logger.info("karaoke download %s fell back to audio only", song_id)
        updates: dict[str, Any] = {
            "filename": path.name,
            "duration": info.get("duration"),
            "size_bytes": path.stat().st_size,
            "status": "ready",
            "error": AUDIO_ONLY_NOTE if fell_back else None,
        }
        # Only fill in metadata the caller didn't supply — an explicit
        # title/artist from the agent or the UI outranks yt-dlp's guess.
        #
        # `title` is never empty here: the route seeds it with the URL, because
        # the column is NOT NULL and a row has to show *something* in the library
        # while it downloads. So the placeholder has to be recognised explicitly —
        # testing only for emptiness left a row permanently titled with its own
        # URL whenever the caller passed no title (any direct API/agent call that
        # skipped it).
        placeholder = not current.get("title") or current.get("title") == current.get(
            "url"
        )
        if not current.get("artist") and artist:
            updates["artist"] = artist
        if placeholder and title:
            updates["title"] = title
        song = store.update_song(song_id, **updates)
        if song:
            await publish_song(song)
        # The file exists now. This is what unblocks an entry that reached the
        # stage before its media did — the stage is waiting on the broadcast.
        await session.song_downloaded(song_id, ok=True)


def start_download(song_id: str, url: str, *, audio_only: bool = False) -> None:
    """Kick off a detached download, keeping a strong reference to the task."""
    ytdl.spawn(download_song(song_id, url, audio_only=audio_only))
