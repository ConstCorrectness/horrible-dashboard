"""Drive as a virtual root: id addressing, listing, text extraction, and caching."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors import store
from backend.modules.connectors.providers import drive_api, drive_fs
from backend.modules.connectors.store import Credential

FOLDER = drive_fs.FOLDER_MIME


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_drive():
    from backend.modules.connectors import register_connectors

    register_connectors()
    drive_fs.clear_cache()
    yield
    drive_fs.clear_cache()


@pytest.fixture
def connected():
    store.save(
        "google", Credential(access_token="t", account={"id": "1", "label": "me"})
    )


def _fake_pages(monkeypatch, pages: list[dict[str, Any]]):
    """Stub `list_files`, returning the given pages in order."""
    calls: list[dict[str, Any]] = []
    remaining = list(pages)

    async def fake_list_files(**kwargs):
        calls.append(kwargs)
        return remaining.pop(0) if remaining else {"files": []}

    monkeypatch.setattr(drive_api, "list_files", fake_list_files)
    return calls


# --- roots ------------------------------------------------------------------


def test_no_root_until_google_is_connected():
    import asyncio

    assert asyncio.run(drive_fs.provider.roots()) == []


def test_the_root_appears_once_connected(connected):
    import asyncio

    roots = asyncio.run(drive_fs.provider.roots())
    assert [(r.name, r.path) for r in roots] == [("Google Drive", "gdrive:/root")]


def test_the_root_shows_up_in_the_files_api(client: TestClient, connected):
    body = client.get("/api/files/roots").json()
    assert {"name": "Google Drive", "path": "gdrive:/root"} in body


# --- listing ----------------------------------------------------------------


def test_entries_map_ids_to_paths_and_names_to_display(
    client: TestClient, connected, monkeypatch
):
    """The row displays a filename while the path carries the id — that split is what
    lets Drive's id graph ride in a tree that thinks in paths."""
    _fake_pages(
        monkeypatch,
        [
            {
                "files": [
                    {
                        "id": "folder1",
                        "name": "Projects",
                        "mimeType": FOLDER,
                        "modifiedTime": "2026-01-02T03:04:05Z",
                    },
                    {
                        "id": "doc1",
                        "name": "Notes.doc",
                        "mimeType": drive_api.GOOGLE_DOC,
                        "modifiedTime": "2026-01-02T03:04:05Z",
                    },
                    {
                        "id": "pdf1",
                        "name": "Paper.pdf",
                        "mimeType": drive_api.PDF,
                        "size": "1024",
                    },
                ]
            }
        ],
    )

    body = client.get("/api/files/list", params={"path": "gdrive:/somefolder"}).json()
    by_name = {e["name"]: e for e in body["entries"]}

    assert by_name["Projects"]["kind"] == "dir"
    assert by_name["Projects"]["path"] == "gdrive:/folder1"
    assert by_name["Notes.doc"]["kind"] == "file"
    # A Google-native doc isn't stored as bytes, so it reports no size.
    assert by_name["Notes.doc"]["size"] is None
    assert by_name["Paper.pdf"]["size"] == 1024
    assert by_name["Projects"]["mtime"] is not None


def test_my_drive_gains_a_shared_with_me_folder(
    client: TestClient, connected, monkeypatch
):
    _fake_pages(monkeypatch, [{"files": []}])
    body = client.get("/api/files/list", params={"path": "gdrive:/root"}).json()
    assert body["entries"][0]["name"] == "Shared with me"
    assert body["entries"][0]["path"] == "gdrive:/sharedWithMe"


def test_shared_with_me_uses_a_query_not_a_parent(
    client: TestClient, connected, monkeypatch
):
    """Drive has no folder behind 'Shared with me' — it's a flag on files.list."""
    calls = _fake_pages(monkeypatch, [{"files": []}])
    client.get("/api/files/list", params={"path": "gdrive:/sharedWithMe"})
    assert "sharedWithMe = true" in calls[0]["query"]


def test_listing_follows_pagination(client: TestClient, connected, monkeypatch):
    _fake_pages(
        monkeypatch,
        [
            {
                "files": [{"id": "a", "name": "A", "mimeType": "text/plain"}],
                "nextPageToken": "p2",
            },
            {"files": [{"id": "b", "name": "B", "mimeType": "text/plain"}]},
        ],
    )
    body = client.get("/api/files/list", params={"path": "gdrive:/f"}).json()
    assert [e["name"] for e in body["entries"]] == ["A", "B"]


def test_a_drive_error_becomes_an_http_error(
    client: TestClient, connected, monkeypatch
):
    _fake_pages(
        monkeypatch, [{"error": "Google Drive rate limit hit — wait and try again."}]
    )
    res = client.get("/api/files/list", params={"path": "gdrive:/f"})
    assert res.status_code == 502
    assert "rate limit" in res.json()["detail"]


def test_not_connected_is_a_409_not_a_gateway_error(client: TestClient, monkeypatch):
    _fake_pages(monkeypatch, [drive_api.NOT_CONNECTED])
    res = client.get("/api/files/list", params={"path": "gdrive:/f"})
    assert res.status_code == 409


# --- caching ----------------------------------------------------------------


def test_a_second_listing_is_served_from_cache(
    client: TestClient, connected, monkeypatch
):
    calls = _fake_pages(monkeypatch, [{"files": []}, {"files": []}])
    client.get("/api/files/list", params={"path": "gdrive:/f"})
    client.get("/api/files/list", params={"path": "gdrive:/f"})
    assert len(calls) == 1


def test_fresh_bypasses_the_cache(client: TestClient, connected, monkeypatch):
    calls = _fake_pages(monkeypatch, [{"files": []}, {"files": []}])
    client.get("/api/files/list", params={"path": "gdrive:/f"})
    client.get("/api/files/list", params={"path": "gdrive:/f", "fresh": "true"})
    assert len(calls) == 2


def test_the_cache_expires(client: TestClient, connected, monkeypatch):
    calls = _fake_pages(monkeypatch, [{"files": []}, {"files": []}])
    client.get("/api/files/list", params={"path": "gdrive:/f"})

    real_monotonic = drive_fs.time.monotonic
    monkeypatch.setattr(
        drive_fs.time, "monotonic", lambda: real_monotonic() + drive_fs.CACHE_TTL_S + 1
    )
    client.get("/api/files/list", params={"path": "gdrive:/f"})
    assert len(calls) == 2


# --- reading ----------------------------------------------------------------


def _fake_meta(monkeypatch, meta: dict[str, Any]):
    async def fake_request(method, path, **kwargs):
        return meta

    monkeypatch.setattr(drive_api, "request", fake_request)


def test_a_google_doc_is_exported_to_text(client: TestClient, connected, monkeypatch):
    _fake_meta(
        monkeypatch, {"id": "doc1", "name": "Notes", "mimeType": drive_api.GOOGLE_DOC}
    )

    async def fake_extract(file_id, mime, name=""):
        return "exported body"

    monkeypatch.setattr(drive_api, "extract_text", fake_extract)

    body = client.get("/api/files/read", params={"path": "gdrive:/doc1"}).json()
    assert body["content"] == "exported body"
    # The name rides along because the path is an id and can't supply a title.
    assert body["name"] == "Notes"


def test_an_unsupported_mime_is_a_415(client: TestClient, connected, monkeypatch):
    """Matching the local /read's answer for a binary file, so the editor needs no
    Drive-specific error handling."""
    _fake_meta(monkeypatch, {"id": "x", "name": "clip.mp4", "mimeType": "video/mp4"})
    res = client.get("/api/files/read", params={"path": "gdrive:/x"})
    assert res.status_code == 415


def test_reading_a_folder_is_a_400(client: TestClient, connected, monkeypatch):
    _fake_meta(monkeypatch, {"id": "f", "name": "Projects", "mimeType": FOLDER})
    res = client.get("/api/files/read", params={"path": "gdrive:/f"})
    assert res.status_code == 400


def test_an_extraction_failure_is_reported(client: TestClient, connected, monkeypatch):
    _fake_meta(monkeypatch, {"id": "p", "name": "Scan.pdf", "mimeType": drive_api.PDF})

    async def fake_extract(file_id, mime, name=""):
        return {"error": "Scan.pdf has no extractable text (probably scanned)"}

    monkeypatch.setattr(drive_api, "extract_text", fake_extract)
    res = client.get("/api/files/read", params={"path": "gdrive:/p"})
    assert res.status_code == 502
    assert "scanned" in res.json()["detail"]


# --- read-only --------------------------------------------------------------


def test_drive_paths_reject_writes(client: TestClient, connected):
    res = client.put(
        "/api/files/write", json={"path": "gdrive:/doc1", "content": "nope"}
    )
    assert res.status_code == 403


# --- query escaping ---------------------------------------------------------


def test_a_quote_in_an_id_cannot_break_out_of_the_query(connected, monkeypatch):
    """Drive `q` is a query language; an unescaped quote would change its meaning."""
    calls = _fake_pages(monkeypatch, [{"files": []}])
    import asyncio

    asyncio.run(drive_fs.provider.list("gdrive:/a'b"))
    assert "\\'" in calls[0]["query"]


def test_raise_maps_not_connected_to_409():
    with pytest.raises(HTTPException) as exc:
        drive_fs._raise(drive_api.NOT_CONNECTED["error"])
    assert exc.value.status_code == 409
