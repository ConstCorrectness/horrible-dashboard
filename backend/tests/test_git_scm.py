"""Source control: the grouped working tree, staging, and the working-tree diff.

Run against a real `git init` repo rather than fixture text, because the thing
being pinned is our reading of git's own porcelain — a hand-written fixture pins
only our idea of what git prints.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app import app


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-b", "main")
    _git(ws, "config", "user.email", "test@example.com")
    _git(ws, "config", "user.name", "Test")
    (ws / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(ws, "add", "tracked.txt")
    _git(ws, "commit", "-m", "initial")
    return ws


@pytest.fixture
def client(repo, tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(repo))
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


def _status(client: TestClient):
    res = client.get("/api/git/scm-status")
    assert res.status_code == 200, res.text
    return res.json()


def _paths(group):
    return {e["path"] for e in group}


# --- grouping ---------------------------------------------------------------


def test_groups_staged_unstaged_and_untracked(client: TestClient, repo) -> None:
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("three\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")

    status = _status(client)
    assert status["is_repo"] is True
    assert status["branch"] == "main"
    tracked = str(repo / "tracked.txt")
    # Staged edits plus later unstaged ones is the ordinary case, and the file
    # belongs in BOTH lists — showing it in one is how a commit silently leaves
    # half the work behind.
    assert tracked in _paths(status["staged"])
    assert tracked in _paths(status["unstaged"])
    assert str(repo / "new.txt") in _paths(status["untracked"])


def test_keeps_the_two_status_characters_apart(client: TestClient, repo) -> None:
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    entry = next(e for e in _status(client)["staged"])
    assert entry["index"] == "M"
    assert entry["worktree"] == "."


def test_a_non_repo_root_is_a_200_not_an_error(tmp_path, monkeypatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(plain))
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    res = TestClient(app).get("/api/git/scm-status")
    assert res.status_code == 200
    assert res.json()["is_repo"] is False


def test_ahead_and_behind_parse_from_branch_ab(
    client: TestClient, repo, tmp_path
) -> None:
    # A local "remote" so the branch gains an upstream to be ahead of.
    remote = tmp_path / "remote.git"
    _git(repo, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    (repo / "tracked.txt").write_text("ahead\n", encoding="utf-8")
    _git(repo, "commit", "-am", "ahead by one")

    status = _status(client)
    assert status["ahead"] == 1
    assert status["behind"] == 0


# --- staging ----------------------------------------------------------------


def test_stage_then_unstage_round_trips(client: TestClient, repo) -> None:
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    path = str(repo / "tracked.txt")

    assert client.post("/api/git/stage", json={"paths": [path]}).json()["ok"] is True
    assert path in _paths(_status(client)["staged"])

    assert client.post("/api/git/unstage", json={"paths": [path]}).json()["ok"] is True
    status = _status(client)
    assert path not in _paths(status["staged"])
    assert path in _paths(status["unstaged"])


def test_staging_a_path_outside_the_roots_is_rejected(
    client: TestClient, tmp_path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    res = client.post("/api/git/stage", json={"paths": [str(outside)]})
    # Rejected by the workspace boundary before git is ever run.
    assert res.status_code in (403, 404)


# --- diff -------------------------------------------------------------------


def test_working_diff_shows_unstaged_changes(client: TestClient, repo) -> None:
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    res = client.get("/api/git/diff", params={"path": str(repo / "tracked.txt")})
    assert res.status_code == 200
    assert "+two" in res.json()["diff"]


def test_staged_diff_is_a_different_question(client: TestClient, repo) -> None:
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    path = str(repo / "tracked.txt")
    # Staged holds the change; the working tree now matches the index, so its
    # diff is empty. Conflating the two shows "no changes" on a modified file.
    assert (
        "+two"
        in client.get("/api/git/diff", params={"path": path, "staged": 1}).json()[
            "diff"
        ]
    )
    assert client.get("/api/git/diff", params={"path": path}).json()["diff"] == ""
