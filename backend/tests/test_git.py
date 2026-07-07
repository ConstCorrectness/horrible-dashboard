"""Tests for the git provenance module. All git runs against a **throwaway temp repo**
(never the real one). The load-bearing behaviour is the provenance loop: a commit
stamps the active chat session as a trailer, and blame reads it back."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.chat.models import ChatSession, ChatSessionsState
from backend.modules.git import service


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(data))
    # Seed an active chat session so commit() has provenance to stamp.
    state = ChatSessionsState(
        active="sess-123",
        sessions=[
            ChatSession(
                id="sess-123", title="Add foo feature", created=0.0, updated=0.0
            )
        ],
    )
    (data / "chat-sessions.json").write_text(state.model_dump_json())

    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Tester")
    _git(r, "config", "commit.gpgsign", "false")
    (r / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "initial")  # a plain (non-agent) commit
    return r


def test_commit_stamps_session_and_blame_reads_it(repo: Path) -> None:
    (repo / "a.py").write_text(
        "def foo():\n    return 2  # changed\n", encoding="utf-8"
    )
    res = service.commit(repo, "tweak foo", ["a.py"])
    assert res.ok and res.sha
    assert res.session_id == "sess-123"

    blame = service.blame(repo / "a.py")
    assert blame.is_repo
    changed = [ln for ln in blame.lines if "changed" in (ln.text or "")]
    assert changed, "changed line should be present in blame"
    assert changed[0].session_id == "sess-123"
    assert changed[0].session_title == "Add foo feature"


def test_log_flags_agent_commit(repo: Path) -> None:
    (repo / "a.py").write_text("def foo():\n    return 9\n", encoding="utf-8")
    service.commit(repo, "agent change", ["a.py"])
    result = service.log(repo, 10)
    assert result.is_repo
    summaries = {c.summary: c for c in result.commits}
    assert summaries["agent change"].session_id == "sess-123"  # agent-authored
    assert summaries["initial"].session_id is None  # plain commit


def test_commit_without_active_session_has_no_trailer(repo: Path, monkeypatch) -> None:
    # Point the chat store at an empty dir → no active session → plain commit.
    empty = repo.parent / "empty-data"
    empty.mkdir()
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(empty))
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    res = service.commit(repo, "human-style change", ["a.py"])
    assert res.ok
    assert res.session_id is None


# --- routes ------------------------------------------------------------------


@pytest.fixture
def client(repo: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(repo))
    return TestClient(app)


def test_blame_route(client: TestClient) -> None:
    res = client.get("/api/git/blame", params={"path": "a.py"})
    assert res.status_code == 200
    body = res.json()
    assert body["is_repo"] is True
    assert len(body["lines"]) >= 2


def test_log_route(client: TestClient) -> None:
    res = client.get("/api/git/log", params={"limit": 5})
    assert res.status_code == 200
    assert res.json()["is_repo"] is True


def test_commit_route_stamps_session(client: TestClient, repo: Path) -> None:
    (repo / "a.py").write_text("def foo():\n    return 4\n", encoding="utf-8")
    res = client.post(
        "/api/git/commit", json={"message": "route commit", "paths": ["a.py"]}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True and body["sha"]
    assert body["session_id"] == "sess-123"


def test_blame_route_rejects_outside_roots(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    res = client.get("/api/git/blame", params={"path": str(outside)})
    assert res.status_code == 403
