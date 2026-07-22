"""Self-contained page capture: turn live HTML into one archivable .html file.

SingleFile-style, but native — SingleFile itself is AGPL, and this repo ships no
AGPL code (the pypdf-over-PyMuPDF rule; an external `single-file-cli` the user
installs themselves is supported behind a setting, crossing only a process
boundary). The capture inlines images and stylesheets as ``data:`` URIs and strips
scripts, so the stored page renders offline inside a sandboxed viewer.

The core is **two-pass and does no I/O**, because its two callers fetch very
differently: the browser engine fetches synchronously on its dedicated Playwright
thread (with the session's cookies), while the no-engine fallback fetches through
the async SSRF guard (`browser.fetch.safe_fetch_bytes`). Protocol:

1. ``list_resources(html, base_url)`` → the subresource URLs worth fetching;
2. the caller fetches them (however it fetches), then for each stylesheet calls
   ``list_css_urls(css, css_url)`` and fetches those too;
3. ``build_page(html, base_url, resources)`` → the final self-contained document.

Whatever the caller failed to fetch just stays an absolute URL — per-resource
failure is non-fatal by design, and the viewer's CSP blocks the network anyway,
so an un-inlined image degrades to a blank box rather than a live request.

Parsing uses ``lxml.html`` (already here via trafilatura), imported lazily like
every optional-heavy import in this codebase.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

PER_RESOURCE_CAP = 5_000_000
TOTAL_CAP = 25_000_000

# Kinds a caller may want to fetch differently (content-type allowlists).
IMAGE_KIND = "image"
STYLESHEET_KIND = "stylesheet"

_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\"\)]+)\1\s*\)", re.IGNORECASE)
_ON_ATTR_RE = re.compile(r"^on", re.IGNORECASE)


def filename_for_title(title: str, ext: str = "html") -> str:
    """A display/download filename from a page title. This is *not* the on-disk
    name (blobs are content-addressed); it only needs to be shell-harmless and
    Windows-legal when a user downloads the artifact."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)[:120].strip()
    return f"{cleaned or 'capture'}.{ext}"


def _parse(html: str) -> Any:
    import lxml.html

    return lxml.html.fromstring(html or "<html></html>")


def _abs(base_url: str, url: str) -> str | None:
    """Absolute http(s) URL for a candidate subresource reference, or None for
    things that can't or shouldn't be fetched (data:, blob:, fragments, …)."""
    url = (url or "").strip()
    if not url or url.startswith("#"):
        return None
    resolved = urljoin(base_url, url)
    if urlsplit(resolved).scheme not in ("http", "https"):
        return None
    return resolved


def list_resources(html: str, base_url: str) -> dict[str, str]:
    """Pass 1: subresource URLs to fetch, as ``{url: kind}`` in document order."""
    doc = _parse(html)
    out: dict[str, str] = {}
    for img in doc.iter("img"):
        resolved = _abs(base_url, img.get("src") or "")
        if resolved is not None:
            out.setdefault(resolved, IMAGE_KIND)
    for link in doc.iter("link"):
        rels = (link.get("rel") or "").lower().split()
        if "stylesheet" not in rels:
            continue
        resolved = _abs(base_url, link.get("href") or "")
        if resolved is not None:
            out.setdefault(resolved, STYLESHEET_KIND)
    return out


def list_css_urls(css_text: str, css_url: str) -> list[str]:
    """Pass 1b: ``url(...)`` references inside a fetched stylesheet (fonts, images),
    resolved against the stylesheet's own URL. ``@import`` is deliberately not
    followed — one level of indirection is the budget."""
    urls: list[str] = []
    for match in _CSS_URL_RE.finditer(css_text):
        resolved = _abs(css_url, match.group(2))
        if resolved is not None and resolved not in urls:
            urls.append(resolved)
    return urls


def _data_uri(data: bytes, mime: str) -> str:
    clean_mime = (mime or "application/octet-stream").split(";")[0].strip()
    return f"data:{clean_mime};base64,{base64.b64encode(data).decode('ascii')}"


class _Budget:
    """Total-size budget across all inlined resources; per-resource cap besides."""

    def __init__(self, total_cap: int, per_resource_cap: int) -> None:
        self.remaining = total_cap
        self.per_resource_cap = per_resource_cap

    def take(self, data: bytes) -> bool:
        if len(data) > self.per_resource_cap or len(data) > self.remaining:
            return False
        self.remaining -= len(data)
        return True


def _inline_css(
    css_text: str,
    css_url: str,
    resources: Mapping[str, tuple[bytes, str]],
    budget: _Budget,
) -> str:
    def replace(match: re.Match[str]) -> str:
        resolved = _abs(css_url, match.group(2))
        if resolved is None:
            return match.group(0)
        fetched = resources.get(resolved)
        if fetched is None or not budget.take(fetched[0]):
            # Keep an absolute reference so the un-inlined URL still means
            # something outside the page's original base.
            return f"url({resolved})"
        return f"url({_data_uri(*fetched)})"

    return _CSS_URL_RE.sub(replace, css_text)


def _sanitize(doc: Any) -> None:
    """Strip active content: scripts, inline handlers, javascript: URLs, meta
    refresh, and any pre-existing ``<base>`` (a fresh one is inserted after)."""
    for el in list(doc.iter("script")):
        el.drop_tree()
    for el in list(doc.iter("base")):
        el.drop_tree()
    for el in list(doc.iter("meta")):
        if (el.get("http-equiv") or "").lower() == "refresh":
            el.drop_tree()
    for el in doc.iter():
        attribs = getattr(el, "attrib", None)
        if attribs is None:
            continue
        for name in [a for a in attribs if _ON_ATTR_RE.match(a)]:
            del attribs[name]
        for name in ("href", "src", "action"):
            value = (attribs.get(name) or "").strip()
            if value.lower().startswith("javascript:"):
                del attribs[name]


def build_page(
    html: str,
    base_url: str,
    resources: Mapping[str, tuple[bytes, str]],
    *,
    keep_scripts: bool = False,
    per_resource_cap: int = PER_RESOURCE_CAP,
    total_cap: int = TOTAL_CAP,
) -> str:
    """Pass 2: rewrite the document, inlining every fetched resource as a data:
    URI within budget, and return the serialized self-contained HTML."""
    import lxml.html

    doc = _parse(html)
    budget = _Budget(total_cap, per_resource_cap)

    if not keep_scripts:
        _sanitize(doc)

    for img in doc.iter("img"):
        resolved = _abs(base_url, img.get("src") or "")
        if resolved is None:
            continue
        fetched = resources.get(resolved)
        if fetched is None or not budget.take(fetched[0]):
            img.set("src", resolved)  # absolute, so it means something anywhere
            continue
        img.set("src", _data_uri(*fetched))
        # A surviving srcset would out-vote the inlined src at render time.
        for attr in ("srcset", "sizes"):
            if img.get(attr) is not None:
                del img.attrib[attr]

    for link in list(doc.iter("link")):
        rels = (link.get("rel") or "").lower().split()
        if "stylesheet" not in rels:
            continue
        resolved = _abs(base_url, link.get("href") or "")
        fetched = resources.get(resolved) if resolved else None
        if resolved is None or fetched is None:
            continue
        css_data, _mime = fetched
        if not budget.take(css_data):
            link.set("href", resolved)
            continue
        css_text = css_data.decode("utf-8", errors="replace")
        style = link.makeelement("style", {})
        style.text = _inline_css(css_text, resolved, resources, budget)
        link.getparent().replace(link, style)

    # Absolute links for everything left un-inlined: the archive must not depend
    # on the original base URL, and a fresh <base> keeps relative hrefs working.
    head = doc.find("head")
    if head is None:
        head = doc.makeelement("head", {})
        doc.insert(0, head)
    base = head.makeelement("base", {"href": base_url})
    head.insert(0, base)

    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    comment = f"<!-- saved from {base_url} at {saved_at} by horrible-dashboard -->\n"
    body = lxml.html.tostring(doc, encoding="unicode", doctype="<!DOCTYPE html>")
    return comment + body
