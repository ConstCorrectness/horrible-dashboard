"""yt-dlp, shared: search YouTube and fetch media, for every feature that needs it.

Karaoke grew this, and the music library needs the same machinery: a lazy import,
flat search, the JS-runtime table YouTube's player challenges need, and a bounded
pool of detached downloads. What stays with each caller is what it does with the
file — karaoke keeps video and its own catalog, music keeps audio and its library.

Two design notes worth keeping:

**Downloads do not ride the shared task queue.** `backend.modules.tasks` runs jobs
serially, and a download is minutes of network I/O — putting one there would park
every library ingest behind it. They run as their own asyncio tasks under one small
semaphore instead. It is *one* semaphore for every caller on purpose: a playlist
import and a karaoke night share the same link to YouTube, and two pools of two
would be four concurrent fetches — the point where YouTube starts throttling.

**yt-dlp is called in a thread, not a subprocess.** It's a library dependency here,
so there's no binary to locate and no output to parse — but it is thoroughly
blocking, so every call goes through `asyncio.to_thread`.

Searching uses the `ytsearchN:` pseudo-extractor with `extract_flat`, which returns
the result list from one page fetch without resolving each video's formats. Doing it
non-flat is roughly a second *per hit* and makes a search box unusable.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INSTALL_HINT = "yt-dlp is not installed. Run: uv sync"

# Two at a time, across every feature. See the module docstring.
download_slots = asyncio.Semaphore(2)
# Strong refs to in-flight downloads: asyncio only holds weak ones, so a task
# nobody awaits can be garbage-collected mid-download.
_in_flight: set[asyncio.Task[Any]] = set()


def load() -> Any | None:
    """Import yt_dlp lazily, or None when it isn't installed.

    Lazy because the import is ~1s of module scanning and most sessions never
    download anything; optional-tolerant because everything around it (a library,
    a queue, playback of files already on disk) works fine without it.
    """
    try:
        import yt_dlp  # noqa: PLC0415 — deliberately deferred; see docstring
    except ImportError:
        return None
    return yt_dlp


def available() -> bool:
    """Whether downloads can run here. The three-state answer (and the install
    hint) is `extras.probe("yt-dlp")`."""
    return load() is not None


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Run a detached coroutine, keeping a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _in_flight.add(task)
    task.add_done_callback(_in_flight.discard)
    return task


# --- search -------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """One flat search result. Nothing is on disk yet."""

    video_id: str
    title: str
    url: str
    channel: str = ""
    duration: int | None = None
    thumbnail: str | None = None


def duration_of(entry: dict[str, Any]) -> int | None:
    value = entry.get("duration")
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def thumbnail_of(entry: dict[str, Any]) -> str | None:
    thumbs = entry.get("thumbnails") or []
    if thumbs and isinstance(thumbs, list):
        last = thumbs[-1]
        if isinstance(last, dict) and last.get("url"):
            return str(last["url"])
    return entry.get("thumbnail")


def search_blocking(yt_dlp: Any, query: str, limit: int) -> list[dict[str, Any]]:
    """The raw flat entries for `query`. Takes the yt_dlp module so a caller that
    resolved (or faked) it once keeps using that one."""
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


def to_hits(entries: list[dict[str, Any]]) -> list[Hit]:
    hits: list[Hit] = []
    for entry in entries:
        video_id = str(entry.get("id") or "")
        if not video_id:
            continue
        hits.append(
            Hit(
                video_id=video_id,
                title=str(entry.get("title") or video_id),
                url=str(
                    entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                ),
                channel=str(entry.get("channel") or entry.get("uploader") or ""),
                duration=duration_of(entry),
                thumbnail=thumbnail_of(entry),
            )
        )
    return hits


async def search(query: str, limit: int = 10) -> tuple[list[Hit], str]:
    """Search YouTube literally. Returns (hits, note) — `note` explains an empty list."""
    if not query.strip():
        return [], "Empty query."
    yt_dlp = load()
    if yt_dlp is None:
        return [], INSTALL_HINT
    try:
        entries = await asyncio.to_thread(search_blocking, yt_dlp, query.strip(), limit)
    except Exception as exc:  # yt-dlp raises a wide variety of network errors
        logger.warning("youtube search failed: %s", exc)
        return [], f"Search failed: {exc}"
    hits = to_hits(entries)
    return hits, "" if hits else "No results."


# --- identifiers and titles ---------------------------------------------------

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


def split_title(title: str) -> tuple[str, str]:
    """Best-effort "Artist - Title" split off a YouTube title.

    Deliberately conservative: if there's no ` - ` separator we return an empty
    artist rather than guessing, because a wrong artist is worse than none — it
    shows up in every library list and queue row.
    """
    for sep in (" - ", " – ", " — "):
        if sep in title:
            artist, _, rest = title.partition(sep)
            return artist.strip(), rest.strip()
    return "", title.strip()


# --- download -----------------------------------------------------------------

#: JavaScript runtimes yt-dlp may use to solve YouTube's player challenges. yt-dlp
#: enables only deno by default; without a runtime YouTube withholds most formats
#: and serves the rest behind 403s. Listing several is safe: an absent runtime is
#: skipped and the highest-priority one present is used (node and bun are common
#: on a dev box; deno usually is not). The challenge scripts themselves come from
#: `yt-dlp-ejs`, pulled in by the `yt-dlp[default]` dependency.
JS_RUNTIMES: dict[str, dict[str, Any]] = {"deno": {}, "node": {}, "bun": {}}

#: Audio only, for music that is heard rather than watched. A single stream needs no
#: ffmpeg merge -- and it is what YouTube still serves reliably: measured 2026-09-10,
#: `ba[ext=m4a]` downloaded where the pre-muxed mp4 the video path asks for was gone
#: or 403.
AUDIO_FORMAT = "ba[ext=m4a]/ba/b"

AUDIO_EXTS = frozenset({".m4a", ".mp3", ".opus", ".ogg", ".aac", ".wav", ".flac"})


def base_opts(file_id: str, target: Path) -> dict[str, Any]:
    """Options every download shares. The caller adds `format` (and any merge
    settings)."""
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # The output name is the caller's row id: unique, ASCII, and short enough for
        # Windows' path limit whatever the video is called.
        "outtmpl": str(target / f"{file_id}.%(ext)s"),
        # A fresh copy per download: yt-dlp mutates `js_runtimes` in place
        # (`_clean_js_runtimes` pops entries).
        "js_runtimes": {name: dict(cfg) for name, cfg in JS_RUNTIMES.items()},
    }


def fetch_blocking(yt_dlp: Any, url: str, opts: dict[str, Any]) -> dict[str, Any]:
    """Fetch one URL with `opts`. Returns the info dict; raises on failure."""
    if yt_dlp is None:
        raise RuntimeError(INSTALL_HINT)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp returned no metadata")
    return info


def landed_file(info: dict[str, Any], directory: Path, file_id: str) -> Path | None:
    """The file a finished download actually wrote, or None.

    yt-dlp reports the real extension only after the fact (the format it got may not
    be the one the template guessed), so trust `info["ext"]` first and fall back to
    whatever `<file_id>.*` exists.
    """
    ext = str(info.get("ext") or "")
    if ext:
        path = directory / f"{file_id}.{ext}"
        if path.exists():
            return path
    matches = sorted(directory.glob(f"{file_id}.*"))
    return matches[0] if matches else None


def got_audio_only(info: dict[str, Any], path: Path) -> bool:
    """Whether a finished download has no picture.

    yt-dlp reports `vcodec: "none"` for an audio-only format, and a merged download
    carries its video codec, so the codec decides; the extension is only a fallback
    for an info dict that omits codecs.
    """
    vcodec = info.get("vcodec")
    if vcodec is not None:
        return str(vcodec) == "none"
    return path.suffix.lower() in AUDIO_EXTS
