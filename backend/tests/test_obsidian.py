"""Obsidian export: sanitization, collisions, frontmatter, and vault safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.artifacts import store as artifacts
from backend.modules.research import obsidian
from backend.modules.settings.routes import set_value


@pytest.fixture
def vault(tmp_path) -> Path:
    path = tmp_path / "vault"
    path.mkdir()
    set_value("research.obsidianVault", str(path))
    return path


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _page_artifact(title: str = "A Saved Page") -> dict:
    html = f"<html><head><title>{title}</title></head><body><p>Body text here.</p></body></html>"
    return artifacts.store_bytes(
        html.encode(),
        kind="page",
        mime="text/html",
        filename="saved.html",
        origin_url="https://example.com/a",
        meta={"title": title},
    )


def test_export_page_writes_note_and_attachment(vault: Path) -> None:
    artifact = _page_artifact()
    result = obsidian.export_source(None, artifact)

    note = vault / result["note_path"]
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert 'url: "https://example.com/a"' in text
    assert "type: page" in text
    assert "Body text here." in text
    assert "![[attachments/" in text

    attachment = vault / result["attachment_path"]
    assert attachment.is_file()
    assert attachment.suffix == ".html"
    assert attachment.parent.name == "attachments"


def test_export_report_is_note_only(vault: Path) -> None:
    artifact = artifacts.store_bytes(
        b"# Findings\n\nEverything is fine.",
        kind="report",
        mime="text/markdown",
        filename="report.md",
        meta={"title": "Weekly Findings"},
    )
    result = obsidian.export_source(None, artifact)
    assert result["attachment_path"] is None
    note = vault / result["note_path"]
    assert "Everything is fine." in note.read_text(encoding="utf-8")


def test_hostile_titles_are_sanitized(vault: Path) -> None:
    artifact = _page_artifact(title='../../evil: "quotes" <b>#tag [[link]]  ')
    result = obsidian.export_source(None, artifact)
    note = vault / result["note_path"]
    assert note.is_file()
    # Nothing escaped the vault, and the reserved characters are gone.
    assert note.resolve().is_relative_to(vault.resolve())
    assert all(c not in note.name for c in '<>:"/\\|?*#[]')


def test_collisions_get_numbered(vault: Path) -> None:
    artifact = _page_artifact(title="Same Title")
    first = obsidian.export_source(None, artifact)
    second = obsidian.export_source(None, artifact)
    third = obsidian.export_source(None, artifact)
    names = {Path(r["note_path"]).name for r in (first, second, third)}
    assert names == {"Same Title.md", "Same Title (2).md", "Same Title (3).md"}


def test_unconfigured_vault_is_a_clear_error(tmp_path) -> None:
    set_value("research.obsidianVault", "")
    with pytest.raises(obsidian.ObsidianNotConfigured):
        obsidian.export_source(None, _page_artifact())

    set_value("research.obsidianVault", str(tmp_path / "nope"))
    with pytest.raises(obsidian.ObsidianNotConfigured, match="not found"):
        obsidian.export_source(None, _page_artifact())


def test_export_route_by_source(vault: Path, client: TestClient) -> None:
    artifact = _page_artifact(title="Routed")
    res = client.post(
        "/api/library/sources", json={"type": "page", "artifact_id": artifact["id"]}
    )
    assert res.status_code == 200
    source_id = res.json()["id"]

    res = client.post("/api/research/export", json={"source_id": source_id})
    assert res.status_code == 200
    assert (vault / res.json()["note_path"]).is_file()


def test_export_route_errors(client: TestClient) -> None:
    res = client.post("/api/research/export", json={})
    assert res.status_code == 400
    res = client.post("/api/research/export", json={"artifact_id": "0" * 32})
    assert res.status_code == 404
