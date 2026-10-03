"""Share cards: the 1200×630 image a link shows on X, LinkedIn, Slack and in feeds.

Each page gets one, drawn from the theme's `og.html` (`themes.og_template`) with the
page's title, description and date, and screenshotted by headless Chromium through
Playwright. A page that names its own `thumbnail:` uses that instead; the static build
decides which, and only asks here for the cards it needs.

Rules that are quiet if wrong:

- **Every value is HTML-escaped** before it goes into the template. Titles are often
  agent-written, and the template is rendered by a real browser.
- **No network.** The card is set as page content with every request refused, so a
  theme cannot make publishing depend on a font CDN, and a title cannot fetch anything.
- **Cached by content.** A card is keyed by a hash of its finished HTML and kept in
  `.scrive/cache/og/`, so re-publishing a site of fifty posts renders only what changed.
- **Never fails a publish.** Chromium is a separate download (`playwright install
  chromium`) that a packaged install may not have. Without it a plainer card is drawn
  with Pillow from the theme's colours, and the result says so.
- **Sync Playwright on a worker thread** — the Windows-safe pattern; the asyncio
  subprocess API breaks under `--reload`.
"""

from __future__ import annotations

import hashlib
import html
import io
import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 1200, 630


class Card(BaseModel):
    """One card the static build wants, written at `path` in the published site."""

    path: str
    title: str
    description: str = ""
    #: The small line above the title: a date, "Post", a tag.
    kicker: str = ""


@dataclass
class Rendered:
    files: dict[str, bytes]
    #: How they were drawn: `chromium`, `pillow` (Chromium unavailable), or `cache`.
    engine: str
    note: str = ""


def card_html(template: str, tokens: str, site_title: str, card: Card) -> str:
    values = {
        "tokens": tokens,  # the theme's own CSS, not page text
        "title": html.escape(card.title),
        "description": html.escape(card.description),
        "kicker": html.escape(card.kicker),
        "site": html.escape(site_title),
    }
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}", lambda m: values.get(m.group(1), ""), template
    )


def _key(page_html: str) -> str:
    return hashlib.sha256(page_html.encode("utf-8")).hexdigest()[:20]


def _chromium(pages: dict[str, str]) -> dict[str, bytes]:
    """Screenshot each HTML document. Raises when Chromium cannot start."""
    from playwright.sync_api import sync_playwright

    out: dict[str, bytes] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1
            )
            # Nothing a card draws comes from the network.
            page.route("**/*", lambda route: route.abort())
            for key, doc in pages.items():
                page.set_content(doc, wait_until="load")
                out[key] = page.screenshot(type="png", full_page=False)
        finally:
            browser.close()
    return out


_LIGHT_ROOT = re.compile(r":root\s*\{(.*?)\}", re.S)


def _token(tokens: str, name: str, fallback: str) -> str:
    """A colour from the theme's first `:root` block (its default scheme)."""
    block = _LIGHT_ROOT.search(tokens)
    match = re.search(
        rf"--{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{3,8}})",
        block.group(1) if block else "",
    )
    return match.group(1) if match else fallback


def _pillow(tokens: str, site_title: str, card: Card) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    bg = _token(tokens, "bg", "#ffffff")
    text = _token(tokens, "text-strong", _token(tokens, "text", "#111111"))
    dim = _token(tokens, "text-dim", "#666666")
    accent = _token(tokens, "accent", "#2f5fd0")
    image = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(image)

    def font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1 has no sized default font
            return ImageFont.load_default()

    draw.rectangle((0, 0, WIDTH, 8), fill=accent)
    y = 80
    if card.kicker:
        draw.text((88, y), card.kicker, fill=dim, font=font(26))
        y += 56
    for line in textwrap.wrap(card.title, width=30)[:3]:
        draw.text((88, y), line, fill=text, font=font(64))
        y += 78
    for line in textwrap.wrap(card.description, width=60)[:2]:
        draw.text((88, y + 16), line, fill=dim, font=font(30))
        y += 42
    draw.text((88, HEIGHT - 96), site_title, fill=accent, font=font(28))
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_cards(
    cards: list[Card],
    *,
    template: str,
    tokens: str,
    site_title: str,
    cache_dir: Path,
) -> Rendered:
    """Every card as PNG bytes by its site path. Blocking — call on a thread."""
    docs = {c.path: card_html(template, tokens, site_title, c) for c in cards}
    files: dict[str, bytes] = {}
    missing: dict[str, str] = {}
    for path, doc in docs.items():
        cached = cache_dir / f"{_key(doc)}.png"
        if cached.is_file():
            files[path] = cached.read_bytes()
        else:
            missing[_key(doc)] = doc
    if not missing:
        return Rendered(files, "cache")

    engine, note = "chromium", ""
    try:
        drawn = _chromium(missing)
    except Exception as exc:  # noqa: BLE001 — no Chromium, or it failed to start
        logger.info("share cards: Chromium unavailable, drawing with Pillow: %s", exc)
        engine = "pillow"
        note = (
            "Share cards were drawn plainly: headless Chromium is not installed "
            "(`uv run playwright install chromium` for the theme's own card)."
        )
        by_key = {_key(docs[c.path]): c for c in cards}
        drawn = {key: _pillow(tokens, site_title, by_key[key]) for key in missing}

    cache_dir.mkdir(parents=True, exist_ok=True)
    for key, png in drawn.items():
        if engine == "chromium":
            # Only the theme's real card is worth keeping; a Pillow stand-in is
            # redrawn properly once Chromium is installed.
            (cache_dir / f"{key}.png").write_bytes(png)
    for path, doc in docs.items():
        if path not in files:
            files[path] = drawn[_key(doc)]
    return Rendered(files, engine, note)
