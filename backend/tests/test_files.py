"""Tests for the workspace file-access module (B1). Path-traversal safety is the
load-bearing concern, so it gets the most coverage."""

import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def root(tmp_path):
    """A workspace root (a subdir of tmp_path, so siblings are genuinely outside
    it) with a couple of seeded files, configured via env."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    sub = ws / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("nested", encoding="utf-8")
    return ws


@pytest.fixture
def client(root, tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(root))
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


# --- roots & listing --------------------------------------------------------


def test_roots_listed(client: TestClient, root) -> None:
    res = client.get("/api/files/roots")
    assert res.status_code == 200
    assert [r["path"] for r in res.json()] == [str(root.resolve())]


def test_list_dir_sorts_dirs_first(client: TestClient, root) -> None:
    res = client.get("/api/files/list", params={"path": str(root)})
    assert res.status_code == 200
    entries = res.json()["entries"]
    assert [(e["name"], e["kind"]) for e in entries] == [
        ("sub", "dir"),
        ("a.txt", "file"),
    ]


def test_read_file(client: TestClient, root) -> None:
    res = client.get("/api/files/read", params={"path": str(root / "a.txt")})
    assert res.status_code == 200
    assert res.json()["content"] == "hello"


def test_read_binary_rejected(client: TestClient, root) -> None:
    (root / "bin").write_bytes(b"\xff\xfe\x00\x01")
    res = client.get("/api/files/read", params={"path": str(root / "bin")})
    assert res.status_code == 415


# --- path traversal / outside-root rejection --------------------------------


def test_list_outside_root_rejected(client: TestClient, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    res = client.get("/api/files/list", params={"path": str(outside)})
    assert res.status_code == 403


def test_dotdot_escape_rejected(client: TestClient, root) -> None:
    res = client.get("/api/files/read", params={"path": str(root / ".." / "secret")})
    assert res.status_code in (403, 404)


# --- workspace-relative paths (agents pass bare/relative paths) --------------


def test_relative_write_anchors_to_root(client: TestClient, root) -> None:
    # A bare filename (what a model passes for "create notes.txt") lands in the root,
    # not the backend CWD — and is no longer rejected as outside the workspace.
    res = client.put("/api/files/write", json={"path": "notes.txt", "content": "hi"})
    assert res.status_code == 200
    assert (root / "notes.txt").read_text(encoding="utf-8") == "hi"


def test_relative_read_anchors_to_root(client: TestClient, root) -> None:
    res = client.get("/api/files/read", params={"path": "a.txt"})
    assert res.status_code == 200
    assert res.json()["content"] == "hello"


def test_relative_with_root_name_segment(client: TestClient, root) -> None:
    # A leading segment naming the root selects it (e.g. "ws/sub/b.txt").
    res = client.get("/api/files/read", params={"path": f"{root.name}/sub/b.txt"})
    assert res.status_code == 200
    assert res.json()["content"] == "nested"


def test_relative_dotdot_escape_still_rejected(client: TestClient) -> None:
    # Anchoring happens before the boundary check, so a relative `..` escape is
    # still rejected.
    res = client.get("/api/files/read", params={"path": "../secret.txt"})
    assert res.status_code in (403, 404)


def test_symlink_escape_rejected(client: TestClient, root, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("classified", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/account")
    res = client.get("/api/files/read", params={"path": str(link)})
    assert res.status_code == 403


def test_no_roots_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HORRIBLE_WORKSPACE_ROOTS", raising=False)
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(app)
    res = client.get("/api/files/read", params={"path": str(tmp_path / "x")})
    assert res.status_code == 400


# --- mutations --------------------------------------------------------------


def test_create_file_and_dir(client: TestClient, root) -> None:
    res = client.post(
        "/api/files/create",
        json={"path": str(root / "new.txt"), "kind": "file", "content": "hi"},
    )
    assert res.status_code == 200
    assert (root / "new.txt").read_text() == "hi"

    res = client.post(
        "/api/files/create", json={"path": str(root / "d"), "kind": "dir"}
    )
    assert res.status_code == 200
    assert (root / "d").is_dir()


def test_create_existing_conflicts(client: TestClient, root) -> None:
    res = client.post("/api/files/create", json={"path": str(root / "a.txt")})
    assert res.status_code == 409


def test_write_overwrites(client: TestClient, root) -> None:
    res = client.put(
        "/api/files/write", json={"path": str(root / "a.txt"), "content": "changed"}
    )
    assert res.status_code == 200
    assert (root / "a.txt").read_text() == "changed"


def test_write_outside_root_rejected(client: TestClient, tmp_path) -> None:
    res = client.put(
        "/api/files/write",
        json={"path": str(tmp_path / "outside.txt"), "content": "x"},
    )
    assert res.status_code == 403


def test_rename(client: TestClient, root) -> None:
    res = client.post(
        "/api/files/rename",
        json={"path": str(root / "a.txt"), "new_path": str(root / "renamed.txt")},
    )
    assert res.status_code == 200
    assert not (root / "a.txt").exists()
    assert (root / "renamed.txt").read_text() == "hello"


def test_rename_destination_outside_root_rejected(
    client: TestClient, root, tmp_path
) -> None:
    res = client.post(
        "/api/files/rename",
        json={"path": str(root / "a.txt"), "new_path": str(tmp_path / "escaped.txt")},
    )
    assert res.status_code == 403


def test_delete_file(client: TestClient, root) -> None:
    res = client.post("/api/files/delete", json={"path": str(root / "a.txt")})
    assert res.status_code == 200
    assert not (root / "a.txt").exists()


def test_delete_nonempty_dir_requires_recursive(client: TestClient, root) -> None:
    res = client.post("/api/files/delete", json={"path": str(root / "sub")})
    assert res.status_code == 400
    res = client.post(
        "/api/files/delete", json={"path": str(root / "sub"), "recursive": True}
    )
    assert res.status_code == 200
    assert not (root / "sub").exists()


def test_delete_outside_root_rejected(client: TestClient, tmp_path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("x", encoding="utf-8")
    res = client.post("/api/files/delete", json={"path": str(target)})
    assert res.status_code == 403
    assert target.exists()


# --- git status -------------------------------------------------------------


def _git(root, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_status_not_a_repo(client: TestClient, root) -> None:
    res = client.get("/api/files/git-status", params={"path": str(root)})
    assert res.status_code == 200
    body = res.json()
    assert body["is_repo"] is False
    assert body["entries"] == []


def test_git_status_reports_changes(client: TestClient, root) -> None:
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")

    (root / "a.txt").write_text("changed", encoding="utf-8")  # modified
    (root / "new.txt").write_text("x", encoding="utf-8")  # untracked

    res = client.get("/api/files/git-status", params={"path": str(root)})
    assert res.status_code == 200
    body = res.json()
    assert body["is_repo"] is True
    assert body["branch"] == "main"
    # Match by basename so OS path-resolution quirks don't matter.
    by_name = {
        e["path"].replace("\\", "/").rsplit("/", 1)[-1]: e["status"]
        for e in body["entries"]
    }
    assert by_name.get("a.txt") == "modified"
    assert by_name.get("new.txt") == "untracked"
