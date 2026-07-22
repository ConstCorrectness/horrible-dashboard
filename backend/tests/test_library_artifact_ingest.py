"""Library ingestion of artifact-backed sources: `pdf` and `page`.

Embeddings run against whatever `get_embedding` resolves to (hash fallback when no
provider is up), same as the other library tests — what's under test is the ingest
branch, not the embedder.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.artifacts import store as artifacts
from backend.modules.library import store
from backend.modules.library.ingest import ingest_source
from backend.modules.library.models import IngestRequest

from backend.tests.test_drive_pdf import make_pdf


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _ingest(source: dict, req: IngestRequest) -> dict:
    asyncio.run(ingest_source(source["id"], req))
    updated = store.get_source(source["id"])
    assert updated is not None
    return updated


def test_pdf_source_ingests_extracted_text() -> None:
    artifact = artifacts.store_bytes(
        make_pdf("Attention is all you need"),
        kind="pdf",
        mime="application/pdf",
        filename="attention.pdf",
    )
    source = store.create_source(
        library="default",
        type="pdf",
        title="attention.pdf",
        url=None,
        author=None,
        tags=[],
        artifact_id=artifact["id"],
    )
    updated = _ingest(
        source,
        IngestRequest(type="pdf", title="attention.pdf", artifact_id=artifact["id"]),
    )
    assert updated["status"] == "ready"
    assert updated["chunk_count"] >= 1
    assert updated["artifact_id"] == artifact["id"]

    chunks = store.chunk_docs_for(updated)
    assert any("Attention is all you need" in c["text"] for c in chunks)


def test_scanned_pdf_fails_with_reason() -> None:
    artifact = artifacts.store_bytes(
        make_pdf(None), kind="pdf", mime="application/pdf", filename="scan.pdf"
    )
    source = store.create_source(
        library="default",
        type="pdf",
        title="scan.pdf",
        url=None,
        author=None,
        tags=[],
        artifact_id=artifact["id"],
    )
    updated = _ingest(
        source, IngestRequest(type="pdf", title="scan.pdf", artifact_id=artifact["id"])
    )
    assert updated["status"] == "failed"
    assert "no extractable text" in updated["error"]


def test_page_source_ingests_extracted_article() -> None:
    html = (
        "<html><head><title>Saved Page</title></head><body><article>"
        + " ".join(f"Paragraph {i} of the captured page." for i in range(40))
        + "</article></body></html>"
    )
    artifact = artifacts.store_bytes(
        html.encode(),
        kind="page",
        mime="text/html",
        filename="saved.html",
        origin_url="https://example.com/post",
    )
    source = store.create_source(
        library="default",
        type="page",
        title="saved.html",
        url="https://example.com/post",
        author=None,
        tags=[],
        artifact_id=artifact["id"],
    )
    updated = _ingest(
        source,
        IngestRequest(
            type="page", url="https://example.com/post", artifact_id=artifact["id"]
        ),
    )
    assert updated["status"] == "ready"
    assert updated["title"] == "Saved Page"
    assert updated["chunk_count"] >= 1


def test_missing_blob_fails_cleanly() -> None:
    source = store.create_source(
        library="default",
        type="pdf",
        title="ghost.pdf",
        url=None,
        author=None,
        tags=[],
        artifact_id="0" * 32,
    )
    updated = _ingest(
        source, IngestRequest(type="pdf", title="ghost.pdf", artifact_id="0" * 32)
    )
    assert updated["status"] == "failed"
    assert "artifact blob missing" in updated["error"]


def test_add_source_route_validates_artifact(client: TestClient) -> None:
    # No artifact_id at all.
    res = client.post("/api/library/sources", json={"type": "pdf"})
    assert res.status_code == 400

    # Kind mismatch: a page artifact filed as a pdf source.
    page = artifacts.store_bytes(
        b"<html/>", kind="page", mime="text/html", filename="p.html"
    )
    res = client.post(
        "/api/library/sources", json={"type": "pdf", "artifact_id": page["id"]}
    )
    assert res.status_code == 400
    assert "not a pdf" in res.json()["detail"]

    # Happy path: title falls back to the artifact filename.
    pdf = artifacts.store_bytes(
        make_pdf("hello"), kind="pdf", mime="application/pdf", filename="hello.pdf"
    )
    res = client.post(
        "/api/library/sources", json={"type": "pdf", "artifact_id": pdf["id"]}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "hello.pdf"
    assert body["artifact_id"] == pdf["id"]
    assert body["status"] == "queued"
