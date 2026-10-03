"""Post templates: the *shape* of a page, separate from how it is written.

A template is a MyST file whose frontmatter has a `template:` block — a name, a
description, the page kind and its **inputs** — and whose body is headings with
`{pending}` placeholders saying what belongs under each. `{{input}}` in the body is
replaced by the input's value when a page is made from it. Beside it, an optional
`<id>.skill.md` tells the agent how to write that kind of page well (OpenDesign's
split: the template is the shape, the skill is the craft).

Templates come from two places, and a site's own wins over a built-in of the same id:

- built-in: `backend/modules/scrive/builtin_templates/<id>.md`
- per site: `<site>/templates/<id>.md` — "Save as template" writes here, and Scrive
  never lists that folder as pages.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.modules.scrive import sections, store

BUILTIN_DIR = Path(__file__).parent / "builtin_templates"
SITE_DIR = "templates"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\s*\}\}")


class TemplateInput(BaseModel):
    description: str = ""
    required: bool = False
    default: str = ""


class TemplateInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    kind: str = "post"
    inputs: dict[str, TemplateInput] = Field(default_factory=dict)
    source: str = "builtin"  # 'builtin' | 'site'
    #: The template's section headings, in order — what an outline starts from.
    sections: list[str] = Field(default_factory=list)


class TemplateError(ValueError):
    pass


def _info(template_id: str, text: str, source: str) -> TemplateInfo:
    meta = sections.frontmatter_of(text).get("template")
    meta = meta if isinstance(meta, dict) else {}
    inputs: dict[str, TemplateInput] = {}
    raw_inputs = meta.get("inputs")
    if isinstance(raw_inputs, dict):
        for key, spec in raw_inputs.items():
            spec = spec if isinstance(spec, dict) else {"description": str(spec or "")}
            inputs[str(key)] = TemplateInput(
                description=str(spec.get("description") or ""),
                required=bool(spec.get("required")),
                default="" if spec.get("default") is None else str(spec.get("default")),
            )
    return TemplateInfo(
        id=template_id,
        name=str(meta.get("name") or template_id),
        description=str(meta.get("description") or ""),
        kind="page" if meta.get("kind") == "page" else "post",
        inputs=inputs,
        source=source,
        sections=[h.text for h in sections.headings(text)],
    )


def _dirs(site_id: str | None) -> list[tuple[Path, str]]:
    dirs: list[tuple[Path, str]] = []
    if site_id:
        dirs.append((store.site_dir(site_id) / SITE_DIR, "site"))
    dirs.append((BUILTIN_DIR, "builtin"))
    return dirs


def _files(site_id: str | None) -> dict[str, tuple[Path, str]]:
    found: dict[str, tuple[Path, str]] = {}
    for folder, source in _dirs(site_id):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name.endswith(".skill.md") or not _ID.match(path.stem):
                continue
            found.setdefault(path.stem, (path, source))
    return found


def list_templates(site_id: str | None = None) -> list[TemplateInfo]:
    out = []
    for template_id, (path, source) in _files(site_id).items():
        text = path.read_bytes().decode("utf-8", "replace")
        out.append(_info(template_id, text, source))
    return sorted(out, key=lambda t: (t.source != "site", t.name.lower()))


def get_template(
    site_id: str | None, template_id: str
) -> tuple[TemplateInfo, str, str]:
    """`(info, text, skill)` — the skill is `''` when the template has none."""
    entry = _files(site_id).get(template_id)
    if entry is None:
        have = ", ".join(sorted(_files(site_id))) or "none"
        raise TemplateError(f"no template {template_id!r} (have: {have})")
    path, source = entry
    text = path.read_bytes().decode("utf-8", "replace")
    skill_path = path.with_name(f"{template_id}.skill.md")
    skill = skill_path.read_text(encoding="utf-8") if skill_path.is_file() else ""
    return _info(template_id, text, source), text, skill


def fill_inputs(text: str, info: TemplateInfo, values: dict[str, Any]) -> str:
    """The template's text with `{{input}}` replaced. A required input with no
    value is an error naming every one that is missing."""
    resolved: dict[str, str] = {}
    missing = []
    for key, spec in info.inputs.items():
        value = values.get(key)
        value = "" if value is None else str(value).strip()
        if not value and spec.required:
            missing.append(key)
        resolved[key] = value or spec.default
    if missing:
        detail = "; ".join(f"{k}: {info.inputs[k].description}" for k in missing)
        raise TemplateError(f"template {info.id!r} needs: {detail}")
    for key, value in values.items():
        resolved.setdefault(str(key), "" if value is None else str(value))
    return _PLACEHOLDER.sub(lambda m: resolved.get(m.group(1), m.group(0)), text)


def instantiate(
    site_id: str | None, template_id: str, values: dict[str, Any]
) -> tuple[TemplateInfo, str]:
    """A new page's text from a template: inputs filled, the `template:` block
    removed. The caller sets title, date and status."""
    info, text, _skill = get_template(site_id, template_id)
    text = fill_inputs(text, info, values)
    return info, sections.set_field(text, "template", None)


def save_as_template(
    site_id: str, page_path: str, template_id: str, name: str, description: str = ""
) -> TemplateInfo:
    """Write a page's text as `<site>/templates/<id>.md`, with a `template:` block
    added and the page's own title, date and status removed (a page made from it
    gets its own). Never overwrites an existing template."""
    if not _ID.match(template_id):
        raise TemplateError(
            f"not a template id: {template_id!r} (lowercase letters, digits, dashes)"
        )
    page = store.read_page(site_id, page_path)
    target = store.site_dir(site_id) / SITE_DIR / f"{template_id}.md"
    if target.exists():
        raise FileExistsError(f"{SITE_DIR}/{template_id}.md")
    text = page.content
    for key in ("title", "date", "status", "template"):
        text = sections.set_field(text, key, None)
    kind = "post" if page.meta.kind == "post" else "page"
    meta = {
        "name": name.strip() or template_id,
        "description": description.strip(),
        "kind": kind,
    }
    block = "template:\n" + "".join(
        f"  {k}: {sections.yaml_value(v)}\n" for k, v in meta.items()
    )
    lines = text.split("\n")
    if sections.body_start(lines):
        eol = sections.eol_of(text)
        text = text.replace("---" + eol, "---" + eol + block.replace("\n", eol), 1)
    else:
        text = f"---\n{block}---\n\n{text}"
    store.write_bytes_atomic(target, text.encode("utf-8"))
    return _info(template_id, text, "site")
