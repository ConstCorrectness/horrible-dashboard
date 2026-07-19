from typing import Any, Literal

from pydantic import BaseModel

# The three corpora the index holds, each namespaced by an id prefix so a re-sync
# of one kind replaces only its own rows: `pkg:` / `schema:` / `doc:`.
SymdexKind = Literal["packages", "schema", "docs"]

KIND_PREFIXES: dict[str, str] = {
    "packages": "pkg:",
    "schema": "schema:",
    "docs": "doc:",
}


class ReindexRequest(BaseModel):
    kinds: list[SymdexKind] = ["packages", "schema", "docs"]


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
