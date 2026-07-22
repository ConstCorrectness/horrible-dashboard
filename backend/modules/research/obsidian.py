"""Obsidian export: mirror stored research material into a user-configured vault.

The node's artifact store stays the source of truth (see docs/modules/research.mdx);
the vault gets a **readable mirror** — a `.md` note with frontmatter + extracted
text, and the original blob copied under `attachments/` and linked with `![[...]]`.

Paths are plain settings (`research.obsidianVault` / `research.obsidianFolder`) —
they're local directories, not secrets. Everything written is derived from a
sanitized *title*, never from user-controlled path fragments, and a resolve +
`is_relative_to` assertion backstops the sanitizer: nothing escapes the vault.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.modules.artifacts.pdftext import extract_pdf_text_from_path
from backend.modules.artifacts.store import artifact_path
from backend.modules.library.extract import extract_article
from backend.modules.settings.routes import get_value

_EXT_BY_KIND = {"page": "html", "pdf": "pdf", "report": "md"}


class ObsidianNotConfigured(RuntimeError):
    """The vault setting is empty or points at a missing directory."""


def _vault_dir() -> Path:
    vault = str(get_value("research.obsidianVault", "") or "").strip()
    if not vault:
        raise ObsidianNotConfigured(
            "set the research.obsidianVault setting to an Obsidian vault path first"
        )
    path = Path(vault).expanduser()
    if not path.is_dir():
        raise ObsidianNotConfigured(f"Obsidian vault directory not found: {path}")
    return path


def _folder() -> str:
    return str(get_value("research.obsidianFolder", "Horrible Research") or "").strip()


def _safe_stem(title: str) -> str:
    """A Windows-legal, vault-safe filename stem from a title. `[[` / `]]` and `#`
    are also dropped — they're Obsidian link/tag syntax and poison note names."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f#^\[\]]', "", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return cleaned[:120].strip() or "untitled"


def _unique(path: Path) -> Path:
    """First non-existing variant: `name.md`, `name (2).md`, `name (3).md`…"""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"couldn't find a free name for {path.name}")


def _assert_inside(vault: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(vault.resolve()):
        raise RuntimeError(f"refusing to write outside the vault: {path}")


def _frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            # Quote strings that would otherwise be YAML syntax (URLs with ':').
            text = str(value)
            lines.append(f'{key}: "{text}"' if ":" in text else f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines)


def export_source(
    source: dict[str, Any] | None,
    artifact: dict[str, Any],
) -> dict[str, str | None]:
    """Write the note (+ attachment) for a stored artifact into the vault.

    `source` is the library catalog row when the artifact is filed (title/tags/url
    come from it); a bare artifact exports with its own metadata. Returns
    `{"note_path": ..., "attachment_path": ...}` (vault-relative strings).
    """
    vault = _vault_dir()
    folder = vault / _folder()
    attachments = folder / "attachments"
    folder.mkdir(parents=True, exist_ok=True)

    kind = artifact["kind"]
    blob = artifact_path(artifact["id"])
    if blob is None or not blob.is_file():
        raise RuntimeError("artifact blob missing")

    title = (
        (source or {}).get("title")
        or (artifact.get("meta") or {}).get("title")
        or artifact["filename"]
    )
    stem = _safe_stem(str(title))
    url = (source or {}).get("url") or artifact.get("origin_url")
    tags = list((source or {}).get("tags") or [])

    body = _body_for(kind, blob, str(url or ""))

    front = _frontmatter(
        {
            "source": "horrible-dashboard",
            "source_id": (source or {}).get("id"),
            "artifact_id": artifact["id"],
            "url": url,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tags": tags,
            "type": kind,
        }
    )

    attachment_rel: str | None = None
    attachment_line = ""
    if kind != "report":
        # Reports *are* markdown — the note is the artifact. Everything else gets
        # the original blob alongside, so Obsidian can open the real thing.
        attachments.mkdir(parents=True, exist_ok=True)
        ext = _EXT_BY_KIND.get(kind, "bin")
        attachment = _unique(attachments / f"{stem}.{ext}")
        _assert_inside(vault, attachment)
        shutil.copyfile(blob, attachment)
        attachment_rel = str(attachment.relative_to(vault)).replace("\\", "/")
        attachment_line = f"\n![[attachments/{attachment.name}]]\n"

    note = _unique(folder / f"{stem}.md")
    _assert_inside(vault, note)
    note.write_text(
        f"{front}\n\n# {title}\n{attachment_line}\n{body}\n", encoding="utf-8"
    )

    return {
        "note_path": str(note.relative_to(vault)).replace("\\", "/"),
        "attachment_path": attachment_rel,
    }


def _body_for(kind: str, blob: Path, url: str) -> str:
    if kind == "report":
        return blob.read_text(encoding="utf-8", errors="replace")
    if kind == "pdf":
        extracted = extract_pdf_text_from_path(blob)
        return extracted if isinstance(extracted, str) else f"> {extracted['error']}"
    if kind == "page":
        html = blob.read_text(encoding="utf-8", errors="replace")
        return extract_article(html, url).text
    return ""
