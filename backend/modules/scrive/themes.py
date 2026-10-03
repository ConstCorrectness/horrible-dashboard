"""Site themes: how a published site looks, separate from what its pages say.

A theme is a folder of three files (OpenDesign's split):

- `template.json` — `{name, description, layout}`. `layout` names one of the four
  built-in layouts (`minimal`, `tactical`, `book`, `article`), which are React
  components in `packages/core/src/modules/scrive/site/` — the same renderer the
  Preview uses, so what you preview is what gets published.
- `tokens.css` — CSS custom properties (`--bg`, `--text`, `--accent`, `--font-body`…)
  that the layout and the page styles read. A theme *is* mostly its tokens.
- `THEME.md` — the brand and voice guide the agent reads before writing for the site.

and optionally `og.html`, the share-card template (`cards.py`); without one the shared
`builtin_themes/og.html` is used.

Built-ins live in `builtin_themes/<id>/`. A site's own theme lives in
`<site>/themes/<id>/` and **overrides tokens only**: its `template.json` names the
built-in it `extends` (default `minimal`), and its `tokens.css` is appended after that
built-in's, so it only has to say what differs. Custom layouts are code, and come later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from backend.modules.scrive import store

BUILTIN_DIR = Path(__file__).parent / "builtin_themes"
SITE_DIR = "themes"
LAYOUTS = ("minimal", "tactical", "book", "article")
DEFAULT_THEME = "minimal"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ThemeInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    layout: str = DEFAULT_THEME
    source: str = "builtin"  # 'builtin' | 'site'
    #: What the static build inlines into `_scrive/site.css`.
    tokens: str = ""
    #: THEME.md — the brand and voice guide for whoever writes for the site.
    guide: str = ""


class ThemeError(ValueError):
    pass


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _manifest(folder: Path) -> dict[str, object]:
    try:
        data = json.loads(_read(folder / "template.json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _builtin(theme_id: str) -> ThemeInfo | None:
    folder = BUILTIN_DIR / theme_id
    if theme_id not in LAYOUTS or not folder.is_dir():
        return None
    meta = _manifest(folder)
    return ThemeInfo(
        id=theme_id,
        name=str(meta.get("name") or theme_id),
        description=str(meta.get("description") or ""),
        layout=theme_id,
        tokens=_read(folder / "tokens.css"),
        guide=_read(folder / "THEME.md"),
    )


def _site_theme(base: Path, theme_id: str) -> ThemeInfo | None:
    folder = base / SITE_DIR / theme_id
    if not _ID.match(theme_id) or not folder.is_dir():
        return None
    meta = _manifest(folder)
    parent = _builtin(str(meta.get("extends") or DEFAULT_THEME)) or _builtin(
        DEFAULT_THEME
    )
    assert parent is not None
    own_tokens = _read(folder / "tokens.css")
    return ThemeInfo(
        id=theme_id,
        name=str(meta.get("name") or theme_id),
        description=str(meta.get("description") or f"{parent.name}, restyled"),
        layout=parent.layout,
        source="site",
        tokens=f"{parent.tokens}\n/* {SITE_DIR}/{theme_id}/tokens.css */\n{own_tokens}"
        if own_tokens
        else parent.tokens,
        guide=_read(folder / "THEME.md") or parent.guide,
    )


def list_themes(site_id: str | None = None) -> list[ThemeInfo]:
    """The four built-ins, then the site's own (which may shadow a built-in id)."""
    themes = {t: info for t in LAYOUTS if (info := _builtin(t))}
    if site_id:
        folder = store.site_dir(site_id) / SITE_DIR
        if folder.is_dir():
            for child in sorted(folder.iterdir()):
                info = _site_theme(store.site_dir(site_id), child.name)
                if info:
                    themes[info.id] = info
    return list(themes.values())


def get_theme(site_id: str | None, theme_id: str) -> ThemeInfo:
    if site_id:
        own = _site_theme(store.site_dir(site_id), theme_id)
        if own:
            return own
    info = _builtin(theme_id)
    if info is None:
        raise ThemeError(f"no theme named {theme_id!r}")
    return info


def site_theme(site_id: str) -> ThemeInfo:
    """The theme `scrive.yml` names, or the default when it names nothing usable —
    a typo in the config must not make the site unpublishable."""
    config = store.read_config(store.site_dir(site_id))
    try:
        return get_theme(site_id, config.theme or DEFAULT_THEME)
    except ThemeError:
        return get_theme(None, DEFAULT_THEME)


def og_template(site_id: str | None, theme: ThemeInfo) -> str:
    """The theme's share-card HTML: the site theme's own, its layout's, or the shared one."""
    candidates = []
    if site_id and theme.source == "site":
        candidates.append(store.site_dir(site_id) / SITE_DIR / theme.id / "og.html")
    candidates += [BUILTIN_DIR / theme.layout / "og.html", BUILTIN_DIR / "og.html"]
    for path in candidates:
        text = _read(path)
        if text:
            return text
    raise ThemeError("no share-card template")
