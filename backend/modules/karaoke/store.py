"""The `karaoke_songs` catalog — one row per downloaded video.

A SQLite table in the app database (`$HORRIBLE_DATA_DIR/app.db`), same as
`library_sources`; the media files themselves live under
`$HORRIBLE_DATA_DIR/karaoke/songs/`. The row is the index (title, artist, play
count) and the file is the payload, exactly the split the library module uses for
artifacts.

The filename is derived from the row id, not from the title. PiKaraoke encodes
metadata into the filename (`Artist - Title---videoid.mp4`) because its library
*is* the directory listing; here the database is the index, so a filename only has
to be unique and safe on every OS — which a title never is (`AC/DC`, emoji, a
1,200-character clickbait title, and Windows' 260-char path limit).
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from backend.modules.database.app_db import ensure_app_db_dir, get_data_dir


# Database files this process has already run the schema DDL against. The library
# module calls its `init_*_db` from each public entry point; this does the same job
# without repeating the call at every call site, and without paying the DDL on
# every connection.
#
# Keyed by *path*, not a bare bool: `HORRIBLE_DATA_DIR` is env-driven, and a test
# that points it at a fresh tmp dir would otherwise inherit a True flag from the
# previous test and query a table that was never created in the new file.
_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked *before* the call: `init_karaoke_db` opens a connection through
        # this same helper, so marking afterwards would recurse forever.
        _initialized.add(path)
        init_karaoke_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def songs_dir() -> Path:
    """Where downloaded media lives. Created on demand."""
    path = get_data_dir() / "karaoke" / "songs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def init_karaoke_db() -> None:
    """Create the songs table (idempotent)."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS karaoke_songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT '',
                video_id TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                duration REAL,
                status TEXT NOT NULL DEFAULT 'ready',
                error TEXT,
                size_bytes INTEGER,
                play_count INTEGER NOT NULL DEFAULT 0,
                last_played TIMESTAMP,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Not UNIQUE: a failed download leaves a row behind, and re-requesting the
        # same video must be allowed to produce a second one. `find_by_video_id`
        # filters to ready rows instead.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_karaoke_video ON karaoke_songs(video_id)"
        )


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "title": r["title"],
        "artist": r["artist"],
        "video_id": r["video_id"],
        "url": r["url"],
        "filename": r["filename"],
        "duration": r["duration"],
        "status": r["status"],
        "error": r["error"],
        "size_bytes": r["size_bytes"],
        "play_count": r["play_count"],
        "last_played": r["last_played"],
        "added_at": r["added_at"],
    }


def create_song(
    *,
    title: str,
    artist: str = "",
    video_id: str = "",
    url: str = "",
    status: str = "queued",
) -> dict[str, Any]:
    song_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO karaoke_songs (id, title, artist, video_id, url, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (song_id, title, artist, video_id, url, status),
        )
    song = get_song(song_id)
    assert song is not None
    return song


def update_song(song_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {
        "title",
        "artist",
        "video_id",
        "url",
        "filename",
        "duration",
        "status",
        "error",
        "size_bytes",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_song(song_id)
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with get_db_conn() as conn:
        conn.execute(
            f"UPDATE karaoke_songs SET {assignments} WHERE id = ?",
            (*updates.values(), song_id),
        )
    return get_song(song_id)


def get_song(song_id: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM karaoke_songs WHERE id = ?", (song_id,)
        ).fetchone()
    return _row(row) if row else None


def find_by_video_id(video_id: str) -> dict[str, Any] | None:
    """The ready song for this video, if we already have it.

    Only `ready` rows count: a `failed` row means we tried and don't have the file,
    and returning it would make the UI offer "queue" for something unplayable.
    """
    if not video_id:
        return None
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM karaoke_songs WHERE video_id = ? AND status = 'ready'"
            " ORDER BY added_at DESC LIMIT 1",
            (video_id,),
        ).fetchone()
    return _row(row) if row else None


def list_songs(search: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """The library, newest first. `search` matches title or artist, case-insensitively."""
    sql = "SELECT * FROM karaoke_songs"
    params: list[Any] = []
    if search.strip():
        sql += " WHERE (title LIKE ? OR artist LIKE ?)"
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern]
    sql += " ORDER BY added_at DESC LIMIT ?"
    params.append(limit)
    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows]


def mark_played(song_id: str) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE karaoke_songs SET play_count = play_count + 1,"
            " last_played = CURRENT_TIMESTAMP WHERE id = ?",
            (song_id,),
        )


def delete_song(song_id: str) -> bool:
    """Drop the row and its file. Returns False if there was no such song."""
    song = get_song(song_id)
    if song is None:
        return False
    path = song_path(song)
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            # The row goes regardless: a file we can't delete (locked by a player
            # on Windows) is a stale blob, not a reason to keep it listed.
            pass
    with get_db_conn() as conn:
        conn.execute("DELETE FROM karaoke_songs WHERE id = ?", (song_id,))
    return True


def song_path(song: dict[str, Any]) -> Path | None:
    """Resolve a row's media file, or None if it has none / escapes the songs dir.

    The containment check is not paranoia about our own writes — `filename` comes
    from yt-dlp's chosen extension and could in principle carry separators. Since
    this path is handed to a file response, a row must never be able to address
    something outside the songs directory.
    """
    filename = (song.get("filename") or "").strip()
    if not filename:
        return None
    root = songs_dir().resolve()
    candidate = (root / filename).resolve()
    if root not in candidate.parents and candidate != root:
        return None
    return candidate
