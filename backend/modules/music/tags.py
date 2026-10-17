"""Read what a file says about itself: tags, embedded art, a content hash.

Read-only by design (see `library.py`): tags seed a row on import, and the row is
authoritative from then on. tinytag rather than mutagen because it is MIT-licensed,
pure Python, and reading is all this needs.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.modules.music import library

logger = logging.getLogger(__name__)

AUDIO_EXTS = frozenset(
    {
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".oga",
        ".opus",
        ".wav",
        ".wma",
        ".aiff",
        ".aif",
    }
)

_ART_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@dataclass
class FileTags:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_no: int | None = None
    disc_no: int | None = None
    year: str = ""
    genre: str = ""
    duration: float | None = None
    bitrate: float | None = None
    lyrics: str = ""
    external_ids: dict[str, str] = field(default_factory=dict)
    art: bytes | None = None
    art_mime: str = ""


def read_tags(path: Path) -> FileTags:
    """Whatever tinytag can find. Never raises: an untaggable file is still a file."""
    try:
        from tinytag import TinyTag  # noqa: PLC0415 — keeps app import light
    except ImportError:
        return FileTags()
    try:
        tag = TinyTag.get(path, image=True)
    except Exception as exc:  # noqa: BLE001 — tinytag raises its own error types per format
        logger.info("could not read tags from %s: %s", path.name, exc)
        return FileTags()

    other: dict[str, Any] = dict(getattr(tag, "other", {}) or {})

    def first(key: str) -> str:
        value = other.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        return str(value or "").strip()

    out = FileTags(
        title=(tag.title or "").strip(),
        artist=(tag.artist or "").strip(),
        album=(tag.album or "").strip(),
        album_artist=(tag.albumartist or "").strip(),
        track_no=tag.track,
        disc_no=tag.disc,
        year=(tag.year or "").strip()[:4],
        genre=(tag.genre or "").strip(),
        duration=tag.duration,
        bitrate=tag.bitrate,
        lyrics=first("lyrics"),
    )
    if isrc := first("isrc"):
        out.external_ids["isrc"] = isrc
    for key in ("musicbrainz_trackid", "musicbrainz_recordingid"):
        if mbid := first(key):
            out.external_ids["mbid"] = mbid
            break
    image = tag.images.any if getattr(tag, "images", None) else None
    if image is not None and image.data:
        out.art = bytes(image.data)
        out.art_mime = (image.mime_type or "image/jpeg").lower()
    return out


def hash_file(path: Path) -> str:
    """sha256 of the contents — the dedup key for imports."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_art(data: bytes, mime: str = "image/jpeg") -> str:
    """Write cover art into `music/art/` keyed by its hash. Returns the hash.

    Content-addressed, so an album's twelve tracks share one file.
    """
    art_hash = hashlib.sha256(data).hexdigest()[:32]
    ext = _ART_EXT.get(mime.lower(), "jpg")
    target = library.art_dir() / f"{art_hash}.{ext}"
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    return art_hash


def row_fields(tags: FileTags) -> dict[str, Any]:
    """The `music_tracks` columns a file's tags fill in. Empty values are left out
    so they don't overwrite something better."""
    fields: dict[str, Any] = {
        "title": tags.title,
        "artist": tags.artist,
        "album": tags.album,
        "album_artist": tags.album_artist,
        "track_no": tags.track_no,
        "disc_no": tags.disc_no,
        "year": tags.year,
        "genre": tags.genre,
        "duration": tags.duration,
        "bitrate": tags.bitrate,
    }
    out = {k: v for k, v in fields.items() if v not in ("", None)}
    if tags.art:
        out["art_hash"] = store_art(tags.art, tags.art_mime)
    return out
