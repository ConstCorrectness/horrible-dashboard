"""The browser game's build is served so that a deploy shows at the next load.

`index.html` names the content-hashed bundles; served with no `Cache-Control`, a
browser cached it heuristically and kept loading the previous deploy's bundles.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.games_server.app import WebClientFiles


def client(tmp_path):
    (tmp_path / "index.html").write_text(
        "<script src='/assets/index-abc123.js'></script>"
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "hd_bank.glb").write_bytes(b"glTF")
    app = FastAPI()
    app.mount("/", WebClientFiles(directory=tmp_path, html=True), name="web")
    return TestClient(app)


def test_the_page_is_revalidated_on_every_load(tmp_path):
    c = client(tmp_path)
    for path in ("/", "/index.html"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache", path
        assert r.headers.get("etag"), "no-cache revalidates by the ETag"


def test_the_hashed_bundles_are_cached_for_good(tmp_path):
    r = client(tmp_path).get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_other_files_keep_the_ordinary_cache(tmp_path):
    # The maps are rebuilt under the same name: they must revalidate, not stick.
    r = client(tmp_path).get("/hd_bank.glb")
    assert r.status_code == 200
    assert "immutable" not in r.headers.get("cache-control", "")
