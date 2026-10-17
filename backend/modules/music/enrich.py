"""Clean up a track's metadata from MusicBrainz, and fetch its cover from the Cover
Art Archive.

A YouTube download arrives as "Toto - Africa (Official HD Video)" with a 16:9 video
frame for art. MusicBrainz turns that into title "Africa", artist "Toto", album
"Toto IV", 1982, and the Cover Art Archive has the sleeve. Both are free and
keyless; MusicBrainz asks for **at most one request per second** and a meaningful
User-Agent, and bans clients that ignore it, so every call goes through `_mb_get`.

What enrichment may change, in order of trust:

- fields a person edited (`locked_fields`) — never;
- a track with importer metadata (it came from a Spotify playlist, so its title
  and artist are already right) — only fills what's empty;
- a bare YouTube download — title and artist are replaced, but only when the
  MusicBrainz title is recognisably inside the YouTube one. A confident match on a
  *different* song (a cover, a live cut) must not rename the file.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from backend.modules.music import events, library, tags

logger = logging.getLogger(__name__)

MB_URL = "https://musicbrainz.org/ws/2/recording"
CAA_URL = "https://coverartarchive.org/release/{mbid}/front-500"
USER_AGENT = "horrible-dashboard-music/1.0 ( music-library )"
_TIMEOUT = 15.0
_MIN_SCORE = 85
_DURATION_SLACK = 8.0

_mb_lock = asyncio.Lock()
_mb_last = 0.0

# What's left of a YouTube title once the song name is taken out: noise that says
# nothing about *which* song it is.
_TITLE_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|visuali[sz]er|hd|hq|4k|remaster|mv|m/v)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s+(?:ft\.?|feat\.?|featuring)\s+.*$", re.IGNORECASE)

# Ids a describer gave us. Their presence means the title/artist are trustworthy.
_TRUSTED_ID_KINDS = frozenset({"spotify", "apple", "isrc", "mbid", "deezer", "tidal"})


def clean_title(title: str) -> str:
    return _FEAT.sub("", _TITLE_NOISE.sub("", title or "")).strip(" -–—|")


async def _mb_get(params: dict[str, Any]) -> dict[str, Any] | None:
    """One MusicBrainz request, spaced ≥1.1 s from the last one, process-wide."""
    global _mb_last
    async with _mb_lock:
        wait = 1.1 - (time.monotonic() - _mb_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT}
            ) as client:
                resp = await client.get(MB_URL, params={**params, "fmt": "json"})
        finally:
            _mb_last = time.monotonic()
    if resp.status_code != 200:
        logger.info("musicbrainz %s for %s", resp.status_code, params.get("query"))
        return None
    return resp.json()


def _quote(text: str) -> str:
    return '"' + text.replace("\\", " ").replace('"', " ") + '"'


def _credit(recording: dict[str, Any]) -> str:
    parts = []
    for credit in recording.get("artist-credit") or []:
        if isinstance(credit, dict):
            parts.append(
                str(credit.get("name") or credit.get("artist", {}).get("name") or "")
            )
            parts.append(str(credit.get("joinphrase") or ""))
    return "".join(parts).strip()


def _pick_release(recording: dict[str, Any]) -> dict[str, Any] | None:
    """The release a listener would call "the album": official, an Album over a
    compilation or single, earliest first."""
    releases = [r for r in recording.get("releases") or [] if isinstance(r, dict)]
    if not releases:
        return None

    def rank(release: dict[str, Any]) -> tuple[int, int, str]:
        group = release.get("release-group") or {}
        primary = group.get("primary-type") or ""
        secondary = group.get("secondary-types") or []
        type_rank = (
            0
            if primary == "Album" and not secondary
            else 1
            if primary in ("Album", "EP")
            else 2
        )
        status_rank = 0 if release.get("status") == "Official" else 1
        return (status_rank, type_rank, release.get("date") or "9999")

    return sorted(releases, key=rank)[0]


async def lookup(
    title: str, artist: str, duration: float | None
) -> dict[str, Any] | None:
    """The best MusicBrainz recording for a description, or None."""
    clean = clean_title(title)
    if not clean:
        return None
    query = f"recording:{_quote(clean)}"
    if artist:
        query += f" AND artist:{_quote(artist)}"
    data = await _mb_get({"query": query, "limit": 8})
    if not data:
        return None
    best = None
    for rec in data.get("recordings") or []:
        score = int(rec.get("score") or 0)
        if score < _MIN_SCORE:
            continue
        length = rec.get("length")
        if duration and length and abs(length / 1000 - duration) > _DURATION_SLACK:
            continue
        best = rec
        break
    if best is None:
        return None
    release = _pick_release(best)
    group = (release or {}).get("release-group") or {}
    out: dict[str, Any] = {
        "mbid": best.get("id"),
        "title": best.get("title") or "",
        "artist": _credit(best),
        "album": (release or {}).get("title") or "",
        "year": ((release or {}).get("date") or "")[:4],
        "release_id": (release or {}).get("id"),
        "release_group_id": group.get("id"),
        "isrc": (best.get("isrcs") or [None])[0],
        "duration": (best.get("length") or 0) / 1000 or None,
    }
    # Album artist: the release's credit when MusicBrainz gave it, else the track's.
    if release and release.get("artist-credit"):
        out["album_artist"] = _credit(release)
    return out


async def fetch_cover(release_id: str) -> str | None:
    """Cover Art Archive front image for a release → art hash, or None."""
    if not release_id:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(CAA_URL.format(mbid=release_id))
    except httpx.HTTPError as exc:
        logger.info("cover art fetch failed for %s: %s", release_id, exc)
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    if not mime.startswith("image/"):
        return None
    return await asyncio.to_thread(tags.store_art, resp.content, mime)


async def fetch_image(url: str) -> str | None:
    """Any image URL (a Spotify album cover, a YouTube thumbnail) → art hash."""
    if not url or not url.startswith("https://"):
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return None
    mime = resp.headers.get("content-type", "").split(";")[0]
    if resp.status_code != 200 or not mime.startswith("image/"):
        return None
    return await asyncio.to_thread(tags.store_art, resp.content, mime)


def _title_contains(youtube_title: str, mb_title: str) -> bool:
    have = library.normalize(youtube_title)
    want = library.normalize(mb_title)
    return bool(want) and (want == have or want in have)


def plan_updates(track: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """Which fields a lookup result may change on this row. Pure, for tests."""
    locked = set(track.get("locked_fields") or [])
    ids = track.get("external_ids") or {}
    trusted = bool(_TRUSTED_ID_KINDS & set(ids)) and track.get("source") != "import"
    updates: dict[str, Any] = {}

    def fill(field: str, value: Any, *, replace: bool = False) -> None:
        if not value or field in locked:
            return
        if replace or not track.get(field):
            updates[field] = value

    renamable = (
        track.get("source") not in ("import",)
        and not trusted
        and _title_contains(track.get("title", ""), found["title"])
    )
    fill("title", found["title"], replace=renamable)
    fill("artist", found["artist"], replace=renamable)
    fill("album", found["album"])
    fill("album_artist", found.get("album_artist") or "")
    fill("year", found["year"])
    new_ids = dict(ids)
    if found.get("mbid") and "mbid" not in new_ids:
        new_ids["mbid"] = found["mbid"]
    if found.get("isrc") and "isrc" not in new_ids:
        new_ids["isrc"] = found["isrc"]
    if new_ids != ids:
        updates["external_ids"] = new_ids
    return updates


async def enrich_track(track_id: str, *, force: bool = False) -> dict[str, Any] | None:
    """Look a track up and apply what may be applied. Returns the updated row."""
    track = library.get_track(track_id)
    if track is None or track["status"] not in ("ready", "needs_review"):
        return track
    if track.get("enriched_at") and not force:
        return track
    try:
        found = await lookup(
            track["title"], track.get("artist") or "", track.get("duration")
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("musicbrainz lookup failed for %s: %s", track_id, exc)
        return track
    updates: dict[str, Any] = {"enriched_at": library.now_iso()}
    if found:
        updates.update(plan_updates(track, found))
    if not track.get("art_hash"):
        updates.update(await _art_for(track, found))
    updated = library.update_track(track_id, **updates)
    if updated is not None:
        await events.publish_track(updated)
        from backend.modules.music.session import session  # noqa: PLC0415 — cycle

        await session.track_changed(updated)
    return updated


async def _art_for(track: dict[str, Any], found: dict[str, Any] | None) -> dict[str, Any]:
    """Cover art, most-right first: an importer's own cover (Spotify knows exactly
    which release it meant), then the Cover Art Archive, then whatever URL the
    source offered (a YouTube thumbnail — a video frame, but better than nothing)."""
    ids = track.get("external_ids") or {}
    art_url = track.get("art_url") or ""
    trusted = bool(_TRUSTED_ID_KINDS & set(ids))
    art = await fetch_image(art_url) if trusted and art_url else None
    if not art and found:
        art = await fetch_cover(found.get("release_id") or "")
    if not art and art_url and not trusted:
        art = await fetch_image(art_url)
    return {"art_hash": art} if art else {}


def auto_enrich_enabled() -> bool:
    try:
        from backend.modules.settings.routes import get_value  # noqa: PLC0415

        return bool(get_value("music.autoEnrich", True))
    except Exception:  # noqa: BLE001 — settings unavailable in some test setups
        return True


async def on_ready(track: dict[str, Any]) -> None:
    """Ready hook: enrich in the background, so a download finishes immediately."""
    if not auto_enrich_enabled():
        return
    from backend.modules import ytdl  # noqa: PLC0415

    ytdl.spawn(enrich_track(track["id"]))
