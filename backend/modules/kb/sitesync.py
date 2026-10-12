"""Filing a site import's pages into the catalog as they are captured.

The doc-set crawl owns the archive and the page tree; this module mirrors each
captured page into a `kb_items` row so the import shows up as a course with lessons
(or a site with primers) the user can track, annotate and search — and so a page the
user had already bookmarked is *adopted* by the import rather than duplicated.

Called from `docviewer/site_crawl.py` only for sets the knowledge base created
(`recipe.kb_root_item_id` set). Never raises into the crawl: a catalog hiccup must
not cost the user an archived page.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.kb import store
from backend.modules.kb.broadcast import publish_item

logger = logging.getLogger(__name__)


def _emit(item_id: str) -> None:
    from backend.modules.kb.service import item_model

    item = item_model(item_id)
    if item is not None:
        publish_item(item.model_dump())


def file_page(
    *,
    root_item_id: str,
    docset_id: str,
    docpage_id: str,
    parent_docpage_id: str | None,
    url: str,
    declared_canonical: str | None,
    title: str,
    kind: str,
    ordinal: int,
    is_root: bool,
    library: str,
    artifact_id: str,
    tags: list[str],
    content_signal: str | None,
    profile_id: str | None,
) -> str | None:
    """Create or adopt the catalog item for one captured page. Returns its id."""
    try:
        parent_item = root_item_id
        if parent_docpage_id:
            parent = _item_for_docpage(docset_id, parent_docpage_id)
            if parent is not None:
                parent_item = parent
        fields: dict[str, Any] = {
            "title": title,
            "docset_id": docset_id,
            "docpage_id": docpage_id,
            "artifact_id": artifact_id,
            "library": library,
            "index_mode": "site",
            "content_signal": content_signal,
            "profile_id": profile_id,
        }
        if declared_canonical:
            fields["alias_url"] = declared_canonical
        if is_root:
            # The page *is* the import's root (an apxml course page): file it onto
            # the root item instead of creating a child of itself. The root was
            # created before the crawl, titled with the URL slug at best; the page's
            # own title replaces a slug, never a title the user typed.
            root = store.get_item(root_item_id)
            if root is not None and " " in root["title"].strip():
                fields.pop("title")
            store.update_item(root_item_id, **fields)
            _emit(root_item_id)
            return root_item_id
        existing = store.get_by_url(url)
        if existing is None and declared_canonical:
            existing = store.get_by_url(declared_canonical)
        if existing is not None:
            # Adopt: the user's status, notes, rating and collections stay. Text it
            # was indexed with on its own (a saved page in `kb-pages`) is replaced
            # by the import's, so drop it rather than orphan it.
            if existing["source_id"] and existing["docset_id"] != docset_id:
                from backend.modules.kb.service import drop_content

                drop_content(existing)
                fields["source_id"] = None
            store.update_item(
                existing["id"],
                **fields,
                parent_id=parent_item,
                ordinal=ordinal,
                kind=kind if existing["kind"] == "bookmark" else existing["kind"],
                tags=sorted({*existing["tags"], *tags}),
            )
            item_id = existing["id"]
        else:
            item = store.create_item(
                url=url,
                kind=kind,
                parent_id=parent_item,
                ordinal=ordinal,
                tags=tags,
                **fields,
            )
            item_id = item["id"]
        _emit(item_id)
        return item_id
    except Exception:  # noqa: BLE001 — the archive is already stored; log and go on
        logger.exception("kb: could not file %s into the catalog", url)
        return None


def attach_source(item_id: str | None, source_id: str | None) -> None:
    if not item_id:
        return
    try:
        store.update_item(item_id, source_id=source_id)
        _emit(item_id)
    except Exception:  # noqa: BLE001
        logger.exception("kb: could not attach source %s to %s", source_id, item_id)


def _item_for_docpage(docset_id: str, docpage_id: str) -> str | None:
    store.init_kb_db()
    with store.get_db_conn() as conn:
        r = conn.execute(
            "SELECT id FROM kb_items WHERE docset_id = ? AND docpage_id = ? LIMIT 1",
            (docset_id, docpage_id),
        ).fetchone()
    return str(r["id"]) if r else None


def relink(docset_id: str, root_item_id: str) -> int:
    """Fix parents once the whole crawl is in.

    A sitemap can list a lesson before its chapter, and then the lesson was filed
    under the root because its parent had no item yet. With every page captured the
    doc-set tree is complete, so re-derive each item's parent from it. Returns how
    many items moved.
    """
    from backend.modules.docviewer import store as docstore

    pages = {p["id"]: p for p in docstore.list_pages(docset_id)}
    rows, _total = store.list_items(docset_id=docset_id, limit=5000)
    item_by_page = {r["docpage_id"]: r for r in rows if r["docpage_id"]}
    moved = 0
    for row in rows:
        if row["id"] == root_item_id or not row["docpage_id"]:
            continue
        page = pages.get(row["docpage_id"])
        if page is None:
            continue
        parent_page = page.get("parent_id")
        parent = item_by_page.get(parent_page) if parent_page else None
        want = parent["id"] if parent is not None else root_item_id
        if row["parent_id"] != want:
            store.update_item(row["id"], parent_id=want)
            moved += 1
    return moved


def detach_set(docset_id: str) -> int:
    """A doc set is being deleted: turn its items back into plain links.

    Content references are cleared (the archives and sources are about to go); the
    items themselves — and the user's state on them — stay.
    """
    from backend.modules.kb.service import drop_content

    rows, _total = store.list_items(docset_id=docset_id, limit=5000)
    for row in rows:
        # The page loop deletes the sources pages hold; an item can also point at a
        # source whose ingest failed, which no page row records.
        drop_content(row, include_site=False)
        store.update_item(
            row["id"],
            docset_id=None,
            docpage_id=None,
            artifact_id=None,
            source_id=None,
            library=None,
            index_mode="link",
        )
        _emit(row["id"])
    return len(rows)
