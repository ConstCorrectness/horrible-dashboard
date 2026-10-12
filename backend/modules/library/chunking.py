"""Split extracted text into overlapping chunks for embedding.

Character-based and dependency-free: paragraphs are packed into ~`size`-char
windows (over-long paragraphs are hard-split), then a `overlap`-char tail of each
chunk is prepended to the next so a passage straddling a boundary is still
retrievable from both sides.

`chunk_markdown` is the structure-aware sibling for long, sectioned documents (a
primer, a course lesson): it splits on headings, keeps the heading path for
citation, and never cuts through a code fence or a display-math block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARA_SPLIT = re.compile(r"\n\s*\n")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
SECTION_SEP = " › "


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if overlap >= size:
        overlap = size // 4

    # 1) Units no larger than `size`: paragraphs, hard-split when a single one is
    #    over budget.
    units: list[str] = []
    for para in _PARA_SPLIT.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            units.append(para)
        else:
            units.extend(para[i : i + size] for i in range(0, len(para), size))

    # 2) Pack units into chunks up to `size`.
    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 2 + len(unit) <= size:
            current += "\n\n" + unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)

    # 3) Prepend an overlap tail from the previous chunk for retrieval context.
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            overlapped.append((prev[-overlap:] + "\n\n" + cur).strip())
        chunks = overlapped

    return chunks


@dataclass
class Chunk:
    """One embedded unit of a structured document.

    `text` is what gets embedded and stored, and it **leads with its breadcrumb**
    (`Title › Section › Subsection`): a passage like "it lacks the need for
    normalization" means nothing on its own, and the heading path is what tells the
    embedder — and the model reading the hit — that it is about Reciprocal Rank
    Fusion inside a RAG primer.
    """

    text: str
    section: str  # heading path below the title, "" for preamble
    anchor: str | None  # element id of the nearest heading that has one


@dataclass
class _Section:
    path: list[str]
    anchor: str | None
    blocks: list[str]


def _is_fence_open(stripped: str) -> str | None:
    if stripped.startswith(("```", "~~~")):
        return stripped[:3]
    if stripped == "$$" or (stripped.startswith("$$") and not stripped.endswith("$$")):
        return "$$"
    return None


def _blocks(lines: list[str]) -> list[str]:
    """Blank-line-separated blocks, with fences and `$$` math kept whole."""
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in lines:
        stripped = line.strip()
        if fence is not None:
            current.append(line)
            if stripped.endswith(fence):
                fence = None
            continue
        if not stripped:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        fence = _is_fence_open(stripped)
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def _sections(md: str, anchors: dict[str, str]) -> list[_Section]:
    sections: list[_Section] = [_Section(path=[], anchor=None, blocks=[])]
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    in_fence = False

    def close() -> None:
        sections[-1].blocks.extend(_blocks(body))
        body.clear()

    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m is None:
            body.append(line)
            continue
        close()
        level, text = len(m.group(1)), " ".join(m.group(2).split())
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        sections.append(
            _Section(path=[t for _lvl, t in stack], anchor=anchors.get(text), blocks=[])
        )
    close()
    return [s for s in sections if s.blocks]


def _split_oversized(block: str, size: int) -> list[str]:
    """Break one over-budget block, preferring sentence boundaries.

    A fence or display-math block may run to 3× `size` before it is cut — half a
    code listing retrieves worse than a long one — and is then cut on lines.
    """
    if len(block) <= size:
        return [block]
    atomic = block.startswith(("```", "~~~", "$$"))
    if atomic and len(block) <= size * 3:
        return [block]
    pieces = block.splitlines() if atomic else _SENTENCE_END.split(block)
    joiner = "\n" if atomic else " "
    out: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > size:  # a single run-on sentence or line
            if current:
                out.append(current)
                current = ""
            out.append(piece[:size])
            piece = piece[size:]
        if current and len(current) + len(joiner) + len(piece) > size:
            out.append(current)
            current = piece
        else:
            current = f"{current}{joiner}{piece}" if current else piece
    if current:
        out.append(current)
    return out


def _section_bodies(sec: _Section, size: int, overlap: int) -> list[str]:
    bodies: list[str] = []
    current = ""
    for block in sec.blocks:
        for piece in _split_oversized(block, size):
            if current and len(current) + 2 + len(piece) > size:
                bodies.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        bodies.append(current)
    if overlap > 0 and len(bodies) > 1:
        bodies = [bodies[0]] + [
            (_word_tail(prev, overlap) + "\n\n" + cur).strip()
            for prev, cur in zip(bodies, bodies[1:])
        ]
    return bodies


def _word_tail(text: str, overlap: int) -> str:
    """The last ~`overlap` chars, starting on a word boundary.

    A raw slice starts mid-token, and in math-heavy text that is a dangling
    `||\\vec{b}||$` — half an expression whose `$` then pairs with the wrong one.
    """
    tail = text[-overlap:]
    if len(text) > overlap and not text[-overlap - 1].isspace():
        _head, _sep, rest = tail.partition(" ")
        tail = rest or tail
    return tail


def _mergeable(first: list[str], nxt: list[str]) -> bool:
    """Whether `nxt` may ride in a chunk labelled with `first`'s path.

    Only its own subsections or its siblings: absorbing a section from a different
    branch would cite "Reciprocal Rank Fusion" as part of "Overview".
    """
    return nxt[: len(first)] == first or nxt[:-1] == first[:-1]


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    out: list[str] = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out


def chunk_markdown(
    md: str,
    *,
    title: str = "",
    size: int = 1200,
    overlap: int = 150,
    anchors: dict[str, str] | None = None,
) -> list[Chunk]:
    """Heading-aware chunks of a markdown document.

    Sections are the unit: blocks inside one section pack up to `size`, and a
    chunk only spans several sections when they are small enough to fit whole and
    are siblings or subsections (it is then labelled with their common parent). The
    overlap tail is carried *within* a section
    only — a tail from the previous section would bolt unrelated text onto the front
    of this one and pull its vector toward it.
    """
    md = md.strip()
    if not md:
        return []
    if overlap >= size:
        overlap = size // 4
    sections = _sections(md, anchors or {})
    # A leading H1 that just restates the document title adds nothing to the path.
    norm_title = " ".join(title.split()).lower()
    for sec in sections:
        if sec.path and sec.path[0].lower() == norm_title:
            sec.path = sec.path[1:]

    raw: list[tuple[list[str], str | None, str]] = []  # (path, anchor, body)
    # A whole section small enough to share a chunk with whatever follows.
    pending: tuple[list[str], str | None, str] | None = None
    for sec in sections:
        bodies = _section_bodies(sec, size, overlap)
        if len(bodies) == 1:
            body = bodies[0]
            if pending is not None and _mergeable(pending[0], sec.path):
                heading = f"### {sec.path[-1]}" if sec.path else ""
                merged = "\n\n".join(p for p in (pending[2], heading, body) if p)
                if len(merged) <= size:
                    # Label by the shared parent: a subsection keeps its parent's
                    # path, two siblings fall back to the path they have in common.
                    common = _common_prefix(pending[0], sec.path)
                    pending = (common, pending[1], merged)
                    continue
            if pending is not None:
                raw.append(pending)
            pending = (sec.path, sec.anchor, body)
            continue
        if pending is not None:
            raw.append(pending)
            pending = None
        raw.extend((sec.path, sec.anchor, body) for body in bodies)
    if pending is not None:
        raw.append(pending)

    out: list[Chunk] = []
    for path, anchor, body in raw:
        label = SECTION_SEP.join(path)
        crumb = SECTION_SEP.join(p for p in (title.strip(), label) if p)
        text = f"{crumb}\n\n{body}" if crumb else body
        out.append(Chunk(text=text, section=label, anchor=anchor))
    return out
