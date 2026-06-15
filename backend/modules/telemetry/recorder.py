import asyncio
import itertools
import time
from collections import deque
from typing import Any

from backend.modules.telemetry.models import IoEvent

_MAXLEN = 500


class Recorder:
    """In-memory ring buffer of I/O events with live subscribers.

    Records *metadata only*. Append and notify run on the event loop, so a plain
    set of asyncio.Queues is safe without locking.
    """

    def __init__(self, maxlen: int = _MAXLEN) -> None:
        self._buffer: deque[IoEvent] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue[IoEvent]] = set()
        self._ids = itertools.count(1)

    def record(self, **fields: Any) -> IoEvent:
        event = IoEvent(id=next(self._ids), ts=time.time(), **fields)
        self._buffer.append(event)
        self._notify(event)
        return event

    def amend(self, event_id: int, **fields: Any) -> IoEvent | None:
        """Update an already-recorded event in place and re-emit it under the same
        id, so subscribers replace the existing row. Used to fill in a streaming
        response body once the stream finishes (the body isn't known when the
        event is first recorded). No-op if the event has aged out of the buffer."""
        for i, event in enumerate(self._buffer):
            if event.id == event_id:
                updated = event.model_copy(update=fields)
                self._buffer[i] = updated
                self._notify(updated)
                return updated
        return None

    def _notify(self, event: IoEvent) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def recent(self) -> list[IoEvent]:
        return list(self._buffer)

    def subscribe(self) -> asyncio.Queue[IoEvent]:
        queue: asyncio.Queue[IoEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[IoEvent]) -> None:
        self._subscribers.discard(queue)

    def clear(self) -> None:
        self._buffer.clear()


recorder = Recorder()
