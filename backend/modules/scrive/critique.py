"""A page's review pass: the things an editor would flag before publishing.

Deterministic and cheap, so the agent can run it after filling a page and fix what
it finds, and the page pane can show the same list to a person. It reads the text
the way `sections.py` does (top-level lines, fences skipped) and never needs the
full MyST parser.

Rules, from most to least serious:

- **error** — a relative link, image, figure or scene that points at nothing.
- **warning** — `{pending}` placeholders left, an image with no alt text, a heading
  that skips a level.
- **info** — an equation no sentence explains, a sentence that states a figure or a
  finding with no link or citation near it, a very long section, a post with no
  description, a code block with no language.

The claim and math rules are heuristics and say so in their messages: they point a
reviewer at a paragraph, they do not judge it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel

from backend.modules.scrive import sections

Severity = Literal["error", "warning", "info"]


class Finding(BaseModel):
    line: int  # 1-based, in the whole file
    severity: Severity
    rule: str
    message: str


_LINK = re.compile(r"(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_PATH_DIRECTIVES = {"figure", "image", "r3f", "video", "include", "literalinclude"}
_CLAIM = re.compile(
    r"\b\d+(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?x\b|\b\d+(?:\.\d+)?\s+times\s+(?:faster|slower|more|less|larger|smaller)\b"
    r"|\b(?:studies|research|surveys?|experts?|scientists)\s+(?:show|shows|found|find|suggest|suggests|say)\b"
    r"|\baccording to\b|\bon average\b|\bmost people\b",
    re.I,
)
_SOURCE = re.compile(r"\]\(|https?://|\{cite[:\w]*\}|\[@|<https?://|\{ref\}|\{doc\}")
_LONG_SECTION_WORDS = 700


def _external(target: str) -> bool:
    if target.startswith(("#", "mailto:", "tel:", "data:")):
        return True
    parts = urlsplit(target)
    return bool(parts.scheme or parts.netloc)


def _exists(site_root: Path, page_rel: str, target: str) -> bool:
    path = unquote(urlsplit(target).path)
    if not path:
        return True
    base = site_root if path.startswith("/") else (site_root / page_rel).parent
    try:
        resolved = (base / path.lstrip("/")).resolve()
    except OSError:
        return False
    return resolved.is_relative_to(site_root.resolve()) and resolved.exists()


def critique(
    text: str, site_root: Path | None = None, page_rel: str = ""
) -> list[Finding]:
    found: list[Finding] = []
    lines = text.split("\n")
    bare = [line.rstrip("\r") for line in lines]
    start = sections.body_start(lines)
    fm = sections.frontmatter_of(text)

    def add(index: int, severity: Severity, rule: str, message: str) -> None:
        found.append(
            Finding(line=index + 1, severity=severity, rule=rule, message=message)
        )

    # Placeholders.
    for p in sections.pending_blocks(text):
        where = f"under {p.heading!r}" if p.heading else "in the lead"
        add(p.start, "warning", "pending", f"Still to write {where}: {p.intent[:120]}")

    # Frontmatter a published post needs.
    if page_rel.startswith("posts/"):
        if not str(fm.get("description") or "").strip():
            add(
                0,
                "info",
                "description",
                "No description: link cards and the post list will be empty.",
            )
        if not fm.get("tags"):
            add(0, "info", "tags", "No tags.")

    # Headings.
    heads = sections.headings(text)
    previous = 1
    for h in heads:
        if h.level == 1 and fm.get("title"):
            add(
                h.line,
                "info",
                "heading-h1",
                "A `#` heading repeats the title, which the frontmatter already sets; start at `##`.",
            )
        if h.level > previous + 1:
            add(
                h.line,
                "warning",
                "heading-skip",
                f"Heading jumps from level {previous} to {h.level}.",
            )
        previous = h.level
    for i, h in enumerate(heads):
        end = heads[i + 1].line if i + 1 < len(heads) else len(lines)
        nested = (
            i + 1 < len(heads)
            and heads[i + 1].level > h.level
            and heads[i + 1].line < end
        )
        words = sum(len(bare[j].split()) for j in range(h.line + 1, end))
        if words > _LONG_SECTION_WORDS and not nested:
            add(
                h.line,
                "info",
                "long-section",
                f"Section {h.text!r} runs {words} words with no subheading.",
            )

    # Walk the top-level blocks: paragraphs, fences and their options.
    block: list[int] = []
    fence_start: int | None = None
    fence_name = ""
    fence_lines: list[int] = []
    math_blocks: list[tuple[int, int]] = []  # (first line, last line)
    in_dollars: int | None = None

    def end_paragraph() -> None:
        if not block:
            return
        para = " ".join(bare[i] for i in block)
        if _CLAIM.search(para) and not _SOURCE.search(para):
            add(
                block[0],
                "info",
                "uncited",
                "Possible claim without a source (a figure or finding with no link or citation nearby).",
            )
        block.clear()

    def end_fence(close_index: int) -> None:
        nonlocal fence_start
        if fence_start is None:
            return
        opener = bare[fence_start]
        options = {
            m.group(1): m.group(2).strip()
            for i in fence_lines
            if (m := re.match(r"^\s*:([\w-]+):\s*(.*)$", bare[i]))
        }
        if fence_name in ("figure", "image") and not options.get("alt"):
            add(
                fence_start,
                "warning",
                "alt-text",
                f"{{{fence_name}}} has no :alt: text.",
            )
        if fence_name in _PATH_DIRECTIVES and site_root is not None:
            arg = re.sub(r"^\s*[`~:]{3,}\s*\{[\w:-]+\}\s*", "", opener).strip()
            if arg and not _external(arg) and not _exists(site_root, page_rel, arg):
                add(
                    fence_start,
                    "error",
                    "dead-link",
                    f"{{{fence_name}}} points at {arg!r}, which does not exist.",
                )
        if fence_name == "math":
            math_blocks.append((fence_start, close_index))
        if not fence_name and re.match(r"^\s*(`{3,}|~{3,})\s*$", opener):
            add(
                fence_start,
                "info",
                "code-language",
                "Code block with no language: it will not be highlighted.",
            )
        fence_start = None

    for i, depth, opened in sections.scan_fences(lines, start):
        line = bare[i]
        if fence_start is not None:
            if depth == 0:
                end_fence(i)
            else:
                fence_lines.append(i)
            continue
        if in_dollars is not None:
            if line.strip().endswith("$$"):
                math_blocks.append((in_dollars, i))
                in_dollars = None
            continue
        if opened is not None and depth == 0:
            end_paragraph()
            fence_start, fence_name, fence_lines = i, opened.name, []
            continue
        if line.strip().startswith("$$"):
            end_paragraph()
            if (
                line.strip() != "$$"
                and line.strip().endswith("$$")
                and len(line.strip()) > 4
            ):
                math_blocks.append((i, i))
            else:
                in_dollars = i
            continue
        if not line.strip() or sections.HEADING_RE.match(line):
            end_paragraph()
            continue
        block.append(i)
        for m in _LINK.finditer(line):
            image, alt, target = m.group(1), m.group(2), m.group(3)
            if image and not alt.strip():
                add(i, "warning", "alt-text", f"Image {target!r} has no alt text.")
            if (
                site_root is not None
                and not _external(target)
                and not _exists(site_root, page_rel, target)
            ):
                kind = "Image" if image else "Link"
                add(
                    i,
                    "error",
                    "dead-link",
                    f"{kind} points at {target!r}, which does not exist.",
                )
    end_paragraph()

    # An equation should be introduced or explained by prose next to it.
    for first, last in math_blocks:
        after = next((j for j in range(last + 1, len(bare)) if bare[j].strip()), None)
        before = next(
            (j for j in range(first - 1, start - 1, -1) if bare[j].strip()), None
        )

        def prose(j: int | None) -> bool:
            if j is None:
                return False
            s = bare[j].strip()
            return not (
                sections.HEADING_RE.match(s)
                or s.startswith(("$$", ":::", "```", "~~~"))
            )

        if not prose(after) and not prose(before):
            add(
                first,
                "info",
                "unexplained-math",
                "An equation with no sentence before or after it to explain its symbols.",
            )

    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda f: (order[f.severity], f.line))
