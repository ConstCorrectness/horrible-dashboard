"""Artifact store: content addressing, dedup/refcounting, and the byte route."""

import io

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.artifacts import store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_store_and_get_roundtrip() -> None:
    a = store.store_bytes(
        b"hello blob",
        kind="page",
        mime="text/html",
        filename="hello.html",
        origin_url="https://example.com/x",
        meta={"title": "Hello"},
    )
    assert a["size"] == 10
    assert a["kind"] == "page"
    assert a["meta"]["title"] == "Hello"
    path = store.artifact_path(a["id"])
    assert path is not None and path.read_bytes() == b"hello blob"
    # Blob path is derived from the hash, sharded by its first two hex chars.
    assert path.parent.name == a["sha256"][:2]
    assert path.name == a["sha256"]


def test_dedup_shares_blob_and_delete_refcounts() -> None:
    a = store.store_bytes(
        b"same bytes", kind="pdf", mime="application/pdf", filename="a.pdf"
    )
    b = store.store_bytes(
        b"same bytes", kind="pdf", mime="application/pdf", filename="b.pdf"
    )
    assert a["id"] != b["id"]
    assert a["sha256"] == b["sha256"]
    path = store.artifact_path(a["id"])
    assert path is not None and path.exists()

    assert store.delete_artifact(a["id"]) is True
    # b still references the hash, so the blob survives.
    assert path.exists()
    assert store.delete_artifact(b["id"]) is True
    assert not path.exists()


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        store.store_bytes(
            b"x", kind="exe", mime="application/octet-stream", filename="x"
        )


def test_serve_artifact_and_page_csp(client: TestClient) -> None:
    page = store.store_bytes(
        b"<html><body>saved</body></html>",
        kind="page",
        mime="text/html",
        filename="saved.html",
    )
    res = client.get(f"/api/artifacts/{page['id']}")
    assert res.status_code == 200
    assert res.content == b"<html><body>saved</body></html>"
    assert res.headers["content-security-policy"].startswith("sandbox")
    assert res.headers["x-content-type-options"] == "nosniff"

    pdf = store.store_bytes(
        b"%PDF-1.4 fake", kind="pdf", mime="application/pdf", filename="a.pdf"
    )
    res = client.get(f"/api/artifacts/{pdf['id']}")
    assert res.status_code == 200
    assert "content-security-policy" not in res.headers


@pytest.mark.parametrize(
    "bad_id",
    [
        "nope",
        "..%2F..%2Fsecrets.db",
        "../../secrets.db",
        "0" * 31,
        "Z" * 32,
        "0" * 64,
    ],
)
def test_malformed_ids_404(client: TestClient, bad_id: str) -> None:
    res = client.get(f"/api/artifacts/{bad_id}")
    assert res.status_code == 404


def test_upload_pdf_and_meta(client: TestClient) -> None:
    res = client.post(
        "/api/artifacts/upload",
        files={"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4 body"), "application/pdf")},
    )
    assert res.status_code == 200
    artifact = res.json()["artifact"]
    assert artifact["filename"] == "paper.pdf"
    assert artifact["kind"] == "pdf"

    meta = client.get(f"/api/artifacts/{artifact['id']}/meta")
    assert meta.status_code == 200
    assert meta.json()["sha256"] == artifact["sha256"]


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    res = client.post(
        "/api/artifacts/upload",
        files={"file": ("evil.html", io.BytesIO(b"<script>"), "text/html")},
    )
    assert res.status_code == 415


def test_delete_route(client: TestClient) -> None:
    a = store.store_bytes(
        b"to delete", kind="report", mime="text/markdown", filename="r.md"
    )
    res = client.delete(f"/api/artifacts/{a['id']}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True
    assert client.get(f"/api/artifacts/{a['id']}").status_code == 404
