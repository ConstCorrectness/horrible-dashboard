"""llms.txt — the publisher's own machine-readable index of its docs.

Most modern doc sites (Hugging Face, Anthropic, anything on Mintlify or the
Docusaurus plugin) now publish two files at their origin:

- **`/llms.txt`** — a markdown *index*: a title, a summary blockquote, and
  `## Section` lists of `- [Title](url): description` links. It is a curated table of
  contents, written by the people who wrote the docs.
- **`/llms-full.txt`** — the whole corpus concatenated into one markdown file.

Both are worth having, for different reasons, and the crawler uses them in a strict
order of preference:

1. **`llms-full.txt`, when its documents carry source URLs.** One conditional GET
   replaces two hundred, and the text is already boilerplate-free — no nav, no cookie
   banner, no "Edit this page". Cheap to diff, too: an unchanged corpus is a single
   304.
2. **`llms.txt` as the frontier.** The link list is better than BFS discovery: it is
   the pages the publisher considers documentation, in order, with no crawl-depth
   guesswork and no `/genindex` to deny-pattern away. Pages are still fetched and
   extracted as HTML, so per-page etags and content hashes keep working.
3. **The existing HTML crawl**, unchanged, when neither file exists.

## Why level 1 insists on source URLs

A search hit has to be openable. Chunks are grouped back into pages by their `url`
metadata (`providers/crawl._group_by_page`), and `canonical_url` drops fragments — so
a corpus indexed under one URL collapses into exactly **one** result no matter how
many sections matched, and clicking it lands on a 4 MB text file rather than the page
that answered the question. A document we cannot attribute to a real page is
therefore skipped rather than indexed against the corpus URL, and if too few survive
we fall back to level 2 instead.

The formats in the wild are not standardized on this point, so `_source_url` accepts
the several shapes generators actually emit (an explicit `Source:` line, a bare URL
line under the heading, a linked heading) and gives up quietly otherwise.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# A corpus whose sections mostly can't be attributed to a page is not usable as
# content — below this share of attributable documents we fall back to the index.
_MIN_ATTRIBUTED = 0.5

# Same floor the crawler applies to an extracted article: below this it's a stub.
_MIN_DOC_CHARS = 200

_LINK_RE = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)\s]+)\)\s*(?::\s*(.*))?$")
_SECTION_RE = re.compile(r"^##\s+(.*)$")
_TITLE_RE = re.compile(r"^#\s+(.*)$")
_SOURCE_RE = re.compile(
    r"^\s*(?:source|url|link|canonical)\s*[:=]\s*<?(https?://\S+?)>?\s*$", re.IGNORECASE
)
_BARE_URL_RE = re.compile(r"^\s*<?(https?://\S+?)>?\s*$")
_LINKED_HEADING_RE = re.compile(r"^\[([^\]]+)\]\((https?://[^)\s]+)\)\s*$")


@dataclass
class LlmsEntry:
    """One link from an llms.txt index."""

    url: str
    title: str = ""
    description: str = ""
    section: str = ""


@dataclass
class LlmsIndex:
    title: str = ""
    summary: str = ""
    entries: list[LlmsEntry] = field(default_factory=list)


@dataclass
class LlmsDoc:
    """One attributable document carved out of an llms-full.txt corpus."""

    url: str
    title: str
    text: str


def llms_txt_urls(start_url: str) -> tuple[list[str], list[str]]:
    """`(full, index)` candidate URLs for a seed's start URL, best guess first.

    **Path-scoped before origin-rooted**, which is the opposite of what the
    convention says and what the web actually does. A multi-product host publishes
    one file per product — `huggingface.co/docs/transformers/llms.txt` is 65 KB of
    Transformers links while `huggingface.co/llms.txt` is a 404 — and taking the
    origin file when a scoped one exists would index the wrong product, or nothing.

    Both are probed because the reverse is just as common: `docs.claude.com` serves
    its files at the origin and answers the scoped path with an HTML 404.
    """
    parts = urlsplit(start_url)
    origin = urlunsplit((parts.scheme or "https", parts.netloc, "/", "", ""))
    directory = (parts.path or "/").rsplit("/", 1)[0] + "/"
    scoped = urlunsplit((parts.scheme or "https", parts.netloc, directory, "", ""))

    def candidates(name: str) -> list[str]:
        seen: list[str] = []
        for base in (scoped, origin):
            url = urljoin(base, name)
            if url not in seen:
                seen.append(url)
        return seen

    return candidates("llms-full.txt"), candidates("llms.txt")


def looks_like_markdown(text: str) -> bool:
    """Whether a body is plausibly markdown rather than an HTML page.

    Used for two different jobs, both of which need the same evidence. Probing,
    because doc sites answer a missing path with a 200 and their own SPA shell far
    more often than with a 404 — so "we got a body" is not evidence the file exists.
    And fetching, because the pages an llms.txt index links to are increasingly `.md`
    twins (OpenAI, Ollama and ADK all do this), which trafilatura extracts nothing
    from: it parses HTML, and markdown has no tags to find an article inside.
    """
    head = text.lstrip()[:600].lower()
    if not head:
        return False
    return not head.startswith(("<!doctype", "<html", "<?xml")) and "<html" not in head


def markdown_article(text: str) -> tuple[str, str]:
    """`(title, text)` for a markdown body — the raw text *is* the article.

    Headings are kept in the body rather than stripped: a chunk cut out of the middle
    of a long page is far more useful when it still says which section it came from.
    """
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
        if line.strip() and not line.lstrip().startswith(("---", ">")):
            break
    return title, text.strip()


def parse_llms_txt(text: str, base_url: str) -> LlmsIndex:
    """Parse an llms.txt index. Pure — the format is a convention, so this is where
    it gets pinned."""
    index = LlmsIndex()
    section = ""
    seen: set[str] = set()

    for line in text.splitlines():
        if match := _TITLE_RE.match(line):
            if not index.title:
                index.title = match.group(1).strip()
            continue
        if match := _SECTION_RE.match(line):
            section = match.group(1).strip()
            continue
        if line.lstrip().startswith(">") and not index.summary:
            index.summary = line.lstrip()[1:].strip()
            continue
        if match := _LINK_RE.match(line):
            url = urljoin(base_url, match.group(2).strip())
            if urlsplit(url).scheme not in ("http", "https"):
                continue
            url = url.split("#", 1)[0]
            if url in seen:
                continue
            seen.add(url)
            index.entries.append(
                LlmsEntry(
                    url=url,
                    title=match.group(1).strip(),
                    description=(match.group(3) or "").strip(),
                    section=section,
                )
            )
    return index


def parse_llms_full(text: str, base_url: str) -> tuple[list[LlmsDoc], int]:
    """Split a corpus into attributable documents.

    Returns `(docs, total)` — `total` counts every section found, so the caller can
    tell "this corpus has no source URLs" (fall back to the index) from "this corpus
    is small" (index it).
    """
    docs: list[LlmsDoc] = []
    total = 0
    for heading, body in _sections(text):
        total += 1
        url, body = _source_url(heading, body, base_url)
        title = _LINKED_HEADING_RE.sub(r"\1", heading).strip()
        cleaned = body.strip()
        if not url or len(cleaned) < _MIN_DOC_CHARS:
            continue
        docs.append(LlmsDoc(url=url.split("#", 1)[0], title=title or url, text=cleaned))
    return docs, total


def usable_as_corpus(docs: list[LlmsDoc], total: int) -> bool:
    """Whether a parsed corpus should be indexed as content rather than discarded."""
    if not docs:
        return False
    return total <= 0 or (len(docs) / total) >= _MIN_ATTRIBUTED


def _sections(text: str) -> list[tuple[str, str]]:
    """Split on top-level `#` headings, ignoring fenced code blocks.

    Fences matter: a docs page that shows a shell session full of `# comment` lines
    would otherwise be shredded into a dozen bodiless documents.
    """
    heading = ""
    buffer: list[str] = []
    out: list[tuple[str, str]] = []
    fence = ""

    for line in text.splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            buffer.append(line)
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            buffer.append(line)
            continue
        if line.startswith("# "):
            if heading or "".join(buffer).strip():
                out.append((heading, "\n".join(buffer)))
            heading = line[2:].strip()
            buffer = []
            continue
        buffer.append(line)

    if heading or "".join(buffer).strip():
        out.append((heading, "\n".join(buffer)))
    return out


def _source_url(heading: str, body: str, base_url: str) -> tuple[str, str]:
    """The document's own page URL, plus the body with that line removed.

    Removed because a lone `Source: https://…` line embedded mid-chunk is noise in an
    embedding and noise in a snippet — the URL is metadata, and it is about to become
    metadata.
    """
    if match := _LINKED_HEADING_RE.match(heading.strip()):
        return match.group(2), body

    lines = body.splitlines()
    for i, line in enumerate(lines[:6]):
        match = _SOURCE_RE.match(line) or _BARE_URL_RE.match(line)
        if match:
            url = urljoin(base_url, match.group(1))
            if urlsplit(url).scheme in ("http", "https"):
                return url, "\n".join(lines[:i] + lines[i + 1 :])
    return "", body


def entry_metadata(entries: list[LlmsEntry]) -> dict[str, dict[str, Any]]:
    """`{url: {title, description, section}}` — what the publisher said about each
    page, so a crawled page can keep the curated title even when the HTML's `<title>`
    is "Transformers"."""
    return {
        e.url: {"title": e.title, "description": e.description, "section": e.section}
        for e in entries
    }
