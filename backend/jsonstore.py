"""Serialization for the backend's whole-file JSON stores.

Half the backend's state lives in a file that is read, changed in memory and
written back whole: `settings.json`, `workspaces.json`, `notes.json`,
`flows.json`, `chat-sessions.json`, `connections.json`, the plugin registry, the
peer/invite tables. Every one of those is a **read-modify-write of the entire
document**, and the routes that do it are sync `def` — which FastAPI runs on the
threadpool, so two requests genuinely run at the same time.

Without a lock, two overlapping writes each read the pre-change document and the
second writes it back missing the first one's change. Nothing fails: both
requests answer 200, and one edit simply never happened. That is not theoretical
— first-run setup writes the user's name, the theme and `desktop.oobeComplete`
from a single click, and losing the last of those is what made a completed setup
wizard reappear on the next launch.

`backend.atomic_write` is the other half and does not replace this one. It stops a
*reader* from seeing a half-written file; it cannot stop two writers from
computing their new documents from the same stale read. A store needs both.

Usage — hold the lock across the read *and* the write, never around the write
alone:

    with jsonstore.locked(_state_path()):
        state = _read()
        state.notes.append(note)
        _write(state)

The lock is re-entrant, so a helper that takes it may be called from a route that
already holds it. It is process-local: it orders this backend's own threads, and
two *processes* sharing a data directory (a stray second uvicorn) are outside
what it can promise — the atomic write keeps that case to a lost update rather
than a corrupt file.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from backend.atomic_write import read_text_or_none, write_text_atomic

#: One lock per store file, keyed by resolved path so `.data/notes.json` and an
#: absolute path to the same file cannot end up with a lock each. Guarded by its
#: own lock because `dict.setdefault` on a fresh `RLock()` would still build one
#: lock object per caller — cheap, but two callers would then hold *different*
#: locks for the same file, which is no lock at all.
_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: Path) -> threading.RLock:
    """The lock guarding one store file."""
    key = Path(path).resolve()
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold a store file's lock for the whole read-modify-write."""
    with lock_for(path):
        yield


def read_text(path: Path) -> str | None:
    """The store's text, or None if it is not there. Re-exported so a store needs
    one import rather than two."""
    return read_text_or_none(path)


def write_text(path: Path, text: str) -> None:
    """Replace the store's contents atomically."""
    write_text_atomic(path, text)


F = TypeVar("F", bound=Callable[..., Any])


def serialized(path_for: Callable[[], Path]) -> Callable[[F], F]:
    """Decorator: hold the store's lock for the whole call.

    For a route that is itself one read-modify-write, which is most of them —
    `state = _read()`, change a field, `_write(state)`. Written as a decorator
    rather than a `with` inside each body so the locked region cannot be quietly
    narrowed to the write later; the read is the half that must be inside it.

    `path_for` is a callable, not a path: the data directory is resolved at call
    time (tests point `HORRIBLE_DATA_DIR` somewhere else), so binding a path at
    import time would lock a file nobody is writing to. Applied **below** the
    router decorator so the router registers the wrapper:

        @router.post("")
        @jsonstore.serialized(_state_path)
        def create_note(...): ...
    """

    def wrap(fn: F) -> F:
        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Any:
            with locked(path_for()):
                return fn(*args, **kwargs)

        return inner  # type: ignore[return-value]

    return wrap
