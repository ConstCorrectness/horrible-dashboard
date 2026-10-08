"""Tell open editors when a page changes on disk — from VS Code, `git pull`, or an
agent tool writing the file.

One task over the whole Scrive root, started from the app lifespan. Every change to a
page becomes a `scrive` / `page.changed` event carrying the new revision. That
includes the editor's **own** saves: a client compares the revision with the one it
holds and drops its echo, which is simpler and more honest than the backend trying to
guess who wrote a file.

`watchfiles` is optional (as for the files module). Without it nothing is pushed and
the editor still catches a stale save through the 409 — slower, never wrong.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.modules.scrive import apps, index, store
from backend.modules.scrive.models import PageChanged
from backend.modules.ws import broadcast_event

try:
    from watchfiles import Change, awatch
except ImportError:  # pragma: no cover - optional dependency
    Change, awatch = None, None

logger = logging.getLogger(__name__)

CHANNEL = "scrive"
#: How long one watch runs before re-reading the root setting, so pointing
#: `scrive.root` somewhere else takes effect without a restart.
_RECHECK_MS = 5000

_task: asyncio.Task[None] | None = None


def event_for(base: Path, path: Path, change: str) -> PageChanged | None:
    """The event for one filesystem change, or None when it is not about a page.

    The reported change is what is **on disk now**, not what the OS said happened. An
    atomic save (temp file + `os.replace`) reaches `watchfiles` on Windows as
    *deleted* then *added* for the same path; taken literally, every save from every
    editor told open panes their page had been deleted.
    """
    if change == "deleted" and path.is_file():
        change = "modified"
    elif change != "deleted" and not path.is_file():
        change = "deleted"
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    site, page = rel.parts[0], Path(*rel.parts[1:]).as_posix()
    if not path.name.lower().endswith(store.PAGE_SUFFIXES):
        return None
    if any(part in store.SKIP_DIRS for part in rel.parts[1:-1]):
        return None
    if path.name.endswith(".scrive-tmp"):
        return None
    revision = ""
    if change != "deleted":
        try:
            revision = store.revision_of(path.read_bytes())
        except OSError:
            # Replaced again between the event and this read; the next event has it.
            return None
    origin = (
        "agent" if revision and store.take_agent_write(site, page, revision) else ""
    )
    return PageChanged(
        site=site,
        path=page,
        change=change,  # type: ignore[arg-type]
        revision=revision,
        origin=origin,  # type: ignore[arg-type]
    )


def app_for(base: Path, path: Path) -> tuple[str, str] | None:
    """`(site, app)` when `path` is a file of a web app (`<site>/apps/<app>/…`), so an
    open preview can reload; None otherwise."""
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    if len(rel.parts) < 4 or rel.parts[1] != apps.APPS_DIR:
        return None
    if path.name.endswith(".scrive-tmp"):
        return None
    return rel.parts[0], rel.parts[2]


async def _run() -> None:
    names = {
        Change.added: "added",
        Change.modified: "modified",
        Change.deleted: "deleted",
    }
    stop = asyncio.Event()
    while True:
        base = store.root()
        if not base.is_dir():
            await asyncio.sleep(_RECHECK_MS / 1000)
            continue
        try:
            async for batch in awatch(
                base, stop_event=stop, rust_timeout=_RECHECK_MS, yield_on_timeout=True
            ):
                if store.root() != base:
                    break
                # One event per page per batch, judged by the file's state now
                # (see `event_for`), so the order changes arrived in cannot matter.
                latest: dict[str, str] = {}
                changed_apps: set[tuple[str, str]] = set()
                for change, raw in batch:
                    latest[raw] = names.get(change, "modified")
                    app = app_for(base, Path(raw))
                    if app is not None:
                        changed_apps.add(app)
                for site, app in sorted(changed_apps):
                    await broadcast_event(CHANNEL, "app.changed", {"site": site, "app": app})
                for raw, change in latest.items():
                    event = event_for(base, Path(raw), change)
                    if event is not None:
                        await broadcast_event(
                            CHANNEL, "page.changed", event.model_dump()
                        )
                        index.schedule(event.site, event.path)
        except asyncio.CancelledError:
            raise
        except Exception:  # a vanished root, a permissions error: back off, retry
            logger.exception("scrive watcher failed; retrying")
            await asyncio.sleep(_RECHECK_MS / 1000)


def start() -> None:
    global _task
    if awatch is None or (_task is not None and not _task.done()):
        return
    _task = asyncio.create_task(_run(), name="scrive-watcher")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None
