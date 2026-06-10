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
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

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
