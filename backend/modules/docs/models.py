"""Wire types for the documentation popup (`/api/docs/*`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: The sources a lookup may consult, in the order the chain tries them.
#:
#: `lsp` is absent on purpose: the LSP client lives in the *frontend* (see
#: docs/architecture/lsp.mdx — the backend is a dumb JSON-RPC pipe), so that source
#: is resolved client-side and merged with whatever these return.
DocSourceId = Literal["kernel", "index", "web"]


class DocLookupContext(BaseModel):
    """Where the symbol was found, for the sources that need more than a name.

    `kernel` needs the surrounding code and a cursor offset rather than a symbol,
    because Jupyter's `inspect_request` resolves the expression itself — `df.merge`
    means something only in a namespace where `df` exists, which is exactly the
    advantage a live kernel has over a static index.
    """

    notebook_path: str | None = None
    code: str | None = None
    cursor_pos: int | None = None


class DocLookupRequest(BaseModel):
    symbol: str = ""
    sources: list[DocSourceId] = Field(
        default_factory=lambda: ["kernel", "index", "web"]
    )
    context: DocLookupContext | None = None
    lang: str = "python"


class DocEntry(BaseModel):
    source: DocSourceId
    title: str = ""
    signature: str = ""
    #: Markdown. The frontend renders it through the editor's existing sanitizing
    #: markdown renderer — a docstring is untrusted text from an installed package
    #: or, for `web`, from an arbitrary page.
    body: str = ""
    url: str = ""


class DocLookupResponse(BaseModel):
    symbol: str = ""
    entries: list[DocEntry] = Field(default_factory=list)
    #: Sources that were asked and had nothing, so the caller can say "no docs
    #: found" rather than leaving a tooltip empty. Distinguishing "not configured"
    #: from "found nothing" is the difference between a bug and an answer.
    tried: list[DocSourceId] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
