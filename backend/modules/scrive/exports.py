"""A page as a PDF, by one of two engines.

- **typst** — the page through mystmd and Typst (`myst build`, then `typst compile`):
  real typesetting, numbered equations and cross-references, the same tool chain a
  Jupyter Book 2 project exports with. Needs both binaries on PATH (`npm i -g mystmd`;
  Typst from its own releases — the npm build is stale). Its look is the Typst
  template's, not the site theme's.
- **print** — the page as the published site draws it (the client's print build,
  `site/build.tsx` `buildPrint`), printed by headless Chromium through Playwright. Always
  available where share cards are; it looks like the site.

`auto` picks typst when both binaries are there, else print.

Rules that are quiet if wrong:

- **The person's files are never touched.** Typst works on a copy of the page (with an
  `exports:` entry written into the copy's frontmatter) in a temporary folder, beside
  copies of only the files the page embeds; print serves the page from memory.
- **Drafts export.** A PDF is for the person holding it, not the published site, so
  `isPublic` does not apply (the client's print build forces the page in).
- **The print page reaches nothing but the site.** Requests to the print origin are
  answered from the bundle, the site's files and the scene runtime; anything else is
  refused except the KaTeX stylesheet the page names, so a page cannot make an export
  depend on, or report to, an arbitrary server.
- **Blocking work on a worker thread**: sync Playwright and `subprocess.run`, the
  Windows-safe pattern (the asyncio subprocess API breaks under `--reload`).
"""

from __future__ import annotations

import logging
import mimetypes
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from backend.modules.scrive import publish, store
from backend.modules.scrive.publish import Bundle, Finding
from backend.modules.scrive.sections import _bare, _lines, body_start

logger = logging.getLogger(__name__)

Engine = Literal["auto", "print", "typst"]
Paper = Literal["a4", "letter"]

#: Where exports land, inside the site (`_build` is never published or indexed).
EXPORT_DIR = "_build/exports"
#: The origin the print page is served from inside Chromium; never a real host.
PRINT_ORIGIN = "https://scrive-print.invalid"
_ALLOWED_EXTERNAL = re.compile(r"^https://cdn\.jsdelivr\.net/npm/katex@[\w.-]+/dist/")
MYST_TIMEOUT_S = 300.0
PRINT_TIMEOUT_MS = 60_000
#: How long a 3D scene may take to start drawing (its frame loads the runtime), and
#: how long it then gets to draw before it is photographed for print.
SCENE_WAIT_MS = 20_000
SCENE_SETTLE_MS = 1200
_FOR_PAPER = """() => {
  // A Space's "Run it here" stays shut: on paper it is the caption and its link.
  document.querySelectorAll('details:not(.scrive-run)').forEach((d) => { d.open = true; });
  document.querySelectorAll('[loading="lazy"]').forEach((e) => { e.loading = 'eager'; });
}"""


class ExportError(RuntimeError):
    pass


class ExportStatus(BaseModel):
    myst: bool
    typst: bool
    #: Which engine `auto` would use.
    auto: Literal["print", "typst"]


class ExportRequest(BaseModel):
    page: str
    engine: Engine = "auto"
    paper: Paper = "a4"
    #: The client's print build of the page (`buildPrint`): required by print, and
    #: its `assets` tell typst which files the page embeds.
    bundle: Bundle = Field(default_factory=Bundle)


class ExportOutcome(BaseModel):
    #: Site-relative path of the PDF.
    path: str
    engine: Literal["print", "typst"]
    bytes: int
    seconds: float
    findings: list[Finding] = Field(default_factory=list)
    note: str = ""


def status() -> ExportStatus:
    myst = shutil.which("myst") is not None
    typst = shutil.which("typst") is not None
    return ExportStatus(
        myst=myst, typst=typst, auto="typst" if myst and typst else "print"
    )


def output_path(page: str) -> str:
    """`posts/2026-10-01-priors.md` → `_build/exports/posts/2026-10-01-priors.pdf`."""
    stem = re.sub(r"\.(md|ipynb)$", "", page, flags=re.I)
    return f"{EXPORT_DIR}/{stem}.pdf"


def export_file(site_id: str, rel: str) -> Path:
    """An exported PDF, for the download route. Only files under `_build/exports`."""
    root = (store.site_dir(site_id) / EXPORT_DIR).resolve()
    path = (store.site_dir(site_id) / rel).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".pdf":
        raise store.StoreError(f"not an export: {rel}")
    if not path.is_file():
        raise FileNotFoundError(rel)
    return path


def export(site_id: str, request: ExportRequest) -> ExportOutcome:
    """Blocking: run on a worker thread."""
    store.read_page(site_id, request.page)  # exists, and inside the site
    engine = request.engine
    if engine == "auto":
        engine = status().auto
    started = time.monotonic()
    out = store.site_dir(site_id) / output_path(request.page)
    out.parent.mkdir(parents=True, exist_ok=True)
    if engine == "typst":
        findings, note = _typst(site_id, request, out)
    else:
        findings, note = _print(site_id, request, out)
    return ExportOutcome(
        path=output_path(request.page),
        engine=engine,
        bytes=out.stat().st_size,
        seconds=round(time.monotonic() - started, 2),
        findings=findings,
        note=note,
    )


# --- print (Chromium) -----------------------------------------------------------------


def _print_files(
    site_id: str, bundle: Bundle
) -> tuple[dict[str, bytes], list[Finding]]:
    """The page's files, as the publisher completes them (media, scene pages, the
    runtime), and what is missing: an embed or a scene the page names but the site
    does not have (the publisher's own findings)."""
    files, findings, _note = publish.static_files(
        site_id, bundle.model_copy(update={"cards": []}), _theme(site_id)
    )
    return files, findings


def _theme(site_id: str):
    from backend.modules.scrive import themes

    return themes.site_theme(site_id)


def _page_out(page: str) -> str:
    from backend.modules.scrive.social import page_dir

    return f"{page_dir(page)}index.html"


def _print(
    site_id: str, request: ExportRequest, out: Path
) -> tuple[list[Finding], str]:
    if not request.bundle.files:
        raise ExportError("The print engine needs the page's print build.")
    entry = _page_out(request.page)
    if entry not in request.bundle.files:
        raise ExportError(f"The print build has no {entry}.")
    try:
        files, findings = _print_files(site_id, request.bundle)
    except publish.PublishError as exc:
        raise ExportError(str(exc)) from exc
    # Absolute links (feeds, the canonical tag) point at the published site when
    # there is one; in a PDF they are only ever read.
    record = publish.read_record(site_id)
    files = publish._fill_url(files, record.url if record else f"{PRINT_ORIGIN}/")
    try:
        pdf = _chromium_pdf(files, entry, request.paper, bool(request.bundle.scenes))
    except ExportError:
        raise
    except Exception as exc:  # noqa: BLE001 — Playwright raises its own error types
        raise ExportError(
            "Chromium could not print the page "
            f"({exc}). `uv run playwright install chromium` installs it."
        ) from exc
    out.write_bytes(pdf)
    note = (
        "3D scenes print as a still of their first moments, at their default tweaks."
        if request.bundle.scenes
        else ""
    )
    return findings, note


def _chromium_pdf(
    files: dict[str, bytes], entry: str, paper: Paper, scenes: bool
) -> bytes:
    from playwright.sync_api import Route, sync_playwright

    prefix = f"{PRINT_ORIGIN}/"

    def handle(route: Route) -> None:
        url = route.request.url
        if url.startswith(prefix):
            rel = url[len(prefix) :].split("#", 1)[0].split("?", 1)[0]
            from urllib.parse import unquote

            rel = unquote(rel)
            if rel == "" or rel.endswith("/"):
                rel += "index.html"
            body = files.get(rel)
            if body is None:
                route.fulfill(status=404, body=b"")
                return
            mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
            route.fulfill(status=200, body=body, headers={"content-type": mime})
            return
        if _ALLOWED_EXTERNAL.match(url) or url.startswith(("data:", "blob:", "about:")):
            route.continue_()
            return
        route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(color_scheme="light")
            page = context.new_page()
            page.route("**/*", handle)
            page.goto(
                prefix + entry, wait_until="networkidle", timeout=PRINT_TIMEOUT_MS
            )
            # Paper has no clicks and no scrolling: open every dropdown, and load what
            # the site defers until it is scrolled to (a lazy scene frame below the
            # fold would otherwise print as an empty box).
            page.evaluate(_FOR_PAPER)
            page.wait_for_load_state("networkidle", timeout=PRINT_TIMEOUT_MS)
            page.evaluate("document.fonts.ready.then(() => true)")
            _freeze_frames(page, files)
            page.emulate_media(media="print", color_scheme="light")
            return page.pdf(
                format="A4" if paper == "a4" else "Letter",
                print_background=True,
                margin={
                    "top": "18mm",
                    "bottom": "20mm",
                    "left": "16mm",
                    "right": "16mm",
                },
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;font-size:8px;color:#888;text-align:center;'
                    'font-family:sans-serif"><span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></div>'
                ),
            )
        finally:
            browser.close()


_SWAP_FOR_IMAGE = """(el, src) => {
  const img = document.createElement('img');
  img.src = src;
  img.alt = el.title || '';
  img.style.cssText = 'display:block;width:100%;height:auto';
  el.replaceWith(img);
}"""

_SWAP_FOR_LINK = """(el) => {
  const p = document.createElement('p');
  const a = document.createElement('a');
  a.href = el.src;
  a.textContent = el.title || el.src;
  p.append('Embedded: ', a);
  el.replaceWith(p);
}"""


def _freeze_frames(page: Any, files: dict[str, bytes]) -> None:
    """Frames, made printable. A 3D scene is a WebGL canvas, which Chromium does not
    carry into print, so each scene frame is photographed as it is on screen and
    replaced by the picture; any other embed (a video), and a scene whose page the site
    does not have (a finding already says so), becomes a link."""
    import base64
    from urllib.parse import unquote

    prefix = f"{PRINT_ORIGIN}/"
    for frame in page.query_selector_all("figure.scrive-scene iframe"):
        try:
            inner = frame.content_frame()
            url = inner.url if inner is not None else ""
            rel = (
                unquote(url[len(prefix) :].split("#", 1)[0])
                if url.startswith(prefix)
                else ""
            )
            if not rel or rel not in files:
                continue  # nothing will ever draw there
            # The frame loaded late (it was lazy), and the runtime is megabytes: wait
            # for the scene's canvas, then for its first frames to draw.
            assert inner is not None
            inner.wait_for_selector("canvas", timeout=SCENE_WAIT_MS)
            page.wait_for_timeout(SCENE_SETTLE_MS)
            png = frame.screenshot(type="png", timeout=10_000)
        except Exception:  # noqa: BLE001 — a frame that cannot be shot prints as it is
            logger.debug("export: could not photograph a scene", exc_info=True)
            continue
        frame.evaluate(
            _SWAP_FOR_IMAGE, "data:image/png;base64," + base64.b64encode(png).decode()
        )
    for frame in page.query_selector_all("iframe"):
        frame.evaluate(_SWAP_FOR_LINK)


# --- typst (mystmd) ------------------------------------------------------------------


def _with_export(text: str, output: str, template: str) -> str:
    """The page with its frontmatter `exports:` set to the one Scrive asks for."""
    lines = [_bare(line) for line in _lines(text)]
    end = body_start(lines)
    data: dict = {}
    if end:
        try:
            parsed = yaml.safe_load("\n".join(lines[1 : end - 1]))
            data = parsed if isinstance(parsed, dict) else {}
        except yaml.YAMLError:
            data = {}
    spec: dict = {"format": "typst", "output": output}
    if template:
        spec["template"] = template
    data["exports"] = [spec]
    front = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n" + "\n".join(lines[end:])


def _typst(
    site_id: str, request: ExportRequest, out: Path
) -> tuple[list[Finding], str]:
    myst = shutil.which("myst")
    typst = shutil.which("typst")
    if not myst or not typst:
        missing = " and ".join(
            name for name, path in (("myst", myst), ("typst", typst)) if not path
        )
        raise ExportError(
            f"The Typst engine needs {missing} on PATH: `npm install -g mystmd`, and "
            "Typst from typst.app (not from npm)."
        )
    base = store.site_dir(site_id)
    config = store.read_config(base)
    # `scrive.yml` → `targets: {pdf: {template: …}}`: a mystmd Typst template by name.
    pdf = config.targets.get("pdf")
    template = str(pdf.get("template") or "") if isinstance(pdf, dict) else ""
    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="scrive-typst-") as tmp:
        root = Path(tmp)
        for name in ("myst.yml",):
            if (base / name).is_file():
                shutil.copy2(base / name, root / name)
        for bib in base.glob("*.bib"):
            shutil.copy2(bib, root / bib.name)
        for asset in request.bundle.assets:
            try:
                source = store.resolve_asset(site_id, asset)
            except (FileNotFoundError, store.StoreError):
                findings.append(
                    Finding(
                        rule="missing-asset",
                        message=f"{asset} is embedded by the page but is not in the site.",
                        file=asset,
                    )
                )
                continue
            target = root / asset
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        text = store.read_page(site_id, request.page).content
        depth = len(PurePosixPath(request.page).parts) - 1
        output = "/".join([".."] * depth + ["_export", "page.pdf"])
        page = root / request.page
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(_with_export(text, output, template), encoding="utf-8")
        try:
            run = subprocess.run(
                [myst, "build", request.page, "--typst"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MYST_TIMEOUT_S,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExportError(
                f"myst build took longer than {int(MYST_TIMEOUT_S)} seconds."
            ) from exc
        except OSError as exc:
            raise ExportError(f"could not run myst: {exc}") from exc
        pdf = root / "_export" / "page.pdf"
        log = (run.stdout + "\n" + run.stderr).strip()
        if run.returncode != 0 or not pdf.is_file():
            tail = "\n".join(log.splitlines()[-12:])
            raise ExportError(f"myst build did not produce a PDF:\n{tail}")
        shutil.copy2(pdf, out)
    for line in log.splitlines():
        if re.search(r"\b(warn|warning)\b", line, re.I) and len(findings) < 12:
            findings.append(Finding(severity="info", rule="myst", message=line.strip()))
    return findings, "Typeset by mystmd and Typst; code cells show their source."
