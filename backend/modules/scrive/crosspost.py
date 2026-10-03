"""A page's MyST, rewritten as the Markdown dev.to or Hashnode will render.

Cross-posting sends the whole article, not a link card, so the body has to survive a
Markdown dialect that knows nothing of MyST. `to_markdown` keeps what has an equivalent
and says what it dropped (`Converted.notes`, shown with the draft):

| MyST                          | dev.to                         | Hashnode                     |
| ----------------------------- | ------------------------------ | ---------------------------- |
| admonition (`{note}` …)       | blockquote with a bold title   | the same                     |
| `{dropdown}`, `:class: dropdown` | `{% details %}` liquid tag  | `<details>`                  |
| `$…$`, `$$…$$`, `{math}`      | `{% katex %}` liquid tags      | `$…$`, `$$…$$`               |
| `{figure}`, `{image}`         | image + italic caption         | the same                     |
| `{code-cell}`, `{code}`       | fenced code (outputs dropped)  | the same                     |
| `{iframe}` (YouTube, …)       | `{% embed %}`                  | `%[url]`                     |
| `{r3f}`, `{video}`            | a link to the page / the file  | the same                     |
| `{tab-set}`, `{card}`, `{grid}` | flattened, labels in bold    | the same                     |

Rules that are quiet if wrong:

- **Every site link is absolute.** A relative image or page link means nothing on
  dev.to. Site paths become `{{site.url}}<path>` (a page: its published directory),
  which approval fills from the publish record, exactly as `{{post.url}}` is.
- **Headings move down a level when the body has an `#`**: the title is the article's
  only h1 on both platforms.
- **Only text outside code is rewritten.** Fenced code and inline code spans pass
  through byte for byte; a `$` in a shell snippet is not math.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import unquote

from backend.modules.scrive.sections import _bare, _lines, body_start, scan_fences

Flavor = Literal["devto", "hashnode"]

#: Filled at approval with the published site's root URL (ends in `/`).
SITE_URL = "{{site.url}}"
#: Filled at approval with the page's own published URL.
POST_URL = "{{post.url}}"

ADMONITIONS = {
    "note",
    "tip",
    "hint",
    "important",
    "warning",
    "caution",
    "attention",
    "danger",
    "error",
    "seealso",
    "admonition",
}
_QUOTES = {"epigraph", "pull-quote", "blockquote", "margin", "sidebar", "aside"}
_CODE = {"code", "code-block", "sourcecode", "code-cell"}
_FLATTEN = {"grid", "glossary", "div", "container"}
_DROP = {"toctree", "tableofcontents", "include", "literalinclude", "bibliography"}

_ROLE = re.compile(r"\{([A-Za-z][\w:-]*)\}(`+)(.+?)\2")
_LINK = re.compile(r"(!?\[[^\]\n]*\]\()(\s*<?)([^)\s>]+)(>?(?:\s+\"[^\"]*\")?\s*\))")
#: A reference definition (`[docs]: url`), not a footnote (`[^fn]: text`).
_REF_DEF = re.compile(r"^( {0,3}\[(?!\^)[^\]]+\]:\s*<?)(\S+?)(>?(?:\s.*)?)$")
_HTML_SRC = re.compile(
    r"(<(?:img|a|video|source)\b[^>]*?\s(?:src|href)=)([\"'])([^\"']+)\2"
)
_LABEL = re.compile(r"^\s*\(([^()\s]+)\)=\s*$")
_HEADING = re.compile(r"^( {0,3})(#{1,6})(?=[ \t]|$)(.*)$")
_OPTION = re.compile(r"^\s*:([\w-]+):\s?(.*)$")
_CODE_SPAN = re.compile(r"(`+)(?:.+?)\1", re.S)
#: MyST's dollarmath: `$` not followed by a space opens, `$` not preceded by a space
#: and not followed by a digit closes — so "$5 and $10" is not math.
_INLINE_MATH = re.compile(r"(?<![\\$])\$(?=[^\s$])([^$\n]+?)(?<=[^\s\\])\$(?![\d$])")
_YOUTUBE = re.compile(
    r"^https?://(?:www\.)?(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=)|youtu\.be/)"
    r"([\w-]{6,})"
)


@dataclass
class Converted:
    markdown: str
    #: What did not carry over, once each, in the order first met.
    notes: list[str] = field(default_factory=list)


@dataclass
class _Directive:
    name: str
    argument: str
    options: dict[str, str]
    body: list[str]


class _Converter:
    def __init__(self, page: str, flavor: Flavor, labels: dict[str, str]) -> None:
        self.page = page
        self.flavor = flavor
        self.labels = labels
        self.notes: list[str] = []

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    # --- URLs -------------------------------------------------------------------

    def url(self, target: str) -> str:
        """A link target as the platform needs it: site paths made absolute."""
        if re.match(r"^[a-z][a-z0-9+.-]*:|^//|^#|^\{\{", target, re.I):
            return target
        path, _, anchor = target.partition("#")
        if not path:
            return target
        if path.startswith("/"):
            rel = path.lstrip("/")
        else:
            rel = str(PurePosixPath(self.page).parent / path)
        parts: list[str] = []
        for part in rel.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return target  # climbs out of the site: not ours to rewrite
                parts.pop()
                continue
            parts.append(unquote(part))
        rel = "/".join(parts)
        if re.search(r"\.(md|ipynb)$", rel, re.I):
            from backend.modules.scrive.social import page_dir

            out = SITE_URL + page_dir(rel)
        else:
            out = SITE_URL + rel
        return f"{out}#{anchor}" if anchor else out

    # --- inline -----------------------------------------------------------------

    def inline(self, text: str) -> str:
        """Roles, math and link targets in running text; code spans untouched."""
        out: list[str] = []
        last = 0
        for span in _CODE_SPAN.finditer(text):
            # A role's own backticks are part of the role, not a code span.
            if span.start() > 0 and text[span.start() - 1] == "}":
                continue
            out.append(self._inline_plain(text[last : span.start()]))
            out.append(span.group(0))
            last = span.end()
        out.append(self._inline_plain(text[last:]))
        return "".join(out)

    def _inline_plain(self, text: str) -> str:
        text = _ROLE.sub(self._role, text)
        text = _INLINE_MATH.sub(lambda m: self.inline_math(m.group(1)), text)
        text = _LINK.sub(
            lambda m: m.group(1) + m.group(2) + self.url(m.group(3)) + m.group(4), text
        )
        text = _HTML_SRC.sub(
            lambda m: m.group(1) + m.group(2) + self.url(m.group(3)) + m.group(2), text
        )
        return text

    def inline_math(self, tex: str) -> str:
        if self.flavor == "devto":
            return "{% katex inline %}" + tex + "{% endkatex %}"
        return f"${tex}$"

    def _role(self, m: re.Match[str]) -> str:
        name, content = m.group(1), m.group(3)
        text, target = _split_target(content)
        if name == "math":
            return self.inline_math(content)
        if name == "kbd":
            return f"<kbd>{content}</kbd>"
        if name in ("sub", "subscript"):
            return f"<sub>{content}</sub>"
        if name in ("sup", "superscript"):
            return f"<sup>{content}</sup>"
        if name in ("doc", "download"):
            return f"[{text or target}]({self.url(target)})"
        if name in ("ref", "numref"):
            label = self.labels.get(target)
            return text or label or target
        if name == "eq":
            return text or "the equation"
        if name in ("cite", "cite:p", "cite:t", "cite:ps", "cite:ts"):
            self.note("Citations ({cite}) are kept as their keys in brackets.")
            return f"[{content}]"
        if name == "term":
            return text or target
        if name == "abbr":
            return content
        return text or content

    # --- blocks -----------------------------------------------------------------

    def block(self, lines: list[str]) -> list[str]:
        """Convert a run of lines (a page body, or a directive's body)."""
        out: list[str] = []
        opened_at: int | None = None
        fence_line = ""
        inner: list[str] = []
        in_math = False
        for i, depth, opened in scan_fences(lines):
            line = _bare(lines[i])
            if opened_at is not None:
                if depth == 0 and opened is None:
                    # The closing fence of the block we are collecting.
                    out += self.fenced(fence_line, inner)
                    opened_at = None
                    inner = []
                else:
                    inner.append(line)
                continue
            if opened is not None and depth == 0:
                opened_at, fence_line, inner = i, line, []
                continue
            if in_math:
                if line.strip() == "$$":
                    out.append(self.display_math_close())
                    in_math = False
                else:
                    out.append(line)
                continue
            stripped = line.strip()
            if stripped == "$$":
                out.append(self.display_math_open())
                in_math = True
                continue
            one = re.match(r"^\s*\$\$(.+)\$\$\s*$", line)
            if one:
                out += [
                    self.display_math_open(),
                    one.group(1).strip(),
                    self.display_math_close(),
                ]
                continue
            if _LABEL.match(line) or stripped.startswith("+++"):
                continue
            if re.match(r"^\s*%", line):
                continue  # a MyST comment
            ref = _REF_DEF.match(line)
            if ref:
                out.append(ref.group(1) + self.url(ref.group(2)) + ref.group(3))
                continue
            definition = re.match(r"^:\s+(.*)$", line)
            if definition and out and out[-1].strip():
                # A definition list ("Term\n: its definition"): neither platform has one.
                out[-1] = f"**{out[-1].strip()}**"
                out.append("")
                out.append(self.inline(definition.group(1)))
                continue
            out.append(self.inline(line))
        if opened_at is not None:
            # An unclosed fence runs to the end, as CommonMark reads it.
            out += self.fenced(fence_line, inner)
        if in_math:
            out.append(self.display_math_close())
        return out

    def display_math_open(self) -> str:
        return "{% katex %}" if self.flavor == "devto" else "$$"

    def display_math_close(self) -> str:
        return "{% endkatex %}" if self.flavor == "devto" else "$$"

    def fenced(self, fence_line: str, inner: list[str]) -> list[str]:
        m = re.match(r"^( {0,3})(`{3,}|~{3,}|:{3,})(.*)$", fence_line)
        assert m is not None
        info = m.group(3).strip()
        name = re.match(r"^\{([A-Za-z][\w:-]*)\}\s*(.*)$", info)
        if not name:
            # Plain code: through untouched (the fence keeps its own length).
            return [fence_line, *inner, m.group(1) + m.group(2)]
        directive = _parse_directive(name.group(1), name.group(2), inner)
        return self.directive(directive)

    def directive(self, d: _Directive) -> list[str]:
        name = d.name
        if name in ADMONITIONS:
            title = d.argument if d.argument else _title(name)
            if "dropdown" in d.options.get("class", "").split():
                return self.details(title, d.body)
            body = self.block(d.body)
            return [f"> **{self.inline(title)}**", ">", *_quote(body), ""]
        if name in ("dropdown", "details"):
            return self.details(d.argument or "Details", d.body)
        if name in _QUOTES:
            return [*_quote(self.block(d.body)), ""]
        if name in _CODE:
            if name == "code-cell":
                self.note(
                    "Code cells are sent as code; their outputs stay on the site."
                )
            lang = d.argument.split()[0] if d.argument else ""
            fence = (
                "````"
                if any(line.lstrip().startswith("```") for line in d.body)
                else "```"
            )
            return [f"{fence}{lang}", *d.body, fence, ""]
        if name == "math":
            return [self.display_math_open(), *d.body, self.display_math_close(), ""]
        if name in ("figure", "image"):
            alt = d.options.get("alt", "")
            caption = self.block(d.body) if name == "figure" else []
            if not alt and caption:
                alt = re.sub(
                    r"[*_`\[\]]", "", " ".join(c for c in caption if c)
                ).strip()
            out = [f"![{alt}]({self.url(d.argument)})", ""]
            caption = [c for c in caption if c.strip()]
            if caption:
                out += [f"*{' '.join(c.strip() for c in caption)}*", ""]
            return out
        if name == "table":
            out = []
            if d.argument:
                out += [f"*{self.inline(d.argument)}*", ""]
            return [*out, *self.block(d.body), ""]
        if name == "mermaid":
            self.note(
                "Mermaid diagrams go as ```mermaid code blocks"
                + ("; dev.to shows them as code." if self.flavor == "devto" else ".")
            )
            return ["```mermaid", *d.body, "```", ""]
        if name == "iframe":
            return [self.embed(d.argument), ""]
        if name == "youtube":
            return [self.embed(f"https://www.youtube.com/watch?v={d.argument}"), ""]
        if name == "video":
            self.note("Videos are sent as links to the file on the site.")
            label = PurePosixPath(d.argument).name or "the video"
            return [f"[Video: {label}]({self.url(d.argument)})", ""]
        if name == "r3f":
            self.note("3D scenes are sent as a link to the page, where they run.")
            return [f"[Interactive 3D figure: open it on the site]({POST_URL})", ""]
        if name in ("tab-set",):
            return self.block(d.body)
        if name in ("tab-item", "card"):
            out = [f"**{self.inline(d.argument)}**", ""] if d.argument else []
            return [*out, *self.block(d.body), ""]
        if name in _FLATTEN:
            return self.block(d.body)
        if name == "pending":
            self.note("An unwritten section ({pending}) was left out.")
            return []
        if name in _DROP:
            self.note(f"{{{name}}} has no equivalent there and was left out.")
            return []
        self.note(f"{{{name}}} has no equivalent there; its content is kept as text.")
        return [*self.block(d.body), ""]

    def details(self, title: str, body: list[str]) -> list[str]:
        inner = self.block(body)
        if self.flavor == "devto":
            return [f"{{% details {title} %}}", *inner, "{% enddetails %}", ""]
        return [
            f"<details><summary>{title}</summary>",
            "",
            *inner,
            "",
            "</details>",
            "",
        ]

    def embed(self, url: str) -> str:
        youtube = _YOUTUBE.match(url)
        if youtube:
            url = f"https://www.youtube.com/watch?v={youtube.group(1)}"
        if self.flavor == "devto":
            return f"{{% embed {url} %}}"
        return f"%[{url}]"


def _title(name: str) -> str:
    return "See also" if name == "seealso" else name.capitalize()


def _quote(lines: list[str]) -> list[str]:
    # Trailing blank lines would end the quote with an empty `>`.
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return [f"> {line}" if line.strip() else ">" for line in lines]


def _split_target(content: str) -> tuple[str, str]:
    """`text <target>` → (text, target); a bare target → ('', target)."""
    m = re.match(r"^(.*?)\s*<([^<>]+)>$", content.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", content.strip()


def _parse_directive(name: str, argument: str, lines: list[str]) -> _Directive:
    options: dict[str, str] = {}
    i = 0
    if lines and lines[0].strip() == "---":
        # A YAML options block.
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                for raw in lines[1:j]:
                    key, _, value = raw.partition(":")
                    if key.strip():
                        options[key.strip()] = value.strip()
                i = j + 1
                break
    else:
        while i < len(lines):
            m = _OPTION.match(lines[i])
            if not m:
                break
            options[m.group(1)] = m.group(2).strip()
            i += 1
    body = lines[i:]
    while body and not body[0].strip():
        body = body[1:]
    return _Directive(name=name, argument=argument.strip(), options=options, body=body)


def _labels(lines: list[str]) -> dict[str, str]:
    """`(label)=` before a heading → that heading's text, for `{ref}` to read."""
    labels: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = _LABEL.match(_bare(line))
        if not m:
            continue
        for nxt in lines[i + 1 : i + 3]:
            heading = _HEADING.match(_bare(nxt))
            if heading:
                labels[m.group(1)] = heading.group(3).strip().rstrip("#").strip()
                break
    return labels


def _demote(lines: list[str]) -> list[str]:
    """Move every heading down a level when the body uses `#` (outside code)."""
    tops = [
        i
        for i, depth, _ in scan_fences(lines)
        if depth == 0 and (h := _HEADING.match(lines[i])) and len(h.group(2)) == 1
    ]
    if not tops:
        return lines
    out = list(lines)
    for i, depth, opened in scan_fences(lines):
        if depth or opened:
            continue
        h = _HEADING.match(lines[i])
        if h and len(h.group(2)) < 6:
            out[i] = f"{h.group(1)}#{h.group(2)}{h.group(3)}"
    return out


def to_markdown(text: str, *, page: str, flavor: Flavor) -> Converted:
    """The page's body (frontmatter removed) as `flavor` Markdown."""
    lines = [_bare(line) for line in _lines(text)]
    lines = lines[body_start(lines) :]
    converter = _Converter(page, flavor, _labels(lines))
    out = converter.block(lines)
    demoted = _demote(out)
    if demoted is not out:
        converter.note("Headings moved down a level: the title is the article's h1.")
    markdown = "\n".join(demoted)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"
    return Converted(markdown=markdown, notes=converter.notes)


#: MyST that survived into a body (a person pasted it in, or edited the draft):
#: neither platform renders it, so preflight says so.
LEFTOVER = re.compile(r"^\s*(?:`{3,}|:{3,})\s*\{[\w:-]+\}|\{[\w:-]+\}`", re.M)


def leftover_myst(markdown: str) -> list[str]:
    names = []
    for m in LEFTOVER.finditer(markdown):
        name = re.search(r"\{([\w:-]+)\}", m.group(0))
        if name and name.group(1) not in names:
            names.append(name.group(1))
    return names
