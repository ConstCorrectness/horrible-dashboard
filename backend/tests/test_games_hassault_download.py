"""The native client as a download from the game server.

`/download/hAssault.exe` is the file a player double-clicks; `/download/info` is
how the landing page learns there is one. These pin the three things that fail
quietly: a deploy that carries no build must not look broken, the route must not
hand back anything but the staged client, and a file that changed under its
manifest must not be vouched for with the old checksum.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from backend.games_server import downloads

PAYLOAD = b"MZ" + bytes(range(256)) * 8


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A staging directory holding one client and its manifest."""
    root = tmp_path / "dist"
    root.mkdir()
    (root / "hAssault.exe").write_bytes(PAYLOAD)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "windows": {
                    "file": "hAssault.exe",
                    "version": "0.2.1",
                    "commit": "abc1234",
                    "sizeBytes": len(PAYLOAD),
                    "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                    "builtAt": "2026-11-09T12:00:00.000Z",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GAMES_DOWNLOAD_DIR", str(root))
    return root


@pytest.fixture
def server(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    from backend.games_server.app import app

    return TestClient(app)


def test_the_staged_client_is_described_and_served(staged, server):
    info = server.get("/download/info").json()["windows"]
    assert info == {
        "file": "hAssault.exe",
        "url": "/download/hAssault.exe",
        "version": "0.2.1",
        "commit": "abc1234",
        "sizeBytes": len(PAYLOAD),
        "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        "builtAt": "2026-11-09T12:00:00.000Z",
    }

    res = server.get(info["url"])
    assert res.status_code == 200
    assert res.content == PAYLOAD
    # Saved under the game's name whatever the browser thinks of the URL.
    assert 'filename="hAssault.exe"' in res.headers["content-disposition"]
    assert res.headers["content-disposition"].startswith("attachment")
    # Revalidated every time: the name is stable and the bytes are not.
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["etag"]
    assert res.headers["x-checksum-sha256"] == info["sha256"]


def test_a_download_manager_can_ask_for_the_size_first(staged, server):
    res = server.head("/download/hAssault.exe")
    assert res.status_code == 200
    assert int(res.headers["content-length"]) == len(PAYLOAD)
    assert res.content == b""


def test_an_unchanged_client_is_not_sent_twice(staged, server):
    etag = server.get("/download/hAssault.exe").headers["etag"]
    again = server.get("/download/hAssault.exe", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    # A validator that is not ours is not a match, alone or in a list.
    stale = server.get("/download/hAssault.exe", headers={"If-None-Match": '"old"'})
    assert stale.status_code == 200
    listed = server.get(
        "/download/hAssault.exe", headers={"If-None-Match": f'"old", W/{etag}'}
    )
    assert listed.status_code == 304


def test_an_interrupted_download_can_resume(staged, server):
    """130 MB over a home connection gets cut off. A browser resumes with
    `Range`, and a server that ignored it would start from the first byte."""
    res = server.get("/download/hAssault.exe", headers={"Range": "bytes=100-199"})
    assert res.status_code == 206
    assert res.content == PAYLOAD[100:200]
    assert res.headers["content-range"] == f"bytes 100-199/{len(PAYLOAD)}"


def test_it_is_served_before_the_web_client_s_mount(staged, server):
    """The browser game is mounted at `/` and matches every path. A download
    route registered after it would be an `index.html` saved as an executable."""
    from backend.games_server.app import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/download/info" in paths
    if "" in paths:  # the mount is only there when a web build exists
        assert paths.index("/download/info") < paths.index("")
    assert (
        server.get("/download/info")
        .headers["content-type"]
        .startswith("application/json")
    )


@pytest.mark.parametrize(
    "manifest",
    [
        None,  # never staged
        "not json",
        "[]",
        json.dumps({"windows": None, "reason": "staged on linux"}),
        json.dumps({"windows": {"file": "missing.exe"}}),
        json.dumps({"windows": {"file": ""}}),
    ],
)
def test_a_deploy_with_no_client_says_so_rather_than_failing(
    tmp_path, monkeypatch, server, manifest
):
    root = tmp_path / "dist"
    root.mkdir()
    if manifest is not None:
        (root / "manifest.json").write_text(manifest, encoding="utf-8")
    monkeypatch.setenv("GAMES_DOWNLOAD_DIR", str(root))

    assert server.get("/download/info").json() == {"windows": None}
    assert server.get("/download/hAssault.exe").status_code == 404


@pytest.mark.parametrize(
    "name", ["../secret.txt", "..\\secret.txt", "sub/hAssault.exe", "..", "."]
)
def test_the_manifest_cannot_name_a_file_outside_the_directory(
    tmp_path, monkeypatch, name
):
    root = tmp_path / "dist"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "hAssault.exe").write_bytes(PAYLOAD)
    (tmp_path / "secret.txt").write_text("not for download", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"windows": {"file": name}}), encoding="utf-8"
    )
    monkeypatch.setenv("GAMES_DOWNLOAD_DIR", str(root))

    assert downloads.windows_build() is None


def test_a_file_that_changed_under_its_manifest_is_not_vouched_for(staged, server):
    """The checksum is the staging script's claim about the file it staged. A
    different file is still served — it is what is there — but with no checksum
    rather than one describing something else."""
    (staged / "hAssault.exe").write_bytes(PAYLOAD + b"tampered")

    info = server.get("/download/info").json()["windows"]
    assert info["sizeBytes"] == len(PAYLOAD) + len(b"tampered")
    assert info["sha256"] == ""
    res = server.get("/download/hAssault.exe")
    assert res.status_code == 200
    assert "x-checksum-sha256" not in res.headers
