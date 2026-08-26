"""HTTP surface for the artifact store: serve, inspect, upload, delete.

`GET /api/artifacts/{id}` is the node's only byte-serving route — the PDF and
page viewers load stored blobs from here. Captured pages are served with a
sandboxing CSP on top of the viewer's iframe ``sandbox`` attribute: the stored
HTML is third-party content and never gets an origin.

Whether it gets *scripts* depends on how it was captured, and that is the one
security decision on this route. A `research` capture is sanitized at build time
(`build_page` drops every `<script>`) and served inert. A `docviewer` doc-set
capture keeps its scripts — a documentation page whose tabs, collapsibles and
client-side search are all JS reads as broken without them — and declares
`meta.scripts`, which unlocks `sandbox allow-scripts` here.

`allow-scripts` is only safe because `allow-same-origin` is absent: the document
lands in a **unique opaque origin**, so its script can hydrate and animate the
page but cannot reach our DOM, our storage, our cookies, or `/api`. The two
directives together would void the sandbox entirely. `default-src 'none'` then
denies the page any network of its own, so nothing that was not inlined at
capture time can be fetched.
"""

from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.modules.artifacts.models import (
    ArtifactModel,
    ArtifactsListResponse,
    DeleteArtifactResult,
    UploadResponse,
)
from backend.modules.artifacts import store
from backend.modules.artifacts.store import page_csp

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Multipart uploads are PDFs only for now; captured pages and reports are stored
# server-side by their own flows, never uploaded raw by the browser.
_UPLOAD_MIMES = {"application/pdf"}
_MAX_UPLOAD_BYTES = 100_000_000


def _get_or_404(artifact_id: str) -> dict:
    # Reject malformed ids before touching the database: the id is the only
    # user-controlled input on the byte route, and a well-formed id can only
    # ever resolve to a hash-derived path inside the store.
    if not _ID_RE.match(artifact_id):
        raise HTTPException(status_code=404, detail="artifact not found")
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return artifact


@router.get("")
def list_artifacts(kind: str | None = None) -> ArtifactsListResponse:
    return ArtifactsListResponse(
        artifacts=[ArtifactModel(**a) for a in store.list_artifacts(kind)]
    )


@router.get("/{artifact_id}/meta")
def artifact_meta(artifact_id: str) -> ArtifactModel:
    return ArtifactModel(**_get_or_404(artifact_id))


@router.get("/{artifact_id}")
def serve_artifact(artifact_id: str) -> FileResponse:
    artifact = _get_or_404(artifact_id)
    path = store.artifact_path(artifact_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact blob missing")
    headers = {"X-Content-Type-Options": "nosniff"}
    if artifact["kind"] == "page":
        # Defense in depth: the viewing iframe already carries a `sandbox`
        # attribute, but anything that loads this URL directly gets the same
        # treatment. See the module docstring for why `allow-scripts` without
        # `allow-same-origin` is the whole containment.
        headers["Content-Security-Policy"] = page_csp(artifact)
    return FileResponse(
        path,
        media_type=artifact["mime"],
        filename=artifact["filename"],
        content_disposition_type="inline",
        headers=headers,
    )


@router.post("/upload")
async def upload_artifact(file: UploadFile) -> UploadResponse:
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in _UPLOAD_MIMES:
        raise HTTPException(
            status_code=415, detail=f"unsupported upload type: {mime or 'unknown'}"
        )
    hasher = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1_048_576)
        if not chunk:
            break
        size += len(chunk)
        if size > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="upload exceeds 100 MB limit")
        hasher.update(chunk)
        chunks.append(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="empty upload")
    artifact = store.store_bytes(
        b"".join(chunks),
        kind="pdf",
        mime=mime,
        filename=file.filename or "upload.pdf",
    )
    return UploadResponse(artifact=ArtifactModel(**artifact))


@router.delete("/{artifact_id}")
def delete_artifact(artifact_id: str) -> DeleteArtifactResult:
    _get_or_404(artifact_id)
    return DeleteArtifactResult(
        deleted=store.delete_artifact(artifact_id), id=artifact_id
    )
