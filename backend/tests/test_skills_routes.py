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


# --- a skill's own files ------------------------------------------------------
#
# A skill is a directory, not a file. `copy_to_user` has always copied the siblings
# along, so they demonstrably exist — but nothing served them, so a skill's own
# references were invisible in the app that hosts it.


def test_the_list_response_carries_the_skill_s_files(client, dirs):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    (user / "tidy" / "references").mkdir()
    (user / "tidy" / "references" / "rules.md").write_text("rules", encoding="utf-8")
    # Read off the HTTP body, never `Skill.public()`: the response model is what
    # decides whether the browser ever sees this field.
    row = next(s for s in client.get("/api/skills").json()["skills"] if s["name"] == "tidy")
    names = [f["name"] for f in row["files"]]
    # SKILL.md first — the entry point is not just another sibling.
    assert names == ["SKILL.md", "references/rules.md"]
    # Against the file on disk, not against `GOOD`: `write_text` translates newlines
    # on Windows, so a length computed from the source string is short there and the
    # test would fail on one OS for a reason that has nothing to do with skills.
    assert row["files"][0]["bytes"] == (user / "tidy" / "SKILL.md").stat().st_size


def test_dotfiles_and_pycache_are_not_listed(client, dirs):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    (user / "tidy" / ".DS_Store").write_text("x", encoding="utf-8")
    (user / "tidy" / "__pycache__").mkdir()
    (user / "tidy" / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    row = next(s for s in client.get("/api/skills").json()["skills"] if s["name"] == "tidy")
    assert [f["name"] for f in row["files"]] == ["SKILL.md"]


def test_reading_a_resource_file_returns_its_text(client, dirs):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    (user / "tidy" / "references").mkdir()
    (user / "tidy" / "references" / "rules.md").write_text(
        "# Rules\n", encoding="utf-8", newline=""
    )
    body = client.get("/api/skills/tidy/files/references/rules.md").json()
    assert body["text"] == "# Rules\n"
    assert body["name"] == "references/rules.md"


@pytest.mark.parametrize(
    "rel",
    [
        "../../../secret.txt",
        "references/../../../secret.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "..",
    ],
)
def test_a_path_outside_the_skill_directory_is_refused(dirs, tmp_path, rel):
    """The whole security argument for the viewer.

    Asserted against `store.read_file` rather than over HTTP on purpose: an HTTP client
    collapses `..` segments before the request is ever sent, so a route-level test of
    an escape mostly measures the client's URL normalizer. The guard is what has to
    hold, for every shape — relative, mid-path, and absolute on either OS.
    """
    user, _ = dirs
    _write(user, "tidy", GOOD)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    text, err = store.read_file("tidy", rel)
    assert text is None
    assert err


def test_an_escaping_path_never_returns_content_over_http(client, dirs, tmp_path):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    res = client.get("/api/skills/tidy/files/../../../secret.txt")
    assert res.status_code != 200
    assert "nope" not in res.text


def test_a_binary_file_is_refused_rather_than_mangled(client, dirs):
    user, _ = dirs
    _write(user, "tidy", GOOD)
    (user / "tidy" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    # Listed — it is genuinely part of the skill — but not readable as text.
    row = next(s for s in client.get("/api/skills").json()["skills"] if s["name"] == "tidy")
    assert "logo.png" in [f["name"] for f in row["files"]]
    res = client.get("/api/skills/tidy/files/logo.png")
    assert res.status_code == 404
    assert "binary" in res.json()["detail"]


def test_a_file_read_on_a_missing_skill_is_a_404(client, dirs):
    assert client.get("/api/skills/nope/files/SKILL.md").status_code == 404
