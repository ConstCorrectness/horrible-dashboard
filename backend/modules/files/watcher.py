"""Live file-watch over the configured workspace roots.

A single process-wide watcher (mirroring telemetry's recorder/stream split)
broadcasts filesystem changes to per-connection subscribers, which forward them on
the `files` `/ws` channel. The frontend tree applies these instead of manual
re-listing; the same stream later feeds the LSP and the agent's awareness of
changes. See docs/modules/file-explorer.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from watchfiles import Change, awatch

from backend.modules.files.routes import _roots

logger = logging.getLogger(__name__)

# How often (ms) awatch yields with no changes, so we can re-resolve roots and
# pick up a settings change without an explicit restart signal.
_ROOTS_RECHECK_MS = 5000

_CHANGE_NAMES = {
    Change.added: "added",
    Change.modified: "modified",
    Change.deleted: "deleted",
}


class FileWatcher:
    """Process-wide filesystem watcher with live subscribers.

    Lazily starts one `awatch` task on the first subscriber and keeps it running;
    broadcasting to an empty subscriber set is a harmless no-op. The task
    re-resolves the workspace roots periodically, so it survives an empty initial
    config and restarts its watch when the roots change.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[list[dict[str, Any]]]] = set()
        self._task: asyncio.Task[None] | None = None

    def subscribe(self) -> asyncio.Queue[list[dict[str, Any]]]:
        queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return queue

    def unsubscribe(self, queue: asyncio.Queue[list[dict[str, Any]]]) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, changes: list[dict[str, Any]]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(changes)
            except asyncio.QueueFull:
                pass

    async def _run(self) -> None:
        while True:
            roots = _roots()
            if not roots:
                await asyncio.sleep(3)
                continue
            root_strs = [str(r) for r in roots]
            try:
                async for batch in awatch(
                    *root_strs,
                    yield_on_timeout=True,
                    rust_timeout=_ROOTS_RECHECK_MS,
                ):
                    if not batch:
                        # Timeout tick — restart the watch if the roots changed.
                        if [str(r) for r in _roots()] != root_strs:
                            break
                        continue
                    self._broadcast(_to_events(batch))
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception:  # noqa: BLE001 — keep the watcher alive on any FS error
                logger.exception("file watcher error; retrying")
                await asyncio.sleep(2)


def _to_events(batch: set[tuple[Change, str]]) -> list[dict[str, Any]]:
    """Map a watchfiles batch to the wire shape the tree consumes. `parent` lets
    the tree re-list just the affected directory."""
    events: list[dict[str, Any]] = []
    for change, raw in batch:
        events.append(
            {
                "type": _CHANGE_NAMES.get(change, "modified"),
                "path": raw,
                "parent": os.path.dirname(raw) or str(Path(raw).parent),
            }
        )
    return events


watcher = FileWatcher()


async def push_file_events(conn: Any) -> None:
    """Forward filesystem changes to one `/ws` connection on the `files` channel.

    Push-only: the shared `/ws` handler owns the inbound receive loop and cancels
    this task on disconnect. Sends go through the connection's lock.
    """
    queue = watcher.subscribe()
    try:
        while True:
            changes = await queue.get()
            await conn.send_json(
                {"channel": "files", "event": "change", "data": {"changes": changes}}
            )
    finally:
        watcher.unsubscribe(queue)
