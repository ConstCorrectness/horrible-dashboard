"""HTTP surface for the symdex index: reindex kick, status, and search."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from backend.modules.symdex.index import symdex_index
from backend.modules.symdex.models import (
    ReferenceCorpus,
    ReferenceDoc,
    ReferenceHit,
    ReferenceMember,
    ReferenceModule,
    ReferenceSources,
    ReindexRequest,
    SearchResponse,
    SymdexStatus,
)

router = APIRouter(prefix="/symdex", tags=["symdex"])


@router.post("/reindex")
async def reindex(body: ReindexRequest) -> dict[str, object]:
    """Kick a rebuild of the given kinds, detached — progress streams on the
    `symdex` /ws channel; poll /status for the outcome."""
    if symdex_index.building:
        return {"started": False, "reason": "already building"}
    asyncio.create_task(symdex_index.reindex(list(body.kinds)))
    return {"started": True, "kinds": list(body.kinds)}


@router.get("/status", response_model=SymdexStatus)
async def status() -> SymdexStatus:
    return SymdexStatus(**symdex_index.status())


@router.get("/search", response_model=SearchResponse)
async def search(q: str, kind: str = "", limit: int = 8) -> SearchResponse:
    result = await symdex_index.search(q, kind or None, max(1, min(limit, 50)))
    return SearchResponse(**result)


# --- the Python reference pane --------------------------------------------------
#
# Browses the `code_symbols` rows completion already reads (so it needs no index of
# its own), and resolves a symbol's *whole* docstring on demand — see
# `symdex/reference.py`.


def _interpreter() -> str | None:
    from backend.modules.lsp.pyenv import resolve_python_interpreter

    return resolve_python_interpreter(None)


@router.get("/reference/sources", response_model=ReferenceSources)
async def reference_sources() -> ReferenceSources:
    from backend.modules.lsp import symbol_store
    from backend.modules.lsp.pyenv import installed_versions
    from backend.modules.symdex import reference

    interpreter = await asyncio.to_thread(_interpreter)
    rows = await asyncio.to_thread(symbol_store.reference_sources)
    versions = (
        await asyncio.to_thread(installed_versions, interpreter) if interpreter else {}
    )
    python = await asyncio.to_thread(reference.python_version, interpreter) if interpreter else ""
    corpora = []
    for row in rows:
        source = str(row["source"])
        corpus, _, name = source.partition(":")
        version = python if corpus == "std" else versions.get(name, "") if corpus == "pkg" else ""
        corpora.append(
            ReferenceCorpus(
                corpus=corpus,
                source=source,
                name=name,
                version=version,
                count=int(row["count"]),  # type: ignore[call-overload]
            )
        )
    return ReferenceSources(
        python=python,
        interpreter=interpreter or "",
        corpora=corpora,
        empty=not corpora,
    )


@router.get("/reference/modules", response_model=list[ReferenceModule])
async def reference_modules(source: str) -> list[ReferenceModule]:
    from backend.modules.lsp import symbol_store

    rows = await asyncio.to_thread(symbol_store.reference_modules, source)
    return [ReferenceModule(**r) for r in rows]  # type: ignore[arg-type]


@router.get("/reference/members", response_model=list[ReferenceMember])
async def reference_members(source: str, module: str) -> list[ReferenceMember]:
    from backend.modules.lsp import symbol_store

    rows = await asyncio.to_thread(symbol_store.reference_members, source, module)
    return [ReferenceMember(**r) for r in rows]  # type: ignore[arg-type]


@router.get("/reference/search", response_model=list[ReferenceHit])
async def reference_search(q: str, limit: int = 40) -> list[ReferenceHit]:
    from backend.modules.lsp import symbol_store

    rows = await asyncio.to_thread(
        symbol_store.reference_search, q, max(1, min(limit, 100))
    )
    return [ReferenceHit(**r) for r in rows]  # type: ignore[arg-type]


@router.get("/reference/doc", response_model=ReferenceDoc)
async def reference_doc(source: str, module: str, name: str) -> ReferenceDoc:
    from backend.modules.lsp.pyenv import installed_versions
    from backend.modules.symdex import reference

    interpreter = await asyncio.to_thread(_interpreter)
    data = await asyncio.to_thread(reference.full_doc, source, module, name, interpreter)
    versions = (
        await asyncio.to_thread(installed_versions, interpreter) if interpreter else {}
    )
    python = await asyncio.to_thread(reference.python_version, interpreter) if interpreter else "3"
    corpus = source.split(":", 1)[0]
    importable = "." not in name and not (corpus == "sdk" and module.startswith("dash"))
    return ReferenceDoc(
        **data,
        importLine=f"from {module} import {name}" if importable else "",
        upstream=reference.upstream(
            source, module, name, versions=versions, python=python
        ),
    )
