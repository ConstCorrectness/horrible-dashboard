from typing import Any, Literal

from pydantic import BaseModel

# The five corpora the index holds, each namespaced by an id prefix so a re-sync
# of one kind replaces only its own rows: `pkg:` / `std:` / `sdk:` / `schema:` / `doc:`.
SymdexKind = Literal["packages", "stdlib", "sdk", "schema", "docs"]

KIND_PREFIXES: dict[str, str] = {
    "packages": "pkg:",
    "stdlib": "std:",
    # The dashboard's own Python APIs (`dash`, `backend.sdk`, `horrible_train`).
    "sdk": "sdk:",
    "schema": "schema:",
    "docs": "doc:",
}


class ReindexRequest(BaseModel):
    kinds: list[SymdexKind] = ["packages", "stdlib", "sdk", "schema", "docs"]


class SymdexStatus(BaseModel):
    building: bool
    total: int
    counts: dict[str, int] = {}
    # The embedding model the collection was built with (None = never built).
    embed_model: str | None = None
    # True when the current embedder no longer matches the collection (model
    # changed, or only the offline hash fallback is available) — search returns
    # nothing until a reindex under the new model.
    reindex_needed: bool = False


class SymdexResult(BaseModel):
    id: str
    kind: str
    text: str
    metadata: dict[str, Any] = {}
    score: float


class SearchResponse(BaseModel):
    query: str
    status: Literal["ok", "empty", "building", "reindex_needed"]
    results: list[SymdexResult] = []


# --- the reference pane ---------------------------------------------------------


class ReferenceCorpus(BaseModel):
    #: `std`, `pkg` or `sdk`.
    corpus: str
    #: The `code_symbols` source: `std:json`, `pkg:torch`, `sdk:dash`.
    source: str
    name: str
    #: Installed version for a package, the interpreter's for the stdlib, "" for ours.
    version: str = ""
    count: int = 0


class ReferenceSources(BaseModel):
    python: str = ""
    interpreter: str = ""
    corpora: list[ReferenceCorpus] = []
    #: Nothing indexed yet: the pane offers a reindex rather than an empty tree.
    empty: bool = False


class ReferenceModule(BaseModel):
    module: str
    count: int


class ReferenceMember(BaseModel):
    name: str
    kind: str
    signature: str = ""
    doc: str = ""
    members: list["ReferenceMember"] = []


class ReferenceHit(BaseModel):
    name: str
    kind: str
    signature: str = ""
    doc: str = ""
    source: str
    module: str


class ReferenceUpstream(BaseModel):
    url: str
    label: str
    #: `anchor` (lands on the symbol), `search` (a versioned search), or `release`.
    exact: str


class ReferenceDoc(BaseModel):
    source: str
    module: str
    name: str
    signature: str = ""
    #: The **whole** docstring — the index keeps only its first paragraph.
    doc: str = ""
    file: str = ""
    line: int = 0
    #: Set when `module` only re-exports the name and the page was read from the
    #: module that defines it.
    definedIn: str = ""
    #: `from <module> import <Name>`, or "" where there is no import (a `dash` handle,
    #: a method).
    importLine: str = ""
    upstream: ReferenceUpstream | None = None
