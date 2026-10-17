"""The music session: one queue, one now-playing, one set of controls, app-wide.

Modelled on `karaoke/session.py`, and for the same reasons:

* **The server owns intent, the client owns playback.** `playing` is what the user
  *wants*; the browser's app-wide `<audio>` element reconciles toward it and reports
  its real position back (`report_progress`). The agent, a shell indicator, the
  desktop board widget and a second window are all just renderers of this state —
  which is why "skip this" works from the ask bar with no music pane open.
* **Entry ids, not track ids.** The same track queued twice is two entries.
* **Every mutation bumps `revision`**, so a client drops stale broadcasts.

Unlike karaoke's, this one **persists** (`music_session`): a backend restart keeps
the queue and what was playing, paused. And it **watches what you listen to** — a
play is recorded on retire with how long it was actually heard, which is what
history, "most played", the DJ, and scrobbling are built from.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from backend.modules.music import events, library
from backend.modules.music.models import PlayerState, QueueEntry

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 100
# "Previous" restarts the current track instead of going back once you're this far
# in — what every player does, and what a thumb on a media key expects.
_PREVIOUS_RESTARTS_AFTER = 3.0
# Progress reports further apart than this aren't counted as listening: the tab was
# asleep, or the reports are from two clients.
_MAX_PROGRESS_GAP = 5.0
# Radio tops the queue up when fewer than this many entries are left.
_RADIO_LOW_WATER = 2
# An output owner that hasn't reported progress for this long, while playing, is
# gone (window closed, laptop asleep) and another client may take over.
_OUTPUT_STALE_AFTER = 8.0

StartedHook = Callable[[dict[str, Any]], Awaitable[None]]
# (track, started_epoch, listened_seconds, completed, skipped)
FinishedHook = Callable[[dict[str, Any], int, float, bool, bool], Awaitable[None]]
# Given the session's recent tracks, return rows to append. The curator (DJ) owns it.
RefillHook = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]]


class MusicSession:
    def __init__(self) -> None:
        self._queue: list[QueueEntry] = []
        self._history: list[QueueEntry] = []
        self._now_playing: QueueEntry | None = None
        self._playing = False
        self._position = 0.0
        self._duration: float | None = None
        self._volume = 0.8
        self._shuffle = False
        self._repeat = "off"
        self._radio = False
        self._revision = 0
        self._lock = asyncio.Lock()
        # The current track's listen, for history and scrobbling.
        self._started_iso = ""
        self._started_epoch = 0
        self._listened = 0.0
        self._last_report: float | None = None
        self._started_hooks: list[StartedHook] = []
        self._finished_hooks: list[FinishedHook] = []
        self._refill: RefillHook | None = None
        self._refilling = False
        self._loaded = False
        self._output_client: str | None = None
        self._output_seen = 0.0

    # --- hooks ---------------------------------------------------------------------

    def on_started(self, hook: StartedHook) -> None:
        if hook not in self._started_hooks:
            self._started_hooks.append(hook)

    def on_finished(self, hook: FinishedHook) -> None:
        if hook not in self._finished_hooks:
            self._finished_hooks.append(hook)

    def set_refill(self, hook: RefillHook | None) -> None:
        self._refill = hook

    # --- state ---------------------------------------------------------------------

    def snapshot(self) -> PlayerState:
        return PlayerState(
            now_playing=self._now_playing,
            playing=self._playing,
            position=self._position,
            duration=self._duration,
            volume=self._volume,
            shuffle=self._shuffle,
            repeat=self._repeat,  # type: ignore[arg-type]
            radio=self._radio,
            queue=list(self._queue),
            history=list(self._history),
            output_client=self._output_client,
            revision=self._revision,
        )

    async def broadcast(self) -> None:
        self._revision += 1
        state = self.snapshot()
        try:
            library.save_session_state(state.model_dump(exclude={"revision", "output_client"}))
        except Exception:  # noqa: BLE001 — persistence must never break playback
            logger.exception("could not persist the music session")
        await events.publish("state", state.model_dump())

    def load(self) -> None:
        """Restore the persisted session, paused. Entries whose track is gone are
        dropped. Called once at startup; safe to call again (no-op)."""
        if self._loaded:
            return
        self._loaded = True
        saved = library.load_session_state()
        if not saved:
            return
        try:
            state = PlayerState(**saved)
        except Exception:  # noqa: BLE001 — a stale shape is not worth crashing over
            logger.warning("discarding unreadable saved music session")
            return
        entries = [e for e in [state.now_playing, *state.queue, *state.history] if e]
        alive = library.get_tracks(e.track_id for e in entries)

        def keep(entry: QueueEntry | None) -> QueueEntry | None:
            if entry is None or entry.track_id not in alive:
                return None
            entry.ready = alive[entry.track_id]["status"] == "ready"
            return entry

        self._now_playing = keep(state.now_playing)
        self._queue = [e for e in map(keep, state.queue) if e]
        self._history = [e for e in map(keep, state.history) if e]
        self._position = state.position if self._now_playing else 0.0
        self._duration = state.duration if self._now_playing else None
        self._volume = state.volume
        self._shuffle = state.shuffle
        self._repeat = state.repeat
        self._radio = state.radio
        self._playing = False

    # --- entries -------------------------------------------------------------------

    @staticmethod
    def entry_for(track: dict[str, Any], reason: str = "") -> QueueEntry:
        return QueueEntry(
            entry_id=uuid.uuid4().hex,
            track_id=track["id"],
            title=track["title"],
            artist=track.get("artist") or "",
            album=track.get("album") or "",
            duration=track.get("duration"),
            art_hash=track.get("art_hash") or "",
            ready=track.get("status") == "ready",
            reason=reason,
        )

    def find_entry(self, entry_id: str) -> QueueEntry | None:
        for entry in [self._now_playing, *self._queue, *self._history]:
            if entry is not None and entry.entry_id == entry_id:
                return entry
        return None

    # --- queue ---------------------------------------------------------------------

    async def add(
        self, tracks: list[dict[str, Any]], *, mode: str = "queue", reason: str = ""
    ) -> list[QueueEntry]:
        """Put tracks in the play order.

        `play` jumps the line and starts the first one now; `next` jumps the line;
        `queue` appends; `replace` swaps the whole queue and starts it. Anything
        added while nothing is playing starts playing — the obvious expectation.
        """
        entries = [self.entry_for(t, reason) for t in tracks if t]
        if not entries:
            return []
        async with self._lock:
            if mode == "replace":
                self._queue = list(entries)
            elif mode in ("play", "next"):
                self._queue[0:0] = entries
            else:
                self._queue.extend(entries)
            start = mode in ("play", "replace") or self._now_playing is None
        if start:
            await self._advance(finished=False, skipped=self._now_playing is not None)
        else:
            await self.broadcast()
        return entries

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
            self._queue.insert(max(0, min(position, len(self._queue))), entry)
        await self.broadcast()
        return True

    async def clear(self) -> None:
        async with self._lock:
            self._queue = []
        await self.broadcast()

    # --- transport -----------------------------------------------------------------

    async def _advance(self, *, finished: bool, skipped: bool) -> QueueEntry | None:
        """Retire the current entry and start the next one.

        `finished`: it played to the end (counts as completed; repeat-one replays
        it). `skipped`: a person moved past it early (counted against it).
        """
        async with self._lock:
            done = self._now_playing
            retired = self._retire_locked(completed=finished, skipped=skipped)
            if done is not None and finished and self._repeat == "one":
                entry: QueueEntry | None = done.model_copy(
                    update={"entry_id": uuid.uuid4().hex, "played_at": None}
                )
            else:
                if done is not None and self._repeat == "all":
                    # Cycle: what just played goes to the back of the line.
                    self._queue.append(
                        done.model_copy(
                            update={"entry_id": uuid.uuid4().hex, "played_at": None}
                        )
                    )
                entry = self._queue.pop(0) if self._queue else None
            self._start_locked(entry)
        await self._after_retire(retired)
        if entry is not None:
            library.mark_played(entry.track_id)
            await self._run_started(entry)
        await self.broadcast()
        self._maybe_refill()
        return entry

    def _start_locked(self, entry: QueueEntry | None) -> None:
        self._now_playing = entry
        self._position = 0.0
        self._duration = entry.duration if entry else None
        self._playing = entry is not None
        self._started_iso = library.now_iso() if entry else ""
        self._started_epoch = int(time.time()) if entry else 0
        self._listened = 0.0
        self._last_report = None

    def _retire_locked(
        self, *, completed: bool, skipped: bool
    ) -> dict[str, Any] | None:
        """Move now-playing to history. Returns the listen to record, if any."""
        done = self._now_playing
        if done is None:
            return None
        done.played_at = library.now_iso()
        self._history.insert(0, done)
        del self._history[_HISTORY_LIMIT:]
        listen = {
            "track_id": done.track_id,
            "started_iso": self._started_iso or library.now_iso(),
            "started_epoch": self._started_epoch or int(time.time()),
            "listened": self._listened,
            "duration": self._duration or done.duration,
            "completed": completed,
            # A skip only counts when it was early: moving on in the last tenth is
            # "done with it", not "didn't like it".
            "skipped": skipped and not completed and not self._near_end(),
        }
        self._now_playing = None
        return listen

    def _near_end(self) -> bool:
        if not self._duration:
            return False
        return self._position >= self._duration * 0.9

    async def _after_retire(self, listen: dict[str, Any] | None) -> None:
        if not listen or not listen["track_id"]:
            return
        try:
            library.record_play(
                listen["track_id"],
                started_at=listen["started_iso"],
                played_seconds=round(listen["listened"], 1),
                completed=listen["completed"],
                skipped=listen["skipped"],
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not record a play")
        track = library.get_track(listen["track_id"])
        if track is None:
            return
        track = {**track, "duration": listen["duration"] or track.get("duration")}
        for hook in list(self._finished_hooks):
            try:
                await hook(
                    track,
                    listen["started_epoch"],
                    listen["listened"],
                    listen["completed"],
                    listen["skipped"],
                )
            except Exception:  # noqa: BLE001 — scrobbling must never stall playback
                logger.exception("music finished hook failed")

    async def _run_started(self, entry: QueueEntry) -> None:
        if not self._started_hooks:
            return
        track = library.get_track(entry.track_id)
        if track is None:
            return
        for hook in list(self._started_hooks):
            try:
                await hook(track)
            except Exception:  # noqa: BLE001
                logger.exception("music started hook failed")

    async def next(self) -> QueueEntry | None:
        """A person skipping ahead."""
        return await self._advance(finished=False, skipped=True)

    async def previous(self) -> QueueEntry | None:
        async with self._lock:
            if self._now_playing is not None and (
                self._position > _PREVIOUS_RESTARTS_AFTER or not self._history
            ):
                self._position = 0.0
                self._playing = True
                restart = True
            else:
                restart = False
        if restart:
            await self.broadcast()
            return self._now_playing
        async with self._lock:
            if not self._history:
                return None
            back = self._history.pop(0)
            current = self._now_playing
            retired = None
            if current is not None:
                # Back up without logging the current one to history again: it's
                # going back into the queue, not finished.
                retired = {
                    "track_id": current.track_id,
                    "started_iso": self._started_iso,
                    "started_epoch": self._started_epoch,
                    "listened": self._listened,
                    "duration": self._duration,
                    "completed": False,
                    "skipped": False,
                }
                self._queue.insert(
                    0, current.model_copy(update={"entry_id": uuid.uuid4().hex})
                )
            entry = back.model_copy(
                update={"entry_id": uuid.uuid4().hex, "played_at": None}
            )
            self._start_locked(entry)
        if retired and retired["listened"] >= 1:
            await self._after_retire(retired)
        library.mark_played(entry.track_id)
        await self._run_started(entry)
        await self.broadcast()
        return entry

    async def set_playing(self, playing: bool) -> None:
        async with self._lock:
            pull = playing and self._now_playing is None and bool(self._queue)
            if not pull:
                self._playing = playing and self._now_playing is not None
        if pull:
            await self._advance(finished=False, skipped=False)
        else:
            await self.broadcast()

    async def toggle(self) -> None:
        await self.set_playing(not self._playing)

    async def stop(self) -> None:
        async with self._lock:
            retired = self._retire_locked(completed=False, skipped=False)
            self._start_locked(None)
        await self._after_retire(retired)
        await self.broadcast()

    async def seek(self, position: float) -> None:
        async with self._lock:
            self._position = max(0.0, position)
            # A seek isn't listening: don't count the jump.
            self._last_report = None
        await self.broadcast()

    async def set_volume(self, volume: float) -> None:
        async with self._lock:
            self._volume = max(0.0, min(1.0, volume))
        await self.broadcast()

    async def set_shuffle(self, shuffle: bool) -> None:
        """Shuffle reorders what's queued, once. Turning it off doesn't un-shuffle —
        a queue that silently jumps back to an order you've forgotten is worse."""
        async with self._lock:
            self._shuffle = shuffle
            if shuffle and len(self._queue) > 1:
                random.shuffle(self._queue)
        await self.broadcast()

    async def set_repeat(self, mode: str) -> None:
        if mode not in ("off", "one", "all"):
            raise ValueError(f"repeat must be off|one|all, not {mode!r}")
        async with self._lock:
            self._repeat = mode
        await self.broadcast()

    async def set_radio(self, radio: bool) -> None:
        async with self._lock:
            self._radio = radio
        await self.broadcast()
        self._maybe_refill()

    # --- output ownership -------------------------------------------------------------

    def _output_stale(self) -> bool:
        return self._output_client is None or (
            self._playing and time.monotonic() - self._output_seen > _OUTPUT_STALE_AFTER
        )

    async def claim_output(self, client_id: str, *, force: bool = False) -> bool:
        """Make `client_id` the one client that plays. A person pressing play in a
        window forces it; a window that merely sees "playing, nobody owns it" asks
        without force and gets it only when the owner is absent or silent."""
        if not client_id:
            return False
        async with self._lock:
            if self._output_client == client_id:
                self._output_seen = time.monotonic()
                return True
            if not force and not self._output_stale():
                return False
            self._output_client = client_id
            self._output_seen = time.monotonic()
        await self.broadcast()
        return True

    def is_output(self, client_id: str | None) -> bool:
        """Whether reports from `client_id` are authoritative. Reports from a client
        that isn't the owner (a second window catching up) are ignored; a report with
        no id comes from an older client and is trusted."""
        return client_id is None or self._output_client in (None, client_id)

    # --- reports from the playing client ---------------------------------------------

    async def report_progress(
        self, position: float, duration: float | None, client_id: str | None = None
    ) -> None:
        """~1 Hz from the client with the audio element. Light event, no revision."""
        if not self.is_output(client_id):
            return
        if client_id is not None:
            self._output_seen = time.monotonic()
        now = time.monotonic()
        if self._playing and self._last_report is not None:
            gap = now - self._last_report
            moved = position - self._position
            # Count wall time only when the position moved with it — a paused
            # element or a seek reports, but nobody is listening across it.
            if 0 < gap <= _MAX_PROGRESS_GAP and 0 <= moved <= gap + 1.5:
                self._listened += min(gap, moved + 0.5)
        self._last_report = now
        self._position = max(0.0, position)
        if duration:
            self._duration = duration
        await events.publish(
            "progress", {"position": self._position, "duration": self._duration}
        )

    async def ended(self) -> None:
        """The audio element ran out."""
        await self._advance(finished=True, skipped=False)

    async def track_ready(self, track_id: str, *, ok: bool) -> None:
        """A download finished: unblock (or drop) every entry waiting on it."""
        async with self._lock:
            waiting = [
                e
                for e in ([self._now_playing] if self._now_playing else [])
                + self._queue
                if e.track_id == track_id and not e.ready
            ]
            if not waiting:
                return
            stranded = False
            if ok:
                track = library.get_track(track_id) or {}
                for entry in waiting:
                    entry.ready = True
                    entry.duration = track.get("duration") or entry.duration
                    entry.title = track.get("title") or entry.title
                    entry.artist = track.get("artist") or entry.artist
                    entry.album = track.get("album") or entry.album
                    entry.art_hash = track.get("art_hash") or entry.art_hash
                if self._now_playing in waiting and self._now_playing is not None:
                    self._duration = self._now_playing.duration
            else:
                dead = {e.entry_id for e in waiting}
                self._queue = [e for e in self._queue if e.entry_id not in dead]
                if self._now_playing is not None and self._now_playing.entry_id in dead:
                    self._now_playing = None
                    stranded = True
        if stranded:
            await self._advance(finished=False, skipped=False)
        else:
            await self.broadcast()

    async def track_changed(self, track: dict[str, Any]) -> None:
        """A row was edited or enriched: refresh the entries that show it."""
        changed = False
        async with self._lock:
            for entry in [self._now_playing, *self._queue, *self._history]:
                if entry is not None and entry.track_id == track["id"]:
                    entry.title = track.get("title") or entry.title
                    entry.artist = track.get("artist") or ""
                    entry.album = track.get("album") or ""
                    entry.art_hash = track.get("art_hash") or entry.art_hash
                    changed = True
        if changed:
            await self.broadcast()

    async def track_removed(self, track_id: str) -> None:
        async with self._lock:
            self._queue = [e for e in self._queue if e.track_id != track_id]
            self._history = [e for e in self._history if e.track_id != track_id]
            stranded = (
                self._now_playing is not None and self._now_playing.track_id == track_id
            )
            if stranded:
                self._now_playing = None
        if stranded:
            await self._advance(finished=False, skipped=False)
        else:
            await self.broadcast()

    # --- radio -----------------------------------------------------------------------

    def recent_track_ids(self, limit: int = 10) -> list[str]:
        entries = [self._now_playing, *self._history]
        return [e.track_id for e in entries if e is not None][:limit]

    def _maybe_refill(self) -> None:
        if not self._radio or self._refill is None or self._refilling:
            return
        if len(self._queue) >= _RADIO_LOW_WATER:
            return
        if self._now_playing is None and not self._history:
            return  # nothing to seed from
        self._refilling = True
        task = asyncio.ensure_future(self._do_refill())
        _background.add(task)
        task.add_done_callback(_background.discard)

    async def _do_refill(self) -> None:
        try:
            seeds = list(library.get_tracks(self.recent_track_ids()).values())
            assert self._refill is not None
            picks = await self._refill(seeds)
            if picks and self._radio:
                await self.add(picks, mode="queue", reason="radio")
        except Exception:  # noqa: BLE001 — radio is a nicety, never an error
            logger.exception("radio refill failed")
        finally:
            self._refilling = False


_background: set[asyncio.Future[Any]] = set()

session = MusicSession()
