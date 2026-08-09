"""The `karaoke` agent tool group.

Backend tools rather than browser ones, deliberately: the session lives on the
server, so the agent can run the room with no karaoke pane open anywhere — "queue
three more upbeat ones for Sam" works from the home ask bar.

The group is six tools and stops there. Tools are disclosed by group and each one
costs context on any turn the group loads, so the split is by *what a host asks
for*, not by HTTP route: `karaoke.status` answers "what's on?", `karaoke.control`
absorbs every transport verb (play/pause/skip/…) into one enum instead of six
near-identical tools, and library curation (deleting songs, reordering the queue by
index) stays a UI concern.

`karaoke.queue` deliberately takes a *search phrase*, not a song id. Asked to "put
on some Fleetwood Mac", a model given only an id-taking tool has to search, pick,
download and queue across four turns — and reliably fumbles one. This tool does the
whole path: it prefers a song already in the library, otherwise searches, starts
the download, and queues the entry immediately (the file lands before the singer's
turn, see `routes.download`).
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.karaoke import downloader, store
from backend.modules.karaoke.session import session
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

_MAX_RESULTS = 10


def _entry(entry: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"entry_id": entry.entry_id, "title": entry.title}
    if entry.artist:
        row["artist"] = entry.artist
    if entry.singer:
        row["singer"] = entry.singer
    return row


async def _status(_args: dict[str, Any]) -> dict[str, Any]:
    state = session.snapshot()
    out: dict[str, Any] = {
        "playing": state.playing,
        "queue_length": len(state.queue),
        "queue": [_entry(e) for e in state.queue[:10]],
        "volume": round(state.volume, 2),
        "semitones": state.semitones,
        "autoplay": state.autoplay,
    }
    if state.now_playing is not None:
        out["now_playing"] = _entry(state.now_playing)
        out["position"] = round(state.position, 1)
        if state.duration:
            out["duration"] = round(state.duration, 1)
    else:
        out["now_playing"] = None
    if state.history:
        out["recently_played"] = [_entry(e) for e in state.history[:5]]
    return out


async def _search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    limit = min(int(args.get("limit") or 5), _MAX_RESULTS)
    if not query:
        return {"error": "query is required"}
    results, note = await downloader.search(query, limit=limit)
    out: dict[str, Any] = {
        "query": query,
        "results": [
            {
                "video_id": r.video_id,
                "title": r.title,
                "channel": r.channel,
                "duration": r.duration,
                "in_library": r.downloaded,
            }
            for r in results
        ],
    }
    if note:
        out["note"] = note
    return out


async def _library(args: dict[str, Any]) -> dict[str, Any]:
    search = str(args.get("search") or "")
    songs = store.list_songs(search, limit=50)
    return {
        "count": len(songs),
        "songs": [
            {
                "song_id": s["id"],
                "title": s["title"],
                "artist": s["artist"],
                "status": s["status"],
                "play_count": s["play_count"],
            }
            for s in songs
        ],
    }


async def _queue(args: dict[str, Any]) -> dict[str, Any]:
    """Find-or-fetch a song and put it in the running order. See module docstring."""
    query = str(args.get("query") or "").strip()
    singer = str(args.get("singer") or "")
    next_up = bool(args.get("next"))
    if not query:
        return {"error": "query is required"}

    # Library first: it's instant, it's already the right karaoke cut (someone
    # chose it once), and it costs no bandwidth.
    matches = store.list_songs(query, limit=1)
    ready = [m for m in matches if m["status"] == "ready"]
    if ready:
        song = ready[0]
        entry = await session.add(song, singer=singer, next_up=next_up)
        return {
            "queued": _entry(entry),
            "source": "library",
            "position": 0 if next_up else len(session.snapshot().queue),
        }

    if not downloader.available():
        return {"error": downloader.INSTALL_HINT}
    results, note = await downloader.search(query, limit=1)
    if not results:
        return {"error": note or f"Nothing found for {query!r}."}

    hit = results[0]
    existing = store.find_by_video_id(hit.video_id)
    if existing is not None:
        entry = await session.add(existing, singer=singer, next_up=next_up)
        return {"queued": _entry(entry), "source": "library"}

    song = store.create_song(
        title=hit.title, video_id=hit.video_id, url=hit.url, status="queued"
    )
    downloader.start_download(song["id"], hit.url)
    entry = await session.add(song, singer=singer, next_up=next_up)
    return {
        "queued": _entry(entry),
        "source": "youtube",
        "downloading": True,
        "found": hit.title,
    }


async def _control(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    if action == "play":
        await session.set_playing(True)
    elif action == "pause":
        await session.set_playing(False)
    elif action in ("next", "skip"):
        await session.next_song()
    elif action == "restart":
        await session.restart()
    elif action == "stop":
        await session.stop()
    elif action == "clear_queue":
        await session.clear()
    elif action == "volume":
        value = args.get("value")
        if value is None:
            return {"error": "volume needs `value` (0-1)"}
        await session.set_volume(float(value))
    elif action == "transpose":
        value = args.get("value")
        if value is None:
            return {"error": "transpose needs `value` (semitones, -6..6)"}
        await session.set_semitones(int(value))
    elif action == "autoplay":
        value = args.get("value")
        await session.set_autoplay(bool(value) if value is not None else True)
    else:
        return {"error": f"unknown action {action!r}"}
    return await _status({})


async def _unqueue(args: dict[str, Any]) -> dict[str, Any]:
    entry_id = str(args.get("entry_id") or "")
    if not entry_id:
        return {"error": "entry_id is required (get one from karaoke.status)"}
    if not await session.remove(entry_id):
        return {"error": "no such queue entry — it may have already been played"}
    return await _status({})


_TOOLS = [
    AgentTool(
        name="karaoke.status",
        description=(
            "What the karaoke session is doing right now: what's playing, how far "
            "in, what's queued and for whom, plus volume/key settings. Call this "
            "before answering anything about the current song or the running order, "
            "and to get the `entry_id`s that karaoke.unqueue needs."
        ),
        handler=_status,
        group="karaoke",
    ),
    AgentTool(
        name="karaoke.queue",
        description=(
            "Add a song to the karaoke running order by name — this is the main "
            "tool. Give it what the user said ('Africa by Toto', 'something by "
            "Adele') and it does the whole job: uses a copy already in the library "
            "if there is one, otherwise finds the karaoke version on YouTube and "
            "starts downloading it while the entry holds its place in the queue. "
            "Do NOT search first and then queue — this tool covers both. Pass the "
            "singer's name when the user names one."
        ),
        handler=_queue,
        parameters={
            "query": {
                "type": "string",
                "description": "Song and/or artist, as the user said it.",
            },
            "singer": {
                "type": "string",
                "description": "Who is singing it, if the user said.",
            },
            "next": {
                "type": "boolean",
                "description": "Put it at the front of the queue instead of the back.",
            },
        },
        required=["query"],
        side_effect=True,
        specifier_template="{query}",
        group="karaoke",
    ),
    AgentTool(
        name="karaoke.control",
        description=(
            "Drive the karaoke player: play, pause, next (skip the current singer), "
            "restart the current song, stop (clear the screen, keep the queue), "
            "clear_queue, volume (`value` 0-1), transpose (`value` semitones -6..6, "
            "for a singer who needs the song in a different key), or autoplay "
            "(`value` true/false)."
        ),
        handler=_control,
        parameters={
            "action": {
                "type": "string",
                "enum": [
                    "play",
                    "pause",
                    "next",
                    "restart",
                    "stop",
                    "clear_queue",
                    "volume",
                    "transpose",
                    "autoplay",
                ],
                "description": "What to do.",
            },
            "value": {
                "type": "number",
                "description": "For volume (0-1), transpose (semitones), autoplay (0/1).",
            },
        },
        required=["action"],
        side_effect=True,
        specifier_template="{action}",
        group="karaoke",
    ),
    AgentTool(
        name="karaoke.unqueue",
        description=(
            "Remove one entry from the karaoke queue. Takes the `entry_id` from "
            "karaoke.status — not a song name, since the same song can be queued "
            "several times for different singers."
        ),
        handler=_unqueue,
        parameters={
            "entry_id": {"type": "string", "description": "From karaoke.status."}
        },
        required=["entry_id"],
        side_effect=True,
        group="karaoke",
    ),
    AgentTool(
        name="karaoke.search",
        description=(
            "Search YouTube for karaoke versions without queueing anything, so the "
            "user can choose. Only reach for this when they want options — to just "
            "put a song on, use karaoke.queue, which searches by itself."
        ),
        handler=_search,
        parameters={
            "query": {"type": "string", "description": "Song and/or artist."},
            "limit": {
                "type": "integer",
                "description": f"Max hits (1-{_MAX_RESULTS}).",
            },
        },
        required=["query"],
        group="karaoke",
    ),
    AgentTool(
        name="karaoke.library",
        description=(
            "List songs already downloaded to this node, optionally filtered by a "
            "title/artist substring. Use it for 'what do we have?' and 'what have "
            "we sung most?' — it never touches the network."
        ),
        handler=_library,
        parameters={
            "search": {
                "type": "string",
                "description": "Substring of title or artist. Omit for everything.",
            }
        },
        group="karaoke",
    ),
]


def register_agent_tools() -> None:
    """Register the group. Called from `backend/app.py` at startup."""

    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
