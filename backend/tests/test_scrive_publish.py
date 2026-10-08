"""Scrive site publishing: themes, the two Pages modes, preflight, share cards.

GitHub is faked at the `github_pages` seam (resolve / push / ensure), which
`test_publishing_core.py` covers against a fake Git Data API. The rules under test
are the quiet ones: drafts never leave, the bytes that leave are what preflight read,
only our own files are deleted, absolute URLs are filled in at push time, and the
agent cannot mark its own draft published.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.modules.scrive import cards, publish, store, themes
from backend.publishing import github_pages
from backend.publishing.github_pages import PagesRepo

FAKE_TOKEN = "ghp_" + "b" * 36


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def scrive_root(tmp_path) -> Path:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "scrive"
    (data_dir / "settings.json").write_text(
        json.dumps({"scrive.root": str(root), "scrive.semanticSearch": False})
    )
    return root


@pytest.fixture
def client(scrive_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def site(client) -> str:
    assert client.post("/api/scrive/sites", json={"id": "blog"}).status_code == 200
    return "blog"


def write(site_id: str, rel: str, text: str) -> None:
    path = store.site_dir(site_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def post(status: str, body: str = "Body.\n") -> str:
    return f"---\ntitle: T\nstatus: {status}\n---\n\n{body}"


class FakePages:
    """`github_pages` as the publisher calls it; records what would be pushed."""

    def __init__(self, monkeypatch, target=PagesRepo("alice", "blog", "main")) -> None:
        self.target = target
        self.pushes: list[tuple[dict[str, bytes], list[str]]] = []
        self.build_types: list[str] = []
        monkeypatch.setattr(github_pages, "availability", lambda: (True, ""))

        async def resolve_repo(configured, **kw):
            self.configured = configured
            self.default_repo = kw["default_repo"]
            return self.target

        async def push_files(target, files, *, message, delete=()):
            self.pushes.append((dict(files), list(delete)))
            return f"c{len(self.pushes)}"

        async def ensure_pages(target, build_type="legacy"):
            self.build_types.append(build_type)

        monkeypatch.setattr(github_pages, "resolve_repo", resolve_repo)
        monkeypatch.setattr(github_pages, "push_files", push_files)
        monkeypatch.setattr(github_pages, "ensure_pages", ensure_pages)


# --- themes -------------------------------------------------------------------------------


def test_four_builtin_themes_with_tokens_and_a_guide() -> None:
    listed = {t.id: t for t in themes.list_themes()}
    assert list(listed) == ["minimal", "tactical", "book", "article"]
    for theme in listed.values():
        assert "--accent" in theme.tokens and "--font-body" in theme.tokens
        assert theme.guide.startswith("# ")
        assert theme.layout == theme.id


def test_a_site_theme_extends_a_builtin_with_its_own_tokens(site) -> None:
    write(
        site, "themes/brand/template.json", '{"name": "Brand", "extends": "tactical"}'
    )
    write(site, "themes/brand/tokens.css", ":root { --accent: #ff00aa; }")
    brand = themes.get_theme(site, "brand")
    assert brand.layout == "tactical" and brand.source == "site"
    # The built-in's tokens first, the override after, so the override wins.
    assert brand.tokens.index("--grid") < brand.tokens.index("#ff00aa")
    assert brand.guide == themes.get_theme(None, "tactical").guide
    assert "brand" in {t.id for t in themes.list_themes(site)}


def test_a_site_theme_ships_its_own_layouts(client, site) -> None:
    write(site, "themes/brand/template.json", '{"name": "Brand"}')
    write(site, "themes/brand/layouts/page.html", "<article>{{{content}}}</article>")
    write(site, "themes/brand/layouts/base.html", "<html>{{{body}}}</html>")
    # Only the four known names, and nothing oversized.
    write(site, "themes/brand/layouts/sidebar.html", "<aside></aside>")
    write(site, "themes/brand/layouts/home.html", "x" * (themes.MAX_TEMPLATE_BYTES + 1))
    brand = themes.get_theme(site, "brand")
    assert brand.templates == {
        "base": "<html>{{{body}}}</html>",
        "page": "<article>{{{content}}}</article>",
    }
    # The built-ins have none; the client keeps their React layouts.
    assert themes.get_theme(None, "minimal").templates == {}
    listed = client.get("/api/scrive/themes", params={"site": site}).json()
    assert next(t for t in listed if t["id"] == "brand")["templates"]["page"]


def test_a_theme_folder_is_never_listed_as_pages(site) -> None:
    write(site, "themes/brand/THEME.md", "# Brand\n")
    assert all(not p.path.startswith("themes/") for p in store.list_pages(site))


def test_an_unknown_theme_in_scrive_yml_falls_back_to_minimal(site) -> None:
    write(site, "scrive.yml", "version: 1\ntheme: nope\n")
    assert themes.site_theme(site).id == "minimal"


def test_config_route_sets_theme_and_target_and_keeps_unknown_keys(
    client, site
) -> None:
    write(site, "scrive.yml", "version: 1\ntitle: Blog\nmine: keep\n")
    res = client.put(
        f"/api/scrive/sites/{site}/config",
        json={
            "theme": "book",
            "pages": {
                "repo": "alice/notes/",
                "mode": "jupyter-book",
                "cname": "HTTPS://Blog.Example.com/",
            },
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["theme"] == "book"
    assert publish.pages_config(site) == publish.PagesConfig(
        repo="alice/notes", mode="jupyter-book", cname="blog.example.com"
    )
    assert "mine: keep" in (store.site_dir(site) / "scrive.yml").read_text()
    assert (
        client.put(
            f"/api/scrive/sites/{site}/config", json={"theme": "nope"}
        ).status_code
        == 400
    )
    bad = {"pages": {"cname": "not a domain"}}
    assert client.put(f"/api/scrive/sites/{site}/config", json=bad).status_code == 400


# --- which pages go ---------------------------------------------------------------------


def test_a_post_goes_out_only_once_published(site) -> None:
    base = store.site_dir(site)
    write(site, "posts/a.md", post("published"))
    write(site, "posts/b.md", post("draft"))
    write(site, "posts/c.md", "---\ntitle: C\n---\n")
    write(site, "pages/about.md", "---\ntitle: About\n---\n")
    write(site, "pages/wip.md", post("review"))
    public = {
        p.relative_to(base).as_posix()
        for p in base.rglob("*.md")
        if publish.is_public(store.page_meta(base, p))
    }
    assert public == {"posts/a.md", "pages/about.md", "index.md"}


# --- scene pages --------------------------------------------------------------------------


def test_a_scene_page_cannot_be_broken_out_of() -> None:
    page = publish.scene_page(
        "scenes/orbit.tsx", 'const s = "</script><script>alert(1)</script>";'
    )
    assert "</script><script>alert" not in page
    assert '<script src="../../../_scrive/runtime.js">' in page
    assert (
        publish.scene_page_path("scenes/orbit.tsx") == "_scrive/scene/scenes/orbit.html"
    )


# --- jupyter-book mode --------------------------------------------------------------------


def test_jupyter_book_sources_leave_drafts_and_state_behind(site) -> None:
    write(site, "posts/live.md", post("published"))
    write(site, "posts/draft.md", post("draft"))
    write(site, "templates/t.md", "---\ntitle: T\n---\n")
    write(site, ".scrive/cache/x.json", "{}")
    write(site, "scenes/orbit.tsx", "export default function S() { return null }")
    files, pages = publish.source_files(site, branch="main", base_url="/blog")
    assert "posts/live.md" in files and "posts/draft.md" not in files
    assert not any(p.startswith((".scrive/", "templates/")) for p in files)
    assert sorted(pages) == ["index.md", "posts/live.md"]
    assert "scrive-myst-plugin.mjs" in files[store.MYST_FILE].decode()
    assert files[publish.PLUGIN_FILE].startswith(b"/**")
    flow = files[publish.WORKFLOW_PATH].decode()
    assert 'BASE_URL: "/blog"' in flow and "jupyter book build --html" in flow
    assert "actions/deploy-pages" in flow
    assert publish.RUNTIME_PATH in files
    assert "_scrive/scene/scenes/orbit.html" in files
    # The person's own myst.yml is untouched.
    assert "plugins" not in (store.site_dir(site) / "myst.yml").read_text()


def test_workflow_installs_requirements_when_the_site_has_them() -> None:
    assert "-r requirements.txt" in publish.workflow("main", "", True)
    assert "-r requirements.txt" not in publish.workflow("main", "", False)


# --- preflight ----------------------------------------------------------------------------


def test_preflight_reads_the_files_that_leave(site) -> None:
    write(site, "posts/a.md", post("published", ":::{pending}\nWrite me.\n:::\n"))
    files = {
        "posts/a/index.html": f"<p>token {FAKE_TOKEN}</p>".encode(),
        "feed.xml": b"<rss>C:\\Users\\alice\\notes</rss>",
        publish.RUNTIME_PATH: f"var k='{FAKE_TOKEN}'".encode(),  # ours; not scanned
    }
    findings = publish.preflight(site, files, ["posts/a.md"])
    by_rule = {(f.rule, f.file) for f in findings}
    assert ("secret", "posts/a/index.html") in by_rule
    assert ("path", "feed.xml") in by_rule
    assert ("pending", "posts/a.md") in by_rule
    assert not any(f.file == publish.RUNTIME_PATH for f in findings)
    assert all(FAKE_TOKEN not in f.message for f in findings)
    assert [f.blocking for f in findings if f.rule == "secret"] == [True]


def test_preflight_names_a_webllm_model_readers_cannot_reach(site) -> None:
    body = (
        "```{webllm} gguf-node:C:\\models\\qwen3-sft-q8_0.gguf\n```\n\n"
        "```{webllm} gguf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf\n```\n"
    )
    write(site, "posts/a.md", post("published", body))
    files = {publish.WEBML_GGUF_WORKER_PATH: f"var k='{FAKE_TOKEN}'".encode()}
    findings = publish.preflight(site, files, ["posts/a.md"])
    node = [f for f in findings if f.rule == "node-model"]
    # The node file, by name, and only it: a Hub GGUF is reachable.
    assert [(f.file, f.blocking) for f in node] == [("posts/a.md", False)]
    assert "qwen3-sft-q8_0.gguf" in node[0].message
    # The bundled engine is ours, minified, and not scanned.
    assert not any(f.file == publish.WEBML_GGUF_WORKER_PATH for f in findings)


def test_an_empty_site_cannot_be_acknowledged_into_a_publish(site) -> None:
    findings = publish.preflight(site, {"index.html": b"x"}, [])
    assert publish._blocked(findings, acknowledged=True)


# --- static mode --------------------------------------------------------------------------


def bundle(**extra) -> dict:
    return {
        "files": {
            "index.html": "<a href='__SCRIVE_SITE_URL__/'>home</a>",
            "feed.xml": "<link>__SCRIVE_SITE_URL__/posts/a/</link>",
            "_scrive/site.css": "body{}",
        },
        "assets": ["media/pic.png"],
        "pages": ["index.md"],
        **extra,
    }


def test_static_publish_completes_and_pushes_the_site(
    client, site, monkeypatch
) -> None:
    write(site, "media/pic.png", "PNG")
    write(
        site,
        "scrive.yml",
        "version: 1\ntitle: Blog\ntargets:\n  pages:\n    cname: blog.example.com\n",
    )
    pages = FakePages(monkeypatch)
    monkeypatch.setattr(cards, "_chromium", lambda docs: {k: b"PNGCARD" for k in docs})
    body = bundle(cards=[{"path": "_scrive/og/index.png", "title": "Blog"}])
    res = client.post(f"/api/scrive/sites/{site}/publish", json={"bundle": body})
    out = res.json()
    assert res.status_code == 200 and out["published"], out
    ((files, deleted),) = pages.pushes
    assert files["media/pic.png"] == b"PNG"
    assert files["_scrive/og/index.png"] == b"PNGCARD"
    assert files["CNAME"] == b"blog.example.com\n"
    assert files["feed.xml"] == b"<link>https://blog.example.com/posts/a/</link>"
    assert b"__SCRIVE" not in files["index.html"]
    assert deleted == [] and pages.build_types == ["legacy"]
    assert pages.default_repo == site
    record = publish.read_record(site)
    assert (
        record and record.url == "https://blog.example.com/" and record.commit == "c1"
    )
    assert "CNAME" in record.files


def test_a_republish_deletes_only_what_the_last_one_wrote(
    client, site, monkeypatch
) -> None:
    pages = FakePages(monkeypatch)
    first = bundle(assets=[])
    first["files"]["old/index.html"] = "<p>gone</p>"
    client.post(f"/api/scrive/sites/{site}/publish", json={"bundle": first})
    client.post(f"/api/scrive/sites/{site}/publish", json={"bundle": bundle(assets=[])})
    assert pages.pushes[1][1] == ["old/index.html"]


def test_a_secret_blocks_until_acknowledged(client, site, monkeypatch) -> None:
    pages = FakePages(monkeypatch)
    leaky = bundle(assets=[])
    leaky["files"]["index.html"] = f"<code>{FAKE_TOKEN}</code>"
    out = client.post(
        f"/api/scrive/sites/{site}/publish", json={"bundle": leaky}
    ).json()
    assert out["published"] is False and pages.pushes == []
    assert any(f["blocking"] and f["severity"] == "secret" for f in out["findings"])
    out = client.post(
        f"/api/scrive/sites/{site}/publish",
        json={"bundle": leaky, "acknowledged": True},
    ).json()
    assert out["published"] is True and len(pages.pushes) == 1


def test_a_bundle_cannot_write_outside_the_site_or_into_git(
    client, site, monkeypatch
) -> None:
    FakePages(monkeypatch)
    for path in ("../escape.html", ".git/config", "a//b.html", "/abs.html"):
        body = bundle(assets=[])
        body["files"][path] = "x"
        res = client.post(f"/api/scrive/sites/{site}/publish", json={"bundle": body})
        assert res.status_code == 400, path
    body = bundle(assets=[".scrive/published.json"])
    res = client.post(f"/api/scrive/sites/{site}/publish", json={"bundle": body})
    assert res.status_code == 400


def test_a_missing_embed_is_reported_not_fatal(site) -> None:
    files, findings, _ = publish.static_files(
        site, publish.Bundle.model_validate(bundle()), themes.get_theme(None, "minimal")
    )
    assert "media/pic.png" not in files
    assert [f.rule for f in findings] == ["missing-asset"]


def test_a_missing_scene_is_reported(site) -> None:
    write(site, "scenes/orbit.tsx", "export default function S() { return null }")
    body = publish.Bundle.model_validate(
        bundle(assets=[], scenes=["scenes/orbit.tsx", "posts/scenes/orbit.tsx"])
    )
    _, findings, _ = publish.static_files(site, body, themes.get_theme(None, "minimal"))
    assert [(f.rule, f.file) for f in findings] == [
        ("missing-scene", "posts/scenes/orbit.tsx")
    ]


def test_jupyter_book_publish_deploys_through_a_workflow(
    client, site, monkeypatch
) -> None:
    write(
        site, "scrive.yml", "version: 1\ntargets:\n  pages:\n    mode: jupyter-book\n"
    )
    pages = FakePages(monkeypatch)
    out = client.post(f"/api/scrive/sites/{site}/publish", json={}).json()
    assert out["published"], out
    ((files, _),) = pages.pushes
    assert 'BASE_URL: "/blog"' in files[publish.WORKFLOW_PATH].decode()
    assert pages.build_types == ["workflow"]
    assert "Actions" in out["note"]


def test_publishing_needs_github(client, site) -> None:
    res = client.post(f"/api/scrive/sites/{site}/publish", json={"bundle": bundle()})
    assert res.status_code == 400
    state = client.get(f"/api/scrive/sites/{site}/publish").json()
    assert state["available"] is False and state["reason"]


# --- the local build ----------------------------------------------------------------------


def test_build_writes_a_previewable_site(client, site) -> None:
    write(site, "media/pic.png", "PNG")
    write(site, "scenes/orbit.tsx", "export default function S() { return null }")
    out = client.post(f"/api/scrive/sites/{site}/build", json=bundle()).json()
    assert Path(out["path"], "media", "pic.png").read_bytes() == b"PNG"
    res = client.get(f"/api/scrive/sites/{site}/built/")
    assert res.status_code == 200 and b"href='./'" in res.content
    assert res.headers["content-security-policy"].startswith("sandbox allow-scripts")
    scene = client.get(
        f"/api/scrive/sites/{site}/built/_scrive/scene/scenes/orbit.html"
    )
    assert scene.status_code == 200 and b"__SCRIVE_SCENE__" in scene.content
    assert client.get(
        f"/api/scrive/sites/{site}/built/..%2F..%2Fscrive.yml"
    ).status_code in (
        400,
        404,
    )
    assert (
        client.get(f"/api/scrive/sites/{site}/publish").json()["built"] == out["path"]
    )


# --- share cards ----------------------------------------------------------------------------


def test_card_values_are_escaped() -> None:
    doc = cards.card_html(
        "<h1>{{title}}</h1>{{tokens}}",
        ":root{}",
        "S",
        cards.Card(path="x.png", title="<b>x</b>"),
    )
    assert "<h1>&lt;b&gt;x&lt;/b&gt;</h1>:root{}" == doc


def test_cards_fall_back_to_pillow_without_chromium(tmp_path, monkeypatch) -> None:
    def no_chromium(docs):
        raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(cards, "_chromium", no_chromium)
    theme = themes.get_theme(None, "tactical")
    out = cards.render_cards(
        [cards.Card(path="og/a.png", title="A title", description="d", kicker="2026")],
        template=themes.og_template(None, theme),
        tokens=theme.tokens,
        site_title="Blog",
        cache_dir=tmp_path,
    )
    assert out.engine == "pillow" and "Chromium" in out.note
    assert out.files["og/a.png"].startswith(b"\x89PNG")
    assert list(tmp_path.iterdir()) == []  # a stand-in is never cached


def test_chromium_cards_are_cached_by_content(tmp_path, monkeypatch) -> None:
    calls = []

    def fake(docs):
        calls.append(len(docs))
        return {k: b"\x89PNG" for k in docs}

    monkeypatch.setattr(cards, "_chromium", fake)
    kw = dict(template="{{title}}", tokens="", site_title="S", cache_dir=tmp_path)
    one = [cards.Card(path="a.png", title="A")]
    assert cards.render_cards(one, **kw).engine == "chromium"
    assert cards.render_cards(one, **kw).engine == "cache"
    assert (
        cards.render_cards(one + [cards.Card(path="b.png", title="B")], **kw).engine
        == "chromium"
    )
    assert calls == [1, 1]


def test_a_real_card_renders_when_chromium_is_installed(tmp_path) -> None:
    theme = themes.get_theme(None, "minimal")
    doc = cards.card_html(
        themes.og_template(None, theme),
        theme.tokens,
        "Blog",
        cards.Card(path="a.png", title="Hi"),
    )
    try:
        png = cards._chromium({"k": doc})["k"]
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Chromium: {exc}")
    from PIL import Image
    import io

    assert Image.open(io.BytesIO(png)).size == (cards.WIDTH, cards.HEIGHT)


# --- the agent ------------------------------------------------------------------------------


def test_the_agent_cannot_change_a_pages_status(site) -> None:
    from backend.modules.scrive.agent_tools import register_agent_tools
    from backend.sdk.registry import registry

    register_agent_tools()
    write(site, "posts/a.md", post("draft"))
    page = store.read_page(site, "posts/a.md")
    edit = registry.agent_tools["scrive.editPage"].handler
    for op in (
        {"op": "setFrontmatter", "key": "status", "value": "published"},
        {"op": "replaceText", "find": "status: draft", "replace": "status: published"},
    ):
        out = run(
            edit(
                {
                    "site": site,
                    "path": "posts/a.md",
                    "base_revision": page.meta.revision,
                    "ops": [op],
                }
            )
        )
        assert "status" in out.get("error", ""), out
    assert store.read_page(site, "posts/a.md").content == page.content
    listed = run(registry.agent_tools["scrive.listPages"].handler({"site": site}))
    assert listed["theme"]["id"] == "minimal" and listed["theme"]["guide"]
