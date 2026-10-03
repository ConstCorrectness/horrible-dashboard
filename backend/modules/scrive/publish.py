"""Publishing a site to GitHub Pages, in one of two modes (`scrive.yml` →
`targets.pages.mode`):

- **static** (default) — the site is built *in the client* by the same React renderer
  the Preview uses (`packages/core/src/modules/scrive/site/`), posted here as a file
  set, completed with the files only the backend has (embedded media, scene pages, the
  scene runtime, share cards) and pushed as one commit.
- **jupyter-book** — the site's *source* is pushed with a GitHub Actions workflow that
  runs `jupyter book build --html` and deploys it with `actions/deploy-pages`. Real
  Jupyter Book 2 output, executed code, no local Node. A small MyST plugin
  (`static/scrive-myst-plugin.mjs`) teaches that build Scrive's own `{r3f}`, `{video}`
  and `{pending}` directives.

Every publish starts from a person's click: there is no agent tool here, and the
routes are the only callers.

Rules that are quiet if wrong:

- **Drafts stay home.** A post is published only when its status is `published`; any
  other page unless its status says otherwise (`is_public`). The client build and the
  jupyter-book file set apply the same rule, so a draft is never in the pushed tree.
- **Preflight reads the bytes that leave**, not the sources they came from: every text
  file in the final set goes through the shared secret scanner. A secret blocks until
  acknowledged, as in notebook publishing.
- **Absolute URLs are filled in here.** The client writes `SITE_URL` wherever a feed,
  sitemap or share tag needs an absolute address, because only the push knows the
  repository; everything else in the build is relative, so the site works at any path.
- **Only our own files are deleted.** A publish removes what the *previous* publish
  wrote and this one did not (from `.scrive/published.json`); anything else in the
  repository is left alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field

from backend.modules.scrive import cards, sections, store, themes
from backend.modules.scrive.cards import Card
from backend.modules.scrive.models import PageMeta
from backend.publishing import github_pages
from backend.publishing.errors import PublishError
from backend.publishing.scan import scan_text

#: What the client build writes where an absolute URL is needed (feed, sitemap, share
#: tags, the 404 page). Replaced by the real site URL, with its trailing slash.
SITE_URL = "__SCRIVE_SITE_URL__/"
SUPPORT = "_scrive"
RUNTIME_PATH = f"{SUPPORT}/runtime.js"
PLUGIN_FILE = "scrive-myst-plugin.mjs"
WORKFLOW_PATH = ".github/workflows/scrive-pages.yml"
RECORD_FILE = ".scrive/published.json"
BUILD_DIR = "_build/site"
STATIC = Path(__file__).parent / "static"

#: GitHub refuses blobs over 100 MB, and warns on pushes over 50.
MAX_FILE = 100 * 1024 * 1024
LARGE_FILE = 25 * 1024 * 1024
TEXT_SUFFIXES = {
    ".html",
    ".css",
    ".xml",
    ".txt",
    ".md",
    ".ipynb",
    ".tsx",
    ".ts",
    ".js",
    ".mjs",
    ".json",
    ".csv",
    ".yml",
    ".yaml",
    ".svg",
}
#: Never pushed in jupyter-book mode: Scrive's state, build output, VCS, vendored
#: code, post templates (unwritten pages) and site themes (static-mode only).
SOURCE_SKIP = {
    ".scrive",
    "_build",
    SUPPORT,
    ".git",
    "node_modules",
    "templates",
    "themes",
}
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_OUT_PATH = re.compile(r"^[A-Za-z0-9._~@%+=,-]+(/[A-Za-z0-9._~@%+=,-]+)*$")


class PagesConfig(BaseModel):
    """`scrive.yml` → `targets.pages`."""

    #: `owner/repo`, a bare repo name on your account, or blank for one named after
    #: the site.
    repo: str = ""
    mode: Literal["static", "jupyter-book"] = "static"
    #: A custom domain (`blog.example.com`), written as `CNAME`.
    cname: str = ""


class Bundle(BaseModel):
    """What the client's static build sends."""

    #: Generated text files by their path in the published site.
    files: dict[str, str] = Field(default_factory=dict)
    #: Site files the pages embed (images, video, data), copied as they are on disk.
    assets: list[str] = Field(default_factory=list)
    #: Scene sources the pages embed — each must exist for its frame to show.
    scenes: list[str] = Field(default_factory=list)
    #: Share cards to draw (`cards.py`).
    cards: list[Card] = Field(default_factory=list)
    #: The source pages this build published — preflight reads them for leftovers.
    pages: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    #: Blocking findings stop a publish until the person acknowledges them.
    blocking: bool = False
    severity: Literal["secret", "warning", "info"] = "warning"
    rule: str
    message: str
    file: str = ""


class PublishRecord(BaseModel):
    mode: str
    owner: str
    repo: str
    branch: str
    url: str
    commit: str = ""
    files: list[str] = Field(default_factory=list)
    pages: int = 0
    published_at: float = 0.0


class PublishRequest(BaseModel):
    #: Required in static mode; ignored in jupyter-book mode (the backend reads the
    #: sources itself).
    bundle: Bundle | None = None
    #: The person saw the blocking findings and chose to publish anyway.
    acknowledged: bool = False


class PublishOutcome(BaseModel):
    published: bool
    findings: list[Finding] = Field(default_factory=list)
    record: PublishRecord | None = None
    note: str = ""


class BuildOutcome(BaseModel):
    path: str
    files: int
    findings: list[Finding] = Field(default_factory=list)
    note: str = ""


# --- configuration -------------------------------------------------------------------


def pages_config(site_id: str) -> PagesConfig:
    raw = store.read_config(store.site_dir(site_id)).targets.get("pages")
    try:
        return PagesConfig.model_validate(raw if isinstance(raw, dict) else {})
    except ValueError:
        return PagesConfig()


def clean_cname(value: str) -> str:
    host = value.strip().lower().removeprefix("https://").removeprefix("http://")
    host = host.strip("/")
    if host and not _HOST.match(host):
        raise store.StoreError(f"not a domain name: {value!r}")
    return host


def update_config(
    site_id: str,
    *,
    title: str | None = None,
    theme: str | None = None,
    pages: PagesConfig | None = None,
) -> store.SiteConfig:
    """Change `scrive.yml`. It is Scrive's own file, so it is rewritten whole; keys
    Scrive does not know are kept."""
    base = store.site_dir(site_id)
    path = base / store.CONFIG_FILE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    if title is not None:
        raw["title"] = title.strip()
    if theme is not None:
        themes.get_theme(site_id, theme)  # refuse a name that is not a theme
        raw["theme"] = theme
    if pages is not None:
        targets = raw.get("targets") if isinstance(raw.get("targets"), dict) else {}
        targets["pages"] = {
            "repo": pages.repo.strip().strip("/"),
            "mode": pages.mode,
            "cname": clean_cname(pages.cname),
        }
        raw["targets"] = targets
    store.write_bytes_atomic(
        path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).encode("utf-8")
    )
    return store.read_config(base)


def read_record(site_id: str) -> PublishRecord | None:
    path = store.site_dir(site_id) / RECORD_FILE
    try:
        return PublishRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_record(site_id: str, record: PublishRecord) -> None:
    store.write_bytes_atomic(
        store.site_dir(site_id) / RECORD_FILE,
        record.model_dump_json(indent=2).encode("utf-8"),
    )


# --- which pages go ------------------------------------------------------------------


def is_public(meta: PageMeta) -> bool:
    """A post goes out once its status is `published`; any other page unless its
    status says it is not ready. The client build applies the same rule
    (`site/build.ts`)."""
    status = meta.status.strip().lower()
    if meta.kind == "post":
        return status == "published"
    return status in ("", "published")


# --- scene pages ---------------------------------------------------------------------


def scene_page_path(scene: str) -> str:
    """Where a scene's standalone page is published: `scenes/orbit.tsx` →
    `_scrive/scene/scenes/orbit.html`. The JB plugin and the client build use the
    same mapping."""
    return f"{SUPPORT}/scene/{PurePosixPath(scene).with_suffix('.html').as_posix()}"


def _script_json(value: object) -> str:
    # Inside <script>, `</script>` (any `</`) would end the element early.
    return json.dumps(value, ensure_ascii=True).replace("<", "\\u003c")


def scene_page(scene: str, source: str) -> str:
    """A page that runs one scene: the source inline, the runtime beside it, the
    tweak values from the URL fragment (`#{"speed": 2}`, URI-encoded). Embedded by a
    published page in an `allow-scripts` sandbox, as in the app."""
    depth = len(PurePosixPath(scene_page_path(scene)).parts) - 1
    runtime = "../" * depth + RUNTIME_PATH
    boot = (
        "(function(){var p={};try{var h=decodeURIComponent(location.hash.slice(1));"
        "if(h)p=JSON.parse(h)}catch(e){}"
        f"window.__SCRIVE_SCENE__={{source:{_script_json(source)},params:p}};}})();"
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{PurePosixPath(scene).stem}</title>"
        "<style>html,body,#scene{margin:0;width:100%;height:100%;overflow:hidden;"
        "background:transparent}</style></head>"
        f'<body><div id="scene"></div><script>{boot}</script>'
        f'<script src="{runtime}"></script></body></html>\n'
    )


def _scenes(base: Path) -> dict[str, str]:
    """Every `.tsx` scene in the site, by its site path."""
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SOURCE_SKIP)
        for name in sorted(filenames):
            if name.endswith(".tsx"):
                path = Path(dirpath) / name
                found[path.relative_to(base).as_posix()] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
    return found


def _support_files(base: Path) -> dict[str, bytes]:
    """The scene runtime and one page per scene — only when the site has scenes."""
    scenes = _scenes(base)
    if not scenes:
        return {}
    runtime = STATIC / "scrive-runtime.js"
    if not runtime.is_file():
        raise PublishError(
            "This site has 3D scenes, but the scene runtime is not built. Run: "
            "pnpm --filter @horrible/scrive-runtime build"
        )
    files = {RUNTIME_PATH: runtime.read_bytes()}
    for scene, source in scenes.items():
        files[scene_page_path(scene)] = scene_page(scene, source).encode("utf-8")
    return files


# --- the two file sets ---------------------------------------------------------------


def _out_path(path: str) -> str:
    """A path the client asked to write, checked: relative, no `..`, nothing hidden."""
    clean = path.strip()
    if (
        not _OUT_PATH.match(clean)
        or any(part in ("..", ".") for part in clean.split("/"))
        or clean.split("/")[0] in {".git", ".github", ".scrive"}
    ):
        raise store.StoreError(f"not a path a site can publish: {path!r}")
    return clean


def static_files(
    site_id: str, bundle: Bundle, theme: themes.ThemeInfo
) -> tuple[dict[str, bytes], list[Finding], str]:
    """The finished static site: the client's files, plus media, scenes and cards.
    Returns the files, findings about them, and a note (how the cards were drawn)."""
    base = store.site_dir(site_id)
    files: dict[str, bytes] = {}
    findings: list[Finding] = []
    for path, text in bundle.files.items():
        files[_out_path(path)] = text.encode("utf-8")
    for asset in sorted(set(bundle.assets)):
        rel = _out_path(asset)
        try:
            files[rel] = store.resolve_asset(site_id, rel).read_bytes()
        except (FileNotFoundError, store.StoreError):
            findings.append(
                Finding(
                    rule="missing-asset",
                    message=f"{rel} is embedded by a page but is not in the site.",
                    file=rel,
                )
            )
    support = _support_files(base)
    for scene in sorted(set(bundle.scenes)):
        if scene_page_path(scene) not in support:
            findings.append(
                Finding(
                    rule="missing-scene",
                    message=f"{scene} is embedded as a 3D scene but is not in the site; "
                    "its frame will be empty.",
                    file=scene,
                )
            )
    files.update(support)
    note = ""
    if bundle.cards:
        rendered = cards.render_cards(
            bundle.cards,
            template=themes.og_template(site_id, theme),
            tokens=theme.tokens,
            site_title=store.read_config(base).title or site_id,
            cache_dir=base / ".scrive" / "cache" / "og",
        )
        for path, png in rendered.files.items():
            files[_out_path(path)] = png
        note = rendered.note
    return files, findings, note


def _myst_with_plugin(text: str) -> str:
    """`myst.yml` with the Scrive plugin listed — in the pushed copy only; the
    person's own file is never rewritten."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PublishError(f"myst.yml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PublishError("myst.yml must be a mapping")
    project = data.setdefault("project", {})
    if not isinstance(project, dict):
        raise PublishError("myst.yml: `project` must be a mapping")
    plugins = project.get("plugins")
    plugins = list(plugins) if isinstance(plugins, list) else []
    if PLUGIN_FILE not in plugins:
        plugins.append(PLUGIN_FILE)
    project["plugins"] = plugins
    return (
        "# Pushed by Scrive: the site's myst.yml with scrive-myst-plugin.mjs listed.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )


def workflow(branch: str, base_url: str, requirements: bool) -> str:
    """The Actions workflow that builds the site with Jupyter Book 2 and deploys it.

    `BASE_URL` is the path the site is served under (`/repo`, or empty for a
    `<you>.github.io` repository or a custom domain); Jupyter Book and the Scrive
    plugin both read it. Code cells are executed during the build.
    """
    install = "pip install 'jupyter-book>=2' ipykernel"
    if requirements:
        install += " -r requirements.txt"
    return f"""# Written by Scrive (jupyter-book mode). Rewritten on every publish.
name: Scrive site

on:
  push:
    branches: [{json.dumps(branch)}]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

env:
  BASE_URL: {json.dumps(base_url)}

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: {install}
      - run: jupyter book build --html --execute
      - name: Scene pages and runtime
        run: if [ -d {SUPPORT} ]; then cp -r {SUPPORT} _build/html/{SUPPORT}; fi
      - uses: actions/upload-pages-artifact@v3
        with:
          path: _build/html
      - id: deployment
        uses: actions/deploy-pages@v4
"""


def source_files(
    site_id: str, *, branch: str = "main", base_url: str = ""
) -> tuple[dict[str, bytes], list[str]]:
    """jupyter-book mode: the site's sources minus drafts, the plugin, the workflow,
    and the scene support files. Returns the files and the pages among them."""
    base = store.site_dir(site_id)
    files: dict[str, bytes] = {}
    pages: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(base)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SOURCE_SKIP and not (rel_dir == Path(".") and d == ".github")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            if name.endswith(".scrive-tmp"):
                continue
            if name.lower().endswith(store.PAGE_SUFFIXES):
                if not is_public(store.page_meta(base, path)):
                    continue
                pages.append(rel)
            files[rel] = path.read_bytes()
    myst = files.get(store.MYST_FILE)
    if myst is None:
        raise PublishError("The site has no myst.yml, so Jupyter Book cannot build it.")
    files[store.MYST_FILE] = _myst_with_plugin(myst.decode("utf-8")).encode("utf-8")
    files[PLUGIN_FILE] = (STATIC / PLUGIN_FILE).read_bytes()
    files[WORKFLOW_PATH] = workflow(
        branch, base_url, "requirements.txt" in files
    ).encode("utf-8")
    files.update(_support_files(base))
    return files, pages


# --- preflight -----------------------------------------------------------------------


def preflight(site_id: str, files: dict[str, bytes], pages: list[str]) -> list[Finding]:
    """What is wrong with this exact file set. Reports "found these", never "clean"."""
    findings: list[Finding] = []
    if not pages:
        findings.append(
            Finding(
                blocking=True,
                severity="warning",
                rule="empty",
                message="No page is ready to publish. A post goes out once its status "
                "is `published` (move it on the posts board).",
            )
        )
    for path, data in sorted(files.items()):
        if len(data) > MAX_FILE:
            findings.append(
                Finding(
                    blocking=True,
                    rule="too-large",
                    message=f"{path} is {len(data) // 2**20} MB; GitHub refuses files "
                    "over 100 MB.",
                    file=path,
                )
            )
            continue
        if len(data) > LARGE_FILE:
            findings.append(
                Finding(
                    rule="large",
                    message=f"{path} is {len(data) // 2**20} MB; every publish uploads it.",
                    file=path,
                )
            )
        if path == RUNTIME_PATH or PurePosixPath(path).suffix not in TEXT_SUFFIXES:
            continue
        for hit in scan_text(data.decode("utf-8", "replace")):
            findings.append(
                Finding(
                    blocking=hit.blocking,
                    severity="secret" if hit.kind == "secret" else "warning",
                    rule=hit.kind,
                    message=f"{hit.label} ({hit.excerpt})",
                    file=path,
                )
            )
    for page in pages:
        try:
            text = store.read_page(site_id, page).content
        except (FileNotFoundError, store.StoreError):
            continue
        left = sections.pending_blocks(text) if page.endswith(".md") else []
        if left:
            findings.append(
                Finding(
                    rule="pending",
                    message=f"{len(left)} section{'s' if len(left) != 1 else ''} still "
                    "to write; the placeholders are left out of the published page.",
                    file=page,
                )
            )
    return findings


def _blocked(findings: list[Finding], acknowledged: bool) -> bool:
    blocking = [f for f in findings if f.blocking]
    # An empty site cannot be acknowledged into a publish.
    return any(f.rule == "empty" for f in blocking) or (
        bool(blocking) and not acknowledged
    )


# --- build locally, and publish ------------------------------------------------------


def _fill_url(files: dict[str, bytes], url: str) -> dict[str, bytes]:
    placeholder = SITE_URL.encode("utf-8")
    real = url.encode("utf-8")
    return {
        path: data.replace(placeholder, real)
        if PurePosixPath(path).suffix in {".html", ".xml", ".txt"}
        else data
        for path, data in files.items()
    }


def build_local(site_id: str, bundle: Bundle) -> BuildOutcome:
    """Write the static site to `<site>/_build/site/` — a preview served by the
    backend, and a folder that can be opened or copied anywhere. Blocking."""
    base = store.site_dir(site_id)
    theme = themes.site_theme(site_id)
    files, findings, note = static_files(site_id, bundle, theme)
    findings += preflight(site_id, files, bundle.pages)
    record = read_record(site_id)
    files = _fill_url(files, record.url if record else "./")
    out = base / BUILD_DIR
    if out.exists():
        shutil.rmtree(out)
    for path, data in files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return BuildOutcome(path=str(out), files=len(files), findings=findings, note=note)


def built_file(site_id: str, rel_path: str) -> Path:
    """A file of the local build, for the preview route. A folder means its index."""
    root = (store.site_dir(site_id) / BUILD_DIR).resolve()
    resolved = (root / rel_path.lstrip("/")).resolve()
    if not resolved.is_relative_to(root):
        raise store.StoreError(f"path escapes the build: {rel_path}")
    if resolved.is_dir():
        resolved = resolved / "index.html"
    if not resolved.is_file():
        raise FileNotFoundError(rel_path)
    return resolved


def _site_url(target: github_pages.PagesRepo, cname: str) -> str:
    return f"https://{cname}/" if cname else target.site_url


def _base_path(url: str) -> str:
    """`https://you.github.io/blog/` → `/blog`; a root site → `""`."""
    return urlsplit(url).path.rstrip("/")


async def publish(site_id: str, request: PublishRequest) -> PublishOutcome:
    config = pages_config(site_id)
    ok, reason = github_pages.availability()
    if not ok:
        raise PublishError(reason)
    theme = themes.site_theme(site_id)
    site_title = store.read_config(store.site_dir(site_id)).title or site_id

    # Gather and check before touching the network: a blocked publish costs nothing.
    note = ""
    if config.mode == "static":
        if request.bundle is None:
            raise PublishError("A static publish needs the built site.")
        bundle = request.bundle
        files, findings, note = await asyncio.to_thread(
            static_files, site_id, bundle, theme
        )
        pages = bundle.pages
    else:
        # The workflow's BASE_URL depends on the repository; it is filled in below.
        files, pages = await asyncio.to_thread(source_files, site_id)
        findings = []
    findings += await asyncio.to_thread(preflight, site_id, files, pages)
    if _blocked(findings, request.acknowledged):
        return PublishOutcome(published=False, findings=findings, note=note)

    target = await github_pages.resolve_repo(
        config.repo,
        default_repo=site_id,
        description=f"{site_title} — published with Scrive",
        setting="targets.pages.repo in the site's scrive.yml",
    )
    url = _site_url(target, config.cname)
    if config.mode == "jupyter-book":
        files[WORKFLOW_PATH] = workflow(
            target.branch, _base_path(url), "requirements.txt" in files
        ).encode("utf-8")
    else:
        files = _fill_url(files, url)
    if config.cname:
        files["CNAME"] = f"{config.cname}\n".encode("utf-8")

    previous = read_record(site_id)
    stale = [p for p in (previous.files if previous else []) if p not in files]
    commit = await github_pages.push_files(
        target,
        files,
        message=f"Publish {site_title} ({len(pages)} page{'s' if len(pages) != 1 else ''})",
        delete=stale,
    )
    await github_pages.ensure_pages(
        target, build_type="workflow" if config.mode == "jupyter-book" else "legacy"
    )
    record = PublishRecord(
        mode=config.mode,
        owner=target.owner,
        repo=target.repo,
        branch=target.branch,
        url=url,
        commit=commit or (previous.commit if previous else ""),
        files=sorted(files),
        pages=len(pages),
        published_at=time.time(),
    )
    _write_record(site_id, record)
    wait = (
        "The Actions build takes a few minutes; its progress is under the "
        "repository's Actions tab."
        if config.mode == "jupyter-book"
        else "GitHub Pages takes about a minute to show a change."
    )
    if commit is None:
        wait = "Nothing changed since the last publish, so nothing was pushed."
    return PublishOutcome(
        published=True,
        findings=findings,
        record=record,
        note=" ".join(n for n in (wait, note) if n),
    )
