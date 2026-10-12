"""Extract a web page's main article text + metadata (pure — no network).

Uses `trafilatura` (the same engine behind many read-it-later/clipper tools) for
main-content extraction, dropping nav/boilerplate. `trafilatura` is imported lazily
inside the extractor — like the kaggle provider's client — so a missing/broken
install can't break backend boot, and code paths that never extract don't import
it. A regex tag-strip is the last-resort fallback.

Fetching lives elsewhere on purpose: blog ingest goes through the browser module's
SSRF guard (`browser.fetch.fetch_readable`), which imports `extract_article` from
here. This module fetching for itself is exactly the unguarded URL sink that guard
was built to close.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t]+")


@dataclass
class Article:
    title: str
    author: str | None
    text: str
    url: str


def extract_article(html: str, url: str) -> Article:
    """Pure extraction (no network) — split out so it's unit-testable on raw HTML."""
    title: str | None = None
    author: str | None = None
    text = ""

    try:
        import trafilatura

        text = (
            trafilatura.extract(html, include_comments=False, include_tables=False)
            or ""
        )
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = meta.title or None
            author = meta.author or None
    except Exception:  # noqa: BLE001 — any extractor failure falls back below
        text = ""

    if not text:
        text = _strip_tags(html)
    if not title:
        title = _html_title(html) or url

    return Article(title=title, author=author, text=text, url=url)


@dataclass
class StructuredArticle:
    """Main content as **markdown**, headings kept, math kept as TeX.

    The plain-text `Article` is enough for a blog post. It is not enough for a
    150-page primer: without headings a chunk can't say which section it came from,
    so a citation can only name the page, and MathJax/KaTeX either vanish or
    survive as the rendered glyph soup (`a⃗·b⃗`) that no query will ever match.
    """

    title: str
    author: str | None
    markdown: str
    url: str
    # heading text → its element id, so a chunk can deep-link to `url#anchor`.
    # Markdown has no ids, so this is harvested from the DOM before conversion.
    anchors: dict[str, str]
    # The canonical URL the page declares, when it declares one. Sites serve the same
    # page at several paths (aman.ai answers at both /primers/RAG/ and
    # /primers/ai/RAG/); this is what a crawl dedupes on.
    canonical: str | None = None


def extract_structured(
    html: str, url: str, content_selector: str | None = None
) -> StructuredArticle:
    """Pure extraction to markdown — no network.

    `content_selector` (a small CSS subset: `tag`, `.class`, `#id`, compounds and
    descendant combinators) names the content element outright for sites whose
    layout defeats trafilatura's main-content guess. apxml.com is the reference
    case: its lesson body is `div.course-content` inside Mantine scaffolding, and
    trafilatura keeps under half the text and none of the headings. When a selector
    matches, the element *is* the content decision, so it is converted directly
    rather than handed back to a heuristic that already chose wrong.
    """
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001 — unparseable markup degrades to plain text
        article = extract_article(html, url)
        return StructuredArticle(
            title=article.title,
            author=article.author,
            markdown=article.text,
            url=url,
            anchors={},
        )

    canonical = _canonical_link(doc, url)
    _math_to_tex(doc)
    _drop_toc(doc)
    anchors = _heading_anchors(doc)

    markdown = ""
    if content_selector:
        nodes = doc.xpath(css_to_xpath(content_selector))
        if nodes:
            markdown = node_markdown(nodes[0])

    title: str | None = None
    author: str | None = None
    try:
        import trafilatura

        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = meta.title or None
            author = meta.author or None
        if not markdown:
            # Re-serialized *after* the math pass, so TeX survives as text.
            prepared = lxml.html.tostring(doc, encoding="unicode")
            markdown = (
                trafilatura.extract(
                    prepared,
                    output_format="markdown",
                    include_formatting=True,
                    include_tables=True,
                    include_links=False,
                    include_images=False,
                    include_comments=False,
                )
                or ""
            )
    except Exception:  # noqa: BLE001 — any extractor failure falls back below
        pass

    if not markdown:
        markdown = _strip_tags(html)
    if not title:
        title = _html_title(html) or url
    return StructuredArticle(
        title=title,
        author=author,
        markdown=_tidy_markdown(markdown),
        url=url,
        anchors=anchors,
        canonical=canonical,
    )


def _canonical_link(doc: Any, url: str) -> str | None:
    from urllib.parse import urljoin

    for href in doc.xpath('//link[@rel="canonical"]/@href'):
        href = str(href).strip()
        if href:
            return urljoin(url, href)
    return None


# --- math -------------------------------------------------------------------
#
# Four renderers, four DOM shapes. Each is replaced *in place* by a text node holding
# its TeX source in `$…$` / `$$…$$`, which is what an LLM reads natively and what a
# query like "softmax temperature formula" can actually match.


def _replace_with_text(el: Any, text: str) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail or ""
    prev = el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + text + tail
    else:
        parent.text = (parent.text or "") + text + tail
    parent.remove(el)


def _wrap_tex(tex: str, display: bool) -> str:
    tex = " ".join(tex.split())
    return f"\n\n$${tex}$$\n\n" if display else f"${tex}$"


def _math_to_tex(doc: Any) -> None:
    # KaTeX (server-rendered on apxml): the TeX is in the MathML annotation.
    for el in doc.xpath('//*[contains(concat(" ", @class, " "), " katex-display ")]'):
        tex = el.xpath('.//annotation[@encoding="application/x-tex"]/text()')
        if tex:
            _replace_with_text(el, _wrap_tex(str(tex[0]), True))
    for el in doc.xpath('//*[contains(concat(" ", @class, " "), " katex ")]'):
        tex = el.xpath('.//annotation[@encoding="application/x-tex"]/text()')
        if tex:
            _replace_with_text(el, _wrap_tex(str(tex[0]), False))

    # MathJax 3/4 drops the source; the docviewer crawl stamps it back on as
    # `data-tex` before capture (see docviewer/crawl.py `_STAMP_TEX`). Read FIRST:
    # an `mjx-container` carries `class="MathJax …"` too, so the MathJax 2 sweep
    # below would delete it, TeX and all — which emptied every formula on aman.ai.
    for el in doc.xpath("//mjx-container"):
        tex = el.get("data-tex") or el.get("data-latex")
        if tex:
            _replace_with_text(el, _wrap_tex(tex, el.get("display") == "true"))

    # MathJax 2 keeps its source in <script type="math/tex[; mode=display]"> next to
    # the rendered spans, which would otherwise be extracted as glyph soup.
    for el in doc.xpath(
        '//*[contains(concat(" ", @class, " "), " MathJax_Preview ")'
        ' or contains(concat(" ", @class, " "), " MathJax ")'
        ' or contains(concat(" ", @class, " "), " MathJax_Display ")'
        ' or contains(concat(" ", @class, " "), " MathJax_SVG ")'
        ' or contains(concat(" ", @class, " "), " MathJax_CHTML ")]'
        "[not(self::mjx-container)]"
    ):
        if el.getparent() is not None:
            _replace_with_text(el, "")
    for el in doc.xpath('//script[starts-with(@type, "math/tex")]'):
        display = "mode=display" in (el.get("type") or "")
        _replace_with_text(el, _wrap_tex(el.text or "", display))

    # Bare MathML with an alttext (some generators emit only this).
    for el in doc.xpath("//math[@alttext]"):
        display = el.get("display") == "block"
        _replace_with_text(el, _wrap_tex(el.get("alttext") or "", display))


def _drop_toc(doc: Any) -> None:
    """Remove in-page tables of contents.

    A long primer opens with a nested list of every heading (`#markdown-toc` on
    kramdown sites). Extracted, it becomes one chunk of bare section titles that
    matches *every* query a little and none of them usefully — and it carries no
    content the headings themselves don't already give each chunk.

    Recognized structurally rather than by name: an outermost list whose links all
    point at `#fragments` and which holds almost no text beyond those links.
    """
    for el in doc.xpath('//*[@id="markdown-toc" or @id="toc" or @id="TOC"]'):
        if el.getparent() is not None:
            el.getparent().remove(el)
    for lst in doc.xpath(
        "//ul[not(ancestor::ul or ancestor::ol)] | //ol[not(ancestor::ul or ancestor::ol)]"
    ):
        links = lst.xpath(".//a[@href]")
        if len(links) < 5:
            continue
        fragment = [a for a in links if str(a.get("href") or "").startswith("#")]
        if len(fragment) < 0.8 * len(links):
            continue
        link_text = sum(len(a.text_content().strip()) for a in links)
        all_text = len(" ".join(lst.text_content().split()))
        if all_text and link_text >= 0.8 * all_text and lst.getparent() is not None:
            lst.getparent().remove(lst)


def _heading_anchors(doc: Any) -> dict[str, str]:
    anchors: dict[str, str] = {}
    for el in doc.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        anchor = el.get("id")
        if not anchor:
            # Many generators put the id on an inner <a name=…> or a wrapping section.
            inner = el.xpath(".//*[@id]/@id")
            anchor = str(inner[0]) if inner else None
        text = " ".join(el.text_content().split())
        if anchor and text and text not in anchors:
            anchors[text] = str(anchor)
    return anchors


# --- a tiny CSS → XPath, so profiles don't need the cssselect package -------------

_SIMPLE_SEL = re.compile(r"^([a-zA-Z][a-zA-Z0-9-]*)?((?:[.#][a-zA-Z0-9_-]+)*)$")


def css_to_xpath(selector: str) -> str:
    """Translate `tag.class#id` compounds joined by descendant spaces.

    Deliberately small: a site profile names one content element, and that needs
    no pseudo-classes or attribute operators. Anything outside the subset raises
    `ValueError` so a typo in a profile fails loudly instead of matching nothing.
    """
    steps: list[str] = []
    for part in selector.split():
        m = _SIMPLE_SEL.match(part)
        if not m or not part:
            raise ValueError(f"unsupported selector {part!r} in {selector!r}")
        tag = m.group(1) or "*"
        preds: list[str] = []
        for kind, name in re.findall(r"([.#])([a-zA-Z0-9_-]+)", m.group(2)):
            if kind == "#":
                preds.append(f'@id="{name}"')
            else:
                preds.append(
                    f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'
                )
        steps.append(tag + "".join(f"[{p}]" for p in preds))
    if not steps:
        raise ValueError("empty selector")
    return "//" + "//".join(steps)


# --- direct HTML → markdown for a chosen content element ---------------------------

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "button",
        "form",
        "iframe",
        "template",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "aside",
        "figure",
        "figcaption",
        "blockquote",
        "dl",
        "dt",
        "dd",
        "details",
        "summary",
    }
)


def node_markdown(node: Any) -> str:
    """Markdown for one element: headings, paragraphs, lists, code, tables.

    Not a general converter — just enough structure for heading-aware chunking and
    for an LLM to read. Inline formatting is reduced to code spans; emphasis carries
    no retrieval signal.
    """
    out: list[str] = []
    _emit_block(node, out, list_depth=0)
    return "\n".join(out)


def _inline_text(el: Any) -> str:
    parts: list[str] = []

    def walk(e: Any) -> None:
        if not isinstance(e.tag, str) or e.tag in _SKIP_TAGS:
            if e.tail:
                parts.append(e.tail)
            return
        if e.tag == "br":
            parts.append("\n")
        elif e.tag == "code":
            parts.append(f"`{e.text_content()}`")
            if e.tail:
                parts.append(e.tail)
            return
        if e.text:
            parts.append(e.text)
        for child in e:
            walk(child)
        if e.tail:
            parts.append(e.tail)

    if el.text:
        parts.append(el.text)
    for child in el:
        walk(child)
    return " ".join("".join(parts).split())


def _emit_block(el: Any, out: list[str], list_depth: int) -> None:
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in _SKIP_TAGS:
        return
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = " ".join(el.text_content().split())
        if text:
            out.extend(["", "#" * int(tag[1]) + " " + text, ""])
        return
    if tag == "pre":
        out.extend(["", "```", el.text_content().rstrip("\n"), "```", ""])
        return
    if tag in ("ul", "ol"):
        for i, li in enumerate(c for c in el if getattr(c, "tag", "") == "li"):
            marker = f"{i + 1}." if tag == "ol" else "-"
            text = _inline_text_excluding_lists(li)
            if text:
                out.append("  " * list_depth + f"{marker} {text}")
            for sub in li:
                if getattr(sub, "tag", "") in ("ul", "ol"):
                    _emit_block(sub, out, list_depth + 1)
        out.append("")
        return
    if tag == "table":
        rows = []
        for tr in el.xpath(".//tr"):
            cells = [" ".join(c.text_content().split()) for c in tr.xpath("./th|./td")]
            if cells:
                rows.append("| " + " | ".join(cells) + " |")
        if rows:
            width = rows[0].count("|") - 1
            out.extend(["", rows[0], "|" + " --- |" * width, *rows[1:], ""])
        return

    has_block_child = any(
        isinstance(c.tag, str)
        and (
            c.tag in _BLOCK_TAGS
            or c.tag in ("ul", "ol", "pre", "table")
            or c.tag[:1] == "h"
            and c.tag[1:].isdigit()
        )
        for c in el
    )
    if not has_block_child:
        text = _inline_text(el)
        if text:
            out.extend([text, ""])
        return
    # Mixed content: emit loose text runs between block children as paragraphs.
    if el.text and el.text.strip():
        out.extend([" ".join(el.text.split()), ""])
    for child in el:
        if not isinstance(child.tag, str):
            continue
        is_block = (
            child.tag in _BLOCK_TAGS
            or child.tag in ("ul", "ol", "pre", "table")
            or (child.tag[:1] == "h" and child.tag[1:].isdigit())
        )
        if is_block:
            _emit_block(child, out, list_depth)
        elif child.tag not in _SKIP_TAGS:
            text = _inline_text(child)
            if text:
                out.extend([text, ""])
        if child.tail and child.tail.strip():
            out.extend([" ".join(child.tail.split()), ""])


def _inline_text_excluding_lists(li: Any) -> str:
    nested = [c for c in li if getattr(c, "tag", "") in ("ul", "ol")]
    saved = [(c, c.getparent(), c.getparent().index(c), c.tail) for c in nested]
    for c, parent, _idx, _tail in saved:
        parent.remove(c)
    try:
        return _inline_text(li)
    finally:
        for c, parent, idx, tail in saved:
            c.tail = tail
            parent.insert(idx, c)


_LOOSE_BULLET = re.compile(r"^(\s*[-*]|\s*\d+\.)\s*\n\s+", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def _tidy_markdown(md: str) -> str:
    # trafilatura emits list items as "- \n    text"; fold them onto one line.
    md = _LOOSE_BULLET.sub(lambda m: m.group(1) + " ", md)
    return _BLANK_RUN.sub("\n\n", md).strip()


def _html_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    return _WS_RE.sub(" ", m.group(1)).strip() if m else None


def _strip_tags(html: str) -> str:
    # Drop script/style blocks, then tags, then collapse whitespace.
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.I)
    text = _TAG_RE.sub(" ", html)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
