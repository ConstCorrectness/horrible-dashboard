"""A doc-set crawl recipe: where to start, what is in scope, and how pages nest.

The original doc set had one knob — a URL prefix that was both the scope and (via the
seed) the start. That cannot express the knowledge base's sites:

- aman.ai's primer index is its **home page**, outside the `/primers/` scope it
  leads to. Start and scope have to be separate, and the index page itself is a
  frontier, not a page worth keeping (`index_pages`).
- apxml.com nests course → chapter → lesson in its **URL path**, and its outline is
  rendered client-side, so the tree has to come from the path, not from which page
  happened to link to which (`hierarchy="url_path"`).

A set created without a recipe is the old prefix crawl, unchanged
(`Recipe.legacy`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from backend.modules.search.canonical import canonical_url


@dataclass
class Recipe:
    start_urls: list[str]
    prefix: str | None = None  # legacy scope; None when patterns are used
    allow_domains: list[str] = field(default_factory=list)
    allow_patterns: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)
    index_pages: list[str] = field(default_factory=list)
    frontier: str = "bfs"
    sitemap_url: str | None = None
    keep_scripts: bool = True
    content_selector: str | None = None
    hierarchy: str = "links"
    path_root: str | None = None
    path_kinds: list[str] = field(default_factory=list)
    page_kind: str = "article"
    profile_id: str | None = None
    # Set when the knowledge base owns this set: every captured page is filed as a
    # catalog item under this root.
    kb_root_item_id: str | None = None
    tags: list[str] = field(default_factory=list)
    structured: bool = False
    # Regexes removed from page titles: "Primers • Transformers" on every aman page
    # is a site name the catalog doesn't need to repeat 150 times.
    title_strip: list[str] = field(default_factory=list)

    @classmethod
    def legacy(cls, seed_url: str, prefix: str) -> "Recipe":
        return cls(start_urls=[seed_url], prefix=prefix)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}

    def clean_title(self, title: str) -> str:
        cleaned = title
        for pattern in self.title_strip:
            try:
                cleaned = re.sub(pattern, "", cleaned)
            except re.error:
                continue
        cleaned = " ".join(cleaned.split())
        return cleaned or title

    # ---- scope --------------------------------------------------------------

    def in_scope(self, url: str) -> bool:
        if self.prefix is not None and not self.allow_patterns:
            return canonical_url(url).startswith(canonical_url(self.prefix).rstrip("/"))
        from backend.modules.search.crawl.crawler import in_scope

        return in_scope(
            url,
            {
                "allow_domains": self.allow_domains,
                "allow_patterns": self.allow_patterns,
                "deny_patterns": self.deny_patterns,
            },
        )

    def is_index(self, url: str) -> bool:
        """A frontier-only page: navigated for its links, never kept."""
        path = urlsplit(url).path or "/"
        return any(re.search(p, path) for p in self.index_pages)

    def keeps(self, url: str) -> bool:
        """In scope *and* worth keeping — what an intra-set link may point at."""
        return self.in_scope(url) and not self.is_index(url)

    # ---- hierarchy ----------------------------------------------------------

    def _segments(self, url: str) -> list[str] | None:
        if not self.path_root:
            return None
        root = self.path_root.rstrip("/")
        path = (urlsplit(url).path or "/").rstrip("/")
        if path == root:
            return []
        if not path.startswith(root + "/"):
            return None
        return [s for s in path[len(root) + 1 :].split("/") if s]

    def path_parent(self, url: str) -> str | None:
        """For `url_path` hierarchy: the URL one path segment up, or None at the
        root (or outside it)."""
        segments = self._segments(url)
        if not segments:
            return None
        parts = urlsplit(url)
        root = self.path_root.rstrip("/") if self.path_root else ""
        parent_path = "/".join([root, *segments[:-1]])
        return urlunsplit((parts.scheme, parts.netloc, parent_path, "", ""))

    def kind_for(self, url: str) -> str:
        if self.hierarchy == "url_path" and self.path_kinds:
            segments = self._segments(url)
            if segments is not None:
                return self.path_kinds[min(len(segments), len(self.path_kinds) - 1)]
        return self.page_kind

    def is_root(self, url: str) -> bool:
        return self.hierarchy == "url_path" and self._segments(url) == []
