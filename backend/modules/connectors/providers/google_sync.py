"""Google Drive → library sync.

Walks the connected Drive, extracts text (Google Docs, PDFs, plain text), and files
each document into a library as a `note` source, so Drive content becomes searchable
alongside everything else the library holds.

Recovered and rebuilt from `backend/modules/integrations/google_sync.py`, which rode on
the old broken Google OAuth and died with it. Four things are different:

* it reads the **connector** credential (`connector:google`) rather than a bare
  `get_secret("google_oauth_credentials")`;
* **PDFs are parsed** instead of skipped;
* it **paginates and syncs incrementally** via Drive's `changes` API, instead of only
  ever taking the 20 most recent files;
* the target **library is configurable**, not hardcoded to `google_drive`.

It also no longer duplicates: the original called `create_source` unconditionally, so
every run filed another copy of the same document. A file→source mapping makes a
re-sync *replace* the source it already made.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from backend.modules.connectors.providers import drive_api
from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.library import store as library_store
from backend.modules.library.ingest import ingest_source
from backend.modules.library.models import IngestRequest

# `queue` here is the TaskQueue *instance* — backend/modules/tasks/__init__.py
# re-exports it, shadowing the submodule of the same name. `enqueue_task` is a
# module-level function, so it has to be imported separately.
from backend.modules.tasks import enqueue_task, queue

logger = logging.getLogger(__name__)

TASK_TYPE = "sync_google_drive"
DEFAULT_LIBRARY = "google_drive"

# Drive allows up to 1000; 100 keeps each response small while still making a full
# crawl of a big Drive a handful of round-trips rather than dozens.
PAGE_SIZE = 100
# A backstop so a first run against an enormous Drive can't spin forever. Reaching it
# leaves the sync state unset, so the next run picks up where this one left off.
MAX_FILES_PER_RUN = 500


# --- sync state -------------------------------------------------------------
#
# Lives in the app DB next to `library_sources` rather than in settings: a page token
# is machine state, not a user preference, and settings are readable by the browser.


@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_sync_db() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS google_drive_sync (
                library TEXT PRIMARY KEY,
                start_page_token TEXT,
                last_synced_at TIMESTAMP
            )
            """
        )
        # file -> the source we created for it, so a re-sync replaces rather than
        # duplicates, and an unchanged file is skipped outright.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS google_drive_files (
                file_id TEXT NOT NULL,
                library TEXT NOT NULL,
                source_id TEXT NOT NULL,
                modified_time TEXT,
                PRIMARY KEY (file_id, library)
            )
            """
        )


def get_start_page_token(library: str) -> str | None:
    init_sync_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT start_page_token FROM google_drive_sync WHERE library = ?",
            (library,),
        ).fetchone()
    return row["start_page_token"] if row else None


def set_start_page_token(library: str, token: str | None) -> None:
    init_sync_db()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO google_drive_sync (library, start_page_token, last_synced_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(library) DO UPDATE SET
                start_page_token = excluded.start_page_token,
                last_synced_at = CURRENT_TIMESTAMP
            """,
            (library, token),
        )


def _known_file(file_id: str, library: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT source_id, modified_time FROM google_drive_files WHERE file_id = ? AND library = ?",
            (file_id, library),
        ).fetchone()
    return dict(row) if row else None


def _remember_file(
    file_id: str, library: str, source_id: str, modified: str | None
) -> None:
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO google_drive_files (file_id, library, source_id, modified_time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_id, library) DO UPDATE SET
                source_id = excluded.source_id,
                modified_time = excluded.modified_time
            """,
            (file_id, library, source_id, modified),
        )


def _forget_file(file_id: str, library: str) -> None:
    with _db() as conn:
        conn.execute(
            "DELETE FROM google_drive_files WHERE file_id = ? AND library = ?",
            (file_id, library),
        )


# --- the sync ---------------------------------------------------------------


def target_library(payload: dict[str, Any]) -> str:
    """Explicit payload wins, then the setting, then the default."""
    from backend.modules.settings.routes import get_value

    return str(
        payload.get("library")
        or get_value("connectors.google.driveLibrary", "")
        or DEFAULT_LIBRARY
    )


def synced_file_count(library: str) -> int:
    """How many Drive files this library currently tracks."""
    init_sync_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM google_drive_files WHERE library = ?", (library,)
        ).fetchone()
    return int(row["n"]) if row else 0


async def _ingest_file(item: dict[str, Any], library: str) -> str:
    """Ingest one Drive file. Returns a short outcome for the run summary."""
    file_id = str(item.get("id") or "")
    name = str(item.get("name") or "untitled")
    mime = str(item.get("mimeType") or "")
    modified = item.get("modifiedTime")
    link = item.get("webViewLink")

    known = _known_file(file_id, library)
    if known and known.get("modified_time") == modified:
        return "unchanged"

    text = await drive_api.extract_text(file_id, mime, name)
    if isinstance(text, dict):
        # Unreadable (scanned PDF, password-protected, an unsupported type). Log and
        # move on — one bad file must not abort the whole sync.
        logger.info("drive sync: skipping %s — %s", name, text.get("error"))
        return "skipped"

    # Replace rather than duplicate: the same Drive file must map to one source.
    if known:
        library_store.delete_source(known["source_id"])

    source = library_store.create_source(
        library=library,
        type="note",
        title=name,
        url=link,
        author="Google Drive",
        tags=["google-drive"],
    )
    await ingest_source(
        source["id"],
        IngestRequest(type="note", library=library, text=text, title=name, url=link),
    )
    _remember_file(file_id, library, source["id"], modified)
    return "updated" if known else "added"


async def _full_crawl(library: str) -> dict[str, int]:
    """Every readable file, following `nextPageToken`. The original stopped at 20."""
    counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    page_token: str | None = None
    seen = 0

    while True:
        page = await drive_api.list_files(
            query=drive_api.readable_mime_query(),
            page_token=page_token,
            page_size=PAGE_SIZE,
        )
        if isinstance(page, dict) and page.get("error"):
            logger.warning("drive sync: listing failed — %s", page["error"])
            break
        for item in (page or {}).get("files") or []:
            counts[await _ingest_file(item, library)] += 1
            seen += 1
            if seen >= MAX_FILES_PER_RUN:
                logger.info("drive sync: stopping at %s files this run", seen)
                return counts
        page_token = (page or {}).get("nextPageToken")
        if not page_token:
            break
    return counts


async def _incremental(library: str, token: str) -> dict[str, int]:
    """Only what changed since `token`, via Drive's changes feed."""
    counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "removed": 0}
    page_token: str | None = token

    while page_token:
        page = await drive_api.request(
            "GET",
            "/changes",
            params={
                "pageToken": page_token,
                "pageSize": PAGE_SIZE,
                "fields": (
                    "nextPageToken, newStartPageToken, "
                    f"changes(removed, fileId, file({drive_api.FILE_FIELDS}))"
                ),
            },
        )
        if isinstance(page, dict) and page.get("error"):
            logger.warning("drive sync: changes failed — %s", page["error"])
            return counts

        for change in page.get("changes") or []:
            file_id = str(change.get("fileId") or "")
            item = change.get("file") or {}
            # A file that was deleted or trashed should leave the library too —
            # otherwise the library slowly fills with documents that no longer exist.
            if change.get("removed") or item.get("trashed"):
                if known := _known_file(file_id, library):
                    library_store.delete_source(known["source_id"])
                    _forget_file(file_id, library)
                    counts["removed"] += 1
                continue
            if item.get("mimeType") not in drive_api.READABLE_MIMES:
                continue
            counts[await _ingest_file(item, library)] += 1

        if new_start := page.get("newStartPageToken"):
            set_start_page_token(library, new_start)
        page_token = page.get("nextPageToken")

    return counts


async def sync_google_drive(payload: dict[str, Any]) -> None:
    """Task handler: bring `library` up to date with the connected Drive.

    First run does a paginated full crawl and records a change token; later runs read
    only what changed. Payload: `{"library": "...", "full": bool}`.
    """
    from backend.modules.connectors.providers import google

    library = target_library(payload)
    init_sync_db()

    if not await google.token():
        logger.warning("drive sync: Google isn't connected — nothing to do")
        return

    token = None if payload.get("full") else get_start_page_token(library)

    if token:
        counts = await _incremental(library, token)
    else:
        # Grab the change token *before* crawling, so edits made during the crawl are
        # caught by the next incremental run instead of being missed forever.
        start = await drive_api.request("GET", "/changes/startPageToken")
        counts = await _full_crawl(library)
        if isinstance(start, dict) and start.get("startPageToken"):
            set_start_page_token(library, str(start["startPageToken"]))

    logger.info("drive sync: %s -> %s", library, counts)


def enqueue_sync(library: str | None = None, *, full: bool = False) -> str:
    payload: dict[str, Any] = {"full": full}
    if library:
        payload["library"] = library
    return enqueue_task(TASK_TYPE, payload)


queue.register_handler(TASK_TYPE, sync_google_drive)
