"""Listeners (Last.fm, ListenBrainz) and the scrobbler that feeds them.

The scrobble rule is the one both services document: a track longer than 30 s,
listened to for half its length or four minutes, whichever comes first. "Listened"
is the time the session actually saw the position advance (`session.py`), so a
track left paused, or skipped through, doesn't scrobble.

Failed scrobbles go into `music_scrobble_queue` and are retried with backoff, so
an evening offline is submitted when the connection comes back — scrobbles carry
their own timestamps, so late is fine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from backend.modules.music import library
from backend.modules.music.providers import DescribedTrack, listeners

logger = logging.getLogger(__name__)

MIN_TRACK_SECONDS = 30.0
MAX_REQUIRED_SECONDS = 240.0
_RETRY_EVERY = 300.0
_MAX_ATTEMPTS = 20

_retry_task: asyncio.Task[None] | None = None


def register_builtin_listeners() -> None:
    from backend.modules.music.listeners.lastfm import listener as lastfm  # noqa: PLC0415
    from backend.modules.music.listeners.listenbrainz import listener as listenbrainz  # noqa: PLC0415

    for listener in (lastfm, listenbrainz):
        if listeners.get(listener.id) is None:
            listeners.register(listener)


def should_scrobble(duration: float | None, listened: float) -> bool:
    if not duration or duration <= MIN_TRACK_SECONDS:
        return False
    return listened >= min(duration / 2, MAX_REQUIRED_SECONDS)


def describe(track: dict[str, Any]) -> DescribedTrack:
    return DescribedTrack(
        title=track.get("title") or "",
        artist=track.get("artist") or "",
        album=track.get("album") or "",
        duration=track.get("duration"),
        external_ids=dict(track.get("external_ids") or {}),
    )


def scrobbling_enabled() -> bool:
    try:
        from backend.modules.settings.routes import get_value  # noqa: PLC0415

        return bool(get_value("music.scrobble", True))
    except Exception:  # noqa: BLE001
        return True


def _scrobblers() -> list[Any]:
    out = []
    for listener in listeners.available():
        can = getattr(listener, "can_scrobble", None)
        if can is None or can():
            out.append(listener)
    return out


async def on_started(track: dict[str, Any]) -> None:
    if not scrobbling_enabled():
        return
    desc = describe(track)
    for listener in _scrobblers():
        try:
            await listener.now_playing(desc)
        except Exception as exc:  # noqa: BLE001 — "now playing" is fire-and-forget
            logger.info("%s now_playing failed: %s", listener.id, exc)


def _enqueue(
    listener_id: str, desc: DescribedTrack, started_at: int, error: str
) -> None:
    payload = {
        "title": desc.title,
        "artist": desc.artist,
        "album": desc.album,
        "duration": desc.duration,
        "external_ids": desc.external_ids,
    }
    with library.get_db_conn() as conn:
        conn.execute(
            "INSERT INTO music_scrobble_queue (listener, payload, started_at, last_error) VALUES (?, ?, ?, ?)",
            (listener_id, json.dumps(payload), started_at, error[:300]),
        )


async def on_finished(
    track: dict[str, Any],
    started_at: int,
    listened: float,
    completed: bool,
    skipped: bool,
) -> None:
    if not scrobbling_enabled() or not should_scrobble(track.get("duration"), listened):
        return
    desc = describe(track)
    for listener in _scrobblers():
        try:
            await listener.scrobble(desc, started_at)
        except Exception as exc:  # noqa: BLE001 — queued for retry instead
            logger.info("%s scrobble failed, queued: %s", listener.id, exc)
            _enqueue(listener.id, desc, started_at, str(exc))


def pending_count() -> int:
    with library.get_db_conn() as conn:
        return int(
            conn.execute("SELECT COUNT(*) FROM music_scrobble_queue").fetchone()[0]
        )


async def flush_queue() -> int:
    """Retry queued scrobbles. Returns how many went through."""
    with library.get_db_conn() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM music_scrobble_queue ORDER BY id LIMIT 200"
            )
        ]
    sent = 0
    for row in rows:
        listener = listeners.get(row["listener"])
        done = False
        error = ""
        if listener is not None and listener.configured():
            data = json.loads(row["payload"])
            desc = DescribedTrack(
                title=data.get("title") or "",
                artist=data.get("artist") or "",
                album=data.get("album") or "",
                duration=data.get("duration"),
                external_ids=data.get("external_ids") or {},
            )
            try:
                await listener.scrobble(desc, int(row["started_at"]))
                done = True
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        else:
            error = "not connected"
        with library.get_db_conn() as conn:
            if done or row["attempts"] + 1 >= _MAX_ATTEMPTS:
                conn.execute(
                    "DELETE FROM music_scrobble_queue WHERE id = ?", (row["id"],)
                )
            else:
                conn.execute(
                    "UPDATE music_scrobble_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                    (error[:300], row["id"]),
                )
        sent += int(done)
    return sent


async def _retry_loop() -> None:
    while True:
        await asyncio.sleep(_RETRY_EVERY)
        try:
            if pending_count():
                await flush_queue()
        except Exception:  # noqa: BLE001 — the loop must survive anything
            logger.exception("scrobble retry failed")


def start_retry_loop() -> None:
    global _retry_task
    if _retry_task is None or _retry_task.done():
        _retry_task = asyncio.create_task(_retry_loop())
