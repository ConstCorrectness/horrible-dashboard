"""yt-dlp: search YouTube, and fetch a video into the songs directory.

Two design notes worth keeping:

**Downloads do not ride the shared task queue.** `backend.modules.tasks` runs jobs
serially, and a karaoke download is minutes of network I/O — putting one there
would park every library ingest behind "Bohemian Rhapsody (Karaoke Version)". They
run as their own asyncio tasks under a small semaphore instead, so several guests
can queue songs at once without saturating the link.

**yt-dlp is called in a thread, not a subprocess.** It's a library dependency here,
so there's no binary to locate and no output to parse — but it is thoroughly
blocking, so every call goes through `asyncio.to_thread`.

Searching uses the `ytsearchN:` pseudo-extractor with `extract_flat`, which returns
the result list from one page fetch without resolving each video's formats. Doing it
non-flat is roughly a second *per hit* and makes the search box unusable.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from backend.modules.karaoke import store
from backend.modules.karaoke.models import SearchResult

logger = logging.getLogger(__name__)

# Two at a time. Enough that a party queueing songs doesn't feel serial, few
# enough that YouTube doesn't start throttling the node.
_download_semaphore = asyncio.Semaphore(2)
# Strong refs to in-flight downloads: asyncio only holds weak ones, so a task
# nobody awaits can be garbage-collected mid-download.
_in_flight: set[asyncio.Task[None]] = set()

INSTALL_HINT = "yt-dlp is not installed. Run: uv sync"


def _ytdlp() -> Any | None:
    """Import yt_dlp lazily, or None when it isn't installed.

    Lazy because the import is ~1s of module scanning and most sessions never open
    the karaoke pane; optional-tolerant because the rest of the module (library,
    queue, playback of already-downloaded songs) works fine without it.
    """
    try:
        import yt_dlp  # noqa: PLC0415 — deliberately deferred; see docstring
    except ImportError:
        return None
    return yt_dlp


def available() -> bool:
    """Whether downloads can run here. Kept as a bare bool for its callers; the
    three-state answer (and the install hint) is `extras.probe("yt-dlp")`."""
    return _ytdlp() is not None


def _duration(entry: dict[str, Any]) -> int | None:
    value = entry.get("duration")
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _thumbnail(entry: dict[str, Any]) -> str | None:
    thumbs = entry.get("thumbnails") or []
    if thumbs and isinstance(thumbs, list):
        last = thumbs[-1]
        if isinstance(last, dict) and last.get("url"):
            return str(last["url"])
    return entry.get("thumbnail")


def _search_blocking(query: str, limit: int) -> list[dict[str, Any]]:
    yt_dlp = _ytdlp()
    if yt_dlp is None:
        return []
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    if not isinstance(info, dict):
        return []
    entries = info.get("entries") or []
    return [e for e in entries if isinstance(e, dict)]


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
        entries = await asyncio.to_thread(_search_blocking, effective, limit)
    except Exception as exc:  # yt-dlp raises a wide variety of network errors
        logger.warning("karaoke search failed: %s", exc)
        return [], f"Search failed: {exc}"

    results: list[SearchResult] = []
    for entry in entries:
        video_id = str(entry.get("id") or "")
        if not video_id:
            continue
        results.append(
            SearchResult(
                video_id=video_id,
                title=str(entry.get("title") or video_id),
                url=str(
                    entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                ),
                channel=str(entry.get("channel") or entry.get("uploader") or ""),
                duration=_duration(entry),
                thumbnail=_thumbnail(entry),
                downloaded=store.find_by_video_id(video_id) is not None,
            )
        )
    note = "" if results else "No results."
    return results, note


_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def parse_video_id(url: str) -> str:
    """Pull a video id out of a YouTube URL, or "" if there isn't one.

    A bare 11-character id is accepted as-is so the agent can pass either form.
    """
    text = (url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    match = _VIDEO_ID_RE.search(text)
    return match.group(1) if match else ""


def _split_title(title: str) -> tuple[str, str]:
    """Best-effort "Artist - Title" split off a YouTube title.

    Deliberately conservative: if there's no ` - ` separator we return an empty
    artist rather than guessing, because a wrong artist is worse than none — it
    shows up in the library list and in every queue row.
    """
    for sep in (" - ", " – ", " — "):
        if sep in title:
            artist, _, rest = title.partition(sep)
            return artist.strip(), rest.strip()
    return "", title.strip()


def _download_blocking(song_id: str, url: str) -> dict[str, Any]:
    """Fetch one video. Returns the info dict; raises on failure."""
    yt_dlp = _ytdlp()
    if yt_dlp is None:
        raise RuntimeError(INSTALL_HINT)
    target = store.songs_dir()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # The output name is the row id: unique, ASCII, and short enough for
        # Windows' path limit whatever the video is called. See store.py.
        "outtmpl": str(target / f"{song_id}.%(ext)s"),
        # Prefer a single already-muxed file. Karaoke videos are 720p at worst and
        # the merge step needs ffmpeg, which we don't want to hard-require just to
        # play a song at the original pitch.
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp returned no metadata")
    return info


async def download_song(song_id: str, url: str) -> None:
    """Download into `song_id`'s row, updating its status as it goes.

    Never raises: this runs detached, so a failure has to land in the row (where
    the UI shows it) rather than in an unretrieved task exception.
    """
    # Imported here, not at module scope: `session` imports `store`, and a
    # top-level import would close the cycle downloader → session → store →
    # downloader.
    from backend.modules.karaoke.session import publish_song, session

    async with _download_semaphore:
        song = store.update_song(song_id, status="downloading", error=None)
        if song:
            await publish_song(song)
        try:
            info = await asyncio.to_thread(_download_blocking, song_id, url)
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

        # yt-dlp reports the real extension only after the fact (the format it
        # actually got may not be the one the template guessed).
        ext = str(info.get("ext") or "mp4")
        path = store.songs_dir() / f"{song_id}.{ext}"
        if not path.exists():
            matches = sorted(store.songs_dir().glob(f"{song_id}.*"))
            if not matches:
                failed = store.update_song(
                    song_id, status="failed", error="Download produced no file"
                )
                if failed:
                    await publish_song(failed)
                await session.song_downloaded(song_id, ok=False)
                return
            path = matches[0]

        raw_title = str(info.get("title") or "")
        current = store.get_song(song_id) or {}
        artist, title = _split_title(raw_title)
        updates: dict[str, Any] = {
            "filename": path.name,
            "duration": info.get("duration"),
            "size_bytes": path.stat().st_size,
            "status": "ready",
            "error": None,
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


def start_download(song_id: str, url: str) -> None:
    """Kick off a detached download, keeping a strong reference to the task."""
    task = asyncio.create_task(download_song(song_id, url))
    _in_flight.add(task)
    task.add_done_callback(_in_flight.discard)
