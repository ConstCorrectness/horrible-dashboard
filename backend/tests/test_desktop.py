"""Wallpaper routes: upload, list, read, delete — and the traversal guard.

The security-relevant bit is `read_wallpaper`. Its id comes straight off the URL
and is turned into a filesystem path, so this suite pins that a `..` (or any
separator) is rejected rather than served.
"""

import pytest
from fastapi.testclient import TestClient

from backend.app import app

# A one-pixel PNG. Bundling a real image would be exactly the thing the module
# refuses to do; this is a literal, not content.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc0000003010100b6c4f4650000000049454e"
    "44ae426082"
)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def upload(client: TestClient, name: str = "wall.png", content_type: str = "image/png"):
    return client.post(
        "/api/desktop/wallpapers",
        files={"file": (name, PNG_1PX, content_type)},
    )


def test_starts_empty(client: TestClient) -> None:
    assert client.get("/api/desktop/wallpapers").json() == {"wallpapers": []}


def test_upload_then_list_and_read(client: TestClient) -> None:
    res = upload(client)
    assert res.status_code == 200
    body = res.json()
    assert body["content_type"] == "image/png"
    assert body["size"] == len(PNG_1PX)
    # The stored url is api-RELATIVE. An absolute one baked into a saved
    # workspace stops resolving the moment the backend origin changes (dev
    # server vs packaged build vs LAN-bound node).
    assert body["url"] == f"/desktop/wallpapers/{body['id']}"

    listing = client.get("/api/desktop/wallpapers").json()["wallpapers"]
    assert [w["id"] for w in listing] == [body["id"]]

    read = client.get(f"/api/desktop/wallpapers/{body['id']}")
    assert read.status_code == 200
    assert read.content == PNG_1PX
    assert read.headers["content-type"].startswith("image/png")
    # Immutable: the id is minted per upload, so a re-upload gets a new one and
    # this can be cached hard. Without it the image refetches on every boot.
    assert "immutable" in read.headers["cache-control"]


def test_each_upload_gets_its_own_id(client: TestClient) -> None:
    first = upload(client).json()["id"]
    second = upload(client).json()["id"]
    assert first != second
    assert len(client.get("/api/desktop/wallpapers").json()["wallpapers"]) == 2


def test_rejects_a_type_the_browser_cannot_paint(client: TestClient) -> None:
    res = client.post(
        "/api/desktop/wallpapers",
        files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")},
    )
    assert res.status_code == 415


def test_extension_comes_from_the_type_not_the_filename(
    client: TestClient, tmp_path
) -> None:
    # Trusting the client's extension is how an upload route starts writing
    # `.html` into a directory it also serves.
    body = upload(client, name="pwn.html").json()
    stored = list((tmp_path / "wallpapers").iterdir())
    assert [p.name for p in stored] == [f"{body['id']}.png"]


def test_rejects_empty_upload(client: TestClient) -> None:
    res = client.post(
        "/api/desktop/wallpapers", files={"file": ("empty.png", b"", "image/png")}
    )
    assert res.status_code == 400


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../../../etc/passwd",
        "..%2f..%2fapp",
        "not-hex-at-all",
        "0123456789abcdef",  # right alphabet, wrong length
        "0123456789abcdef0123456789abcdefff",  # too long
    ],
)
def test_rejects_a_traversing_or_malformed_id(client: TestClient, bad_id: str) -> None:
    res = client.get(f"/api/desktop/wallpapers/{bad_id}")
    # 404 from the router (no route matched the pattern) or 422 from validation —
    # either way the file is never resolved. What must NOT happen is a 200.
    assert res.status_code in (404, 422)


def test_read_missing_is_404(client: TestClient) -> None:
    assert client.get("/api/desktop/wallpapers/" + "ab" * 16).status_code == 404


def test_delete_removes_it(client: TestClient) -> None:
    wallpaper_id = upload(client).json()["id"]
    assert client.delete(f"/api/desktop/wallpapers/{wallpaper_id}").json() == {
        "deleted": True
    }
    assert client.get("/api/desktop/wallpapers").json() == {"wallpapers": []}
    assert client.get(f"/api/desktop/wallpapers/{wallpaper_id}").status_code == 404


def test_listing_ignores_foreign_files(client: TestClient, tmp_path) -> None:
    # Something else wrote into the directory. It is not one of ours (no known
    # extension), so it must not appear as a wallpaper.
    upload(client)
    (tmp_path / "wallpapers" / "notes.txt").write_text("hello")
    listing = client.get("/api/desktop/wallpapers").json()["wallpapers"]
    assert len(listing) == 1
