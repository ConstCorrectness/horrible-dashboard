"""Scrive sites and pages: files on disk are the source of truth.

The rules under test are the ones that are quiet if wrong: bytes round-trip exactly
(no CRLF translation), a stale save is a 409 carrying the current page, paths cannot
escape the site, and delete is a move into the site's trash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.modules.scrive import store, watcher


@pytest.fixture
def scrive_root(tmp_path) -> Path:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "scrive"
    (data_dir / "settings.json").write_text(json.dumps({"scrive.root": str(root)}))
    return root


@pytest.fixture
def client(scrive_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def site(client) -> str:
    res = client.post("/api/scrive/sites", json={"id": "blog", "title": "My: Blog"})
    assert res.status_code == 200, res.text
    return "blog"


def test_a_new_site_is_a_myst_project(client, scrive_root, site) -> None:
    base = scrive_root / site
    for name in ("myst.yml", "scrive.yml", "index.md", ".gitignore"):
        assert (base / name).is_file(), name
    assert "pattern: posts/*.md" in (base / "myst.yml").read_text()
    # A colon in the title is quoted, not a YAML mapping.
    detail = client.get(f"/api/scrive/sites/{site}").json()
    assert detail["site"]["title"] == "My: Blog"
    assert detail["config"]["version"] == 1
    assert [s["id"] for s in client.get("/api/scrive/sites").json()] == ["blog"]


def test_creating_a_site_twice_is_409(client, site) -> None:
    assert client.post("/api/scrive/sites", json={"id": site}).status_code == 409


def test_a_bad_site_id_is_refused(client) -> None:
    assert client.post("/api/scrive/sites", json={"id": "../x"}).status_code == 422
    assert client.get("/api/scrive/sites/NOPE/pages").status_code == 400


def test_new_post_is_dated_draft_and_listed_from_frontmatter(client, site) -> None:
    page = client.post(
        f"/api/scrive/sites/{site}/pages",
        json={"kind": "post", "title": "Hello, World"},
    ).json()
    assert page["meta"]["path"].startswith("posts/")
    assert page["meta"]["path"].endswith("-hello-world.md")
    assert page["meta"]["status"] == "draft"
    assert page["meta"]["kind"] == "post"
    listed = client.get(f"/api/scrive/sites/{site}/pages").json()
    assert listed[0]["title"] == "Hello, World"  # posts first
    assert {m["path"] for m in listed} >= {"index.md", page["meta"]["path"]}


def test_same_title_twice_gets_a_new_file(client, site) -> None:
    a = client.post(f"/api/scrive/sites/{site}/pages", json={"title": "Same"}).json()
    b = client.post(f"/api/scrive/sites/{site}/pages", json={"title": "Same"}).json()
    assert a["meta"]["path"] != b["meta"]["path"]


def test_save_round_trips_bytes_exactly(client, scrive_root, site) -> None:
    body = "---\r\ntitle: CRLF\r\n---\r\n\r\n# CRLF\r\n\r\nMath $x^2$ and ünïcode\r\n"
    page = client.get(
        f"/api/scrive/sites/{site}/page", params={"path": "index.md"}
    ).json()
    saved = client.put(
        f"/api/scrive/sites/{site}/page",
        params={"path": "index.md"},
        json={"content": body, "base_revision": page["meta"]["revision"]},
    ).json()
    assert (scrive_root / site / "index.md").read_bytes() == body.encode("utf-8")
    assert saved["content"] == body
    assert saved["meta"]["title"] == "CRLF"
    assert saved["meta"]["revision"] == store.revision_of(body.encode("utf-8"))


def test_a_stale_save_is_409_with_the_current_page(client, scrive_root, site) -> None:
    page = client.get(
        f"/api/scrive/sites/{site}/page", params={"path": "index.md"}
    ).json()
    (scrive_root / site / "index.md").write_bytes(b"# Edited elsewhere\n")
    res = client.put(
        f"/api/scrive/sites/{site}/page",
        params={"path": "index.md"},
        json={"content": "# Mine\n", "base_revision": page["meta"]["revision"]},
    )
    assert res.status_code == 409
    assert res.json()["current"]["content"] == "# Edited elsewhere\n"
    assert (scrive_root / site / "index.md").read_bytes() == b"# Edited elsewhere\n"


@pytest.mark.parametrize(
    "path", ["../escape.md", "../../x.md", "notes.txt", ".scrive/trash/x.md", "."]
)
def test_paths_cannot_escape_or_name_non_pages(client, site, path) -> None:
    res = client.get(f"/api/scrive/sites/{site}/page", params={"path": path})
    assert res.status_code == 400


def test_delete_moves_to_trash(client, scrive_root, site) -> None:
    page = client.post(
        f"/api/scrive/sites/{site}/pages", json={"title": "Doomed"}
    ).json()
    path = page["meta"]["path"]
    assert client.delete(f"/api/scrive/sites/{site}/page", params={"path": path}).json()
    assert not (scrive_root / site / path).exists()
    trashed = list((scrive_root / site / ".scrive" / "trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.endswith("doomed.md")
    assert (
        client.get(f"/api/scrive/sites/{site}/page", params={"path": path}).status_code
        == 404
    )


def test_malformed_frontmatter_does_not_hide_the_site(
    client, scrive_root, site
) -> None:
    (scrive_root / site / "pages" / "bad.md").write_bytes(
        b"---\ntitle: [unclosed\n---\n# Fallback\n"
    )
    listed = {
        m["path"]: m for m in client.get(f"/api/scrive/sites/{site}/pages").json()
    }
    assert listed["pages/bad.md"]["title"] == "Fallback"


def test_watcher_events_name_the_page_and_ignore_everything_else(
    scrive_root, site
) -> None:
    base = scrive_root
    page = base / site / "posts" / "a.md"
    page.write_bytes(b"# A\n")
    event = watcher.event_for(base, page, "modified")
    assert event is not None
    assert (event.site, event.path) == (site, "posts/a.md")
    assert event.revision == store.revision_of(b"# A\n")
    assert (
        watcher.event_for(base, base / site / ".scrive" / "trash" / "x.md", "added")
        is None
    )
    assert watcher.event_for(base, base / site / "media" / "x.png", "added") is None
    assert watcher.event_for(base, base / site / "scrive.yml", "modified") is None
    gone = watcher.event_for(base, base / site / "posts" / "gone.md", "deleted")
    assert gone is not None and gone.revision == ""


def test_an_atomic_replace_is_a_modification_not_a_deletion(scrive_root, site) -> None:
    """`os.replace` reaches watchfiles on Windows as deleted + added for one path.
    Judged by the event alone, every save told open editors their page was gone."""
    page = scrive_root / site / "posts" / "b.md"
    store.write_bytes_atomic(page, b"# B\n")
    event = watcher.event_for(scrive_root, page, "deleted")
    assert event is not None
    assert event.change == "modified"
    assert event.revision == store.revision_of(b"# B\n")
    missing = watcher.event_for(
        scrive_root, scrive_root / site / "posts" / "x.md", "added"
    )
    assert missing is not None and missing.change == "deleted"


def test_assets_are_served_and_escape_guarded(client, scrive_root, site) -> None:
    (scrive_root / site / "media" / "dot.png").write_bytes(b"PNG-ish bytes")
    res = client.get(f"/api/scrive/sites/{site}/asset", params={"path": "media/dot.png"})
    assert res.status_code == 200
    assert res.content == b"PNG-ish bytes"
    assert res.headers["content-type"] == "image/png"
    # A leading slash means "from the site root", as in MyST.
    assert client.get(f"/api/scrive/sites/{site}/asset", params={"path": "/media/dot.png"}).status_code == 200
    for bad in ("../x.png", ".scrive/trash/a.md", ".git/config"):
        assert client.get(f"/api/scrive/sites/{site}/asset", params={"path": bad}).status_code == 400
    assert client.get(f"/api/scrive/sites/{site}/asset", params={"path": "media/nope.png"}).status_code == 404
    # Opened directly, an asset cannot run script on the app's origin.
    assert res.headers["content-security-policy"] == "sandbox"
    assert res.headers["x-content-type-options"] == "nosniff"


def test_asset_upload_never_overwrites(client, scrive_root, site) -> None:
    def upload(name: str, data: bytes, **form):
        return client.post(
            f"/api/scrive/sites/{site}/assets",
            files={"file": (name, data)},
            data=form,
        )

    first = upload("My Figure!.PNG", b"one")
    assert first.status_code == 200
    assert first.json() == {"path": "media/My-Figure.png"}
    second = upload("My Figure!.PNG", b"two")
    assert second.json() == {"path": "media/My-Figure-2.png"}
    assert (scrive_root / site / "media" / "My-Figure.png").read_bytes() == b"one"
    assert (scrive_root / site / "media" / "My-Figure-2.png").read_bytes() == b"two"

    assert upload("clip.mp4", b"v", folder="posts/media").json() == {"path": "posts/media/clip.mp4"}
    # Refused: unknown types, escapes, Scrive's own state. Nothing is left behind.
    assert upload("run.exe", b"x").status_code == 400
    assert upload("a.png", b"x", folder="../out").status_code == 400
    assert upload("a.png", b"x", folder=".scrive").status_code == 400
    leftovers = [p for p in (scrive_root / site).rglob("*.scrive-tmp")]
    assert leftovers == []


def test_code_cell_outputs_live_in_a_shadow_notebook(client, scrive_root, site) -> None:
    from backend.modules.scrive import kernel
    from backend.notebook_core import notebooks as nb_core

    page = client.post(f"/api/scrive/sites/{site}/pages", json={"kind": "post", "title": "Run"})
    rel = page.json()["meta"]["path"]
    # Nothing has run: no cache, and reading it starts no kernel.
    res = client.get(f"/api/scrive/sites/{site}/cells", params={"path": rel})
    assert res.status_code == 200 and res.json() == {"cells": []}

    shadow = kernel.shadow_path(site, rel)
    assert shadow == (scrive_root / site / ".scrive" / "cache" / "cells" / f"{rel}.ipynb").resolve()
    nb_core.new_notebook(shadow, [{"cell_type": "code", "source": "1 + 1"}])
    nb = nb_core.load(shadow)
    nb.cells[0]["id"] = kernel.cell_id("1 + 1", 0)
    import nbformat

    nb.cells[0]["outputs"] = [
        nbformat.from_dict({"output_type": "execute_result", "data": {"text/plain": "2"}, "metadata": {}, "execution_count": 1})
    ]
    nb_core.save(shadow, nb)
    cells = client.get(f"/api/scrive/sites/{site}/cells", params={"path": rel}).json()["cells"]
    assert cells[0]["id"] == kernel.cell_id("1 + 1", 0)
    assert cells[0]["outputs"][0]["data"]["text/plain"] == "2"

    # The cache is reached through the page, so it is escape-guarded like one.
    assert client.get(f"/api/scrive/sites/{site}/cells", params={"path": "../x.md"}).status_code == 400


def test_kernel_sessions_are_keyed_by_page_and_refuse_non_pages(site) -> None:
    from backend.modules.scrive.kernel import cell_id, scrive_kernels

    assert scrive_kernels._session_key({"site": site, "path": "index.md"}) == f"scrive:{site}/index.md"
    for bad in ({"site": site}, {"site": site, "path": "../escape.md"}, {"site": site, "path": "x.ipynb"}):
        with pytest.raises(Exception):
            scrive_kernels._session_key(bad)
    # Ids are nbformat-legal and differ by occurrence of the same source.
    assert cell_id("x", 0) != cell_id("x", 1)
    assert all(c.isalnum() or c in "-_" for c in cell_id("print('hi')", 3))
