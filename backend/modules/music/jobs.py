"""Playlist import jobs: describe → match → resolve → download → link.

An importer hands back a list of *described* tracks. For each one the job:

1. **matches the library** — by a stable id (`spotify`, `isrc`, `mbid`, …), then by
   normalized artist+title. Re-syncing a Spotify playlist matches every track it
   imported last time this way, so a re-sync only fetches what's new;
2. imports a local file directly when the description points at one (an M3U or
   Apple XML entry with a `path` on this machine);
3. otherwise **resolves** it against the sources (`resolve.py`) and downloads the
   best candidate — or, under the confidence bar, files it for **review** with its
   alternatives instead of guessing;
4. writes the local playlist in the source's order, every step, so the playlist
   fills in as the job runs.

Jobs and their items are rows, so a job interrupted by a restart resumes where it
stopped (`resume_jobs`, from startup). The playlist *mirrors* its source: a track
removed from the Spotify playlist leaves the local one on the next sync (the file
stays in the library).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.modules import ytdl
from backend.modules.music import acquire, events, library, resolve
from backend.modules.music.providers import (
    DescribedTrack,
    ImportedPlaylist,
    ProviderError,
    TrackCandidate,
    importers,
)

logger = logging.getLogger(__name__)

_running: set[str] = set()


def _job_row(job_id: str) -> dict[str, Any] | None:
    with library.get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_import_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        job = dict(row)
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM music_import_items WHERE job_id = ? GROUP BY status",
            (job_id,),
        ).fetchall()
    tally = {r["status"]: r["n"] for r in counts}
    job["total"] = sum(tally.values())
    job["counts"] = tally
    job["finished"] = sum(
        tally.get(s, 0) for s in ("matched", "done", "review", "failed")
    )
    return job


def get_job(job_id: str, *, with_items: bool = False) -> dict[str, Any] | None:
    job = _job_row(job_id)
    if job is None or not with_items:
        return job
    with library.get_db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM music_import_items WHERE job_id = ? ORDER BY idx", (job_id,)
        ).fetchall()
    job["items"] = [{**dict(r), "described": json.loads(r["described"])} for r in rows]
    return job


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with library.get_db_conn() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM music_import_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    return [j for j in (_job_row(i) for i in ids) if j]


async def _publish(job_id: str) -> None:
    job = _job_row(job_id)
    if job:
        await events.publish("job", job)


def _set_item(job_id: str, idx: int, **fields: Any) -> None:
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with library.get_db_conn() as conn:
        conn.execute(
            f"UPDATE music_import_items SET {assignments} WHERE job_id = ? AND idx = ?",
            (*fields.values(), job_id, idx),
        )


def _set_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = library.now_iso()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with library.get_db_conn() as conn:
        conn.execute(
            f"UPDATE music_import_jobs SET {assignments} WHERE id = ?",
            (*fields.values(), job_id),
        )


async def start_job(
    importer_id: str, playlist: ImportedPlaylist, *, ref: str = ""
) -> dict[str, Any]:
    """Create the local playlist (or find the one this source synced to before) and
    start working through the tracks."""
    local = library.find_playlist_by_origin(playlist.origin)
    if local is None:
        local = library.create_playlist(playlist.name, origin=playlist.origin)
    elif local["name"] != playlist.name:
        library.rename_playlist(local["id"], playlist.name)
    job_id = uuid.uuid4().hex
    with library.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO music_import_jobs (id, importer, ref, name, origin, playlist_id, status, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                importer_id,
                ref,
                playlist.name,
                playlist.origin,
                local["id"],
                "running" if playlist.tracks else "done",
                playlist.note,
            ),
        )
        conn.executemany(
            "INSERT INTO music_import_items (job_id, idx, described) VALUES (?, ?, ?)",
            [(job_id, i, json.dumps(asdict(t))) for i, t in enumerate(playlist.tracks)],
        )
    await events.publish("library", {"playlists": True})
    if playlist.tracks:
        _spawn(job_id)
    await _publish(job_id)
    job = _job_row(job_id)
    assert job is not None
    return job


async def import_from_account(importer_id: str, ref: str) -> dict[str, Any]:
    importer = importers.get(importer_id)
    if importer is None:
        raise ProviderError(f"unknown importer {importer_id!r}")
    if importer.kind != "account":
        raise ProviderError(f"{importer.label} imports a file — upload one")
    if not importer.configured():
        raise ProviderError(f"{importer.label} isn't connected")
    playlist = await importer.read_playlist(ref)
    return await start_job(importer_id, playlist, ref=ref)


async def import_from_file(
    importer_id: str, filename: str, data: bytes
) -> dict[str, Any]:
    importer = importers.get(importer_id)
    if importer is None:
        raise ProviderError(f"unknown importer {importer_id!r}")
    playlist = await asyncio.to_thread(importer.parse, filename, data)
    return await start_job(importer_id, playlist, ref=filename)


def detect_file_importer(filename: str, data: bytes) -> str:
    """Which built-in file importer reads this upload, by extension then content."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".m3u", ".m3u8"):
        return "m3u"
    if suffix == ".csv":
        return "csv"
    if suffix in (".txt", ".xml"):
        return "apple-music"
    head = data[:64].lstrip(b"\xef\xbb\xbf")
    if head.startswith(b"#EXTM3U"):
        return "m3u"
    return "apple-music" if b"\x00" in head or head.startswith(b"<?xml") else "csv"


async def resync(playlist_id: str) -> dict[str, Any]:
    """Re-import an account playlist into the local one it created."""
    playlist = library.get_playlist(playlist_id)
    if playlist is None:
        raise ProviderError("playlist not found")
    origin = playlist["origin"] or ""
    with library.get_db_conn() as conn:
        row = conn.execute(
            "SELECT importer, ref FROM music_import_jobs WHERE origin = ? ORDER BY created_at DESC LIMIT 1",
            (origin,),
        ).fetchone()
    if row is None:
        raise ProviderError(
            "this playlist wasn't imported from an account, so there's nothing to sync"
        )
    importer = importers.get(row["importer"])
    if importer is None or importer.kind != "account":
        raise ProviderError(
            "file imports can't re-sync — import the updated file instead"
        )
    return await import_from_account(row["importer"], row["ref"])


def _spawn(job_id: str) -> None:
    if job_id in _running:
        return
    _running.add(job_id)
    task = ytdl.spawn(_run(job_id))
    task.add_done_callback(lambda _t: _running.discard(job_id))


def resume_jobs() -> int:
    """Pick up every job a restart interrupted. Returns how many."""
    with library.get_db_conn() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM music_import_jobs WHERE status = 'running'"
            )
        ]
        # An item that was mid-download when the process died has a row of its
        # own (the download resumes or fails there); only `pending` needs redoing.
    for job_id in ids:
        _spawn(job_id)
    return len(ids)


def cancel_job(job_id: str) -> bool:
    job = _job_row(job_id)
    if job is None or job["status"] != "running":
        return False
    _set_job(job_id, status="cancelled")
    return True


def _write_playlist(job_id: str, playlist_id: str) -> None:
    with library.get_db_conn() as conn:
        rows = conn.execute(
            "SELECT track_id FROM music_import_items WHERE job_id = ? AND track_id != '' ORDER BY idx",
            (job_id,),
        ).fetchall()
    ids = list(dict.fromkeys(r["track_id"] for r in rows))
    library.set_playlist_tracks(playlist_id, ids)


def _describe(raw: dict[str, Any]) -> DescribedTrack:
    return DescribedTrack(
        title=raw.get("title") or "",
        artist=raw.get("artist") or "",
        album=raw.get("album") or "",
        duration=raw.get("duration"),
        external_ids=dict(raw.get("external_ids") or {}),
        art_url=raw.get("art_url") or "",
    )


async def _run(job_id: str) -> None:
    job = _job_row(job_id)
    if job is None:
        return
    playlist_id = job["playlist_id"]
    with library.get_db_conn() as conn:
        items = [
            dict(r)
            for r in conn.execute(
                "SELECT idx, described FROM music_import_items WHERE job_id = ? AND status = 'pending' ORDER BY idx",
                (job_id,),
            )
        ]
    threshold = resolve.min_confidence()
    for item in items:
        current = _job_row(job_id)
        if current is None or current["status"] == "cancelled":
            return
        desc = _describe(json.loads(item["described"]))
        try:
            status, track_id, note = await _place(desc, threshold)
        except Exception as exc:  # noqa: BLE001 — one bad track never stops the job
            logger.warning("import item %s/%s failed: %s", job_id, item["idx"], exc)
            status, track_id, note = "failed", "", str(exc)[:300]
        _set_item(job_id, item["idx"], status=status, track_id=track_id, note=note)
        if track_id:
            _write_playlist(job_id, playlist_id)
            await events.publish("library", {"playlists": True})
        await _publish(job_id)
    if (_job_row(job_id) or {}).get("status") == "running":
        _set_job(job_id, status="done")
    await _publish(job_id)


async def _place(desc: DescribedTrack, threshold: float) -> tuple[str, str, str]:
    """Find or fetch one described track. Returns (item status, track id, note)."""
    existing = resolve.match_library(desc)
    if existing is not None:
        _merge_ids(existing, desc)
        return "matched", existing["id"], ""

    path = desc.external_ids.get("path")
    if path and Path(path).is_file():
        result = await acquire.import_files([path])
        if result["imported"]:
            row = result["imported"][0]
            _merge_ids(row, desc)
            return "matched", row["id"], "imported from disk"
        found = library.find_by_hash(await asyncio.to_thread(_hash, Path(path)))
        if found is not None:
            return "matched", found["id"], "already in the library"

    resolution = await resolve.resolve(desc)
    if resolution.best is None:
        note = "; ".join(resolution.notes) or "no match found"
        return "failed", "", note
    alternatives = [s.as_dict() for s in resolution.alternatives]
    if resolution.confidence < threshold:
        row = library.create_track(
            title=desc.title,
            artist=desc.artist,
            album=desc.album,
            duration=desc.duration,
            external_ids={k: v for k, v in desc.external_ids.items() if k != "path"},
            art_url=desc.art_url,
            match_confidence=resolution.confidence,
            candidates=alternatives,
            status="needs_review",
        )
        await events.publish_track(row)
        return "review", row["id"], f"best match only {resolution.confidence:.0%} sure"
    best = resolution.best.candidate
    clean = DescribedTrack(
        title=desc.title,
        artist=desc.artist,
        album=desc.album,
        duration=desc.duration,
        external_ids={k: v for k, v in desc.external_ids.items() if k != "path"},
        art_url=desc.art_url,
    )
    row = await acquire.start_download(
        best, meta=clean, confidence=resolution.confidence, alternatives=alternatives
    )
    return "done", row["id"], ""


def _hash(path: Path) -> str:
    from backend.modules.music import tags  # noqa: PLC0415

    return tags.hash_file(path)


def _merge_ids(track: dict[str, Any], desc: DescribedTrack) -> None:
    """Remember the describer's ids on a matched track, so the next sync matches it
    by id instead of by name."""
    ids = dict(track.get("external_ids") or {})
    merged = {**{k: v for k, v in desc.external_ids.items() if k != "path"}, **ids}
    if merged != ids:
        library.update_track(track["id"], external_ids=merged)


# --- review ------------------------------------------------------------------------


async def review(
    track_id: str, *, accept: bool, candidate: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Settle a `needs_review` track: download the chosen candidate, or drop it.

    `candidate` is one of the row's `candidates` (or any `{source, ref}` a person
    found themselves); omitted on accept means "the best one".
    """
    track = library.get_track(track_id)
    if track is None or track["status"] != "needs_review":
        return track
    if not accept:
        library.delete_track(track_id)
        await events.publish("library", {"removed": [track_id], "playlists": True})
        return None
    chosen = candidate or (track["candidates"][0] if track["candidates"] else None)
    if not chosen or not chosen.get("ref"):
        raise ProviderError("no candidate to download — search for one and pass it")
    cand = TrackCandidate(
        source=str(chosen.get("source") or "youtube"),
        ref=str(chosen["ref"]),
        title=str(chosen.get("title") or track["title"]),
        artist=str(chosen.get("artist") or ""),
        duration=chosen.get("duration"),
        url=str(chosen.get("url") or ""),
        art_url=chosen.get("art_url"),
        channel=str(chosen.get("channel") or ""),
    )
    # Reuse the row (it is already in playlists) rather than creating a second.
    ids = dict(track["external_ids"])
    if cand.source == "youtube":
        ids["youtube"] = cand.ref
    library.update_track(
        track_id,
        status="queued",
        source=cand.source,
        source_ref=cand.ref,
        external_ids=ids,
        match_confidence=1.0,
        art_url=track.get("art_url") or (cand.art_url or ""),
    )
    ytdl.spawn(acquire.download_into(track_id, cand))
    return library.get_track(track_id)


def review_list(limit: int = 200) -> list[dict[str, Any]]:
    return library.list_tracks(status="needs_review", limit=limit)
