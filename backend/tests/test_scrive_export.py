"""Exporting a page as a PDF.

The print engine runs for real when Chromium is installed (it is skipped otherwise):
the page is served from memory to headless Chromium, which may load the site's own
files and nothing else. The typst engine is exercised against a stand-in `myst` on
PATH, which checks what Scrive hands it — a copy of the page with one `exports:`
entry, beside copies of the files it embeds — and writes a PDF where that entry says.
The person's own page is never touched.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.modules.scrive import exports, store
from backend.modules.scrive.publish import Bundle

PAGE = (
    "---\ntitle: Draft notes\nstatus: draft\n---\n\n## One\n\n![plot](../media/p.png)\n"
)

# A stand-in for mystmd: reads the page it is asked to build, checks the export
# Scrive wrote into its frontmatter, and writes a "PDF" where that export says.
FAKE_MYST = r"""
import pathlib, sys, yaml
page = pathlib.Path(sys.argv[2])
text = page.read_text(encoding="utf-8")
front = yaml.safe_load(text.split("---")[1])
spec = front["exports"][0]
assert spec["format"] == "typst", spec
assert pathlib.Path("media/p.png").is_file(), "the embedded image was not copied"
out = (page.parent / spec["output"]).resolve()
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(b"%PDF-1.7 typst " + front["title"].encode())
print("warning: a thing myst warns about")
"""


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
    base = store.site_dir("blog")
    (base / "posts").mkdir(exist_ok=True)
    (base / "posts" / "a.md").write_text(PAGE, encoding="utf-8")
    (base / "media").mkdir(exist_ok=True)
    # A 1x1 PNG.
    (base / "media" / "p.png").write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db40000000049454e44ae426082"
        )
    )
    return "blog"


def print_bundle() -> Bundle:
    return Bundle(
        files={
            "posts/a/index.html": (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<link rel='stylesheet' href='../../_scrive/site.css'>"
                "<link rel='stylesheet' href='https://example.com/tracker.css'></head>"
                "<body class='site'><header class='site-header'>chrome</header>"
                "<main><h2>One</h2><img src='../../media/p.png' alt='plot'></main>"
                "</body></html>"
            ),
            "_scrive/site.css": "@media print { .site-header { display: none } }",
        },
        assets=["media/p.png"],
        pages=["posts/a.md"],
    )


def test_paths() -> None:
    assert exports.output_path("posts/2026-a.md") == "_build/exports/posts/2026-a.pdf"
    assert exports.output_path("index.ipynb") == "_build/exports/index.pdf"


def test_the_export_folder_is_the_only_one_served(site) -> None:
    with pytest.raises(store.StoreError):
        exports.export_file(site, "posts/a.md")
    with pytest.raises(store.StoreError):
        exports.export_file(site, "_build/exports/../../posts/a.md")
    with pytest.raises(FileNotFoundError):
        exports.export_file(site, "_build/exports/nope.pdf")


def test_the_copy_gets_one_export_and_keeps_its_body() -> None:
    text = "---\ntitle: T\nexports:\n  - format: docx\n---\n\nBody $x$.\n"
    out = exports._with_export(text, "../_export/page.pdf", "lapreprint-typst")
    assert out.endswith("\nBody $x$.\n")
    import yaml

    front = yaml.safe_load(out.split("---")[1])
    assert front["title"] == "T"
    assert front["exports"] == [
        {
            "format": "typst",
            "output": "../_export/page.pdf",
            "template": "lapreprint-typst",
        }
    ]


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).is_file()
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _chromium_installed(), reason="Chromium is not installed")
def test_print_makes_a_pdf_from_the_site_files_only(client, site, monkeypatch) -> None:
    seen: list[str] = []
    real = exports._chromium_pdf

    def spy(files, entry, paper, scenes):
        seen.extend(sorted(files))
        return real(files, entry, paper, scenes)

    monkeypatch.setattr(exports, "_chromium_pdf", spy)
    res = client.post(
        f"/api/scrive/sites/{site}/export",
        json={
            "page": "posts/a.md",
            "engine": "print",
            "bundle": print_bundle().model_dump(),
        },
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["engine"] == "print" and out["path"] == "_build/exports/posts/a.pdf"
    assert "media/p.png" in seen and "posts/a/index.html" in seen
    pdf = client.get(
        f"/api/scrive/sites/{site}/export-file", params={"path": out["path"]}
    )
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"
    # The draft's source is untouched.
    assert (store.site_dir(site) / "posts" / "a.md").read_text(encoding="utf-8") == PAGE


SCENE = """export default function Box() {
  return (<mesh rotation={[0.4, 0.6, 0]}><boxGeometry args={[2, 2, 2]} /><meshNormalMaterial /></mesh>);
}
"""


@pytest.mark.skipif(
    not _chromium_installed()
    or not (Path(exports.publish.STATIC) / "scrive-runtime.js").is_file(),
    reason="needs Chromium and the built scene runtime",
)
def test_print_photographs_a_scene_and_links_a_missing_one(
    client, site, monkeypatch
) -> None:
    base = store.site_dir(site)
    (base / "scenes").mkdir(exist_ok=True)
    (base / "scenes" / "box.tsx").write_text(SCENE, encoding="utf-8")
    frame = (
        "<figure class='scrive-scene'><iframe title='3D scene {0}' sandbox='allow-scripts' "
        "loading='lazy' src='../../_scrive/scene/{0}.html' style='height:300px'></iframe></figure>"
    )
    bundle = Bundle(
        files={
            "posts/a/index.html": (
                "<!doctype html><html><body class='site'><main>"
                "<div style='height:2400px'>below the fold</div>"
                + frame.format("scenes/box")
                + frame.format("posts/scenes/gone")
                + "</main></body></html>"
            ),
            "_scrive/site.css": "",
        },
        scenes=["scenes/box.tsx", "posts/scenes/gone.tsx"],
        pages=["posts/a.md"],
    )
    swapped: list[str] = []
    real = exports._freeze_frames

    def spy(page, files):
        real(page, files)
        swapped.extend(
            page.eval_on_selector_all(
                "main img, main p a",
                "els => els.map(e => e.tagName + ':' + (e.alt || e.textContent))",
            )
        )

    monkeypatch.setattr(exports, "_freeze_frames", spy)
    out = exports.export(
        site, exports.ExportRequest(page="posts/a.md", engine="print", bundle=bundle)
    )
    # The scene that exists became its picture; the one that does not, a link — and a
    # finding names it.
    assert swapped == ["IMG:3D scene scenes/box", "A:3D scene posts/scenes/gone"]
    assert any(f.rule == "missing-scene" and "gone" in f.file for f in out.findings)


def test_print_without_a_build_is_refused(client, site) -> None:
    res = client.post(
        f"/api/scrive/sites/{site}/export",
        json={"page": "posts/a.md", "engine": "print"},
    )
    assert res.status_code == 422 and "print build" in res.json()["detail"]


def _fake_tools(bin_dir: Path) -> None:
    bin_dir.mkdir()
    script = bin_dir / "fake_myst.py"
    script.write_text(FAKE_MYST, encoding="utf-8")
    if sys.platform == "win32":
        (bin_dir / "myst.cmd").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
        (bin_dir / "typst.cmd").write_text("@exit /b 0\r\n", encoding="utf-8")
    else:
        for name, body in (
            ("myst", f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n'),
            ("typst", "#!/bin/sh\nexit 0\n"),
        ):
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_typst_builds_a_copy_beside_its_files(
    client, site, tmp_path, monkeypatch
) -> None:
    _fake_tools(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    assert exports.status().auto == "typst"
    res = client.post(
        f"/api/scrive/sites/{site}/export",
        json={"page": "posts/a.md", "bundle": print_bundle().model_dump()},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["engine"] == "typst"
    pdf = (store.site_dir(site) / out["path"]).read_bytes()
    assert pdf == b"%PDF-1.7 typst Draft notes"
    assert any("warns about" in f["message"] for f in out["findings"])
    # Built in a temporary copy: nothing was written beside the person's page.
    assert (store.site_dir(site) / "posts" / "a.md").read_text(encoding="utf-8") == PAGE
    assert sorted(p.name for p in (store.site_dir(site) / "posts").iterdir()) == [
        "a.md"
    ]


def test_typst_without_its_tools_says_how_to_install_them(site, monkeypatch) -> None:
    monkeypatch.setattr(exports.shutil, "which", lambda name: None)
    assert exports.status().auto == "print"
    with pytest.raises(exports.ExportError, match="npm install -g mystmd"):
        exports.export(site, exports.ExportRequest(page="posts/a.md", engine="typst"))
