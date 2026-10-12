"""HTTP surface of the knowledge base: `/api/kb`. See docs/modules/kb.mdx."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query

from backend.modules.kb import imports, profiles, service, store
from backend.modules.kb.broadcast import publish_removed
from backend.modules.kb.models import (
    Collection,
    ImportRequest,
    ImportResponse,
    ProfileMatch,
    ProfilesResponse,
    CollectionsResponse,
    CreateCollectionRequest,
    CreateItemRequest,
    IndexRequest,
    Item,
    ItemsResponse,
    LookupResponse,
    OkResponse,
    SearchRequest,
    SearchResponse,
    Summary,
    TreeNode,
    UpdateItemRequest,
)

router = APIRouter(prefix="/kb", tags=["kb"])


def _item_or_404(item_id: str) -> Item:
    item = service.item_model(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return item


# ---- items ---------------------------------------------------------------------


@router.get("/items", response_model=ItemsResponse)
def list_items(
    collection: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    parent_id: str | None = None,
    top_level: bool = True,
    starred: bool | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> ItemsResponse:
    """Catalog listing. `top_level` (default on) hides lessons under a course;
    pass `parent_id` to list one course's children."""
    rows, total = store.list_items(
        collection=collection,
        tag=tag,
        status=status,
        kind=kind,
        q=q,
        parent_id=parent_id,
        top_level=top_level and not parent_id and not q,
        starred=starred,
        limit=limit,
        offset=offset,
    )
    return ItemsResponse(items=service.enrich(rows), total=total)


@router.post("/items", response_model=Item)
async def create_item(req: CreateItemRequest) -> Item:
    """Save a URL (or a note). Idempotent per canonical URL: saving a page that is
    already in the catalog updates it with the fields sent."""
    if not req.url and req.kind != "note":
        raise HTTPException(status_code=400, detail="url is required")
    if req.url and not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    fields = {
        k: v
        for k, v in {
            "kind": req.kind,
            "title": req.title,
            "description": req.description,
            "tags": req.tags,
            "status": req.status,
            "notes": req.notes,
            "rating": req.rating,
            "starred": req.starred,
            "parent_id": req.parent_id,
        }.items()
        if v is not None
    }
    if req.url:
        row, created = store.upsert_by_url(
            req.url, collections=req.collections, **fields
        )
        if created and "kind" not in fields:
            store.update_item(row["id"], kind="bookmark")
    else:
        row = store.create_item(collections=req.collections, **fields)
    if req.index_mode != row["index_mode"] and req.index_mode != "link":
        try:
            await service.set_index_mode(row["id"], req.index_mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    service.emit(row["id"])
    return _item_or_404(row["id"])


@router.get("/items/{item_id}", response_model=Item)
def get_item(item_id: str) -> Item:
    return _item_or_404(item_id)


@router.patch("/items/{item_id}", response_model=Item)
def update_item(item_id: str, req: UpdateItemRequest) -> Item:
    fields = req.model_dump(exclude_unset=True)
    collections = fields.pop("collections", None)
    if store.update_item(item_id, **fields) is None:
        raise HTTPException(status_code=404, detail="item not found")
    if collections is not None:
        store.set_collections(item_id, collections)
    service.emit(item_id)
    return _item_or_404(item_id)


@router.post("/items/{item_id}/opened", response_model=Item)
def mark_opened(item_id: str) -> Item:
    """The user opened this item (in the browser or offline). Moves `to_read` to
    `reading`; never un-finishes a `done` item."""
    if store.touch_opened(item_id) is None:
        raise HTTPException(status_code=404, detail="item not found")
    service.emit(item_id)
    return _item_or_404(item_id)


@router.delete("/items/{item_id}", response_model=OkResponse)
def delete_item(item_id: str, keep_children: bool = False) -> OkResponse:
    """Delete an item, everything under it (unless `keep_children`), and the
    indexed text they own. Deleting an import's root deletes the imported site."""
    row = store.get_item(item_id)
    if row is not None and row["docset_id"]:
        from backend.modules.docviewer import crawl

        if crawl.is_running(row["docset_id"]):
            raise HTTPException(status_code=409, detail="stop the import first")
    doomed = store.delete_item(item_id, cascade=not keep_children)
    if not doomed:
        raise HTTPException(status_code=404, detail="item not found")
    for row in doomed:
        service.drop_content(row)
    publish_removed([row["id"] for row in doomed])
    return OkResponse(deleted=len(doomed))


@router.get("/items/{item_id}/tree", response_model=TreeNode)
def item_tree(item_id: str) -> TreeNode:
    node = service.tree(item_id)
    if node is None:
        raise HTTPException(status_code=404, detail="item not found")
    return node


@router.post("/items/{item_id}/index", response_model=Item)
async def set_index_mode(item_id: str, req: IndexRequest) -> Item:
    try:
        await service.set_index_mode(item_id, req.mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    service.emit(item_id)
    return _item_or_404(item_id)


@router.get("/lookup", response_model=LookupResponse)
def lookup(url: str) -> LookupResponse:
    """Is this URL in the catalog? Matched on canonical form."""
    row = store.get_by_url(url)
    return LookupResponse(item=service.enrich([row])[0] if row else None)


@router.get("/summary", response_model=Summary)
def summary() -> Summary:
    return Summary(**store.summary())


# ---- collections ---------------------------------------------------------------


@router.get("/collections", response_model=CollectionsResponse)
def list_collections() -> CollectionsResponse:
    return CollectionsResponse(
        collections=[Collection(**c) for c in store.list_collections()]
    )


@router.post("/collections", response_model=Collection)
def create_collection(req: CreateCollectionRequest) -> Collection:
    try:
        return Collection(**store.create_collection(req.name, req.description))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="collection exists") from exc


@router.delete("/collections/{collection_id}", response_model=OkResponse)
def delete_collection(collection_id: str) -> OkResponse:
    if not store.delete_collection(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")
    return OkResponse(deleted=1)


# ---- retrieval -----------------------------------------------------------------


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    try:
        return await service.search(req)
    except service.SearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---- site imports + profiles ---------------------------------------------------


@router.post("/imports", response_model=ImportResponse)
async def start_import(req: ImportRequest) -> ImportResponse:
    """Import a whole site or course: archives for offline reading, text for the
    agent, a catalog tree for progress. Returns immediately; pages arrive on the
    `kb` and `docviewer` channels. `async` because the crawl is a detached task."""
    try:
        started = await imports.start_import(
            profile_id=req.profile_id,
            url=req.url,
            params=req.params,
            max_pages=req.max_pages,
            collections=req.collections,
            force=req.force,
        )
    except imports.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportResponse(**started)


@router.post("/imports/{item_id}/stop", response_model=OkResponse)
def stop_import(item_id: str) -> OkResponse:
    from backend.modules.docviewer import crawl

    row = store.get_item(item_id)
    if row is None or not row["docset_id"]:
        raise HTTPException(status_code=404, detail="no import for this item")
    if not crawl.cancel_crawl(row["docset_id"]):
        raise HTTPException(status_code=409, detail="the import is not running")
    return OkResponse()


@router.post("/imports/{item_id}/reindex", response_model=OkResponse)
async def reindex_import(item_id: str) -> OkResponse:
    """Re-extract and re-embed an imported site from its archives — after an
    extraction fix, or once the embedder is back — without crawling it again.
    async: `start_reindex` creates a task on the running loop."""
    from backend.modules.docviewer import crawl

    row = store.get_item(item_id)
    if row is None or not row["docset_id"]:
        raise HTTPException(status_code=404, detail="no import for this item")
    if not crawl.start_reindex(row["docset_id"]):
        raise HTTPException(status_code=409, detail="the import is running")
    return OkResponse()


@router.get("/profiles", response_model=ProfilesResponse)
def list_profiles() -> ProfilesResponse:
    return ProfilesResponse(profiles=profiles.list_profiles())


@router.get("/profiles/match", response_model=ProfileMatch)
def match_profile(url: str) -> ProfileMatch:
    hit = profiles.match(url)
    if hit is None:
        return ProfileMatch()
    return ProfileMatch(profile=hit[0], params=hit[1])


@router.post("/profiles", response_model=dict)
def save_profile(spec: dict) -> dict:
    try:
        return profiles.save_profile(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/profiles/{profile_id}", response_model=OkResponse)
def delete_profile(profile_id: str) -> OkResponse:
    if not profiles.delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="no such user profile")
    return OkResponse(deleted=1)
