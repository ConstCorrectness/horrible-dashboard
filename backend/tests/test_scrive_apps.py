"""Scrive web apps: their own origin (`scrive-apps.localhost`), the gate that keeps
that origin and the API apart, templates, Space import, and publishing."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.modules.scrive import apps, publish, store, watcher
from backend.publishing.errors import PublishError

APPS = {"host": "scrive-apps.localhost:8100"}


@pytest.fixture
def client(tmp_path) -> TestClient:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    (data_dir / "settings.json").write_text(
        json.dumps(
            {"scrive.root": str(tmp_path / "scrive"), "scrive.semanticSearch": False}
        )
    )
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def site(client) -> str:
    assert client.post("/api/scrive/sites", json={"id": "blog"}).status_code == 200
    return "blog"


def write(rel: str, data: str | bytes) -> Path:
    path = store.site_dir("blog") / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


# --- the gate ---------------------------------------------------------------------


def test_the_apps_host_serves_app_files_and_nothing_else(client, site) -> None:
    write(
        "apps/demo/index.html",
        "<!doctype html><html><head><title>Demo</title></head><body>hi</body></html>",
    )
    write("apps/demo/js/main.mjs", "export const x = 1;")

    res = client.get("/blog/demo/", headers=APPS)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    # The console shim runs first, inside <head>.
    assert res.text.index("scriveApp:1") < res.text.index("<title>")
    assert res.headers["cache-control"] == "no-cache"

    assert (
        client.get("/blog/demo/js/main.mjs", headers=APPS)
        .headers["content-type"]
        .startswith("text/javascript")
    )
    # Relative URLs need the folder's slash.
    redirect = client.get("/blog/demo", headers=APPS, follow_redirects=False)
    assert redirect.status_code == 308 and redirect.headers["location"] == "/blog/demo/"

    # On the apps host, an API path is just a missing app file.
    assert client.get("/api/health", headers=APPS).status_code == 404
    assert client.get("/api/scrive/sites", headers=APPS).status_code == 404
    # Nothing outside the app's own folder, however it is spelled.
    write("secret.txt", "no")
    for path in (
        "/blog/demo/../../secret.txt",
        "/blog/demo/%2e%2e/%2e%2e/secret.txt",
        "/blog/../scrive.yml",
    ):
        assert client.get(path, headers=APPS).status_code == 404, path
    assert client.post("/blog/demo/", headers=APPS).status_code == 405


def test_requests_from_the_apps_origin_reach_no_api(client, site) -> None:
    assert client.get("/api/health").status_code == 200
    assert (
        client.get(
            "/api/health", headers={"origin": "http://scrive-apps.localhost:8100"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/scrive/sites",
            json={"id": "evil"},
            headers={"origin": "http://scrive-apps.localhost:8100"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/scrive/sites",
            headers={"referer": "http://scrive-apps.localhost:8100/blog/x/"},
        ).status_code
        == 403
    )
    # The app UI's own origin is unaffected.
    assert (
        client.get(
            "/api/health", headers={"origin": "http://localhost:5173"}
        ).status_code
        == 200
    )


def test_the_apps_origin_cannot_open_the_socket(client) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws", headers={"origin": "http://scrive-apps.localhost:8100"}
        ) as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=APPS) as ws:
            ws.receive_json()


def test_apps_origin_only_for_a_local_caller() -> None:
    assert (
        apps.apps_origin(("127.0.0.1", 8100), "127.0.0.1")
        == "http://scrive-apps.localhost:8100"
    )
    assert (
        apps.apps_origin(("0.0.0.0", 8100), "::1")
        == "http://scrive-apps.localhost:8100"
    )
    assert apps.apps_origin(("0.0.0.0", 8100), "203.0.113.9") is None
    assert apps.apps_origin(None, "127.0.0.1") is None


def test_shim_lands_even_without_a_head() -> None:
    assert apps.with_console_shim(b"<p>x</p>").startswith(b"<script>")
    assert apps.with_console_shim(b"<html lang=en><body>").startswith(
        b"<html lang=en><script>"
    )


# --- making apps --------------------------------------------------------------------


def test_create_list_and_write(client, site) -> None:
    templates = client.get("/api/scrive/app-templates").json()
    assert {"blank", "transformers-chat", "webgpu-compute"} <= set(templates)

    made = client.post(
        "/api/scrive/sites/blog/apps",
        json={"name": "kernel", "template": "webgpu-compute"},
    )
    assert made.status_code == 200
    assert made.json() | {"bytes": 0} == {
        "name": "kernel",
        "title": "WebGPU kernel",
        "hasIndex": True,
        "files": 1,
        "bytes": 0,
        "source": None,
        "skipped": [],
    }
    assert (
        client.post("/api/scrive/sites/blog/apps", json={"name": "kernel"}).status_code
        == 409
    )
    assert (
        client.post("/api/scrive/sites/blog/apps", json={"name": "../x"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/scrive/sites/blog/apps", json={"name": "y", "template": "nope"}
        ).status_code
        == 400
    )
    assert [a["name"] for a in client.get("/api/scrive/sites/blog/apps").json()] == [
        "kernel"
    ]

    assert (
        apps.write_app_file("blog", "kernel", "js/extra.js", "1;")
        == "apps/kernel/js/extra.js"
    )
    with pytest.raises(FileExistsError):
        apps.write_app_file("blog", "kernel", "js/extra.js", "2;")
    for bad in ("../../scrive.yml", "SPACE.json", ""):
        with pytest.raises(apps.AppError):
            apps.write_app_file("blog", "kernel", bad, "x")


def test_app_files_are_not_pages(client, site) -> None:
    write("apps/agate/README.md", "---\ntitle: Agate\n---\n\nA Space's readme.\n")
    write("posts/a.md", "# A\n")
    listed = [p["path"] for p in client.get("/api/scrive/sites/blog/pages").json()]
    assert "posts/a.md" in listed and not any(p.startswith("apps/") for p in listed)


# --- importing a Space ----------------------------------------------------------------


class FakeHub:
    def __init__(self, sdk: str = "static") -> None:
        self.sdk = sdk
        self.fetched: list[str] = []

    async def get(self, url: str, params: Any = None, follow_redirects: bool = False):
        self.fetched.append(url)
        import httpx

        req = httpx.Request("GET", url)
        if url.endswith("/api/spaces/Logolabs/agate-webgpu"):
            return httpx.Response(
                200,
                request=req,
                json={
                    "id": "Logolabs/agate-webgpu",
                    "sdk": self.sdk,
                    "sha": "abc123",
                    "cardData": {"license": "apache-2.0", "title": "Agate WebGPU"},
                },
            )
        if "/tree/abc123" in url:
            return httpx.Response(
                200,
                request=req,
                json=[
                    {"type": "file", "path": ".gitattributes", "size": 10},
                    {"type": "file", "path": "index.html", "size": 30},
                    {"type": "directory", "path": "js"},
                    {"type": "file", "path": "js/app.js", "size": 5},
                    {
                        "type": "file",
                        "path": "models/flow.onnx",
                        "size": 1000,
                        "lfs": {"size": 530_000_000},
                    },
                    {
                        "type": "file",
                        "path": "assets/huge.png",
                        "size": 1,
                        "lfs": {"size": 40 * 1024 * 1024},
                    },
                    {"type": "file", "path": "../escape.js", "size": 1},
                ],
            )
        if "/resolve/abc123/" in url:
            name = url.rsplit("/resolve/abc123/", 1)[1]
            return httpx.Response(200, request=req, content=f"<!-- {name} -->".encode())
        return httpx.Response(404, request=req)


def test_import_copies_a_static_space_and_skips_weights(client, site) -> None:
    hub = FakeHub()
    result = asyncio.run(
        apps.import_space(
            hub, "blog", "https://huggingface.co/spaces/Logolabs/agate-webgpu"
        )
    )
    assert result["name"] == "agate-webgpu" and result["hasIndex"]
    folder = apps.app_dir("blog", "agate-webgpu")
    assert sorted(
        p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()
    ) == [
        "SPACE.json",
        "index.html",
        "js/app.js",
    ]
    record = json.loads((folder / "SPACE.json").read_text())
    assert record["space"] == "Logolabs/agate-webgpu" and record["revision"] == "abc123"
    assert record["license"] == "apache-2.0"
    assert {s["path"] for s in record["skipped"]} == {
        "models/flow.onnx",
        "assets/huge.png",
    }
    assert not any("escape" in u for u in hub.fetched)
    with pytest.raises(FileExistsError):
        asyncio.run(apps.import_space(FakeHub(), "blog", "Logolabs/agate-webgpu"))


def test_import_refuses_a_space_that_needs_a_server(client, site) -> None:
    with pytest.raises(apps.AppError, match="gradio Space"):
        asyncio.run(
            apps.import_space(
                FakeHub(sdk="gradio"), "blog", "Logolabs/agate-webgpu", "g"
            )
        )
    assert not apps.app_dir("blog", "g").exists()


# --- publishing ----------------------------------------------------------------------


def test_apps_publish_verbatim_under_support(client, site) -> None:
    write("apps/demo/index.html", "<html><head></head><body>x</body></html>")
    write("apps/demo/SPACE.json", "{}")
    write("apps/demo/js/a.js", "1;")
    files = publish._app_files(store.site_dir("blog"))
    assert files == {
        "_scrive/apps/demo/index.html": b"<html><head></head><body>x</body></html>",
        "_scrive/apps/demo/js/a.js": b"1;",
    }
    assert b"scriveApp" not in files["_scrive/apps/demo/index.html"]


def test_an_embedded_app_that_is_missing_is_a_finding(client, site) -> None:
    from backend.modules.scrive import themes

    write("apps/demo/index.html", "<html></html>")
    theme = themes.get_theme(None, "minimal")
    bundle = publish.Bundle(
        files={"index.html": "<html></html>"}, apps=["demo", "gone"]
    )
    files, findings, _ = publish.static_files("blog", bundle, theme)
    assert "_scrive/apps/demo/index.html" in files
    assert [f.file for f in findings if f.rule == "missing-app"] == ["apps/gone"]


def test_watcher_names_the_app_a_change_belongs_to(tmp_path) -> None:
    base = tmp_path
    assert watcher.app_for(base, base / "blog" / "apps" / "demo" / "js" / "a.js") == (
        "blog",
        "demo",
    )
    assert watcher.app_for(base, base / "blog" / "apps" / "demo") is None
    assert watcher.app_for(base, base / "blog" / "posts" / "a.md") is None


def test_a_skipped_file_redirects_to_the_hub(client, site) -> None:
    write("apps/agate/index.html", "<html></html>")
    write(
        "apps/agate/SPACE.json",
        json.dumps(
            {
                "space": "Logolabs/agate-webgpu",
                "revision": "abc",
                "skipped": [{"path": "models/flow.onnx", "size": 1, "reason": "weights"}],
            }
        ),
    )
    res = client.get("/blog/agate/models/flow.onnx", headers=APPS, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == (
        "https://huggingface.co/spaces/Logolabs/agate-webgpu/resolve/abc/models/flow.onnx"
    )
    # Anything the record does not list is simply missing.
    assert client.get("/blog/agate/models/other.onnx", headers=APPS).status_code == 404


# --- {webllm} / {tokenviz} ---------------------------------------------------------


def test_the_model_page_ships_only_with_a_webllm(client, site) -> None:
    write("posts/a.md", "# A\n\nNo model here.\n")
    assert publish.WEBML_EMBED_PATH not in publish._support_files(store.site_dir("blog"))
    write("posts/b.md", "```{webllm} onnx-community/Qwen3-0.6B-ONNX\n```\n")
    files = publish._support_files(store.site_dir("blog"))
    page = files[publish.WEBML_EMBED_PATH].decode("utf-8")
    assert "@huggingface/transformers@4.3.0" in page
    # Nothing downloads until the reader clicks.
    assert 'id="load"' in page


def test_a_gguf_model_ships_the_engine_beside_the_page(client, site, tmp_path, monkeypatch) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "webml-embed.html").write_bytes((publish.STATIC / "webml-embed.html").read_bytes())
    monkeypatch.setattr(publish, "STATIC", static)
    base = store.site_dir("blog")

    # An ONNX model needs only the page.
    write("posts/a.md", "```{webllm} onnx-community/Qwen3-0.6B-ONNX\n```\n")
    assert publish.WEBML_GGUF_WORKER_PATH not in publish._support_files(base)

    # A Hub GGUF needs the engine, and says how to build it when it is missing.
    write("posts/b.md", "```{webllm} gguf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf\n```\n")
    assert publish._webllm_models(base) == {
        "posts/a.md": ["onnx-community/Qwen3-0.6B-ONNX"],
        "posts/b.md": ["gguf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf"],
    }
    with pytest.raises(PublishError, match="build:embed"):
        publish._support_files(base)
    (static / publish.WEBML_GGUF_WORKER).write_bytes(b"self.onmessage = () => {};")
    files = publish._support_files(base)
    assert files[publish.WEBML_GGUF_WORKER_PATH] == b"self.onmessage = () => {};"
    # In the page's folder: the page starts it by a relative URL.
    assert publish.WEBML_GGUF_WORKER_PATH.rsplit("/", 1)[0] == (
        publish.WEBML_EMBED_PATH.rsplit("/", 1)[0]
    )
    page = files[publish.WEBML_EMBED_PATH].decode("utf-8")
    assert "new URL('./gguf.worker.js', import.meta.url)" in page


def test_models_and_figures_cross_post_as_links() -> None:
    from backend.modules.scrive import crosspost

    converted = crosspost.to_markdown(
        "```{webllm} a/b\n```\n\n```{tokenviz} data/run.json\n```\n",
        page="posts/a.md",
        flavor="devto",
    )
    assert "[Try the model: open it on the site]({{post.url}})" in converted.markdown
    assert "[Token-probability figure: open it on the site]({{post.url}})" in converted.markdown
    assert crosspost.leftover_myst(converted.markdown) == []
