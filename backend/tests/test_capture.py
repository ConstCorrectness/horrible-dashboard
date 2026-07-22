"""Page capture: the pure two-pass inliner, and the no-engine capture service."""

from __future__ import annotations

import asyncio
import base64

import pytest

from backend.modules.research import service
from backend.modules.research.capture import (
    IMAGE_KIND,
    STYLESHEET_KIND,
    build_page,
    filename_for_title,
    list_css_urls,
    list_resources,
)

BASE = "https://example.com/post/index.html"

HTML = """
<html>
  <head>
    <title>Test Page</title>
    <base href="https://evil.example/">
    <meta http-equiv="refresh" content="0;url=https://evil.example/">
    <link rel="stylesheet" href="/styles/main.css">
    <script src="/app.js"></script>
  </head>
  <body onload="alert(1)">
    <img src="../img/photo.png" srcset="/img/photo@2x.png 2x" sizes="100vw">
    <img src="data:image/gif;base64,R0lGOD">
    <a href="javascript:alert(1)">click</a>
    <a href="/other">other</a>
    <script>alert(2)</script>
  </body>
</html>
"""


def test_list_resources_resolves_and_filters() -> None:
    plan = list_resources(HTML, BASE)
    assert plan["https://example.com/img/photo.png"] == IMAGE_KIND
    assert plan["https://example.com/styles/main.css"] == STYLESHEET_KIND
    # data: URIs and scripts are not fetchable subresources.
    assert all(not u.startswith("data:") for u in plan)
    assert not any(u.endswith("app.js") for u in plan)


def test_list_css_urls_resolves_against_stylesheet() -> None:
    css = "body { background: url('../img/bg.png'); font: url(\"font.woff2\"); }"
    urls = list_css_urls(css, "https://example.com/styles/main.css")
    assert "https://example.com/img/bg.png" in urls
    assert "https://example.com/styles/font.woff2" in urls


def test_build_page_inlines_and_sanitizes() -> None:
    png = b"\x89PNG fake bytes"
    css = "body { background: url('../img/bg.png'); }"
    bg = b"bg-bytes"
    resources = {
        "https://example.com/img/photo.png": (png, "image/png"),
        "https://example.com/styles/main.css": (css.encode(), "text/css"),
        "https://example.com/img/bg.png": (bg, "image/png"),
    }
    out = build_page(HTML, BASE, resources)

    assert out.startswith("<!-- saved from https://example.com/post/index.html")
    assert "<!DOCTYPE html>" in out
    # Image inlined; srcset dropped so the data URI wins.
    assert base64.b64encode(png).decode() in out
    assert "srcset" not in out
    # Stylesheet became a <style> with its own url() inlined.
    assert "<style>" in out
    assert base64.b64encode(bg).decode() in out
    # Active content stripped.
    assert "<script" not in out
    assert "onload" not in out
    assert "javascript:alert" not in out
    assert "http-equiv" not in out
    # The page's own <base> is replaced with one pointing at the true origin.
    assert 'href="https://evil.example/"' not in out
    assert f'<base href="{BASE}">' in out
    # Un-inlined links stay, absolute-resolvable via the fresh base.
    assert 'href="/other"' in out


def test_build_page_leaves_unfetched_as_absolute_urls() -> None:
    out = build_page(HTML, BASE, {})
    assert 'src="https://example.com/img/photo.png"' in out


def test_build_page_respects_caps() -> None:
    big = b"x" * 100
    resources = {"https://example.com/img/photo.png": (big, "image/png")}
    out = build_page(HTML, BASE, resources, per_resource_cap=10)
    assert base64.b64encode(big).decode() not in out
    assert 'src="https://example.com/img/photo.png"' in out

    out = build_page(HTML, BASE, resources, total_cap=10)
    assert base64.b64encode(big).decode() not in out


def test_keep_scripts_keeps_scripts() -> None:
    out = build_page(HTML, BASE, {}, keep_scripts=True)
    assert "<script" in out


def test_filename_for_title_windows_safe() -> None:
    assert filename_for_title('What: a "great" <post>? ') == "What a great post.html"
    assert filename_for_title("x" * 300).endswith(".html")
    assert len(filename_for_title("x" * 300)) <= 126
    assert filename_for_title("   ") == "capture.html"
    assert filename_for_title("paper", "pdf") == "paper.pdf"


def test_capture_url_service(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_html(url: str) -> tuple[str, str]:
        return url, HTML

    async def fake_fetch_typed(url: str, kind: str):
        if url.endswith("photo.png"):
            return url, (b"pixels", "image/png")
        return url, None

    monkeypatch.setattr(service, "safe_fetch_html", fake_fetch_html)
    monkeypatch.setattr(service, "_fetch_typed", fake_fetch_typed)

    result = asyncio.run(service.capture_url("https://example.com/post/index.html"))
    artifact = result["artifact"]
    source = result["source"]
    assert artifact["kind"] == "page"
    assert artifact["origin_url"] == "https://example.com/post/index.html"
    assert source["type"] == "page"
    assert source["artifact_id"] == artifact["id"]
    assert source["status"] == "queued"

    from backend.modules.artifacts.store import artifact_path

    stored = artifact_path(artifact["id"]).read_text(encoding="utf-8")
    assert base64.b64encode(b"pixels").decode() in stored
    assert "<script" not in stored


def test_single_file_cli_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service, "get_value", lambda key, default=None: "definitely-not-a-real-cli"
    )
    with pytest.raises(RuntimeError, match="no such executable"):
        asyncio.run(service.capture_url("https://example.com/x"))
