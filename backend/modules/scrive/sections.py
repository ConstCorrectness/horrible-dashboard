"""Edit a MyST page as text, by section — the agent's side of Scrive.

The agent reads and writes MyST text, never editor JSON (Notion's lesson: a model
writes good Markdown and bad block trees). These are the operations its tools are
built from, and they follow the editor's rule: **every byte the edit does not
touch is left alone**. A hand-written file keeps its spacing, its line endings and
its comments, and its git diff is exactly the section that changed.

Text is handled as lines that keep their own `\\r`, split on `\\n` only, so a CRLF
file stays CRLF and a mixed one stays mixed; new lines take the file's dominant
ending.

A **heading** is an ATX heading (`## Title`) outside any fence — a backtick or
tilde code fence, or a MyST colon fence (`:::{note}`). Headings inside a directive
belong to that directive, not to the page's outline. A **section** runs from its
heading to the next heading of the same or a higher level.

A **placeholder** is a `{pending}` directive: what a template or an approved outline
leaves under a heading for the agent to fill. Its body says what belongs there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml


class EditError(ValueError):
    """An edit that cannot be applied as asked. The message is for the agent: it
    says what was wrong and what exists instead."""


HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+$|^#+$")
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_COLON_FENCE = re.compile(r"^ {0,3}(:{3,})(.*)$")
_DIRECTIVE = re.compile(r"^\s*\{([A-Za-z][\w:-]*)\}")


def eol_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _lines(text: str) -> list[str]:
    """`text` split on `\\n`; each line keeps its `\\r`. The last item is what follows
    the final newline (`''` for a file that ends with one)."""
    return text.split("\n")


def _bare(line: str) -> str:
    return line[:-1] if line.endswith("\r") else line


def body_start(lines: list[str]) -> int:
    """Index of the first line after the frontmatter (0 when there is none)."""
    if not lines or not re.match(r"^---[ \t]*$", _bare(lines[0])):
        return 0
    for i in range(1, len(lines)):
        if re.match(r"^---[ \t]*$", _bare(lines[i])):
            return i + 1
    return 0


@dataclass
class Fence:
    char: str  # '`', '~' or ':'
    length: int
    name: str  # the directive name, '' for a plain code fence
    start: int  # line index of the opening fence


def scan_fences(lines: list[str], start: int = 0):
    """Yield `(index, depth, opened)` for each line from `start`: `depth` is how many
    fences enclose the line (0 = top level), `opened` the fence the line opens, if
    any. A backtick fence hides everything inside it, colon fences included."""
    stack: list[Fence] = []
    for i in range(start, len(lines)):
        line = _bare(lines[i])
        top = stack[-1] if stack else None
        if top and top.char in "`~":
            m = _CODE_FENCE.match(line)
            if (
                m
                and m.group(1)[0] == top.char
                and len(m.group(1)) >= top.length
                and not m.group(2).strip()
            ):
                stack.pop()
            yield i, len(stack), None
            continue
        m = _CODE_FENCE.match(line)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            name = _DIRECTIVE.match(m.group(2))
            fence = Fence(
                m.group(1)[0], len(m.group(1)), name.group(1) if name else "", i
            )
            yield i, len(stack), fence
            stack.append(fence)
            continue
        m = _COLON_FENCE.match(line)
        if m:
            info = m.group(2).strip()
            if not info and top and top.char == ":" and len(m.group(1)) >= top.length:
                stack.pop()
                yield i, len(stack), None
                continue
            name = _DIRECTIVE.match(info)
            if name:
                fence = Fence(":", len(m.group(1)), name.group(1), i)
                yield i, len(stack), fence
                stack.append(fence)
                continue
        yield i, len(stack), None


@dataclass
class Heading:
    line: int  # index into the page's lines
    level: int
    text: str


def _heading_text(raw: str) -> str:
    return _CLOSING_HASHES.sub("", raw.strip()).strip()


def headings(text: str) -> list[Heading]:
    """The page's outline: top-level ATX headings, in order."""
    lines = _lines(text)
    out: list[Heading] = []
    for i, depth, opened in scan_fences(lines, body_start(lines)):
        if depth or opened:
            continue
        m = HEADING_RE.match(_bare(lines[i]))
        if m:
            out.append(Heading(i, len(m.group(1)), _heading_text(m.group(2) or "")))
    return out


def _norm(heading: str) -> str:
    heading = re.sub(r"^\s*#+\s*", "", heading)
    heading = re.sub(r"[*_`]", "", heading)
    return re.sub(r"\s+", " ", heading).strip().lower()


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(heading)).strip("-")


def find_heading(text: str, heading: str, occurrence: int = 1) -> Heading:
    """The heading named `heading` — matched ignoring case, `#` marks, emphasis and
    spacing, then by slug. An ambiguous or missing name is an error that lists what
    the page has, so the agent's retry can be right."""
    all_headings = headings(text)
    want = _norm(heading)
    matches = [h for h in all_headings if _norm(h.text) == want]
    if not matches:
        matches = [h for h in all_headings if _slug(h.text) == _slug(heading)]
    if not matches:
        have = ", ".join(repr(h.text) for h in all_headings) or "no headings"
        raise EditError(f"no heading {heading!r} on this page (it has: {have})")
    if occurrence < 1 or occurrence > len(matches):
        raise EditError(
            f"heading {heading!r} occurs {len(matches)} time(s); occurrence {occurrence} is out of range"
        )
    return matches[occurrence - 1]


def section_span(text: str, heading: Heading) -> tuple[int, int]:
    """`(start, end)` line indices of `heading`'s section: its heading line through
    the line before the next heading of the same or a higher level."""
    lines = _lines(text)
    end = len(lines)
    for h in headings(text):
        if h.line > heading.line and h.level <= heading.level:
            end = h.line
            break
    # A file ending in a newline has a final '' item; it belongs to no section.
    if end == len(lines) and lines and lines[-1] == "":
        end -= 1
    return heading.line, end


def read_section(text: str, heading: str, occurrence: int = 1) -> str:
    lines = _lines(text)
    start, end = section_span(text, find_heading(text, heading, occurrence))
    return "\n".join(_bare(line) for line in lines[start:end]).rstrip("\n") + "\n"


# --- splicing ---------------------------------------------------------------------


def _new_lines(block: str, eol: str) -> list[str]:
    """Agent text as lines in the file's line ending, trimmed of blank edges."""
    block = block.replace("\r\n", "\n").strip("\n")
    cr = "\r" if eol == "\r\n" else ""
    return [line + cr for line in block.split("\n")] if block else []


def _blank(line: str) -> bool:
    return not _bare(line).strip()


def _splice(text: str, start: int, end: int, block: str) -> str:
    """Replace lines `[start, end)` with `block`, keeping exactly one blank line
    between it and any neighbour. Lines outside the range are untouched; only blank
    lines at the seams are added or dropped."""
    lines = _lines(text)
    eol = eol_of(text)
    blank = "\r" if eol == "\r\n" else ""
    new = _new_lines(block, eol)
    # Absorb the blank lines at both seams; exactly one is put back on each side.
    floor = body_start(lines)
    while start > floor and _blank(lines[start - 1]):
        start -= 1
    # A file ending in a newline has a final '' item: it is the ending, not a line.
    final = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    while end < final and _blank(lines[end]):
        end += 1
    before, after = lines[:start], lines[end:]
    content_after = any(item != "" for item in after) or len(after) > 1
    out = list(before)
    if new:
        if before:
            out.append(blank)
        out += new
    if content_after and (new or before):
        out.append(blank)
    out += after
    joined = "\n".join(out)
    if text.endswith("\n") and not joined.endswith("\n"):
        joined += eol
    return joined


def replace_section(text: str, heading: str, block: str, occurrence: int = 1) -> str:
    """Replace a whole section — heading, body and subsections. When `block` does
    not start with a heading, the section keeps its own heading line and `block`
    becomes its body."""
    found = find_heading(text, heading, occurrence)
    start, end = section_span(text, found)
    if not HEADING_RE.match(block.lstrip("\r\n").split("\n", 1)[0]):
        lines = _lines(text)
        block = _bare(lines[start]) + "\n\n" + block.strip("\r\n")
    return _splice(text, start, end, block)


def delete_section(text: str, heading: str, occurrence: int = 1) -> str:
    start, end = section_span(text, find_heading(text, heading, occurrence))
    return _splice(text, start, end, "")


def _block_end(lines: list[str], index: int) -> int:
    """The line after the block containing line `index`. Inside a fence that is the
    line after the outermost fence's closing line — inserting *into* a directive is
    never what an anchor means; at the top level it is the next blank line, with any
    fence the block opens skipped whole."""
    info = {
        i: (depth, opened) for i, depth, opened in scan_fences(lines, body_start(lines))
    }
    depth = lambda i: info.get(i, (0, None))[0]  # noqa: E731
    j = index
    if depth(index) > 0:
        while j < len(lines) and depth(j) > 0:
            j += 1
        return min(j + 1, len(lines))
    while j < len(lines) and not _blank(lines[j]):
        if info.get(j, (0, None))[1] is not None:
            j += 1
            while j < len(lines) and depth(j) > 0:
                j += 1
        j += 1
    return j


def _find_once(text: str, needle: str) -> int:
    if not needle:
        raise EditError("empty text to find")
    count = text.count(needle)
    if count == 0:
        raise EditError(
            f"text not found: {needle[:80]!r} — it must match the page exactly; read the page again"
        )
    if count > 1:
        raise EditError(
            f"text occurs {count} times: {needle[:80]!r} — include more of the surrounding text"
        )
    return text.index(needle)


def insert(
    text: str,
    block: str,
    *,
    after_heading: str | None = None,
    before_heading: str | None = None,
    after_text: str | None = None,
    at: str | None = None,
) -> str:
    """Insert `block` as new blocks: at the end of a section, before a heading,
    after the block that contains some text, or at the start / end of the page."""
    lines = _lines(text)
    if after_heading:
        _, end = section_span(text, find_heading(text, after_heading))
        return _splice(text, end, end, block)
    if before_heading:
        start = find_heading(text, before_heading).line
        return _splice(text, start, start, block)
    if after_text:
        offset = _find_once(text, after_text) + len(after_text) - 1
        line = text.count("\n", 0, max(offset, 0))
        end = _block_end(lines, line)
        return _splice(text, end, end, block)
    if at == "start":
        start = body_start(lines)
        return _splice(text, start, start, block)
    if at in (None, "end"):
        end = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        return _splice(text, end, end, block)
    raise EditError(
        f"insert needs after_heading, before_heading, after_text or at; got at={at!r}"
    )


def replace_text(
    text: str, find: str, replacement: str, *, all_occurrences: bool = False
) -> str:
    """Replace an exact piece of the page. It must occur exactly once unless
    `all_occurrences`. The replacement's line endings follow the file's."""
    replacement = replacement.replace("\r\n", "\n")
    if eol_of(text) == "\r\n":
        replacement = replacement.replace("\n", "\r\n")
        find = find.replace("\r\n", "\n").replace("\n", "\r\n")
    if all_occurrences:
        if find not in text:
            _find_once(text, find)
        return text.replace(find, replacement)
    index = _find_once(text, find)
    return text[:index] + replacement + text[index + len(find) :]


# --- frontmatter ------------------------------------------------------------------


def yaml_value(value: Any) -> str:
    """One line of YAML for `value` — a string quoted only when it must be, a list
    in flow style."""
    return (
        yaml.safe_dump(value, default_flow_style=True, width=10_000, allow_unicode=True)
        .strip()
        .splitlines()[0]
    )


def set_field(text: str, key: str, value: Any) -> str:
    """The page with one top-level frontmatter key set (or removed, for `None`),
    editing only that key's lines — the Python twin of `myst/frontmatter.ts`."""
    if not re.match(r"^[A-Za-z_][\w-]*$", key):
        raise EditError(f"not a frontmatter key: {key!r}")
    lines = _lines(text)
    eol = eol_of(text)
    cr = "\r" if eol == "\r\n" else ""
    line = None if value is None else f"{key}: {yaml_value(value)}{cr}"
    end = body_start(lines)
    if end == 0:
        if line is None:
            return text
        fresh = f"---{eol}{key}: {yaml_value(value)}{eol}---{eol}"
        return fresh + (eol + text.lstrip("\r\n") if text.strip() else "")
    close = end - 1
    key_line = re.compile(rf"^{re.escape(key)}[ \t]*:")
    start = next((i for i in range(1, close) if key_line.match(lines[i])), None)
    if start is None:
        if line is None:
            return text
        lines.insert(close, line)
        return "\n".join(lines)
    stop = start + 1
    while stop < close and (
        re.match(r"^[ \t]", lines[stop]) or re.match(r"^-(?:[ \t]|\r?$)", lines[stop])
    ):
        stop += 1
    lines[start:stop] = [] if line is None else [line]
    return "\n".join(lines)


def frontmatter_of(text: str) -> dict[str, Any]:
    lines = _lines(text)
    end = body_start(lines)
    if not end:
        return {}
    try:
        parsed = yaml.safe_load("\n".join(_bare(x) for x in lines[1 : end - 1]))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --- placeholders -------------------------------------------------------------------


@dataclass
class Pending:
    heading: str  # the section it sits in ('' before the first heading)
    start: int  # line of the opening fence
    end: int  # line after the closing fence
    intent: str  # the directive's body: what belongs here


def pending_blocks(text: str) -> list[Pending]:
    """Every top-level `{pending}` placeholder, in order, with the section it is in."""
    lines = _lines(text)
    found: list[Pending] = []
    current = ""
    open_fence: Fence | None = None
    for i, depth, opened in scan_fences(lines, body_start(lines)):
        if open_fence is not None:
            if depth == 0 and i > open_fence.start:
                body = [
                    _bare(x)
                    for x in lines[open_fence.start + 1 : i]
                    if not re.match(r"^\s*:[\w-]+:", _bare(x))
                ]
                found.append(
                    Pending(current, open_fence.start, i + 1, "\n".join(body).strip())
                )
                open_fence = None
            continue
        if depth == 0 and opened is None:
            m = HEADING_RE.match(_bare(lines[i]))
            if m:
                current = _heading_text(m.group(2) or "")
        if depth == 0 and opened is not None and opened.name == "pending":
            open_fence = opened
    return found


def fill_pending(text: str, heading: str, block: str) -> str:
    """Replace the placeholder under `heading` with `block`. Refuses when the section
    has none — the author may already have written it, and a fill must never
    overwrite a person's words.

    `heading` `''` (or `lead`) names the **lead**: the text before the first heading,
    where a post's opening paragraph goes."""
    if _norm(heading) in ("", "lead", "(lead)"):
        name = "the lead"
        lines = _lines(text)
        first = headings(text)
        start, end = body_start(lines), (first[0].line if first else len(lines))
    else:
        target = find_heading(text, heading)
        name = repr(target.text)
        start, end = section_span(text, target)
    for p in pending_blocks(text):
        if start <= p.start < end:
            if not block.strip():
                raise EditError(
                    "empty content: write the section, or leave the placeholder"
                )
            return _splice(text, p.start, p.end, block)
    raise EditError(
        f"{name} has no {{pending}} placeholder left — it is already written. "
        "To change it, use scrive.editPage."
    )


def pending_directive(intent: str, fence: str = ":::") -> str:
    intent = intent.strip() or "Write this section."
    return f"{fence}{{pending}}\n{intent}\n{fence}"


# --- the edit tool's ops ------------------------------------------------------------


def apply_ops(text: str, ops: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Apply `ops` in order, all or nothing. Answers the new text and a one-line
    summary per op. Raises `EditError` naming the op that failed."""
    summary: list[str] = []
    for n, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            raise EditError(f"op {n} is not an object")
        kind = str(op.get("op") or "")
        try:
            if kind == "replaceSection":
                text = replace_section(
                    text,
                    str(op.get("heading") or ""),
                    str(op.get("text") or ""),
                    int(op.get("occurrence") or 1),
                )
                summary.append(f"replaced section {op.get('heading')!r}")
            elif kind == "deleteSection":
                text = delete_section(
                    text, str(op.get("heading") or ""), int(op.get("occurrence") or 1)
                )
                summary.append(f"deleted section {op.get('heading')!r}")
            elif kind == "insert":
                text = insert(
                    text,
                    str(op.get("text") or ""),
                    after_heading=op.get("after_heading"),
                    before_heading=op.get("before_heading"),
                    after_text=op.get("after_text"),
                    at=op.get("at"),
                )
                summary.append("inserted text")
            elif kind == "replaceText":
                text = replace_text(
                    text,
                    str(op.get("find") or ""),
                    str(op.get("text") or ""),
                    all_occurrences=bool(op.get("all")),
                )
                summary.append("replaced text")
            elif kind == "setFrontmatter":
                text = set_field(text, str(op.get("key") or ""), op.get("value"))
                summary.append(f"set {op.get('key')}")
            else:
                raise EditError(
                    f"unknown op {kind!r} (replaceSection, deleteSection, insert, replaceText, setFrontmatter)"
                )
        except EditError as exc:
            raise EditError(f"op {n} ({kind or '?'}): {exc}") from None
    return text, summary
