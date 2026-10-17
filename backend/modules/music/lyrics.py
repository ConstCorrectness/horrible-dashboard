"""Lyrics for a library track: a sidecar `.lrc`, the file's own tag, then LRCLIB.

Cached per track in `music_lyrics`, including "we looked and there are none"
(source `none`), so a track without lyrics costs one lookup ever, not one per play.

LRCLIB (lrclib.net) is free, keyless, and returns time-synced lines when it has
them. It wants a descriptive User-Agent and matches best with the duration, which
the library always has.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from backend.modules.music import library

logger = logging.getLogger(__name__)

LRCLIB_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
USER_AGENT = "horrible-dashboard-music/1.0"
_TIMEOUT = 10.0

_LRC_LINE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_LRC_META = re.compile(r"^\[(ar|ti|al|by|offset|length|re|ve):", re.IGNORECASE)


def parse_lrc(text: str) -> list[list[Any]]:
    """`[mm:ss.xx]line` → `[[seconds, line], ...]`, sorted. A line with several
    stamps (a repeated chorus) appears once per stamp."""
    out: list[list[Any]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or _LRC_META.match(line):
            continue
        stamps = list(_LRC_LINE.finditer(line))
        if not stamps:
            continue
        words = _LRC_LINE.sub("", line).strip()
        for stamp in stamps:
            minutes = int(stamp.group(1))
            seconds = float(stamp.group(2).replace(":", "."))
            out.append([round(minutes * 60 + seconds, 2), words])
    out.sort(key=lambda pair: pair[0])
    return out


def _plain_from_synced(synced: list[list[Any]]) -> str:
    return "\n".join(str(words) for _, words in synced)


def _save(
    track_id: str, *, source: str, synced: list[list[Any]], plain: str
) -> dict[str, Any]:
    with library.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO music_lyrics (track_id, source, synced, plain, fetched_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(track_id) DO UPDATE SET"
            " source = excluded.source, synced = excluded.synced,"
            " plain = excluded.plain, fetched_at = excluded.fetched_at",
            (track_id, source, json.dumps(synced), plain, library.now_iso()),
        )
    return {"track_id": track_id, "source": source, "synced": synced, "plain": plain}


def cached(track_id: str) -> dict[str, Any] | None:
    with library.get_db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM music_lyrics WHERE track_id = ?", (track_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        synced = json.loads(row["synced"] or "[]")
    except ValueError:
        synced = []
    return {
        "track_id": track_id,
        "source": row["source"],
        "synced": synced,
        "plain": row["plain"],
    }


def store_local(
    track_id: str, *, sidecar: str | None = None, embedded: str | None = None
) -> dict[str, Any] | None:
    """Lyrics that came with the file. A sidecar `.lrc` beats the tag: people make
    sidecars on purpose."""
    for source, text in (("sidecar", sidecar), ("embedded", embedded)):
        if not text or not text.strip():
            continue
        synced = parse_lrc(text)
        plain = _plain_from_synced(synced) if synced else text.strip()
        return _save(track_id, source=source, synced=synced, plain=plain)
    return None


async def _lrclib(track: dict[str, Any]) -> dict[str, Any] | None:
    params: dict[str, Any] = {
        "track_name": track["title"],
        "artist_name": track.get("artist") or "",
    }
    if track.get("album"):
        params["album_name"] = track["album"]
    if track.get("duration"):
        params["duration"] = round(float(track["duration"]))
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
        resp = await client.get(LRCLIB_URL, params=params)
        if resp.status_code == 404:
            # The exact lookup is strict about album and duration; search is not.
            resp = await client.get(
                LRCLIB_SEARCH_URL,
                params={
                    "track_name": params["track_name"],
                    "artist_name": params["artist_name"],
                },
            )
            if resp.status_code != 200:
                return None
            hits = [h for h in resp.json() or [] if isinstance(h, dict)]
            if not hits:
                return None
            want = params.get("duration")

            def close(hit: dict[str, Any]) -> bool:
                return not want or not hit.get("duration") or abs(float(hit["duration"]) - want) <= 4

            timed = [h for h in hits if close(h)]
            if timed:
                return timed[0]
            # Only other cuts of the song (a radio edit against the album version):
            # the words are right, the timestamps aren't — keep the plain text.
            return {**hits[0], "syncedLyrics": None}
        if resp.status_code != 200:
            return None
        return resp.json()


async def get_lyrics(track_id: str, *, refresh: bool = False) -> dict[str, Any] | None:
    """Lyrics for a track, fetching from LRCLIB on a cache miss. None if the track
    doesn't exist; `source == "none"` when nobody has lyrics for it."""
    track = library.get_track(track_id)
    if track is None:
        return None
    hit = cached(track_id)
    if hit is not None and not (refresh and hit["source"] in ("none", "lrclib")):
        return hit
    if not track.get("artist") and not track.get("title"):
        return _save(track_id, source="none", synced=[], plain="")
    try:
        found = await _lrclib(track)
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("lrclib lookup failed for %s: %s", track_id, exc)
        # Don't cache a network failure as "none" — try again next time.
        return hit or {
            "track_id": track_id,
            "source": "unavailable",
            "synced": [],
            "plain": "",
        }
    if not found or found.get("instrumental"):
        return _save(
            track_id,
            source="none" if not found else "instrumental",
            synced=[],
            plain="",
        )
    synced = parse_lrc(found.get("syncedLyrics") or "")
    plain = (found.get("plainLyrics") or "").strip() or _plain_from_synced(synced)
    return _save(track_id, source="lrclib", synced=synced, plain=plain)
