"""Google Drive as a browsable virtual root — the Colab-style "mount".

Registers a `gdrive:` provider with the files module, so Drive shows up as a root in the
ordinary file tree and its files open as ordinary (read-only) editor buffers. Nothing in
the files module or the editor knows this is Drive; they only know the scheme.

**Why the path is a file id, not a name path.** Drive is an id graph, not a tree: a file
can have several parents, and sibling names collide freely. `gdrive://My Drive/notes.txt`
therefore isn't a key — resolving it would mean walking the graph on every request, and
two different files could still claim it. `gdrive:/<fileId>` is the only stable identity.
That costs nothing in the UI because the tree renders `FileEntry.name` independently of
`FileEntry.path`, so the user sees "notes.txt" while the id does the addressing.

Two ids are special: `root` is Drive's own alias for My Drive, and `sharedWithMe` is a
pseudo-folder with no real file behind it (Drive exposes it as a query, not a folder).

**Read-only.** v1 never writes to Drive; the files module's write routes reject the
scheme outright rather than this module having to refuse each one.
"""

from __future__ import annotations

import time
from typing import Any

from backend.modules.connectors import store
from backend.modules.connectors.providers import drive_api
from backend.modules.files import providers
from backend.modules.files.models import DirListing, FileContent, FileEntry, RootInfo

SCHEME = "gdrive"
FOLDER_MIME = "application/vnd.google-apps.folder"

MY_DRIVE_ID = "root"
SHARED_ID = "sharedWithMe"

# Drive's files.list runs 300-800ms, and the tree calls it on every expand — so a short
# TTL is the difference between a snappy tree and a visibly laggy one. Kept small
# because it's a lie by construction: someone else may be editing the same Drive.
CACHE_TTL_S = 60.0
CACHE_MAX = 200

_cache: dict[str, tuple[float, DirListing]] = {}


def _uri(file_id: str) -> str:
    return f"{SCHEME}:/{file_id}"


def _file_id(path: str) -> str:
    """The Drive id inside a `gdrive:/<id>` path."""
    prefix = f"{SCHEME}:/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def _cache_get(key: str) -> DirListing | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    stored_at, listing = hit
    if time.monotonic() - stored_at > CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return listing


def _cache_put(key: str, listing: DirListing) -> None:
    if len(_cache) >= CACHE_MAX:
        # FIFO: dicts preserve insertion order, and the oldest listing is the one least
        # likely to still be on screen.
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (time.monotonic(), listing)


def clear_cache() -> None:
    _cache.clear()


def _entry(item: dict[str, Any]) -> FileEntry:
    """One Drive file as a tree row."""
    is_folder = item.get("mimeType") == FOLDER_MIME
    size = item.get("size")
    return FileEntry(
        name=str(item.get("name") or "(untitled)"),
        path=_uri(str(item.get("id") or "")),
        kind="dir" if is_folder else "file",
        # Google-native docs (Docs, Sheets) report no size — they aren't stored as bytes.
        size=int(size) if size is not None else None,
        mtime=_epoch(item.get("modifiedTime")),
    )


def _epoch(rfc3339: str | None) -> float | None:
    if not rfc3339:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(rfc3339.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class DriveProvider:
    """The `gdrive:` file provider. See the module docstring."""

    scheme = SCHEME
    read_only = True

    async def roots(self) -> list[RootInfo]:
        """Empty unless Google is connected — which is what makes the Drive root appear
        on connect and disappear on disconnect, with no coupling either way."""
        if not store.is_connected("google"):
            return []
        return [RootInfo(name="Google Drive", path=_uri(MY_DRIVE_ID))]

    async def list(self, path: str, *, fresh: bool = False) -> DirListing:
        file_id = _file_id(path)
        if not fresh and (cached := _cache_get(file_id)) is not None:
            return cached

        if file_id == SHARED_ID:
            query = "sharedWithMe = true and trashed = false"
        else:
            query = f"'{_escape(file_id)}' in parents and trashed = false"

        entries: list[FileEntry] = []
        page_token: str | None = None
        while True:
            page = await drive_api.list_files(
                query=query,
                page_token=page_token,
                page_size=100,
                # Folders first, then alphabetical — the tree's own local ordering.
                order_by="folder,name",
            )
            if isinstance(page, dict) and page.get("error"):
                _raise(page["error"])
            entries.extend(_entry(f) for f in (page.get("files") or []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break

        # My Drive gets a sibling pseudo-folder for shared files, which Drive exposes as
        # a query rather than a real folder — so it has to be synthesized here.
        if file_id == MY_DRIVE_ID:
            entries.insert(
                0,
                FileEntry(
                    name="Shared with me",
                    path=_uri(SHARED_ID),
                    kind="dir",
                    size=None,
                    mtime=None,
                ),
            )

        listing = DirListing(path=path, entries=entries)
        _cache_put(file_id, listing)
        return listing

    async def read(self, path: str) -> FileContent:
        file_id = _file_id(path)
        meta = await drive_api.request(
            "GET", f"/files/{file_id}", params={"fields": "id, name, mimeType"}
        )
        if isinstance(meta, dict) and meta.get("error"):
            _raise(meta["error"])

        name = str(meta.get("name") or "")
        mime = str(meta.get("mimeType") or "")
        if mime == FOLDER_MIME:
            _raise("not a file", status=400)
        if mime not in drive_api.READABLE_MIMES:
            # Same 415 the local /read gives for a binary file, so the editor's error
            # path doesn't need a Drive-specific case.
            _raise(f"can't display {mime} as text", status=415)

        text = await drive_api.extract_text(file_id, mime, name)
        if isinstance(text, dict):
            _raise(str(text.get("error") or "couldn't read the file"))

        return FileContent(
            path=path,
            content=text,
            truncated=len(text) >= drive_api.MAX_TEXT_CHARS,
            name=name,
        )


def _escape(value: str) -> str:
    """Escape a literal for a Drive `q` clause."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _raise(message: str, *, status: int = 502) -> None:
    """Turn drive_api's error-as-value into the HTTP error the files API speaks.

    `drive_api` returns errors as values because its other callers are an agent loop and
    a background task; this one is an HTTP route, where an exception is the right shape.
    """
    from fastapi import HTTPException

    # "not connected" is the user's problem to fix, not a gateway failure.
    if message == drive_api.NOT_CONNECTED["error"]:
        status = 409
    raise HTTPException(status_code=status, detail=message)


provider = DriveProvider()


def register() -> None:
    providers.register(provider)
