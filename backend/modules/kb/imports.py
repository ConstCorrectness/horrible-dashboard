"""Starting a site import: profile (or URL) → root catalog item → doc-set crawl.

An import is a doc set with a recipe (`docviewer/recipe.py`) whose
`kb_root_item_id` points at a catalog item standing for the whole site or course.
The crawl files every page it captures under that root (`kb.sitesync`), so the
import appears in the Knowledge pane as one row with progress, and in the Docs pane
as an offline set.

Re-importing something already imported resumes the existing set instead of
starting a second copy — the root item's `docset_id` is how that is recognised.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from backend.modules.kb import profiles, store
from backend.modules.kb.service import emit, site_library


class ImportFailed(ValueError):
    """An import request that can't be started; the message is for the user."""


def _validate_start(url: str) -> str:
    # The same egress rule as browser navigation and the doc-set route: a crawl
    # drives a real browser at this URL.
    from fastapi import HTTPException

    from backend.modules.docviewer.routes import _validate_seed

    try:
        return _validate_seed(url)
    except HTTPException as exc:
        raise ImportFailed(str(exc.detail)) from exc


def _generic_profile(url: str) -> dict[str, Any]:
    """A recipe for a URL no profile matches: its own directory, link-walked."""
    from backend.modules.docviewer.crawl import default_prefix

    prefix = default_prefix(url)
    parts = urlsplit(prefix)
    return {
        "id": "generic",
        "label": parts.netloc,
        "root": {"title": parts.netloc + parts.path, "url": url, "kind": "site"},
        "start_urls": [url],
        "allow_domains": [parts.hostname or ""],
        "allow_patterns": ["^" + re.escape(parts.path)],
        "deny_patterns": [],
        "frontier": "auto",
        "max_depth": 3,
        "max_pages": 200,
        "keep_scripts": True,
        "content_selector": None,
        "hierarchy": "links",
        "page_kind": "article",
        "collections": [],
        "tags": [],
        "params": {},
    }


def plan_import(
    *,
    profile_id: str | None = None,
    url: str | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve what an import would do, without starting it."""
    params = dict(params or {})
    if profile_id:
        profile = profiles.get_profile(profile_id)
        if profile is None:
            raise ImportFailed(f"no profile {profile_id!r}")
        if url:
            hit = profiles.match(url)
            if hit is not None and hit[0]["id"] == profile_id:
                params = {**hit[1], **params}
        return profiles.resolve(profile, params)
    if not url:
        raise ImportFailed("pass a profile_id or a url")
    hit = profiles.match(url)
    if hit is not None:
        profile, matched = hit
        return profiles.resolve(profile, {**matched, **params})
    return _generic_profile(url)


async def start_import(
    *,
    profile_id: str | None = None,
    url: str | None = None,
    params: dict[str, str] | None = None,
    max_pages: int | None = None,
    collections: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create (or resume) an import. Returns `{root_item_id, set_id, resumed, plan}`."""
    from backend.modules.docviewer import crawl
    from backend.modules.docviewer import store as docstore
    from backend.modules.docviewer.recipe import Recipe

    try:
        spec = plan_import(profile_id=profile_id, url=url, params=params)
    except ValueError as exc:
        raise ImportFailed(str(exc)) from exc
    starts = [_validate_start(u) for u in spec["start_urls"]]
    root = spec.get("root") or {}
    root_url = root.get("url") or starts[0]
    pages = max(1, min(int(max_pages or spec.get("max_pages") or 200), 2000))

    root_item, _created = store.upsert_by_url(
        root_url,
        collections=collections if collections is not None else spec.get("collections"),
        kind=root.get("kind") or "site",
        title=root.get("title") or root_url,
        index_mode="site",
        profile_id=spec["id"],
        tags=spec.get("tags") or [],
    )

    # Built before the resume branch so a resumed import follows the profile as it
    # is now: an edited profile (a selector, `keep_scripts`) reached only new imports.
    recipe = Recipe(
        start_urls=starts,
        allow_domains=list(spec.get("allow_domains") or []),
        allow_patterns=list(spec.get("allow_patterns") or []),
        deny_patterns=list(spec.get("deny_patterns") or []),
        index_pages=list(spec.get("index_pages") or []),
        frontier=spec.get("frontier") or "auto",
        sitemap_url=spec.get("sitemap_url"),
        keep_scripts=bool(spec.get("keep_scripts", True)),
        content_selector=spec.get("content_selector"),
        hierarchy=spec.get("hierarchy") or "flat",
        path_root=spec.get("path_root"),
        path_kinds=list(spec.get("path_kinds") or []),
        page_kind=spec.get("page_kind") or "article",
        profile_id=spec["id"],
        kb_root_item_id=root_item["id"],
        tags=list(spec.get("tags") or []),
        structured=True,
        title_strip=list(spec.get("title_strip") or []),
    )
    existing_set = (
        docstore.get_set(root_item["docset_id"]) if root_item["docset_id"] else None
    )
    if existing_set is not None:
        if crawl.is_running(existing_set["id"]):
            raise ImportFailed("this import is already running")
        docstore.update_set(
            existing_set["id"], max_pages=pages, recipe=recipe.to_dict()
        )
        crawl.start_crawl(
            existing_set["id"], int(spec.get("max_depth") or 3), force=force
        )
        emit(root_item["id"])
        return {
            "root_item_id": root_item["id"],
            "set_id": existing_set["id"],
            "resumed": True,
            "plan": spec,
        }

    doc_set = docstore.create_set(
        title=root.get("title") or spec.get("label") or root_url,
        seed_url=starts[0],
        prefix=root_url,
        library=site_library(root_url),
        max_pages=pages,
        recipe=recipe.to_dict(),
    )
    store.update_item(
        root_item["id"], docset_id=doc_set["id"], library=doc_set["library"]
    )
    crawl.start_crawl(doc_set["id"], int(spec.get("max_depth") or 3), force=force)
    emit(root_item["id"])
    return {
        "root_item_id": root_item["id"],
        "set_id": doc_set["id"],
        "resumed": False,
        "plan": spec,
    }
