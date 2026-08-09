"""The shared karaoke session: one queue, one now-playing, one set of controls.

This is process-global on purpose, and it is the module's central design decision.
A karaoke night is *one* room with *one* screen and several people holding phones —
so the queue cannot live in the stage component's React state. It lives here, every
mutation broadcasts the whole `PlayerState` on the `karaoke` `/ws` channel, and
every client (the stage, the queue pane, a guest's browser on the LAN) is a pure
renderer of it. That is what makes the mobile remote work with no extra code, and
it is why a workspace switch — which unmounts the stage pane — loses nothing but
the video element.

Three consequences worth stating, because each one is a bug if you forget it:

* The **stage owns playback, the server owns intent.** `playing` here is what the
  room *wants*; the pane with the `<video>` element reconciles toward it. The
  server never claims to know the true position — the stage reports it back via
  `report_progress` so remotes can draw a scrubber.
* **Entry ids, not song ids.** The same song queued for three singers is three
  entries; removals and reorders address the entry.
* Every mutation bumps `revision`. Broadcasts can arrive out of order after a
  reconnect, and a client that renders a stale one shows a queue that jumps
  backwards.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.modules.karaoke import store
from backend.modules.karaoke.models import PlayerState, QueueEntry, SongModel
from backend.modules.ws import broadcast_event

# How many finished entries to remember. Enough to see the night so far, bounded
# so a long party doesn't grow the broadcast payload without limit.
_HISTORY_LIMIT = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KaraokeSession:
    def __init__(self) -> None:
        self._queue: list[QueueEntry] = []
        self._history: list[QueueEntry] = []
        self._now_playing: QueueEntry | None = None
        self._playing = False
        self._position = 0.0
        self._duration: float | None = None
        self._volume = 1.0
        self._semitones = 0
        self._autoplay = True
        self._revision = 0
        # Guards the compound reads-then-writes below. Two guests tapping "queue"
        # in the same tick otherwise interleave inside `next_song`.
        self._lock = asyncio.Lock()

    # --- state ---

    def snapshot(self) -> PlayerState:
        return PlayerState(
            now_playing=self._now_playing,
            playing=self._playing,
            position=self._position,
            duration=self._duration,
            volume=self._volume,
            semitones=self._semitones,
            queue=list(self._queue),
            history=list(self._history),
            autoplay=self._autoplay,
            revision=self._revision,
        )

    async def broadcast(self) -> None:
        self._revision += 1
        await broadcast_event("karaoke", "state", self.snapshot().model_dump())

    # --- queue ---

    async def add(
        self, song: dict[str, Any], singer: str = "", next_up: bool = False
    ) -> QueueEntry:
        entry = QueueEntry(
            entry_id=uuid.uuid4().hex,
            song_id=song["id"],
            title=song["title"],
            artist=song.get("artist") or "",
            singer=singer,
            duration=song.get("duration"),
        )
        async with self._lock:
            if next_up:
                self._queue.insert(0, entry)
            else:
                self._queue.append(entry)
            # A queue that fills while nothing is on screen should just start. It's
            # what every guest expects from tapping the first song of the night,
            # and without it the host has to walk to the machine and press play.
            start = self._now_playing is None and self._autoplay
        await self.broadcast()
        if start:
            await self.next_song()
        return entry

    async def remove(self, entry_id: str) -> bool:
        async with self._lock:
            before = len(self._queue)
            self._queue = [e for e in self._queue if e.entry_id != entry_id]
            removed = len(self._queue) != before
        if removed:
            await self.broadcast()
        return removed

    async def move(self, entry_id: str, position: int) -> bool:
        async with self._lock:
            index = next(
                (i for i, e in enumerate(self._queue) if e.entry_id == entry_id), None
            )
            if index is None:
                return False
            entry = self._queue.pop(index)
            # Clamp rather than reject: a drag past the end of the list is a
            # legitimate "put it last", not an error to surface to the user.
            target = max(0, min(position, len(self._queue)))
            self._queue.insert(target, entry)
        await self.broadcast()
        return True

    async def clear(self) -> None:
        async with self._lock:
            self._queue = []
        await self.broadcast()

    # --- playback ---

    async def next_song(self) -> QueueEntry | None:
        """Retire the current entry and start the next one, if any."""
        async with self._lock:
            finished = self._now_playing
            if finished is not None:
                finished.played_at = _now()
                self._history.insert(0, finished)
                del self._history[_HISTORY_LIMIT:]
            entry = self._queue.pop(0) if self._queue else None
            self._now_playing = entry
            self._position = 0.0
            self._duration = entry.duration if entry else None
            self._playing = entry is not None
        if entry is not None:
            store.mark_played(entry.song_id)
        await self.broadcast()
        return entry

    async def set_playing(self, playing: bool) -> None:
        async with self._lock:
            # Play with nothing loaded means "start the night" — pull from the queue
            # rather than setting a flag that has nothing to act on.
            pull = playing and self._now_playing is None and bool(self._queue)
            if not pull:
                self._playing = playing and self._now_playing is not None
        if pull:
            await self.next_song()
        else:
            await self.broadcast()

    async def restart(self) -> None:
        async with self._lock:
            self._position = 0.0
            self._playing = self._now_playing is not None
        await self.broadcast()

    async def stop(self) -> None:
        """Clear the screen without touching the queue (the 'take a break' verb)."""
        async with self._lock:
            finished = self._now_playing
            if finished is not None:
                finished.played_at = _now()
                self._history.insert(0, finished)
                del self._history[_HISTORY_LIMIT:]
            self._now_playing = None
            self._playing = False
            self._position = 0.0
            self._duration = None
        await self.broadcast()

    async def seek(self, position: float) -> None:
        async with self._lock:
            self._position = max(0.0, position)
        await self.broadcast()

    async def set_volume(self, volume: float) -> None:
        async with self._lock:
            self._volume = max(0.0, min(1.0, volume))
        await self.broadcast()

    async def set_semitones(self, semitones: int) -> None:
        async with self._lock:
            self._semitones = max(-6, min(6, semitones))
        await self.broadcast()

    async def set_autoplay(self, autoplay: bool) -> None:
        async with self._lock:
            self._autoplay = autoplay
        await self.broadcast()

    async def report_progress(self, position: float, duration: float | None) -> None:
        """The stage telling everyone where it is.

        Deliberately does **not** bump the revision or broadcast a full state: this
        fires every second from the stage, and re-broadcasting the whole queue at
        1 Hz to every client would drown the socket. Remotes get position from the
        lighter `progress` event and everything else from `state`.
        """
        self._position = max(0.0, position)
        if duration is not None:
            self._duration = duration
        await broadcast_event(
            "karaoke",
            "progress",
            {"position": self._position, "duration": self._duration},
        )

    async def song_ended(self) -> None:
        """The stage reporting the video ran out."""
        if self._autoplay:
            await self.next_song()
        else:
            await self.stop()


session = KaraokeSession()


async def publish_song(song: dict[str, Any]) -> None:
    """Broadcast one song row (download progress). Separate from `state` because a
    library change isn't a session change — the queue pane shouldn't re-render
    because someone in the next room finished a download."""
    await broadcast_event("karaoke", "song", SongModel(**song).model_dump())
