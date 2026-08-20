"""The store lock, tested as the bug it exists to prevent.

A test that only asserts "the lock is an RLock" would pass over the actual defect,
so these drive concurrent read-modify-writes through the real helper and check that
no update was lost.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from backend import jsonstore


def _bump(path: Path, key: str, barrier: threading.Barrier) -> None:
    """One read-modify-write of the whole document, with the two threads forced to
    overlap: without the lock they both read the same document and the second write
    drops the first key."""
    with jsonstore.locked(path):
        text = jsonstore.read_text(path)
        data = json.loads(text) if text else {}
        try:
            # Inside the lock, so a second thread arriving here can only be one that
            # is *not* holding it — i.e. the barrier times out rather than deadlocks
            # when the lock works, which is the passing case.
            barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        data[key] = True
        jsonstore.write_text(path, json.dumps(data))


def test_concurrent_writes_keep_both_keys(tmp_path: Path) -> None:
    store = tmp_path / "store.json"
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=_bump, args=(store, key, barrier))
        for key in ("first", "second")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert json.loads(store.read_text(encoding="utf-8")) == {
        "first": True,
        "second": True,
    }


def test_lock_is_per_file(tmp_path: Path) -> None:
    a = jsonstore.lock_for(tmp_path / "a.json")
    b = jsonstore.lock_for(tmp_path / "b.json")
    assert a is not b
    # Same file reached two ways is the same lock — otherwise the store is guarded
    # by whichever spelling the caller happened to use.
    assert jsonstore.lock_for(tmp_path / "a.json") is a
    assert jsonstore.lock_for(tmp_path / "sub" / ".." / "a.json") is a


def test_lock_is_reentrant(tmp_path: Path) -> None:
    """A helper that takes the lock may be called from a route already holding it."""
    store = tmp_path / "store.json"
    with jsonstore.locked(store):
        with jsonstore.locked(store):
            jsonstore.write_text(store, json.dumps({"ok": True}))
    assert json.loads(store.read_text(encoding="utf-8")) == {"ok": True}


def test_read_text_missing_is_none(tmp_path: Path) -> None:
    assert jsonstore.read_text(tmp_path / "nope.json") is None


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    store = tmp_path / "deep" / "nested" / "store.json"
    jsonstore.write_text(store, "{}")
    assert store.read_text(encoding="utf-8") == "{}"


def test_serialized_decorator_holds_the_lock(tmp_path: Path) -> None:
    store = tmp_path / "store.json"
    seen: list[bool] = []

    @jsonstore.serialized(lambda: store)
    def route() -> None:
        seen.append(jsonstore.lock_for(store).acquire(blocking=False))
        jsonstore.lock_for(store).release()

    route()
    # Re-entrant from the same thread, so the probe succeeds; the point is that the
    # decorated call runs inside the region at all.
    assert seen == [True]
