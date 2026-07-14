"""Embedded browser module: reader-mode fetch (SSRF-guarded) + server-side
history/bookmarks backing the `browser` frontend panel. See docs/modules/browser.mdx."""

from backend.modules.browser.routes import router

__all__ = ["router"]
