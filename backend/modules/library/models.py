"""API-boundary models for the knowledge library module.

A **source** is one ingested item (a blog post, a note, or a media asset — an image
or a video; PDF/EPUB later). Each source is chunked and every chunk is stored as a
row in the shared vector store (collection = the library name), so semantic search
reuses the existing engine. See docs/modules/library.mdx.

**Media are embedded by proxy.** The app's embedder is text-only
(``database/embeddings.py``), so an image or video is embedded via the words that
describe it — alt text, caption, nearby heading, page title — harvested from the live
DOM by the browser's ``media`` op. The bytes themselves are *referenced* (``asset.src``),
never copied into the store. This makes "find the diagram about retries" work today
without a multimodal model; a real CLIP/SigLIP vector is a planned supplement, and
because LanceDB fixes vector width per table it lands as an additive sibling table
(``<library>__clip``) keyed by the same doc id — no migration of the text vectors.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["blog", "note", "image", "video"]
SourceStatus = Literal["queued", "fetching", "chunking", "embedding", "ready", "failed"]

# Source types whose content is a referenced binary rather than inline text.
MEDIA_TYPES: frozenset[str] = frozenset({"image", "video"})


class MediaAsset(BaseModel):
    """What we know about a referenced image/video, and where it came from.

    Everything here is either addressing (``src``, ``page_url``) or a text-proxy
    input (``alt``, ``caption``, ``context``). Dimensions/duration are kept for
    display and for ranking (a 20×20 sprite is rarely what you're looking for).
    """

    src: str
    kind: Literal["image", "video", "embed"] = "image"
    page_url: str | None = None
    alt: str | None = None
    caption: str | None = None
    context: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    poster: str | None = None
    mime: str | None = None


class SourceModel(BaseModel):
    """A catalog row: one ingested source and its ingestion status."""

    id: str
    library: str
    type: SourceType
    title: str
    url: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: SourceStatus
    error: str | None = None
    chunk_count: int = 0
    added_at: str
    asset: MediaAsset | None = None


class IngestRequest(BaseModel):
    """Add a source.

    `blog` needs `url`; `note` needs `text` (+ a `title`); `image`/`video` need
    `asset` (whose `src` addresses the media). For media, `text` is optional extra
    description that is embedded alongside the asset's own text proxy.
    """

    type: SourceType
    library: str = "default"
    url: str | None = None
    title: str | None = None
    text: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    asset: MediaAsset | None = None


class SourcesListResponse(BaseModel):
    sources: list[SourceModel]


class LibraryInfo(BaseModel):
    name: str
    source_count: int
    chunk_count: int


class LibrariesResponse(BaseModel):
    libraries: list[LibraryInfo]


class ChunkModel(BaseModel):
    index: int
    text: str


class ChunksResponse(BaseModel):
    source: SourceModel
    chunks: list[ChunkModel]


class LibrarySearchRequest(BaseModel):
    library: str = "default"
    text: str
    limit: int = Field(default=5, ge=1, le=50)


class SearchChunk(BaseModel):
    chunk_index: int
    text: str
    score: float


class SearchGroup(BaseModel):
    """Search hits collapsed to one entry per source, for citation."""

    source_id: str
    title: str
    type: str
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    top_score: float
    chunks: list[SearchChunk]
    # Present for image/video hits: what matched is proxy text, but what the caller
    # wants back is the asset itself.
    asset: MediaAsset | None = None
    # Which space(s) this hit came from — `text` (the app embedder, over prose or a
    # media asset's proxy text) and/or `clip` (the image itself). A `clip`-only hit
    # means the picture matched while its words did not, which is worth telling the
    # user: it's the difference between "we found the caption" and "we found the
    # image". `chunks` is empty for a clip-only hit — there was no passage.
    matched_by: list[Literal["text", "clip"]] = Field(default_factory=list)


class ClipStatus(BaseModel):
    """Availability + coverage of CLIP visual search (`GET /api/library/clip`)."""

    enabled: bool  # library.clipEnabled AND installed
    installed: bool  # the `clip` extra is importable
    model: str
    dim: int
    media_sources: int  # image/video sources in the catalog
    libraries_indexed: list[str]  # libraries with a CLIP sibling table


class ReindexResult(BaseModel):
    started: bool
    queued: int


class LibrarySearchResponse(BaseModel):
    query: str
    library: str
    groups: list[SearchGroup]


class DeleteResult(BaseModel):
    deleted: bool
    id: str
