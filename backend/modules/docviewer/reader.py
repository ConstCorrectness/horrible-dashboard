"""Reader view: serve an archived page as its article alone.

Some sites wrap every lesson in app chrome that means nothing offline. An apxml.com
lesson is a Mantine app shell with a header, a full course outline in a sidebar, an
ad bar, chat buttons, and, for a crawler that is not signed in, a "you've reached
the limit" modal with the lesson blurred behind it. The Docs pane already shows the
course tree and the page title, so a recipe with `reader_view` gets the page's
`content_selector` node and nothing else.

This happens when the page is served, not when it is captured. The archive stays the
whole page as captured, so turning reader view on or off, or fixing a selector, takes
effect on the next click without recapturing or re-indexing anything.
"""

from __future__ import annotations

from typing import Any

from backend.modules.library.extract import css_to_xpath

# Inline <style> is allowed by both archive CSPs (`artifacts.store.page_csp`).
# Literal colours on purpose: the frame has no access to the app's theme tokens, and
# the site's own stylesheet, which stays in <head>, sets the article's colours.
#
# An app shell scrolls inside one of its own boxes and pins the document
# (`html { overflow: hidden }` on apxml); with the shell gone, the document has to
# scroll again, or the article ends at the bottom of the frame.
_READER_CSS = """
html, body { overflow: auto !important; height: auto !important; }
body { margin: 0; }
.hd-reader { max-width: 52rem; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
.hd-reader > h1.hd-reader-title { margin: 0 0 1.5rem; line-height: 1.25; }
"""


def reader_view(html: str, selector: str, title: str) -> str | None:
    """The archive reduced to `title` plus the first node matching `selector`, or
    None when nothing matches. None means serve the whole archive: a page whose
    markup changed is better shown with its chrome than not at all.

    The page's <head> is kept so the article keeps the site's own stylesheets
    (typography, code highlighting, KaTeX). Scripts go: a reader view is static, and
    a kept app bundle would re-render the body it no longer has.
    """
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
        nodes = doc.xpath(css_to_xpath(selector))
    except Exception:  # noqa: BLE001 — unparseable markup or selector: show it whole
        return None
    if not nodes:
        return None
    article: Any = nodes[0]
    # The node's own inline style is the page's runtime state, not the article's
    # look: apxml blurs and masks the lesson there for a visitor past the limit.
    if article.get("style") is not None:
        del article.attrib["style"]

    head = doc.find("head")
    if head is None:
        head = doc.makeelement("head", {})
        doc.insert(0, head)
    for script in list(doc.iter("script")):
        script.drop_tree()
    style = head.makeelement("style", {})
    style.text = _READER_CSS
    head.append(style)

    body = doc.find("body")
    if body is None:
        body = doc.makeelement("body", {})
        doc.append(body)
    article.getparent().remove(article)
    article.tail = None
    for child in list(body):
        body.remove(child)
    body.text = None
    # The site's body classes and inline styles size its app shell (a fixed height,
    # overflow hidden); the article needs the page to scroll normally.
    for attr in list(body.attrib):
        del body.attrib[attr]

    wrapper = body.makeelement("main", {"class": "hd-reader"})
    heading = wrapper.makeelement("h1", {"class": "hd-reader-title"})
    heading.text = title
    wrapper.append(heading)
    wrapper.append(article)
    body.append(wrapper)
    return lxml.html.tostring(doc, encoding="unicode", doctype="<!DOCTYPE html>")
