"""The music library: the authoritative catalog of what this node can play.

Tables live in the app database (`$HORRIBLE_DATA_DIR/app.db`) and files under
`$HORRIBLE_DATA_DIR/music/`. Every track the player plays is a row here pointing at
a file the library owns — downloaded, or copied in from an import. Nothing plays
from a path outside `music/files/`, which is what makes `/media` safe to serve.

The database is the source of truth for metadata, not the file's tags. Tags are
*read* on import (`tags.py`) and never written back: fixing a title is a row
update, and the file stays byte-identical to what was fetched.

Filenames come from the row id, not the title, for the same reason as karaoke's:
a title is never a safe filename (`AC/DC`, emoji, Windows' 260-char path limit).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable

from backend.modules.database.app_db import ensure_app_db_dir, get_data_dir

# Keyed by path, not a bare bool: tests point HORRIBLE_DATA_DIR at a fresh tmp dir
# per test, and a bool would make the second test query tables that don't exist.
_initialized: set[str] = set()


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    path = str(ensure_app_db_dir())
    if path not in _initialized:
        # Marked before the call: init opens a connection through this helper.
        _initialized.add(path)
        init_music_db()
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


def music_dir() -> Path:
    path = get_data_dir() / "music"
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_dir() -> Path:
    path = music_dir() / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def art_dir() -> Path:
    path = music_dir() / "art"
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS music_tracks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        artist TEXT NOT NULL DEFAULT '',
        album TEXT NOT NULL DEFAULT '',
        album_artist TEXT NOT NULL DEFAULT '',
        track_no INTEGER,
        disc_no INTEGER,
        year TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        duration REAL,
        bitrate REAL,
        filename TEXT NOT NULL DEFAULT '',
        size_bytes INTEGER,
        content_hash TEXT NOT NULL DEFAULT '',
        -- queued | downloading | ready | failed | needs_review
        status TEXT NOT NULL DEFAULT 'ready',
        error TEXT,
        -- Where the file came from: `youtube`, `import`, a plugin source id.
        source TEXT NOT NULL DEFAULT '',
        source_ref TEXT NOT NULL DEFAULT '',
        -- JSON object: spotify / isrc / mbid / youtube / apple ids.
        external_ids TEXT NOT NULL DEFAULT '{}',
        art_hash TEXT NOT NULL DEFAULT '',
        -- Where cover art can be fetched if the file has none (an importer's album
        -- cover, a YouTube thumbnail). Enrichment turns it into `art_hash`.
        art_url TEXT NOT NULL DEFAULT '',
        -- How sure the resolver was that the file is the described track (0..1),
        -- and the alternatives it considered, for the review list.
        match_confidence REAL,
        candidates TEXT NOT NULL DEFAULT '[]',
        -- Which fields a person edited. Enrichment never overwrites these.
        locked_fields TEXT NOT NULL DEFAULT '[]',
        enriched_at TIMESTAMP,
        play_count INTEGER NOT NULL DEFAULT 0,
        skip_count INTEGER NOT NULL DEFAULT 0,
        last_played TIMESTAMP,
        rating INTEGER,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_music_tracks_source ON music_tracks(source, source_ref)",
    "CREATE INDEX IF NOT EXISTS idx_music_tracks_hash ON music_tracks(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_music_tracks_artist ON music_tracks(artist COLLATE NOCASE)",
    # Full-text search over the fields people search by. External-content FTS5 kept
    # in step by triggers, so a search never scans the table.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS music_tracks_fts USING fts5(
        title, artist, album, album_artist, genre,
        content='music_tracks', content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS music_tracks_ai AFTER INSERT ON music_tracks BEGIN
        INSERT INTO music_tracks_fts(rowid, title, artist, album, album_artist, genre)
        VALUES (new.rowid, new.title, new.artist, new.album, new.album_artist, new.genre);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS music_tracks_ad AFTER DELETE ON music_tracks BEGIN
        INSERT INTO music_tracks_fts(music_tracks_fts, rowid, title, artist, album, album_artist, genre)
        VALUES ('delete', old.rowid, old.title, old.artist, old.album, old.album_artist, old.genre);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS music_tracks_au AFTER UPDATE ON music_tracks BEGIN
        INSERT INTO music_tracks_fts(music_tracks_fts, rowid, title, artist, album, album_artist, genre)
        VALUES ('delete', old.rowid, old.title, old.artist, old.album, old.album_artist, old.genre);
        INSERT INTO music_tracks_fts(rowid, title, artist, album, album_artist, genre)
        VALUES (new.rowid, new.title, new.artist, new.album, new.album_artist, new.genre);
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS music_playlists (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        -- local | spotify:<id> | apple-file | m3u | csv | lastfm:loved
        origin TEXT NOT NULL DEFAULT 'local',
        description TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS music_playlist_items (
        playlist_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        track_id TEXT NOT NULL,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (playlist_id, position)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_music_pl_track ON music_playlist_items(track_id)",
    """
    CREATE TABLE IF NOT EXISTS music_plays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        played_seconds REAL NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_music_plays_track ON music_plays(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_music_plays_started ON music_plays(started_at)",
    """
    CREATE TABLE IF NOT EXISTS music_session (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        state TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS music_lyrics (
        track_id TEXT PRIMARY KEY,
        source TEXT NOT NULL DEFAULT '',
        synced TEXT NOT NULL DEFAULT '[]',
        plain TEXT NOT NULL DEFAULT '',
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS music_import_jobs (
        id TEXT PRIMARY KEY,
        importer TEXT NOT NULL,
        ref TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        origin TEXT NOT NULL,
        playlist_id TEXT NOT NULL DEFAULT '',
        -- running | done | failed | cancelled
        status TEXT NOT NULL DEFAULT 'running',
        note TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS music_import_items (
        job_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        described TEXT NOT NULL,
        -- pending | matched | downloading | done | review | failed
        status TEXT NOT NULL DEFAULT 'pending',
        track_id TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (job_id, idx)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS music_scrobble_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        listener TEXT NOT NULL,
        payload TEXT NOT NULL,
        started_at INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


def init_music_db() -> None:
    """Create every music table (idempotent)."""
    with get_db_conn() as conn:
        for ddl in _SCHEMA:
            conn.execute(ddl)


# --- tracks -------------------------------------------------------------------

_JSON_FIELDS = ("external_ids", "candidates", "locked_fields")

TRACK_FIELDS = frozenset(
    {
        "title",
        "artist",
        "album",
        "album_artist",
        "track_no",
        "disc_no",
        "year",
        "genre",
        "duration",
        "bitrate",
        "filename",
        "size_bytes",
        "content_hash",
        "status",
        "error",
        "source",
        "source_ref",
        "external_ids",
        "art_hash",
        "art_url",
        "match_confidence",
        "candidates",
        "locked_fields",
        "enriched_at",
        "rating",
    }
)

#: Fields a person may edit from the UI or the agent. Editing one locks it.
EDITABLE_FIELDS = frozenset(
    {
        "title",
        "artist",
        "album",
        "album_artist",
        "track_no",
        "disc_no",
        "year",
        "genre",
        "rating",
    }
)


def _track(row: Any) -> dict[str, Any]:
    out = dict(row)
    for key in _JSON_FIELDS:
        try:
            out[key] = json.loads(
                out.get(key) or ("{}" if key == "external_ids" else "[]")
            )
        except (TypeError, ValueError):
            out[key] = {} if key == "external_ids" else []
    return out


def _encode(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        k: json.dumps(v) if k in _JSON_FIELDS and not isinstance(v, str) else v
        for k, v in fields.items()
    }


def create_track(
    *, title: str, status: str = "queued", **fields: Any
) -> dict[str, Any]:
    track_id = uuid.uuid4().hex
    values = _encode({k: v for k, v in fields.items() if k in TRACK_FIELDS})
    values["title"] = title
    values["status"] = status
    cols = ", ".join(["id", *values])
    marks = ", ".join("?" * (len(values) + 1))
    with get_db_conn() as conn:
        conn.execute(
            f"INSERT INTO music_tracks ({cols}) VALUES ({marks})",
            (track_id, *values.values()),
        )
    track = get_track(track_id)
    assert track is not None
    return track


def update_track(track_id: str, **fields: Any) -> dict[str, Any] | None:
    updates = _encode({k: v for k, v in fields.items() if k in TRACK_FIELDS})
    if not updates:
        return get_track(track_id)
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with get_db_conn() as conn:
        conn.execute(
            f"UPDATE music_tracks SET {assignments} WHERE id = ?",
            (*updates.values(), track_id),
        )
    return get_track(track_id)


def edit_track(track_id: str, **fields: Any) -> dict[str, Any] | None:
    """A person's edit: applies `EDITABLE_FIELDS` and locks them against enrichment."""
    track = get_track(track_id)
    if track is None:
        return None
    edits = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS}
    if not edits:
        return track
    locked = sorted(set(track["locked_fields"]) | (set(edits) - {"rating"}))
    return update_track(track_id, **edits, locked_fields=locked)


def get_track(track_id: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_tracks WHERE id = ?", (track_id,)
        ).fetchone()
    return _track(row) if row else None


def get_tracks(track_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = list(dict.fromkeys(track_ids))
    if not ids:
        return {}
    marks = ", ".join("?" * len(ids))
    with get_db_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM music_tracks WHERE id IN ({marks})", ids
        ).fetchall()
    return {r["id"]: _track(r) for r in rows}


def find_by_source(source: str, ref: str) -> dict[str, Any] | None:
    """The live (not failed) track already fetched from this source ref."""
    if not ref:
        return None
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_tracks WHERE source = ? AND source_ref = ?"
            " AND status != 'failed' ORDER BY added_at DESC LIMIT 1",
            (source, ref),
        ).fetchone()
    return _track(row) if row else None


def find_by_hash(content_hash: str) -> dict[str, Any] | None:
    if not content_hash:
        return None
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_tracks WHERE content_hash = ? AND status = 'ready' LIMIT 1",
            (content_hash,),
        ).fetchone()
    return _track(row) if row else None


def find_by_external_id(kind: str, value: str) -> dict[str, Any] | None:
    if not value:
        return None
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_tracks WHERE json_extract(external_ids, ?) = ?"
            " AND status != 'failed' LIMIT 1",
            (f"$.{kind}", value),
        ).fetchone()
    return _track(row) if row else None


_NORM_RE = re.compile(r"[^\w]+", re.UNICODE)
# Bracketed suffixes that don't change which song it is.
_NOISE_RE = re.compile(
    r"[\(\[](?:official|lyrics?|audio|video|hd|hq|remaster(?:ed)?[^\)\]]*|\d{4} remaster[^\)\]]*)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Lowercase, strip bracketed noise and punctuation: the key two descriptions of
    the same song agree on ("Africa (Remastered 2019)" ~ "africa")."""
    text = _NOISE_RE.sub(" ", text or "")
    return " ".join(_NORM_RE.sub(" ", text.lower()).split())


def find_by_name(artist: str, title: str) -> dict[str, Any] | None:
    """A ready track whose normalized artist and title both match."""
    want_title = normalize(title)
    if not want_title:
        return None
    want_artist = normalize(artist)
    for track in list_tracks(search=title, limit=50, status="ready"):
        if normalize(track["title"]) != want_title:
            continue
        have = normalize(track["artist"])
        if (
            not want_artist
            or not have
            or want_artist == have
            or want_artist in have
            or have in want_artist
        ):
            return track
    return None


def _fts_query(search: str) -> str:
    """Prefix-match every token: `fleet mac` → `"fleet"* "mac"*`."""
    tokens = [t for t in _NORM_RE.sub(" ", search).split() if t]
    return " ".join(f'"{t}"*' for t in tokens)


_SORTS = {
    "added": "t.added_at DESC",
    "title": "t.title COLLATE NOCASE",
    "artist": "t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.disc_no, t.track_no",
    "album": "t.album COLLATE NOCASE, t.disc_no, t.track_no",
    "most_played": "t.play_count DESC, t.last_played DESC",
    "recently_played": "t.last_played DESC",
}


def list_tracks(
    search: str = "",
    *,
    artist: str = "",
    album: str = "",
    status: str | None = None,
    sort: str = "added",
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT t.* FROM music_tracks t"
    where: list[str] = []
    params: list[Any] = []
    query = _fts_query(search) if search.strip() else ""
    if query:
        sql += " JOIN music_tracks_fts f ON f.rowid = t.rowid"
        where.append("music_tracks_fts MATCH ?")
        params.append(query)
    if artist:
        where.append(
            "(t.artist = ? COLLATE NOCASE OR t.album_artist = ? COLLATE NOCASE)"
        )
        params += [artist, artist]
    if album:
        where.append("t.album = ? COLLATE NOCASE")
        params.append(album)
    if status:
        where.append("t.status = ?")
        params.append(status)
    if sort == "recently_played":
        where.append("t.last_played IS NOT NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = _SORTS.get(sort, _SORTS["added"])
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_track(r) for r in rows]


def count_tracks(status: str | None = "ready") -> int:
    with get_db_conn() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) FROM music_tracks WHERE status = ?", (status,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM music_tracks").fetchone()
    return int(row[0])


def list_artists(limit: int = 1000) -> list[dict[str, Any]]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(album_artist, ''), artist) AS name,"
            " COUNT(*) AS tracks, COUNT(DISTINCT album) AS albums,"
            " SUM(play_count) AS plays"
            " FROM music_tracks WHERE status = 'ready' AND artist != ''"
            " GROUP BY name COLLATE NOCASE ORDER BY name COLLATE NOCASE LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_albums(artist: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    sql = (
        "SELECT album AS name, COALESCE(NULLIF(MAX(album_artist), ''), MAX(artist)) AS artist,"
        " MAX(year) AS year, COUNT(*) AS tracks, MAX(art_hash) AS art_hash"
        " FROM music_tracks WHERE status = 'ready' AND album != ''"
    )
    params: list[Any] = []
    if artist:
        sql += " AND (artist = ? COLLATE NOCASE OR album_artist = ? COLLATE NOCASE)"
        params += [artist, artist]
    sql += " GROUP BY album COLLATE NOCASE ORDER BY album COLLATE NOCASE LIMIT ?"
    params.append(limit)
    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def track_path(track: dict[str, Any]) -> Path | None:
    """A row's media file, or None if it has none or it escapes `music/files/`.

    `filename` comes from a source's chosen extension and could in principle carry
    separators; this path is handed to a file response, so containment is checked.
    """
    filename = (track.get("filename") or "").strip()
    if not filename:
        return None
    root = files_dir().resolve()
    candidate = (root / filename).resolve()
    if root not in candidate.parents:
        return None
    return candidate


def art_path(art_hash: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16,64}", art_hash or ""):
        return None
    matches = sorted(art_dir().glob(f"{art_hash}.*"))
    return matches[0] if matches else None


def delete_track(track_id: str) -> bool:
    """Drop the row, its file, and its playlist memberships."""
    track = get_track(track_id)
    if track is None:
        return False
    path = track_path(track)
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            # A file Windows has locked (still playing) is a stale blob, not a
            # reason to keep the row listed.
            pass
    with get_db_conn() as conn:
        conn.execute("DELETE FROM music_tracks WHERE id = ?", (track_id,))
        conn.execute("DELETE FROM music_playlist_items WHERE track_id = ?", (track_id,))
        conn.execute("DELETE FROM music_lyrics WHERE track_id = ?", (track_id,))
    _compact_all_playlists()
    return True


# --- plays ----------------------------------------------------------------------


def record_play(
    track_id: str,
    *,
    started_at: str,
    played_seconds: float,
    completed: bool,
    skipped: bool,
) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO music_plays (track_id, started_at, played_seconds, completed, skipped)"
            " VALUES (?, ?, ?, ?, ?)",
            (track_id, started_at, played_seconds, int(completed), int(skipped)),
        )
        if skipped:
            conn.execute(
                "UPDATE music_tracks SET skip_count = skip_count + 1 WHERE id = ?",
                (track_id,),
            )


def mark_played(track_id: str) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE music_tracks SET play_count = play_count + 1, last_played = ? WHERE id = ?",
            (now_iso(), track_id),
        )


def recent_plays(limit: int = 50, since: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT p.track_id, p.started_at, p.played_seconds, p.completed, p.skipped,"
        " t.title, t.artist, t.album FROM music_plays p"
        " JOIN music_tracks t ON t.id = p.track_id"
    )
    params: list[Any] = []
    if since:
        sql += " WHERE p.started_at >= ?"
        params.append(since)
    sql += " ORDER BY p.started_at DESC LIMIT ?"
    params.append(limit)
    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def top_tracks(since: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
    """Most-played in a window, counting plays that weren't skipped."""
    sql = (
        "SELECT t.*, COUNT(p.id) AS plays_in_window FROM music_plays p"
        " JOIN music_tracks t ON t.id = p.track_id WHERE p.skipped = 0"
    )
    params: list[Any] = []
    if since:
        sql += " AND p.started_at >= ?"
        params.append(since)
    sql += " GROUP BY t.id ORDER BY plays_in_window DESC, t.last_played DESC LIMIT ?"
    params.append(limit)
    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        track = _track(r)
        track["plays_in_window"] = r["plays_in_window"]
        out.append(track)
    return out


# --- playlists ------------------------------------------------------------------


def create_playlist(
    name: str, *, origin: str = "local", description: str = ""
) -> dict[str, Any]:
    playlist_id = uuid.uuid4().hex
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO music_playlists (id, name, origin, description) VALUES (?, ?, ?, ?)",
            (playlist_id, name.strip() or "Untitled", origin, description),
        )
    playlist = get_playlist(playlist_id)
    assert playlist is not None
    return playlist


def get_playlist(
    playlist_id: str, *, with_tracks: bool = False
) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        count = conn.execute(
            "SELECT COUNT(*) FROM music_playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()[0]
        out["track_count"] = int(count)
        if with_tracks:
            items = conn.execute(
                "SELECT i.position, t.* FROM music_playlist_items i"
                " JOIN music_tracks t ON t.id = i.track_id"
                " WHERE i.playlist_id = ? ORDER BY i.position",
                (playlist_id,),
            ).fetchall()
            out["tracks"] = [_track(r) for r in items]
    return out


def find_playlist(name_or_id: str) -> dict[str, Any] | None:
    found = get_playlist(name_or_id)
    if found is not None:
        return found
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM music_playlists WHERE name = ? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1",
            (name_or_id.strip(),),
        ).fetchone()
    return get_playlist(row["id"]) if row else None


def find_playlist_by_origin(origin: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM music_playlists WHERE origin = ? LIMIT 1", (origin,)
        ).fetchone()
    return get_playlist(row["id"]) if row else None


def list_playlists() -> list[dict[str, Any]]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM music_playlist_items i WHERE i.playlist_id = p.id)"
            " AS track_count FROM music_playlists p ORDER BY p.updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def rename_playlist(playlist_id: str, name: str) -> dict[str, Any] | None:
    with get_db_conn() as conn:
        conn.execute(
            "UPDATE music_playlists SET name = ?, updated_at = ? WHERE id = ?",
            (name.strip() or "Untitled", now_iso(), playlist_id),
        )
    return get_playlist(playlist_id)


def delete_playlist(playlist_id: str) -> bool:
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM music_playlists WHERE id = ?", (playlist_id,))
        conn.execute(
            "DELETE FROM music_playlist_items WHERE playlist_id = ?", (playlist_id,)
        )
    return cur.rowcount > 0


def playlist_track_ids(playlist_id: str) -> list[str]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT track_id FROM music_playlist_items WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
    return [r["track_id"] for r in rows]


def set_playlist_tracks(playlist_id: str, track_ids: list[str]) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "DELETE FROM music_playlist_items WHERE playlist_id = ?", (playlist_id,)
        )
        conn.executemany(
            "INSERT INTO music_playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
            [(playlist_id, i, tid) for i, tid in enumerate(track_ids)],
        )
        conn.execute(
            "UPDATE music_playlists SET updated_at = ? WHERE id = ?",
            (now_iso(), playlist_id),
        )


def add_to_playlist(
    playlist_id: str, track_ids: list[str], *, dedupe: bool = True
) -> int:
    """Append tracks. Returns how many were added."""
    current = playlist_track_ids(playlist_id)
    have = set(current)
    added = [t for t in track_ids if not (dedupe and t in have)]
    if dedupe:
        added = list(dict.fromkeys(added))
    if added:
        set_playlist_tracks(playlist_id, current + added)
    return len(added)


def remove_from_playlist(playlist_id: str, track_ids: list[str]) -> int:
    current = playlist_track_ids(playlist_id)
    drop = set(track_ids)
    kept = [t for t in current if t not in drop]
    if len(kept) != len(current):
        set_playlist_tracks(playlist_id, kept)
    return len(current) - len(kept)


def _compact_all_playlists() -> None:
    """Re-number positions after a track delete left gaps."""
    for playlist in list_playlists():
        set_playlist_tracks(playlist["id"], playlist_track_ids(playlist["id"]))


# --- session persistence ------------------------------------------------------------


def load_session_state() -> dict[str, Any] | None:
    with get_db_conn() as conn:
        row = conn.execute("SELECT state FROM music_session WHERE id = 1").fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["state"])
    except (TypeError, ValueError):
        return None


def save_session_state(state: dict[str, Any]) -> None:
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO music_session (id, state) VALUES (1, ?)"
            " ON CONFLICT(id) DO UPDATE SET state = excluded.state",
            (json.dumps(state),),
        )
