"""HTTP surface for the code-intelligence index: per-file outlines and cross-repo
symbol search. Path access reuses the files module's workspace-root boundary
(`_resolve`/`_roots`) so the same path-traversal guard applies everywhere. See
docs/modules/code.mdx."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from backend.modules.code.index import code_index
from backend.modules.code.models import (
    DocumentSymbols,
    FindResult,
    ReindexResult,
    SemanticSearchResult,
)
from backend.modules.code.semantic import semantic_index
from backend.modules.code.ts import lang_for_path
from backend.modules.files.routes import _resolve, _roots

router = APIRouter(prefix="/code", tags=["code"])


@router.get("/symbols", response_model=DocumentSymbols)
def document_symbols(path: str) -> DocumentSymbols:
    """The definitions in one file (functions/classes/methods/…), for the outline
    pane. `path` may be absolute or workspace-relative; it must resolve inside a root."""
    target = _resolve(path)
    return DocumentSymbols(
        path=str(target),
        language=lang_for_path(target),
        symbols=code_index.document_symbols(target),
    )


@router.get("/find", response_model=FindResult)
def find_symbols(q: str, limit: int = 50) -> FindResult:
    """Fuzzy symbol search across all workspace roots (exact > prefix > substring >
    subsequence). Empty `q` returns the first `limit` indexed symbols."""
    return FindResult(query=q, hits=code_index.find_symbols(q, _roots(), limit))


@router.get("/search", response_model=SemanticSearchResult)
async def semantic_search(q: str, limit: int = 20) -> SemanticSearchResult:
    """Semantic search: embed `q` and cosine-rank it against the embedded definitions.
    If nothing is indexed yet, auto-kick a background reindex and report `building`."""
    roots = _roots()
    res = await semantic_index.search(q, roots, limit)
    if (
        not res["results"]
        and not semantic_index.building
        and semantic_index.count() == 0
    ):
        asyncio.create_task(semantic_index.reindex(roots))
        res["building"] = True
    return SemanticSearchResult(**res)


@router.post("/reindex", response_model=ReindexResult)
async def reindex() -> ReindexResult:
    """Kick a full background rebuild of the semantic index. Returns immediately."""
    asyncio.create_task(semantic_index.reindex(_roots()))
    return ReindexResult(started=True)
