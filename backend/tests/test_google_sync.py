"""The Drive → library sync.

The original had no tests at all, and each of these pins one of the gaps it shipped
with: it only ever read the first 20 files, it re-filed a duplicate source on every
run, it skipped PDFs entirely, and its target library was hardcoded.

Drive is faked at the `drive_api` seam, and the library at `create_source`/
`ingest_source`, so nothing here touches the network or the vector store.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from backend.modules.connectors import store
from backend.modules.connectors.providers import google_sync
from backend.modules.connectors.store import Credential

DOC = "application/vnd.google-apps.document"
PDF = "application/pdf"


def _file(
    fid: str, name: str, *, mime: str = DOC, modified: str = "2026-01-01T00:00:00Z"
):
    return {
        "id": fid,
        "name": name,
        "mimeType": mime,
        "modifiedTime": modified,
        "webViewLink": f"https://drive.google.com/file/{fid}",
    }


class FakeLibrary:
    """Captures what the sync files into the library."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.ingested: list[tuple[str, Any]] = []
        self.deleted: list[str] = []
        self._ids = itertools.count(1)

    def create_source(self, **kw: Any) -> dict[str, Any]:
        source = {"id": f"src{next(self._ids)}", **kw}
        self.created.append(source)
        return source

    async def ingest_source(self, source_id: str, req: Any) -> None:
        self.ingested.append((source_id, req))

    def delete_source(self, source_id: str) -> bool:
        self.deleted.append(source_id)
        return True


@pytest.fixture
def lib(monkeypatch) -> FakeLibrary:
    fake = FakeLibrary()
    monkeypatch.setattr(google_sync.library_store, "create_source", fake.create_source)
    monkeypatch.setattr(google_sync.library_store, "delete_source", fake.delete_source)
    monkeypatch.setattr(google_sync, "ingest_source", fake.ingest_source)
    return fake


@pytest.fixture(autouse=True)
def connected(monkeypatch):
    """A live Google credential, so the sync doesn't bail at the front door."""
    store.save("google", Credential(access_token="at", refresh_token="rt"))

    async def token() -> str:
        return "at"

    from backend.modules.connectors.providers import google

    monkeypatch.setattr(google, "token", token)
    yield
    store.clear("google")


def _fake_drive(monkeypatch, *, pages: list[dict[str, Any]], texts: dict[str, str]):
    """Stub drive_api: `pages` are successive files.list responses, `texts` maps a file
    id to its extracted text (a missing id extracts as an error)."""
    page_iter = iter(pages)

    async def list_files(**_kw: Any) -> dict[str, Any]:
        return next(page_iter, {"files": []})

    async def extract_text(file_id: str, mime: str, name: str = "") -> Any:
        if file_id in texts:
            return texts[file_id]
        return {"error": f"can't read {name}"}

    async def request(method: str, path: str, **_kw: Any) -> Any:
        if path == "/changes/startPageToken":
            return {"startPageToken": "tok1"}
        return {"error": "unexpected call"}

    monkeypatch.setattr(google_sync.drive_api, "list_files", list_files)
    monkeypatch.setattr(google_sync.drive_api, "extract_text", extract_text)
    monkeypatch.setattr(google_sync.drive_api, "request", request)


# --- the front door ---------------------------------------------------------


def test_no_credential_is_a_no_op(monkeypatch, lib):
    from backend.modules.connectors.providers import google

    async def token() -> None:
        return None

    monkeypatch.setattr(google, "token", token)
    asyncio.run(google_sync.sync_google_drive({}))
    assert lib.created == []


# --- pagination (the original stopped at 20) --------------------------------


def test_full_crawl_follows_every_page(monkeypatch, lib):
    _fake_drive(
        monkeypatch,
        pages=[
            {"files": [_file("a", "A"), _file("b", "B")], "nextPageToken": "p2"},
            {"files": [_file("c", "C")]},
        ],
        texts={"a": "alpha", "b": "beta", "c": "gamma"},
    )
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))

    assert [s["title"] for s in lib.created] == ["A", "B", "C"]
    assert len(lib.ingested) == 3


def test_full_crawl_records_a_change_token_for_next_time(monkeypatch, lib):
    _fake_drive(monkeypatch, pages=[{"files": [_file("a", "A")]}], texts={"a": "alpha"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))
    assert google_sync.get_start_page_token("lib") == "tok1"


def test_run_stops_at_the_per_run_cap(monkeypatch, lib):
    monkeypatch.setattr(google_sync, "MAX_FILES_PER_RUN", 2)
    _fake_drive(
        monkeypatch,
        pages=[{"files": [_file(str(i), f"F{i}") for i in range(5)]}],
        texts={str(i): "text" for i in range(5)},
    )
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))
    assert len(lib.created) == 2


# --- no duplicates (the original re-filed everything every run) -------------


def test_unchanged_file_is_skipped_on_a_re_run(monkeypatch, lib):
    pages = [{"files": [_file("a", "A")]}]
    _fake_drive(monkeypatch, pages=list(pages), texts={"a": "alpha"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))
    assert len(lib.created) == 1

    # Same file, same modifiedTime, full re-crawl: must not file a second copy.
    _fake_drive(monkeypatch, pages=list(pages), texts={"a": "alpha"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))
    assert len(lib.created) == 1, "a re-run duplicated the source"
    assert lib.deleted == []


def test_changed_file_replaces_its_source(monkeypatch, lib):
    _fake_drive(monkeypatch, pages=[{"files": [_file("a", "A")]}], texts={"a": "v1"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))
    first_id = lib.created[0]["id"]

    _fake_drive(
        monkeypatch,
        pages=[{"files": [_file("a", "A", modified="2026-02-02T00:00:00Z")]}],
        texts={"a": "v2"},
    )
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))

    assert lib.deleted == [first_id], (
        "the stale source should be replaced, not orphaned"
    )
    assert len(lib.created) == 2
    assert lib.ingested[-1][1].text == "v2"


def test_the_same_file_can_live_in_two_libraries(monkeypatch, lib):
    for library in ("one", "two"):
        _fake_drive(
            monkeypatch, pages=[{"files": [_file("a", "A")]}], texts={"a": "alpha"}
        )
        asyncio.run(google_sync.sync_google_drive({"library": library, "full": True}))
    assert len(lib.created) == 2
    assert {s["library"] for s in lib.created} == {"one", "two"}


# --- unreadable files -------------------------------------------------------


def test_an_unreadable_file_does_not_abort_the_run(monkeypatch, lib):
    # A scanned PDF / password-protected doc must be skipped, not fatal.
    _fake_drive(
        monkeypatch,
        pages=[
            {
                "files": [
                    _file("a", "A"),
                    _file("bad", "Scanned", mime=PDF),
                    _file("c", "C"),
                ]
            }
        ],
        texts={"a": "alpha", "c": "gamma"},
    )
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))
    assert [s["title"] for s in lib.created] == ["A", "C"]


# --- the ingested source ----------------------------------------------------


def test_ingested_source_shape(monkeypatch, lib):
    _fake_drive(
        monkeypatch, pages=[{"files": [_file("a", "Notes")]}], texts={"a": "hello"}
    )
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))

    source = lib.created[0]
    assert source["type"] == "note"
    assert source["author"] == "Google Drive"
    assert source["tags"] == ["google-drive"]
    assert source["url"] == "https://drive.google.com/file/a"

    _, req = lib.ingested[0]
    assert req.library == "lib" and req.text == "hello" and req.title == "Notes"


# --- the configurable library (was hardcoded) -------------------------------


def test_library_defaults_to_google_drive():
    assert google_sync.target_library({}) == "google_drive"


def test_library_setting_overrides_the_default():
    from backend.modules.settings.routes import set_value

    set_value("connectors.google.driveLibrary", "work-docs")
    assert google_sync.target_library({}) == "work-docs"


def test_explicit_payload_beats_the_setting():
    from backend.modules.settings.routes import set_value

    set_value("connectors.google.driveLibrary", "work-docs")
    assert google_sync.target_library({"library": "adhoc"}) == "adhoc"


# --- incremental sync -------------------------------------------------------


def _fake_changes(monkeypatch, changes: list[dict[str, Any]], *, texts: dict[str, str]):
    async def request(method: str, path: str, **_kw: Any) -> Any:
        if path == "/changes":
            return {"changes": changes, "newStartPageToken": "tok2"}
        if path == "/changes/startPageToken":
            return {"startPageToken": "tok1"}
        return {"error": "unexpected"}

    async def extract_text(file_id: str, mime: str, name: str = "") -> Any:
        return texts.get(file_id) or {"error": "unreadable"}

    async def list_files(**_kw: Any) -> dict[str, Any]:
        raise AssertionError("an incremental run must not do a full crawl")

    monkeypatch.setattr(google_sync.drive_api, "request", request)
    monkeypatch.setattr(google_sync.drive_api, "extract_text", extract_text)
    monkeypatch.setattr(google_sync.drive_api, "list_files", list_files)


def test_second_run_is_incremental_not_a_full_crawl(monkeypatch, lib):
    google_sync.set_start_page_token("lib", "tok1")
    _fake_changes(
        monkeypatch,
        [{"fileId": "z", "file": _file("z", "New")}],
        texts={"z": "fresh"},
    )
    # `list_files` raises if called — reaching the end proves it didn't.
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))

    assert [s["title"] for s in lib.created] == ["New"]
    assert google_sync.get_start_page_token("lib") == "tok2"


def test_full_flag_forces_a_crawl_even_with_a_token(monkeypatch, lib):
    google_sync.set_start_page_token("lib", "tok1")
    _fake_drive(monkeypatch, pages=[{"files": [_file("a", "A")]}], texts={"a": "alpha"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))
    assert [s["title"] for s in lib.created] == ["A"]


def test_removed_file_is_dropped_from_the_library(monkeypatch, lib):
    _fake_drive(monkeypatch, pages=[{"files": [_file("a", "A")]}], texts={"a": "alpha"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))
    source_id = lib.created[0]["id"]

    google_sync.set_start_page_token("lib", "tok1")
    _fake_changes(monkeypatch, [{"fileId": "a", "removed": True}], texts={})
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))

    assert lib.deleted == [source_id]
    assert google_sync.synced_file_count("lib") == 0


def test_trashed_file_is_dropped_too(monkeypatch, lib):
    _fake_drive(monkeypatch, pages=[{"files": [_file("a", "A")]}], texts={"a": "alpha"})
    asyncio.run(google_sync.sync_google_drive({"library": "lib", "full": True}))
    source_id = lib.created[0]["id"]

    google_sync.set_start_page_token("lib", "tok1")
    trashed = {**_file("a", "A"), "trashed": True}
    _fake_changes(monkeypatch, [{"fileId": "a", "file": trashed}], texts={})
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))

    assert lib.deleted == [source_id]


def test_incremental_ignores_unreadable_types(monkeypatch, lib):
    google_sync.set_start_page_token("lib", "tok1")
    image = _file("i", "Photo", mime="image/png")
    _fake_changes(monkeypatch, [{"fileId": "i", "file": image}], texts={})
    asyncio.run(google_sync.sync_google_drive({"library": "lib"}))
    assert lib.created == []


def test_sync_state_is_per_library():
    google_sync.set_start_page_token("one", "t1")
    google_sync.set_start_page_token("two", "t2")
    assert google_sync.get_start_page_token("one") == "t1"
    assert google_sync.get_start_page_token("two") == "t2"
