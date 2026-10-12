"""`sitemap.xml` as a crawl frontier.

For a site whose pages are reachable only through client-side navigation — apxml.com
renders its course outline with JavaScript, and a link walk from the course page
finds a fraction of the lessons — the publisher's own sitemap is the complete list.
It is also cheaper than a walk: one fetch names every page, so nothing is navigated
just to discover links.

Fetched through the browser module's SSRF guard like every other crawl request. A
sitemap index is followed one level, and the total is capped so a site with a
million-URL sitemap can't stall an import before its first page.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

MAX_SITEMAP_URLS = 20_000
_MAX_CHILD_SITEMAPS = 50


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """`(page_urls, child_sitemap_urls)` from a `urlset` or `sitemapindex` document.

    Namespace-agnostic: sitemaps in the wild use the 0.9 namespace, no namespace, or
    a misspelled one, and all of them mean the same thing.
    """
    import lxml.etree

    try:
        root = lxml.etree.fromstring(
            xml.encode("utf-8"),
            parser=lxml.etree.XMLParser(
                recover=True, resolve_entities=False, no_network=True
            ),
        )
    except Exception:  # noqa: BLE001 — an unparseable sitemap is an empty one
        return [], []
    if root is None:
        return [], []
    tag = lxml.etree.QName(root).localname
    locs = [
        (el.text or "").strip()
        for el in root.iter()
        if isinstance(el.tag, str) and lxml.etree.QName(el).localname == "loc"
    ]
    locs = [u for u in locs if u.startswith(("http://", "https://"))]
    if tag == "sitemapindex":
        return [], locs
    return locs, []


async def sitemap_urls(
    sitemap_url: str,
    in_scope: Callable[[str], bool],
    *,
    cap: int = MAX_SITEMAP_URLS,
) -> list[str]:
    """In-scope page URLs from `sitemap_url` (and, for an index, its children), in
    document order. Empty on any failure — the caller falls back to a link walk."""
    from backend.modules.browser.fetch import safe_fetch_html

    out: list[str] = []
    seen: set[str] = set()
    queue = [sitemap_url]
    fetched = 0
    while queue and len(out) < cap and fetched <= _MAX_CHILD_SITEMAPS:
        url = queue.pop(0)
        fetched += 1
        try:
            _final, xml = await safe_fetch_html(url)
        except Exception as exc:  # noqa: BLE001 — degrade to a link walk
            logger.info("sitemap %s unavailable: %s", url, exc)
            continue
        pages, children = parse_sitemap(xml)
        queue.extend(c for c in children if c not in seen)
        seen.update(children)
        for page in pages:
            if page not in seen and in_scope(page):
                seen.add(page)
                out.append(page)
                if len(out) >= cap:
                    break
    return out
