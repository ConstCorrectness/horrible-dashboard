"""The DJ: pick what to play next from what you've been playing.

Deterministic on purpose, so radio works with no model involved: seeds (now playing
and recent history, or tracks the caller names) are expanded through the listeners'
`similar()` — Last.fm's similarity data, ListenBrainz radio — and each suggestion is
placed the same way an import is: **the library first**, then resolved and
downloaded if allowed and confident. The agent adds taste on top by choosing the
seeds (`music.dj(seeds=[...])`); this module just turns seeds into a good queue.

Spotify's recommendation endpoints were removed for new apps in 2024, which is why
similarity here comes from Last.fm and ListenBrainz and never from Spotify.

House rules for a pick: not played in the last hour, not already queued, not
skipped more often than played, and at most two in a row from one artist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.modules.music import acquire, library, resolve
from backend.modules.music.listeners import describe
from backend.modules.music.providers import DescribedTrack, listeners

logger = logging.getLogger(__name__)

_MAX_PER_ARTIST = 2
_RECENT_WINDOW = timedelta(hours=1)
# Preferred order when several listeners can answer "similar".
_SIMILARITY_ORDER = ("lastfm", "listenbrainz")


def _recently_played_ids() -> set[str]:
    since = (datetime.now(timezone.utc) - _RECENT_WINDOW).isoformat()
    return {p["track_id"] for p in library.recent_plays(limit=200, since=since)}


def _queued_ids() -> set[str]:
    from backend.modules.music.session import session  # noqa: PLC0415 — cycle

    state = session.snapshot()
    entries = ([state.now_playing] if state.now_playing else []) + state.queue
    return {e.track_id for e in entries}


def _acceptable(track: dict[str, Any], blocked: set[str]) -> bool:
    if track["id"] in blocked or track["status"] not in (
        "ready",
        "queued",
        "downloading",
    ):
        return False
    return track.get("skip_count", 0) <= max(1, track.get("play_count", 0))


def default_seeds(limit: int = 5) -> list[dict[str, Any]]:
    from backend.modules.music.session import session  # noqa: PLC0415

    seeds = list(library.get_tracks(session.recent_track_ids(limit)).values())
    if seeds:
        return seeds
    month = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    return library.top_tracks(since=month, limit=limit) or library.list_tracks(
        sort="most_played", limit=limit, status="ready"
    )


def seeds_from_queries(
    queries: list[str],
) -> tuple[list[dict[str, Any]], list[DescribedTrack]]:
    """Seeds the caller named ("Toto - Africa", "Fleetwood Mac"): library rows where
    we have them, bare descriptions where we don't (similarity still works on a name)."""
    rows: list[dict[str, Any]] = []
    described: list[DescribedTrack] = []
    for query in queries:
        query = query.strip()
        if not query:
            continue
        hits = library.list_tracks(query, status="ready", limit=1, sort="most_played")
        if hits:
            rows.append(hits[0])
            continue
        artist, _, title = query.partition(" - ")
        described.append(
            DescribedTrack(title=title.strip(), artist=artist.strip())
            if title
            else DescribedTrack(title="", artist=query)
        )
    return rows, described


async def _similar_to(
    seed: DescribedTrack, per_seed: int
) -> tuple[list[DescribedTrack], str]:
    available = {listener.id: listener for listener in listeners.available()}
    ordered = [available[i] for i in _SIMILARITY_ORDER if i in available] + [
        listener for lid, listener in available.items() if lid not in _SIMILARITY_ORDER
    ]
    for listener in ordered:
        try:
            if not seed.title:
                # An artist-only seed: ask for the artist's neighbours via a
                # placeholder title the listeners fall back from.
                found = await listener.similar(
                    DescribedTrack(title=seed.artist, artist=seed.artist), per_seed
                )
            else:
                found = await listener.similar(seed, per_seed)
        except Exception as exc:  # noqa: BLE001 — try the next listener
            logger.info("%s similar() failed: %s", listener.id, exc)
            continue
        if found:
            return found, listener.label
    return [], ""


def _library_neighbours(seed: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """No listener connected: same artist, then same genre, from the library."""
    out = []
    if seed.get("artist"):
        out += library.list_tracks(
            artist=seed["artist"], status="ready", sort="most_played", limit=limit
        )
    if seed.get("genre"):
        out += library.list_tracks(
            seed["genre"], status="ready", sort="most_played", limit=limit
        )
    return out


async def curate(
    *,
    seeds: list[dict[str, Any]] | None = None,
    seed_queries: list[str] | None = None,
    count: int = 10,
    allow_download: bool = True,
    reason: str = "dj",
) -> dict[str, Any]:
    """Pick up to `count` tracks. Returns `{picks, why, notes}` — `why` is one line
    per pick for the agent to relay."""
    count = max(1, min(count, 50))
    seed_rows = list(seeds or [])
    described_seeds: list[DescribedTrack] = []
    if seed_queries:
        rows, described_seeds = seeds_from_queries(seed_queries)
        seed_rows += rows
    if not seed_rows and not described_seeds:
        seed_rows = default_seeds()
    notes: list[str] = []
    if not seed_rows and not described_seeds:
        return {
            "picks": [],
            "why": [],
            "notes": ["nothing to go on — play something first, or name some seeds"],
        }

    blocked = _recently_played_ids() | _queued_ids() | {s["id"] for s in seed_rows}
    per_artist: dict[str, int] = {}
    picks: list[dict[str, Any]] = []
    why: list[str] = []
    downloads = 0
    max_downloads = count if allow_download else 0
    threshold = resolve.min_confidence()

    def take(track: dict[str, Any], because: str) -> bool:
        artist = library.normalize(track.get("artist") or "")
        if per_artist.get(artist, 0) >= _MAX_PER_ARTIST or not _acceptable(
            track, blocked
        ):
            return False
        per_artist[artist] = per_artist.get(artist, 0) + 1
        blocked.add(track["id"])
        picks.append(track)
        why.append(f"{track.get('artist') or '?'} — {track['title']}: {because}")
        return True

    all_seeds = [describe(r) for r in seed_rows] + described_seeds
    per_seed = max(10, (count * 3) // max(1, len(all_seeds)))
    suggestions: list[tuple[DescribedTrack, str]] = []
    for seed in all_seeds:
        found, via = await _similar_to(seed, per_seed)
        label = seed.title or seed.artist
        for desc in found:
            suggestions.append((desc, f"similar to {label} ({via})"))
    if not suggestions:
        notes.append(
            "no listener is connected for similarity — picked from your library instead"
        )

    # Round-robin across seeds is implicit in `found` order per seed; interleave so
    # one seed doesn't fill the whole queue.
    by_seed: dict[str, list[tuple[DescribedTrack, str]]] = {}
    for desc, because in suggestions:
        by_seed.setdefault(because, []).append((desc, because))
    interleaved: list[tuple[DescribedTrack, str]] = []
    while any(by_seed.values()):
        for key in list(by_seed):
            if by_seed[key]:
                interleaved.append(by_seed[key].pop(0))

    for desc, because in interleaved:
        if len(picks) >= count:
            break
        existing = resolve.match_library(desc)
        if existing is not None:
            take(existing, f"{because}, in your library")
            continue
        if downloads >= max_downloads:
            continue
        artist = library.normalize(desc.artist)
        if per_artist.get(artist, 0) >= _MAX_PER_ARTIST:
            continue
        resolution = await resolve.resolve(desc)
        if resolution.best is None or resolution.confidence < threshold:
            continue
        try:
            row = await acquire.start_download(
                resolution.best.candidate, meta=desc, confidence=resolution.confidence
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"couldn't fetch {desc.title}: {exc}")
            continue
        if take(row, f"{because}, downloading"):
            downloads += 1

    if len(picks) < count:
        for seed in seed_rows:
            for track in _library_neighbours(seed, count):
                if len(picks) >= count:
                    break
                take(
                    track,
                    f"same {'artist' if track.get('artist') == seed.get('artist') else 'genre'} as {seed['title']}",
                )
    return {"picks": picks, "why": why, "notes": notes, "reason": reason}


async def radio_refill(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The session's refill hook: a few more like what's been playing."""
    try:
        from backend.modules.settings.routes import get_value  # noqa: PLC0415

        allow = bool(get_value("music.radioDownloads", True))
    except Exception:  # noqa: BLE001
        allow = True
    result = await curate(
        seeds=seeds[:5], count=5, allow_download=allow, reason="radio"
    )
    return result["picks"]
