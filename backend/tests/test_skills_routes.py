"""The skills HTTP surface, asserted on response bodies.

Response models silently drop fields they don't declare, so every assertion here reads
the JSON the browser would receive. `error` and `shadowed` are the two that matter
most: they are the entire mechanism by which a skill that isn't working explains
itself, and a model that omitted them would leave the pane rendering a healthy-looking
row for a skill the agent never sees.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.modules.skills import agent, store

GOOD = """---
name: tidy
description: Tidy a file the way this project likes it.
allowed-tools:
  - files.read
---

# Tidy

Do the tidy thing.
"""


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "project" / ".claude" / "skills"
    project.mkdir(parents=True)
    monkeypatch.setattr(store, "project_dir", lambda: project)
    agent.invalidate()
    return store.user_dir(), project


@pytest.fixture
def client(dirs):
    from fastapi.testclient import TestClient

    from backend.app import app

    return TestClient(app)


def _write(root: Path, name: str, text: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_creating_a_skill_writes_a_skill_md(client, dirs):
    user, _ = dirs
    body = client.post(
        "/api/skills",
        json={
            "name": "tidy",
            "description": "Tidy things.",
            "body": "# Tidy\n\nSteps.",
            "allowedTools": ["files.read"],
        },
    ).json()
    assert body["scope"] == "user"
    assert body["enabled"] is True
    assert body["error"] == ""
    text = (user / "tidy" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: tidy\n")
    assert "allowed-tools" in text


def test_an_illegal_name_is_a_400(client, dirs):
    response = client.post(
        "/api/skills", json={"name": "Not Legal!", "description": "d", "body": "b"}
    )
    assert response.status_code == 400


def test_a_skill_without_a_description_is_refused(client, dirs):
    """The description is the trigger; without one the skill would sit in the catalog
    costing tokens and never fire."""
    response = client.post(
        "/api/skills", json={"name": "t", "description": "", "body": "b"}
    )
    assert response.status_code == 400


def test_a_broken_skill_is_listed_with_its_error(client, dirs):
    user, _ = dirs
    _write(user, "broken", "---\nname: broken\n---\n\nbody\n")
    rows = client.get("/api/skills").json()["skills"]
    row = next(r for r in rows if r["name"] == "broken")
    assert "no `description`" in row["error"]


def test_shadowing_reaches_the_browser(client, dirs):
    """Otherwise 'I edited the skill and nothing changed' has no explanation."""
    user, project = dirs
    _write(project, "tidy", GOOD)
    _write(user, "tidy", GOOD)
    rows = client.get("/api/skills").json()["skills"]
    assert [r["shadowed"] for r in rows if r["scope"] == "project"] == [True]
    assert [r["shadowed"] for r in rows if r["scope"] == "user"] == [False]


def test_editing_a_project_skill_is_refused_with_a_409(client, dirs):
    """409 rather than 403: the pane's response is to offer the copy action, which is
    a different thing to say than 'you may not'."""
    _, project = dirs
    _write(project, "tidy", GOOD)
    response = client.post(
        "/api/skills", json={"name": "tidy", "description": "mine", "body": "b"}
    )
    assert response.status_code == 409
    assert "Copy it" in response.json()["detail"]


def test_deleting_a_project_skill_is_refused(client, dirs):
    _, project = dirs
    _write(project, "tidy", GOOD)
    assert client.delete("/api/skills/tidy").status_code == 409
    assert (project / "tidy" / "SKILL.md").is_file()


def test_copy_then_edit_is_the_supported_path(client, dirs):
    user, project = dirs
    _write(project, "tidy", GOOD)
    copied = client.post("/api/skills/tidy/copy").json()
    assert copied["scope"] == "user"
    saved = client.post(
        "/api/skills", json={"name": "tidy", "description": "mine now", "body": "b"}
    ).json()
    assert saved["description"] == "mine now"
    assert (user / "tidy" / "SKILL.md").is_file()


def test_the_preview_is_exactly_what_the_model_gets(client, dirs):
    """Assembled strings, not a rendering: the question is 'what did I just make the
    agent read', and a prettified preview hides the stray whitespace that causes
    trouble."""
    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    body = client.get("/api/skills/tidy/preview").json()
    assert body["catalog"].startswith("## Available skills")
    assert "Tidy a file the way this project likes it." in body["catalog"]
    # The body is NOT in the catalog — that split is the whole economic argument.
    assert "Do the tidy thing." not in body["catalog"]
    assert "Do the tidy thing." in body["instructions"]
    assert body["groups"] == ["files"]


def test_cost_separates_per_turn_from_on_demand(client, dirs):
    """A catalog line is paid every round; a body only on a turn that uses it."""
    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    body = client.get("/api/skills/cost").json()
    assert body["catalogTokens"] > 0
    entry = next(s for s in body["skills"] if s["name"] == "tidy")
    assert entry["tokens"] > 0
    assert entry["bodyTokens"] > 0
    assert isinstance(body["exact"], bool)


def test_disabling_zeroes_the_per_turn_cost(client, dirs):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    agent.invalidate()
    before = client.get("/api/skills/cost").json()["catalogTokens"]
    assert before > 0
    row = client.post("/api/skills/tidy/enabled", json={"enabled": False}).json()
    assert row["enabled"] is False
    assert client.get("/api/skills/cost").json()["catalogTokens"] == 0


def test_export_puts_a_readable_skill_in_the_project_dir(client, dirs):
    user, project = dirs
    _write(user, "tidy", GOOD)
    body = client.post("/api/skills/tidy/export").json()
    assert Path(body["path"]).is_file() is False  # the path is the directory
    assert (project / "tidy" / "SKILL.md").is_file()


def test_a_missing_skill_is_a_404(client, dirs):
    assert client.get("/api/skills/nope/preview").status_code == 404
    assert client.delete("/api/skills/nope").status_code == 404
