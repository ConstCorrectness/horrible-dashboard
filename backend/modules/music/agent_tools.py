"""The `music` agent tool group.

Backend tools, like karaoke's: the session lives on the server, so "skip this" and
"put on some Fleetwood Mac" work from the home ask bar with no music pane open.

Seven tools, split by what a person asks for rather than by HTTP route — every tool
costs context on each turn the group is loaded:

- `music.status` — "what's playing?"
- `music.play` — "play/queue X": finds it in the library, or fetches it, in ONE call
- `music.control` — every transport verb behind one enum
- `music.library` — read-only questions about the collection, history, playlists,
  and what an importer (Spotify, Last.fm) has to offer
- `music.playlist` — every playlist *write*, including imports and syncs
- `music.dj` — "more like this", "a chill evening mix": seeds → similar → queue
- `music.info` — "what is this / what's it about": metadata, stats, lyrics, bio

New sources, importers and listeners (plugins included) are reached through these
same tools by id — the group never grows per provider.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.modules.music import acquire, curator, jobs, library, lyrics, resolve
from backend.modules.music.listeners import pending_count
from backend.modules.music.providers import DescribedTrack, ProviderError, importers
from backend.modules.music.session import session
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

# Words people say around a song name that never appear in one.
_FILLER = {
    "by",
    "song",
    "songs",
    "track",
    "tracks",
    "some",
    "something",
    "please",
    "play",
    "music",
    "from",
    "a",
    "me",
}
# Below this, `music.play` asks instead of downloading a guess.
_PLAY_MIN_CONFIDENCE = 0.35
_PERIODS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _brief(track: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"track_id": track["id"], "title": track["title"]}
    for key in ("artist", "album", "year"):
        if track.get(key):
            out[key] = track[key]
    if track.get("duration"):
        out["duration"] = round(float(track["duration"]))
    if track.get("status") and track["status"] != "ready":
        out["status"] = track["status"]
    if track.get("play_count"):
        out["plays"] = track["play_count"]
    return out


def _entry(entry: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "track_id": entry.track_id,
        "title": entry.title,
    }
    if entry.artist:
        row["artist"] = entry.artist
    if not entry.ready:
        row["downloading"] = True
    if entry.reason:
        row["reason"] = entry.reason
    return row


# --- status --------------------------------------------------------------------------


async def _status(_args: dict[str, Any]) -> dict[str, Any]:
    state = session.snapshot()
    out: dict[str, Any] = {
        "playing": state.playing,
        "now_playing": _entry(state.now_playing) if state.now_playing else None,
        "queue_length": len(state.queue),
        "queue": [_entry(e) for e in state.queue[:10]],
        "volume": round(state.volume, 2),
        "shuffle": state.shuffle,
        "repeat": state.repeat,
        "radio": state.radio,
        "library_size": library.count_tracks("ready"),
    }
    if state.now_playing is not None:
        out["position"] = round(state.position)
        if state.duration:
            out["duration"] = round(state.duration)
    if state.history:
        out["recently_played"] = [_entry(e) for e in state.history[:5]]
    running = [j for j in jobs.list_jobs(5) if j["status"] == "running"]
    if running:
        out["imports_running"] = [
            {
                "job_id": j["id"],
                "name": j["name"],
                "done": j["finished"],
                "total": j["total"],
            }
            for j in running
        ]
    review = library.count_tracks("needs_review")
    if review:
        out["needs_review"] = review
    pending = pending_count()
    if pending:
        out["scrobbles_pending"] = pending
    return out


# --- finding a track ------------------------------------------------------------------


def _parse_query(query: str) -> DescribedTrack:
    """'Africa by Toto' / 'Toto - Africa' → a description the resolver can score."""
    text = query.strip()
    if " by " in text.lower():
        idx = text.lower().rindex(" by ")
        return DescribedTrack(title=text[:idx].strip(), artist=text[idx + 4 :].strip())
    if " - " in text:
        artist, _, title = text.partition(" - ")
        return DescribedTrack(title=title.strip(), artist=artist.strip())
    return DescribedTrack(title=text)


def find_in_library(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Library hits for a spoken query, best first. Filler words are dropped so
    "something by Adele" searches for "adele"."""
    words = [w for w in library.normalize(query).split() if w not in _FILLER]
    if not words:
        return []
    cleaned = " ".join(words)
    hits = [
        t
        for t in library.list_tracks(cleaned, limit=limit * 3, sort="most_played")
        if t["status"] != "failed"
    ]
    playable = [t for t in hits if t["status"] in ("ready", "queued", "downloading")]
    return playable[:limit]


async def _play(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    mode = str(args.get("mode") or "play")
    if mode not in ("play", "next", "queue"):
        mode = "play"
    if not query:
        return {"error": "query is required"}

    hits = find_in_library(query)
    if hits:
        desc = _parse_query(query)
        # A named song: prefer an exact title match. Just an artist ("something by
        # Adele"): any of theirs, weighted towards favourites.
        exact = [
            h
            for h in hits
            if desc.title
            and library.normalize(h["title"]) == library.normalize(desc.title)
        ]
        if exact:
            track = exact[0]
        elif len(hits) > 1 and not desc.artist and len(query.split()) <= 3:
            track = random.choice(hits[:3])
        else:
            track = hits[0]
        await session.add([track], mode=mode)
        return {"queued": _brief(track), "mode": mode, "source": "library"}

    desc = _parse_query(query)
    resolution = await resolve.resolve(desc)
    if resolution.best is None:
        return {"error": "; ".join(resolution.notes) or f"Nothing found for {query!r}."}
    if resolution.confidence < _PLAY_MIN_CONFIDENCE:
        return {
            "error": "not confident about any match — ask the user which one, then call music.play with its title",
            "options": [s.as_dict() for s in resolution.alternatives[:4]],
        }
    best = resolution.best.candidate
    try:
        track = await acquire.start_download(best, confidence=resolution.confidence)
    except ProviderError as exc:
        return {"error": str(exc)}
    await session.add([track], mode=mode)
    return {
        "queued": _brief(track),
        "mode": mode,
        "source": best.source,
        "downloading": track["status"] != "ready",
        "found": f"{best.artist} - {best.title}" if best.artist else best.title,
        "confidence": round(resolution.confidence, 2),
    }


# --- transport ---------------------------------------------------------------------


async def _control(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    value = args.get("value")
    if action == "play":
        await session.set_playing(True)
    elif action == "pause":
        await session.set_playing(False)
    elif action == "toggle":
        await session.toggle()
    elif action in ("next", "skip"):
        await session.next()
    elif action == "previous":
        await session.previous()
    elif action == "stop":
        await session.stop()
    elif action == "clear_queue":
        await session.clear()
    elif action == "seek":
        if value is None:
            return {"error": "seek needs `value` (seconds)"}
        await session.seek(float(value))
    elif action == "volume":
        if value is None:
            return {"error": "volume needs `value` (0-1)"}
        await session.set_volume(float(value))
    elif action == "shuffle":
        await session.set_shuffle(bool(value) if value is not None else True)
    elif action == "radio":
        await session.set_radio(bool(value) if value is not None else True)
    elif action == "repeat":
        mode = str(
            args.get("mode")
            or ("one" if value == 1 else "all" if value == 2 else "off")
        )
        try:
            await session.set_repeat(mode)
        except ValueError as exc:
            return {"error": str(exc)}
    elif action == "remove":
        entry_id = str(args.get("entry_id") or "")
        if not entry_id or not await session.remove(entry_id):
            return {"error": "remove needs a queued `entry_id` from music.status"}
    else:
        return {"error": f"unknown action {action!r}"}
    return await _status({})


# --- library -------------------------------------------------------------------------


async def _library(args: dict[str, Any]) -> dict[str, Any]:
    view = str(args.get("view") or "tracks")
    limit = max(1, min(int(args.get("limit") or 25), 200))
    if view == "artists":
        return {"artists": library.list_artists(limit)}
    if view == "albums":
        return {"albums": library.list_albums(str(args.get("artist") or ""), limit)}
    if view == "playlists":
        return {
            "playlists": [
                {k: p[k] for k in ("id", "name", "origin", "track_count")}
                for p in library.list_playlists()
            ]
        }
    if view == "playlist":
        found = library.find_playlist(str(args.get("name") or ""))
        if found is None:
            return {"error": "no such playlist — list them with view=playlists"}
        full = library.get_playlist(found["id"], with_tracks=True) or {}
        return {
            "playlist": found["name"],
            "tracks": [_brief(t) for t in full.get("tracks", [])[:limit]],
        }
    if view in ("top", "history"):
        days = _PERIODS.get(str(args.get("period") or "month"))
        since = (
            (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            if days
            else None
        )
        if view == "top":
            rows = library.top_tracks(since=since, limit=limit)
            return {
                "period": args.get("period") or "month",
                "tracks": [
                    {**_brief(t), "plays_in_period": t["plays_in_window"]} for t in rows
                ],
            }
        plays = library.recent_plays(limit=limit, since=since)
        return {"plays": plays}
    if view == "review":
        return {
            "needs_review": [
                {
                    **_brief(t),
                    "confidence": t.get("match_confidence"),
                    "candidates": t.get("candidates", [])[:3],
                }
                for t in jobs.review_list(limit)
            ]
        }
    if view == "import_sources":
        importer_id = str(args.get("importer") or "")
        if not importer_id:
            return {
                "importers": [
                    {
                        "id": i.id,
                        "label": i.label,
                        "kind": i.kind,
                        "connected": i.configured(),
                    }
                    for i in importers.all()
                ]
            }
        importer = importers.get(importer_id)
        if importer is None or importer.kind != "account":
            return {"error": f"{importer_id!r} is not an account importer"}
        if not importer.configured():
            return {
                "error": f"{importer.label} isn't connected — the user can connect it in the music settings"
            }
        try:
            found = await importer.list_playlists()
        except ProviderError as exc:
            return {"error": str(exc)}
        return {
            "importer": importer_id,
            "playlists": [
                {
                    "ref": p.ref,
                    "name": p.name,
                    "tracks": p.track_count,
                    **({"note": p.note} if p.note else {}),
                }
                for p in found[:limit]
            ],
        }
    tracks = library.list_tracks(
        str(args.get("search") or ""),
        artist=str(args.get("artist") or ""),
        album=str(args.get("album") or ""),
        sort=str(args.get("sort") or "added"),
        status="ready",
        limit=limit,
    )
    return {
        "count": len(tracks),
        "library_size": library.count_tracks("ready"),
        "tracks": [_brief(t) for t in tracks],
    }


# --- playlists -----------------------------------------------------------------------


def _resolve_track_ids(args: dict[str, Any]) -> list[str]:
    ids = [str(t) for t in args.get("track_ids") or [] if t]
    entry_ids = [str(e) for e in args.get("entry_ids") or [] if e]
    for entry_id in entry_ids:
        entry = session.find_entry(entry_id)
        if entry is not None:
            ids.append(entry.track_id)
    query = str(args.get("query") or "").strip()
    if query and not ids:
        ids = [
            t["id"] for t in find_in_library(query, limit=int(args.get("limit") or 1))
        ]
    return ids


async def _playlist(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "")
    name = str(args.get("name") or "").strip()

    if action == "import":
        importer_id = str(args.get("importer") or "")
        ref = str(args.get("ref") or "")
        if not importer_id or not ref:
            return {
                "error": "import needs `importer` and `ref` (see music.library view=import_sources)"
            }
        try:
            job = await jobs.import_from_account(importer_id, ref)
        except ProviderError as exc:
            return {"error": str(exc)}
        return {
            "import_started": job["name"],
            "job_id": job["id"],
            "tracks": job["total"],
            "note": job.get("note")
            or "Runs in the background; music.status shows progress.",
        }

    if action == "create":
        if not name:
            return {"error": "create needs `name`"}
        playlist = library.create_playlist(name)
        ids = _resolve_track_ids(args)
        if args.get("from_queue"):
            state = session.snapshot()
            ids += [
                e.track_id
                for e in ([state.now_playing] if state.now_playing else [])
                + state.queue
            ]
        added = library.add_to_playlist(playlist["id"], ids) if ids else 0
        return {"created": name, "playlist_id": playlist["id"], "tracks": added}

    playlist = library.find_playlist(name) if name else None
    if playlist is None:
        return {
            "error": f"no playlist named {name!r} — list them with music.library view=playlists"
        }

    if action == "add":
        ids = _resolve_track_ids(args)
        if not ids:
            return {
                "error": "nothing to add — pass track_ids, entry_ids, or a query that matches the library"
            }
        return {
            "playlist": playlist["name"],
            "added": library.add_to_playlist(playlist["id"], ids),
        }
    if action == "remove":
        ids = _resolve_track_ids(args)
        return {
            "playlist": playlist["name"],
            "removed": library.remove_from_playlist(playlist["id"], ids),
        }
    if action == "save_queue":
        state = session.snapshot()
        ids = [
            e.track_id
            for e in ([state.now_playing] if state.now_playing else []) + state.queue
        ]
        return {
            "playlist": playlist["name"],
            "added": library.add_to_playlist(playlist["id"], ids),
        }
    if action == "play":
        full = library.get_playlist(playlist["id"], with_tracks=True) or {}
        tracks = [
            t
            for t in full.get("tracks", [])
            if t["status"] in ("ready", "queued", "downloading")
        ]
        if not tracks:
            return {"error": "that playlist has nothing playable yet"}
        if args.get("shuffle"):
            random.shuffle(tracks)
        await session.add(tracks, mode="replace", reason=f"playlist:{playlist['name']}")
        return {"playing": playlist["name"], "tracks": len(tracks)}
    if action == "rename":
        new_name = str(args.get("new_name") or "").strip()
        if not new_name:
            return {"error": "rename needs `new_name`"}
        library.rename_playlist(playlist["id"], new_name)
        return {"renamed": new_name}
    if action == "delete":
        library.delete_playlist(playlist["id"])
        return {"deleted": playlist["name"]}
    if action == "sync":
        try:
            job = await jobs.resync(playlist["id"])
        except ProviderError as exc:
            return {"error": str(exc)}
        return {"sync_started": playlist["name"], "job_id": job["id"]}
    return {"error": f"unknown action {action!r}"}


# --- dj ------------------------------------------------------------------------------


async def _dj(args: dict[str, Any]) -> dict[str, Any]:
    seeds = [str(s) for s in args.get("seeds") or [] if str(s).strip()]
    count = int(args.get("count") or 10)
    mode = str(args.get("mode") or "queue")
    result = await curator.curate(
        seed_queries=seeds or None,
        count=count,
        allow_download=bool(args.get("allow_download", True)),
    )
    picks = result["picks"]
    out: dict[str, Any] = {"picked": [_brief(t) for t in picks], "why": result["why"]}
    if result["notes"]:
        out["notes"] = result["notes"]
    if picks and mode != "preview":
        await session.add(
            picks, mode="replace" if mode == "play" else "queue", reason="dj"
        )
        out["queued"] = len(picks)
    if args.get("radio") is not None:
        await session.set_radio(bool(args["radio"]))
        out["radio"] = bool(args["radio"])
    return out


# --- info ----------------------------------------------------------------------------


async def _artist_bio(artist: str) -> dict[str, Any] | None:
    from backend.modules.music.listeners import lastfm  # noqa: PLC0415

    if not artist or not lastfm.api_key():
        return None
    try:
        data = await lastfm.call("artist.getInfo", {"artist": artist, "autocorrect": 1})
    except ProviderError:
        return None
    info = data.get("artist") or {}
    bio = (info.get("bio") or {}).get("summary") or ""
    bio = bio.split("<a href")[0].strip()
    tags = [
        t.get("name")
        for t in ((info.get("tags") or {}).get("tag") or [])
        if isinstance(t, dict)
    ]
    similar = [
        a.get("name")
        for a in ((info.get("similar") or {}).get("artist") or [])
        if isinstance(a, dict)
    ]
    return {"bio": bio[:1200], "tags": tags[:8], "similar_artists": similar[:6]}


async def _info(args: dict[str, Any]) -> dict[str, Any]:
    track: dict[str, Any] | None = None
    if entry_id := str(args.get("entry_id") or ""):
        entry = session.find_entry(entry_id)
        track = library.get_track(entry.track_id) if entry else None
    elif track_id := str(args.get("track_id") or ""):
        track = library.get_track(track_id)
    elif query := str(args.get("query") or ""):
        hits = find_in_library(query, limit=1)
        track = hits[0] if hits else None
    else:
        now = session.snapshot().now_playing
        track = library.get_track(now.track_id) if now else None
    if track is None:
        return {
            "error": "no such track — nothing is playing, or the query didn't match the library"
        }

    out: dict[str, Any] = {
        **_brief(track),
        "genre": track.get("genre") or None,
        "album_artist": track.get("album_artist") or None,
        "plays": track.get("play_count", 0),
        "skips": track.get("skip_count", 0),
        "last_played": track.get("last_played"),
        "added": track.get("added_at"),
        "source": track.get("source"),
        "ids": track.get("external_ids") or {},
    }
    out = {k: v for k, v in out.items() if v not in (None, "", {})}
    if args.get("lyrics", True):
        found = await lyrics.get_lyrics(track["id"])
        if found and found.get("plain"):
            text = found["plain"]
            out["lyrics"] = text if len(text) <= 4000 else text[:4000] + "\n…"
            out["lyrics_source"] = found["source"]
        elif found:
            out["lyrics"] = None
            out["lyrics_source"] = found["source"]
    if bio := await _artist_bio(track.get("artist") or ""):
        out["artist_info"] = bio
    return out


_TOOLS = [
    AgentTool(
        name="music.status",
        description=(
            "What the music player is doing: what's playing and how far in, the queue "
            "(with entry_ids), shuffle/repeat/radio, recently played, running playlist "
            "imports, and how many imported tracks are waiting for review. Call this "
            "before answering anything about the current song or the queue."
        ),
        handler=_status,
        group="music",
    ),
    AgentTool(
        name="music.play",
        description=(
            "Play or queue a song by name — the main tool. Give it what the user said "
            "('Africa by Toto', 'something by Adele', 'Toto - Rosanna'). It uses the "
            "user's library first; if the song isn't there it finds it on YouTube, "
            "downloads it into the library and queues it while it downloads. Do NOT "
            "search first — this covers it. mode: play (now, default), next, or queue."
        ),
        handler=_play,
        parameters={
            "query": {
                "type": "string",
                "description": "Song and/or artist, as the user said it.",
            },
            "mode": {
                "type": "string",
                "enum": ["play", "next", "queue"],
                "description": "play = start now; next = after this song; queue = at the end.",
            },
        },
        required=["query"],
        side_effect=True,
        specifier_template="{query}",
        group="music",
    ),
    AgentTool(
        name="music.control",
        description=(
            "Drive the player: play, pause, toggle, next (skip), previous, stop, "
            "clear_queue, seek (`value` seconds), volume (`value` 0-1), shuffle / radio "
            "(`value` true/false; radio keeps adding similar songs when the queue runs "
            "out), repeat (`mode` off|one|all), remove (`entry_id` from music.status)."
        ),
        handler=_control,
        parameters={
            "action": {
                "type": "string",
                "enum": [
                    "play",
                    "pause",
                    "toggle",
                    "next",
                    "previous",
                    "stop",
                    "clear_queue",
                    "seek",
                    "volume",
                    "shuffle",
                    "radio",
                    "repeat",
                    "remove",
                ],
            },
            "value": {
                "type": "number",
                "description": "seek seconds, volume 0-1, or 1/0 for shuffle/radio.",
            },
            "mode": {
                "type": "string",
                "enum": ["off", "one", "all"],
                "description": "For repeat.",
            },
            "entry_id": {"type": "string", "description": "For remove."},
        },
        required=["action"],
        side_effect=True,
        specifier_template="{action}",
        group="music",
    ),
    AgentTool(
        name="music.library",
        description=(
            "Read-only questions about the user's music. view=tracks (default; filter "
            "with search/artist/album, sort added|title|artist|most_played|"
            "recently_played), artists, albums, playlists, playlist (one, by `name`), "
            "top (most played in `period` day|week|month|year|all), history (recent "
            "plays), review (imported tracks waiting for a person to confirm the match), "
            "import_sources (connected importers; with `importer`, e.g. spotify, the "
            "playlists it can import and their `ref`s). Never downloads anything."
        ),
        handler=_library,
        parameters={
            "view": {
                "type": "string",
                "enum": [
                    "tracks",
                    "artists",
                    "albums",
                    "playlists",
                    "playlist",
                    "top",
                    "history",
                    "review",
                    "import_sources",
                ],
            },
            "search": {"type": "string"},
            "artist": {"type": "string"},
            "album": {"type": "string"},
            "name": {
                "type": "string",
                "description": "Playlist name, for view=playlist.",
            },
            "sort": {"type": "string"},
            "period": {
                "type": "string",
                "enum": ["day", "week", "month", "year", "all"],
            },
            "importer": {"type": "string", "description": "For view=import_sources."},
            "limit": {"type": "integer"},
        },
        group="music",
    ),
    AgentTool(
        name="music.playlist",
        description=(
            "Change playlists. action: create (`name`, optional track_ids/entry_ids/"
            "query, or from_queue=true), add / remove (`name` + track_ids, entry_ids or "
            "a library `query`), save_queue (`name`), play (`name`, optional shuffle), "
            "rename (`name`, `new_name`), delete (`name`), import (`importer` + `ref` "
            "from music.library view=import_sources — copies e.g. a Spotify playlist "
            "into the library, downloading what's missing, in the background), sync "
            "(`name` of a previously imported playlist — fetches only what's new)."
        ),
        handler=_playlist,
        parameters={
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "add",
                    "remove",
                    "save_queue",
                    "play",
                    "rename",
                    "delete",
                    "import",
                    "sync",
                ],
            },
            "name": {"type": "string"},
            "new_name": {"type": "string"},
            "track_ids": {"type": "array", "items": {"type": "string"}},
            "entry_ids": {"type": "array", "items": {"type": "string"}},
            "query": {"type": "string"},
            "from_queue": {"type": "boolean"},
            "shuffle": {"type": "boolean"},
            "importer": {"type": "string"},
            "ref": {"type": "string"},
        },
        required=["action"],
        side_effect=True,
        specifier_template="{action}",
        group="music",
    ),
    AgentTool(
        name="music.dj",
        description=(
            "Curate: pick songs similar to `seeds` and queue them. Seeds are songs or "
            "artists ('Toto - Africa', 'Fleetwood Mac'); omit them to continue from what's "
            "been playing. Turn a mood or theme ('chill 80s evening') into 3-5 concrete "
            "seeds yourself. Prefers the user's library, downloads the rest. mode: queue "
            "(default), play (replace the queue), preview (just list picks). radio=true "
            "keeps it going when the queue runs out."
        ),
        handler=_dj,
        parameters={
            "seeds": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer", "description": "How many songs (default 10)."},
            "mode": {"type": "string", "enum": ["queue", "play", "preview"]},
            "allow_download": {
                "type": "boolean",
                "description": "False = only songs already in the library.",
            },
            "radio": {"type": "boolean"},
        },
        side_effect=True,
        group="music",
    ),
    AgentTool(
        name="music.info",
        description=(
            "Everything known about one track — the one playing if no argument: title, "
            "artist, album, year, genre, play/skip counts, lyrics, and (with Last.fm "
            "connected) the artist's bio, tags and similar artists. Use it for 'what's "
            "this song about?', 'who sings this?', 'how often have I played it?'."
        ),
        handler=_info,
        parameters={
            "entry_id": {"type": "string"},
            "track_id": {"type": "string"},
            "query": {
                "type": "string",
                "description": "A library search, e.g. 'Rosanna'.",
            },
            "lyrics": {
                "type": "boolean",
                "description": "Include lyrics (default true).",
            },
        },
        group="music",
    ),
]


def register_agent_tools() -> None:
    """Register the group. Called from `backend/app.py` at startup."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
