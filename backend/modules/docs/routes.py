"""REST surface for the documentation popup (`/api/docs/*`).

Two endpoints. The frontend owns which sources are enabled (a setting) and the
`lsp` source (the LSP client is frontend-side), so this is the server half of the
chain rather than the whole of it — see `sources.resolve_docs`.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.modules.docs.models import (
    CompleteRequest,
    CompleteResponse,
    DocLookupRequest,
    DocLookupResponse,
)
from backend.modules.docs.sources import complete_kernel, resolve_docs

router = APIRouter(prefix="/docs", tags=["docs"])


@router.post("/lookup", response_model=DocLookupResponse)
async def lookup(req: DocLookupRequest) -> DocLookupResponse:
    symbol = req.symbol.strip()
    # The kernel source resolves an expression from `code`+`cursor_pos`, so it can
    # work with no symbol at all; every other source needs a name.
    has_kernel_context = req.context is not None and req.context.code is not None
    if not symbol and not has_kernel_context:
        return DocLookupResponse(symbol="", entries=[], tried=[])
    entries, tried, notes = await resolve_docs(
        symbol, req.sources, req.context, req.lang
    )
    return DocLookupResponse(symbol=symbol, entries=entries, tried=tried, notes=notes)


@router.post("/complete", response_model=CompleteResponse)
async def complete(req: CompleteRequest) -> CompleteResponse:
    """Completions from the notebook's live kernel — what the static index cannot
    know (`model.` on a loaded model lists its real submodules). The editor merges
    these with indexed symbols, which carry docs and auto-imports."""
    return await complete_kernel(req.notebook_path, req.code, req.cursor_pos)
