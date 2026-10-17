"""Getting music into the library: search a source, fetch, or import files.

Every path ends the same way — a file in `music/files/<track_id>.<ext>` and a
`ready` row — so the player never has to know where a track came from.

A download returns its row immediately (`queued`) and runs detached, exactly like
karaoke's: the row can be queued for playback before the file lands, and the
session flips its entry to `ready` when it does (`session.track_ready`).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.modules import ytdl
from backend.modules.music import events, library, tags
from backend.modules.music.providers import (
    DescribedTrack,
    FetchedFile,
    ProviderError,
    TrackCandidate,
    sources,
)

logger = logging.getLogger(__name__)

# Called with a freshly-ready track row. Enrichment and lyrics register here; a
# hook that raises is logged and skipped, never fails the download.
ReadyHook = Callable[[dict[str, Any]], Awaitable[None]]
_ready_hooks: list[ReadyHook] = []


def on_track_ready(hook: ReadyHook) -> None:
    if hook not in _ready_hooks:
        _ready_hooks.append(hook)


async def _run_ready_hooks(track: dict[str, Any]) -> None:
    for hook in list(_ready_hooks):
        try:
            await hook(track)
        except Exception:  # noqa: BLE001 — a hook is best-effort by contract
            logger.exception(
                "music ready hook %s failed", getattr(hook, "__name__", hook)
            )


# --- the built-in source: YouTube via yt-dlp ---------------------------------------


class YouTubeSource:
    """Audio-only YouTube through the shared fetcher (`backend.modules.ytdl`)."""

    id = "youtube"
    label = "YouTube"

    def configured(self) -> bool:
        return ytdl.available()

    async def search(self, query: str, *, limit: int = 10) -> list[TrackCandidate]:
        hits, note = await ytdl.search(query, limit=limit)
        if not hits and note and note not in ("No results.", "Empty query."):
            raise ProviderError(note)
        out: list[TrackCandidate] = []
        for hit in hits:
            artist, title = ytdl.split_title(hit.title)
            if not artist and hit.channel.endswith(" - Topic"):
                # YouTube Music's auto-generated "Artist - Topic" channels upload
                # the official audio under the bare song title.
                artist = hit.channel.removesuffix(" - Topic")
            out.append(
                TrackCandidate(
                    source=self.id,
                    ref=hit.video_id,
                    title=title or hit.title,
                    artist=artist,
                    duration=float(hit.duration) if hit.duration else None,
                    url=hit.url,
                    art_url=hit.thumbnail,
                    channel=hit.channel,
                    raw={"title": hit.title},
                )
            )
        return out

    async def fetch(
        self, candidate: TrackCandidate, target: Path, file_id: str
    ) -> FetchedFile:
        yt_dlp = ytdl.load()
        if yt_dlp is None:
            raise ProviderError(ytdl.INSTALL_HINT)
        url = candidate.url or f"https://www.youtube.com/watch?v={candidate.ref}"
        opts = ytdl.base_opts(file_id, target)
        opts["format"] = ytdl.AUDIO_FORMAT
        async with ytdl.download_slots:
            info = await asyncio.to_thread(
                functools.partial(ytdl.fetch_blocking, yt_dlp, url, opts)
            )
        path = ytdl.landed_file(info, target, file_id)
        if path is None:
            raise ProviderError("Download produced no file")
        artist, title = ytdl.split_title(str(info.get("title") or ""))
        if not artist:
            artist = str(info.get("artist") or "")
        return FetchedFile(
            path=path,
            title=str(info.get("track") or title or ""),
            artist=artist,
            duration=float(info["duration"]) if info.get("duration") else None,
        )


def register_builtin_sources() -> None:
    if sources.get("youtube") is None:
        sources.register(YouTubeSource())


# --- search ------------------------------------------------------------------------


async def search(
    query: str, *, limit: int = 10, source_ids: list[str] | None = None
) -> tuple[list[tuple[TrackCandidate, dict[str, Any] | None]], list[str]]:
    """Candidates from every configured source, each paired with the library row it
    already is (or None). Returns (pairs, notes)."""
    chosen, notes = sources.resolve(source_ids)
    if not chosen:
        notes.append("no music source is available (is yt-dlp installed?)")
        return [], notes

    async def one(source: Any) -> list[TrackCandidate]:
        try:
            return await source.search(query, limit=limit)
        except Exception as exc:  # noqa: BLE001 — one dead source degrades the result
            notes.append(f"{source.id}: {exc}")
            return []

    batches = await asyncio.gather(*(one(s) for s in chosen))
    pairs = []
    for batch in batches:
        for cand in batch:
            pairs.append((cand, library.find_by_source(cand.source, cand.ref)))
    return pairs, notes


# --- download ----------------------------------------------------------------------


def _placeholder(track: dict[str, Any]) -> bool:
    return not track.get("title") or track.get("title") == track.get("source_ref")


async def start_download(
    candidate: TrackCandidate,
    *,
    meta: DescribedTrack | None = None,
    confidence: float | None = None,
    alternatives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create the row and start fetching. Returns the row (`queued`) immediately.

    Idempotent per source ref: asking for a video already in the library returns
    that row instead of a second copy. `meta` is clean metadata from an importer
    (Spotify knows the album; a YouTube title doesn't), and wins over the
    candidate's guess.
    """
    existing = library.find_by_source(candidate.source, candidate.ref)
    if existing is not None:
        return existing
    source = sources.get(candidate.source)
    if source is None:
        raise ProviderError(f"unknown source {candidate.source!r}")
    if not source.configured():
        raise ProviderError(f"{candidate.source} is not available here")

    external_ids: dict[str, str] = {}
    if candidate.source == "youtube":
        external_ids["youtube"] = candidate.ref
    if meta is not None:
        external_ids.update(meta.external_ids)
    track = library.create_track(
        title=(meta.title if meta and meta.title else candidate.title) or candidate.ref,
        artist=(meta.artist if meta and meta.artist else candidate.artist),
        album=(meta.album if meta else "") or candidate.album,
        duration=(meta.duration if meta and meta.duration else candidate.duration),
        source=candidate.source,
        source_ref=candidate.ref,
        external_ids=external_ids,
        art_url=(meta.art_url if meta and meta.art_url else candidate.art_url) or "",
        match_confidence=confidence,
        candidates=alternatives or [],
        status="queued",
    )
    await events.publish_track(track)
    ytdl.spawn(_download(track["id"], candidate, has_meta=meta is not None))
    return track


async def _download(
    track_id: str, candidate: TrackCandidate, *, has_meta: bool
) -> None:
    """Fetch into `track_id`'s row. Never raises: it runs detached, so failure has
    to land in the row (where the UI shows it) and in the session."""
    from backend.modules.music.session import session  # noqa: PLC0415 — import cycle

    source = sources.get(candidate.source)
    track = library.update_track(track_id, status="downloading", error=None)
    if track:
        await events.publish_track(track)
    try:
        if source is None:
            raise ProviderError(f"unknown source {candidate.source!r}")
        fetched = await source.fetch(candidate, library.files_dir(), track_id)
        fields = await asyncio.to_thread(_file_fields, fetched.path)
    except Exception as exc:  # noqa: BLE001 — the row is where failure goes
        logger.warning("music download failed for %s: %s", candidate.ref, exc)
        failed = library.update_track(track_id, status="failed", error=str(exc)[:500])
        if failed:
            await events.publish_track(failed)
        await session.track_ready(track_id, ok=False)
        return

    current = library.get_track(track_id) or {}
    tag_fields = fields.pop("tags", {})
    updates: dict[str, Any] = {**fields, "status": "ready", "error": None}
    # Fill only what nobody supplied: importer metadata and a person's input
    # outrank both the file's tags and yt-dlp's title split.
    for key, value in tag_fields.items():
        if key in ("duration", "bitrate", "art_hash") or not current.get(key):
            updates[key] = value
    if not has_meta:
        if fetched.artist and not current.get("artist"):
            updates["artist"] = fetched.artist
        if fetched.title and _placeholder(current):
            updates["title"] = fetched.title
    if fetched.duration and not updates.get("duration"):
        updates["duration"] = fetched.duration
    track = library.update_track(track_id, **updates)
    if track:
        await events.publish_track(track)
    await session.track_ready(track_id, ok=True)
    if track:
        await _run_ready_hooks(track)


async def download_into(track_id: str, candidate: TrackCandidate) -> None:
    """Fetch `candidate` into an existing row (a reviewed import), keeping the row's
    metadata — it came from the importer and is better than the candidate's."""
    await _download(track_id, candidate, has_meta=True)


def _file_fields(path: Path) -> dict[str, Any]:
    """Blocking: hash, size and tags of a file that just landed."""
    file_tags = tags.read_tags(path)
    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "content_hash": tags.hash_file(path),
        "tags": tags.row_fields(file_tags),
    }


# --- import files ------------------------------------------------------------------


def _walk(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            out += sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in tags.AUDIO_EXTS
            )
        elif path.is_file() and path.suffix.lower() in tags.AUDIO_EXTS:
            out.append(path)
    return out


def _import_one(src: Path, mode: str) -> tuple[str, dict[str, Any] | None]:
    """Blocking. Returns (outcome, row): outcome is `imported` | `skipped` | `failed`."""
    try:
        digest = tags.hash_file(src)
    except OSError as exc:
        logger.info("cannot read %s: %s", src, exc)
        return "failed", None
    if library.find_by_hash(digest) is not None:
        return "skipped", None

    file_tags = tags.read_tags(src)
    title = file_tags.title
    artist = file_tags.artist
    if not title:
        # No tags: the filename is the only description there is. "Artist - Title"
        # is the common convention; anything else becomes the title.
        guessed_artist, guessed_title = ytdl.split_title(src.stem)
        title = guessed_title or src.stem
        artist = artist or guessed_artist
    track = library.create_track(
        title=title,
        status="queued",
        artist=artist,
        source="import",
        source_ref=str(src),
        external_ids=file_tags.external_ids,
        content_hash=digest,
    )
    target = library.files_dir() / f"{track['id']}{src.suffix.lower()}"
    try:
        if mode == "move":
            shutil.move(str(src), target)
        else:
            shutil.copy2(src, target)
    except OSError as exc:
        library.update_track(track["id"], status="failed", error=f"copy failed: {exc}")
        return "failed", None

    fields = tags.row_fields(file_tags)
    fields.pop("title", None)
    fields.pop("artist", None)
    row = library.update_track(
        track["id"],
        **fields,
        filename=target.name,
        size_bytes=target.stat().st_size,
        status="ready",
    )
    lrc = src.with_suffix(".lrc")
    if row is not None and lrc.is_file():
        try:
            row["_sidecar_lrc"] = lrc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if row is not None and file_tags.lyrics:
        row["_embedded_lyrics"] = file_tags.lyrics
    return "imported", row


async def import_files(paths: list[str], *, mode: str = "copy") -> dict[str, list[Any]]:
    """Copy (or move) audio files into the library. Duplicates by content are
    skipped, so importing the same folder twice is harmless."""
    files = await asyncio.to_thread(_walk, paths)
    result: dict[str, list[Any]] = {"imported": [], "skipped": [], "failed": []}
    for src in files:
        outcome, row = await asyncio.to_thread(_import_one, src, mode)
        if outcome == "imported" and row is not None:
            sidecar = row.pop("_sidecar_lrc", None)
            embedded = row.pop("_embedded_lyrics", None)
            if sidecar or embedded:
                from backend.modules.music import lyrics  # noqa: PLC0415

                lyrics.store_local(row["id"], sidecar=sidecar, embedded=embedded)
            result["imported"].append(row)
            await events.publish_track(row)
            await _run_ready_hooks(row)
        else:
            result[outcome].append(str(src))
    return result
