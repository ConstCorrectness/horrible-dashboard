"""Sites and pages on disk. The files are the source of truth; nothing is mirrored.

A **site** is a folder under the `scrive.root` setting (default `~/horrible/scrive`)
holding a `scrive.yml`. It is laid out as a valid MyST / Jupyter Book 2 project, so
the same folder builds with `jupyter book build`, lives in git, and opens in any
editor:

    <site>/myst.yml  scrive.yml  index.md  posts/  pages/  media/  scenes/
    <site>/.scrive/  (cache, trash — gitignored)

Three rules that are quiet if wrong:

- **Bytes, not text.** Pages are read and written as raw bytes decoded as UTF-8 with
  no newline translation. `atomic_write.write_text_atomic` opens in text mode, which
  on Windows turns every `\\n` into `\\r\\n` — invisible in the editor, but it rewrites
  every line of a hand-written file on its first save and breaks the editor's
  byte-identical round trip. The revision is a hash of those bytes.
- **Escape-guarded paths.** Every page path resolves under its site root or is
  refused, and only `.md` / `.ipynb` are pages.
- **Delete is a move** into `.scrive/trash/`. These are the person's own files, often
  not yet committed anywhere.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from backend.atomic_write import replace_with_retry
from backend.modules.scrive import sections
from backend.modules.scrive.models import (
    SITE_ID_PATTERN,
    Page,
    PageKind,
    PageMeta,
    SiteConfig,
    SiteMeta,
)
from backend.modules.settings.routes import get_value

ROOT_SETTING = "scrive.root"
DEFAULT_ROOT = "~/horrible/scrive"
CONFIG_FILE = "scrive.yml"
MYST_FILE = "myst.yml"
PAGE_SUFFIXES = (".md", ".ipynb")
#: Never listed as pages: Scrive's own state, build output (`_build/`, and the
#: published `_scrive/` support files), VCS, vendored code, post templates (pages-to-be,
#: listed by the template picker instead) and site themes (whose `THEME.md` is a guide).
SKIP_DIRS = {
    ".scrive",
    "_build",
    "_scrive",
    ".git",
    "node_modules",
    "templates",
    "themes",
    ".github",
}

_SITE_ID = re.compile(SITE_ID_PATTERN)
_FRONTMATTER = re.compile(rb"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class StoreError(ValueError):
    """A request that names something that is not there or not allowed."""


class Conflict(Exception):
    """A save against a stale revision. Carries the page as it is now."""

    def __init__(self, current: Page) -> None:
        super().__init__("stale revision")
        self.current = current


# --- paths ---------------------------------------------------------------------


def root() -> Path:
    return Path(str(get_value(ROOT_SETTING, DEFAULT_ROOT))).expanduser()


def site_dir(site_id: str) -> Path:
    if not _SITE_ID.match(site_id):
        raise StoreError(f"not a site id: {site_id!r}")
    path = root() / site_id
    if not (path / CONFIG_FILE).is_file():
        raise FileNotFoundError(site_id)
    return path


def resolve_page(site_id: str, rel_path: str) -> Path:
    base = site_dir(site_id).resolve()
    resolved = (base / rel_path).resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise StoreError(f"path escapes the site: {rel_path}")
    if resolved.suffix.lower() not in PAGE_SUFFIXES:
        raise StoreError(f"not a page: {rel_path}")
    rel_parts = resolved.relative_to(base).parts
    if any(part in SKIP_DIRS for part in rel_parts[:-1]):
        raise StoreError(f"not a page: {rel_path}")
    return resolved


def resolve_asset(site_id: str, rel_path: str) -> Path:
    """A file a page embeds (an image, a video, a scene). Same escape guard as pages;
    Scrive's own `.scrive/` state and VCS internals are not assets."""
    base = site_dir(site_id).resolve()
    resolved = (base / rel_path.lstrip("/")).resolve()
    if not resolved.is_relative_to(base) or resolved == base:
        raise StoreError(f"path escapes the site: {rel_path}")
    if resolved.relative_to(base).parts[0] in {".git", ".scrive"}:
        raise StoreError(f"not an asset: {rel_path}")
    if not resolved.is_file():
        raise FileNotFoundError(rel_path)
    return resolved


def _rel(base: Path, path: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


# --- bytes on disk ---------------------------------------------------------------


def revision_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Like `atomic_write.write_text_atomic`, but binary — see the module docstring."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".scrive-tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_bytes(path: Path) -> bytes:
    # Same transient-PermissionError retry as atomic_write.read_text_or_none.
    for attempt in range(5):
        try:
            return path.read_bytes()
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


# --- frontmatter -----------------------------------------------------------------


def _scalar(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return "" if value is None else str(value)


def frontmatter(data: bytes) -> dict[str, Any]:
    """The YAML frontmatter of a MyST file, or `{}`. Malformed YAML reads as empty
    rather than failing the listing: one broken file must not hide the whole site."""
    match = _FRONTMATTER.match(data)
    if not match:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1).decode("utf-8", "replace"))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_heading(data: bytes) -> str:
    body = _FRONTMATTER.sub(b"", data, count=1)
    match = re.search(rb"^#[ \t]+(.+?)[ \t#]*\r?$", body, re.M)
    return match.group(1).decode("utf-8", "replace").strip() if match else ""


def _kind(rel_path: str) -> PageKind:
    if rel_path.endswith(".ipynb"):
        return "notebook"
    return "post" if rel_path.startswith("posts/") else "page"


def page_meta(base: Path, path: Path, data: bytes | None = None) -> PageMeta:
    data = _read_bytes(path) if data is None else data
    rel = _rel(base, path)
    fm = frontmatter(data) if path.suffix == ".md" else {}
    tags = fm.get("tags") or []
    return PageMeta(
        path=rel,
        kind=_kind(rel),
        title=_scalar(fm.get("title")) or _first_heading(data) or path.stem,
        status=_scalar(fm.get("status")),
        date=_scalar(fm.get("date")),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [str(tags)],
        description=_scalar(fm.get("description")),
        updated_at=path.stat().st_mtime,
        revision=revision_of(data),
    )


# --- sites -------------------------------------------------------------------------


def read_config(base: Path) -> SiteConfig:
    try:
        raw = yaml.safe_load((base / CONFIG_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    try:
        return SiteConfig.model_validate(raw if isinstance(raw, dict) else {})
    except ValueError:
        return SiteConfig()


def _site_meta(base: Path) -> SiteMeta:
    config = read_config(base)
    return SiteMeta(
        id=base.name,
        title=config.title or base.name,
        theme=config.theme,
        root=str(base),
        pages=sum(1 for _ in _walk_pages(base)),
    )


def list_sites() -> list[SiteMeta]:
    base = root()
    if not base.is_dir():
        return []
    return [
        _site_meta(child)
        for child in sorted(base.iterdir())
        if child.is_dir()
        and _SITE_ID.match(child.name)
        and (child / CONFIG_FILE).is_file()
    ]


def get_site(site_id: str) -> tuple[SiteMeta, SiteConfig]:
    base = site_dir(site_id)
    return _site_meta(base), read_config(base)


def yaml_scalar(value: str) -> str:
    """`value` as one line of YAML, quoted only when it has to be (a colon, a leading
    quote or dash) — so frontmatter stays as readable as if a person typed it."""
    return yaml.safe_dump(value, width=10_000).strip().splitlines()[0]


def _myst_yml(title: str) -> str:
    # `pattern` toc entries keep new posts in the book without editing this file.
    return (
        "# Jupyter Book 2 / mystmd project. Scrive keeps its own settings in scrive.yml.\n"
        "version: 1\n"
        "project:\n"
        f"  title: {yaml_scalar(title)}\n"
        "  toc:\n"
        "    - file: index.md\n"
        "    - title: Posts\n"
        "      children:\n"
        "        - pattern: posts/*.md\n"
        "    - pattern: pages/*.md\n"
        "site:\n"
        "  template: book-theme\n"
    )


def create_site(site_id: str, title: str) -> SiteMeta:
    if not _SITE_ID.match(site_id):
        raise StoreError(f"not a site id: {site_id!r}")
    base = root() / site_id
    if base.exists():
        raise FileExistsError(site_id)
    title = title.strip() or site_id
    for sub in ("posts", "pages", "media", "scenes", ".scrive"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    config = SiteConfig(title=title)
    write_bytes_atomic(
        base / CONFIG_FILE,
        yaml.safe_dump(config.model_dump(), sort_keys=False).encode("utf-8"),
    )
    write_bytes_atomic(base / MYST_FILE, _myst_yml(title).encode("utf-8"))
    write_bytes_atomic(base / ".gitignore", b"_build/\n.scrive/\n")
    write_bytes_atomic(
        base / "index.md",
        (
            f"---\ntitle: {yaml_scalar(title)}\n---\n\n# {title}\n\n"
            "Welcome. Posts live in `posts/`.\n"
        ).encode("utf-8"),
    )
    return _site_meta(base)


# --- pages -------------------------------------------------------------------------


def _walk_pages(base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.lower().endswith(PAGE_SUFFIXES):
                yield Path(dirpath) / name


def list_pages(site_id: str) -> list[PageMeta]:
    base = site_dir(site_id)
    pages = [page_meta(base, p) for p in _walk_pages(base)]
    # Posts newest first by their own date (then path); everything else by path.
    posts = sorted(
        (m for m in pages if m.kind == "post"),
        key=lambda m: (m.date, m.path),
        reverse=True,
    )
    return posts + sorted((m for m in pages if m.kind != "post"), key=lambda m: m.path)


def read_page(site_id: str, rel_path: str) -> Page:
    path = resolve_page(site_id, rel_path)
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    data = _read_bytes(path)
    return Page(
        meta=page_meta(site_dir(site_id), path, data),
        content=data.decode("utf-8", "replace"),
    )


def save_page(site_id: str, rel_path: str, content: str, base_revision: str) -> Page:
    path = resolve_page(site_id, rel_path)
    if path.is_file():
        current = _read_bytes(path)
        if revision_of(current) != base_revision:
            raise Conflict(read_page(site_id, rel_path))
    elif base_revision:
        raise FileNotFoundError(rel_path)
    write_bytes_atomic(path, content.encode("utf-8"))
    return read_page(site_id, rel_path)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "untitled"


def create_page(
    site_id: str, kind: str, title: str, slug: str = "", body: str | None = None
) -> Page:
    """A new page at a fresh path (`-2`, `-3`… when taken). `body` is a whole page
    to start from — a template's text or an approved outline — whose title, date and
    status are set here; without it the page is frontmatter only."""
    base = site_dir(site_id)
    title = title.strip() or "Untitled"
    stem = slugify(slug or title)
    today = date.today().isoformat()
    if kind == "post":
        rel = f"posts/{today}-{stem}.md"
        head = (
            f"---\ntitle: {yaml_scalar(title)}\ndate: {today}\n"
            "status: draft\ntags: []\n---\n"
        )
    else:
        rel = f"pages/{stem}.md"
        head = f"---\ntitle: {yaml_scalar(title)}\n---\n"
    candidate, n = rel, 2
    while (base / candidate).exists():
        candidate = rel.replace(".md", f"-{n}.md")
        n += 1
    if body is None:
        # The title lives in the frontmatter only. A `# Title` heading as well would
        # show the title twice in Jupyter Book, which renders the frontmatter title.
        return save_page(site_id, candidate, f"{head}\n", "")
    text = sections.set_field(body, "title", title)
    if kind == "post":
        text = sections.set_field(text, "date", today)
        text = sections.set_field(text, "status", "draft")
    return save_page(site_id, candidate, text, "")


# --- who wrote it ------------------------------------------------------------------

#: Revisions written by an agent tool, by `(site, path)`, consumed by the watcher so
#: its `page.changed` can say `origin: agent`. The editor shows those as a reviewable
#: agent edit rather than silently loading them. Small and short-lived: an entry is
#: dropped once seen, or after a minute when no watcher is running.
_agent_writes: dict[tuple[str, str], tuple[str, float]] = {}
_AGENT_WRITE_TTL = 60.0


def note_agent_write(site_id: str, rel_path: str, revision: str) -> None:
    now = time.monotonic()
    for key, (_rev, at) in list(_agent_writes.items()):
        if now - at > _AGENT_WRITE_TTL:
            del _agent_writes[key]
    _agent_writes[(site_id, rel_path)] = (revision, now)


def take_agent_write(site_id: str, rel_path: str, revision: str) -> bool:
    """Whether `revision` of the page is one an agent wrote (answered once)."""
    entry = _agent_writes.get((site_id, rel_path))
    if entry is None or entry[0] != revision:
        return False
    del _agent_writes[(site_id, rel_path)]
    return True


# --- uploaded assets --------------------------------------------------------------

ASSET_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".avif",
    ".svg",
    ".mp4",
    ".webm",
    ".mov",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".pdf",
    ".glb",
    ".gltf",
    ".csv",
    ".json",
}
MAX_ASSET_BYTES = 512 * 1024 * 1024


def _asset_name(filename: str) -> str:
    stem, _, suffix = Path(filename).name.rpartition(".")
    suffix = f".{suffix.lower()}" if stem else ""
    if suffix not in ASSET_SUFFIXES:
        raise StoreError(f"not an asset type Scrive keeps: {filename!r}")
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:80] or "asset"
    return f"{clean}{suffix}"


def asset_folder(site_id: str, folder: str) -> Path:
    """`folder` under the site — relative, inside it, never `.git` or `.scrive`."""
    base = site_dir(site_id).resolve()
    resolved = (base / folder.strip("/")).resolve()
    if not resolved.is_relative_to(base):
        raise StoreError(f"path escapes the site: {folder}")
    parts = resolved.relative_to(base).parts
    if parts and parts[0] in {".git", ".scrive"}:
        raise StoreError(f"not an asset folder: {folder}")
    return resolved


def save_asset(site_id: str, filename: str, chunks, folder: str = "media") -> str:
    """Store an uploaded file under `folder` and answer its site-relative path.

    Never overwrites: a name already taken gets `-2`, `-3`… — a page elsewhere may
    embed the existing file. Streamed to a temporary file first, so a failed or
    oversized upload leaves nothing behind.
    """
    name = Path(_asset_name(filename))
    target_dir = asset_folder(site_id, folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target_dir), suffix=".scrive-tmp")
    claimed: Path | None = None
    size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_ASSET_BYTES:
                    raise StoreError("file is larger than 512 MB")
                handle.write(chunk)
        candidate, n = target_dir / name, 2
        while claimed is None:
            try:
                # Exclusive create claims the name; the upload then replaces it.
                os.close(os.open(candidate, os.O_CREAT | os.O_EXCL))
                claimed = candidate
            except FileExistsError:
                candidate = target_dir / f"{name.stem}-{n}{name.suffix}"
                n += 1
        replace_with_retry(tmp, claimed)
    except BaseException:
        for leftover in (Path(tmp), claimed):
            try:
                if leftover is not None:
                    leftover.unlink()
            except OSError:
                pass
        raise
    return _rel(site_dir(site_id), claimed)


def delete_page(site_id: str, rel_path: str) -> None:
    path = resolve_page(site_id, rel_path)
    if not path.is_file():
        return
    trash = site_dir(site_id) / ".scrive" / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    replace_with_retry(path, trash / f"{stamp}-{path.name}")
