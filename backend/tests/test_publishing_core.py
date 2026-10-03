"""`backend/publishing`: the scanner and the one-commit Pages push.

GitHub is faked at `github_api.request`, the seam every publisher shares.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

from backend.modules.connectors.providers import github_api
from backend.publishing import github_pages
from backend.publishing.github_pages import PagesRepo
from backend.publishing.scan import scan_text

FAKE_TOKEN = "ghp_" + "a" * 36
TARGET = PagesRepo("alice", "site", "main")


class FakeGit:
    """A repository whose tip tree holds `existing`; records every call."""

    def __init__(self, existing: set[str], *, same_tree: bool = False) -> None:
        self.existing = existing
        self.same_tree = same_tree
        self.calls: list[tuple[str, str, Any]] = []
        self.ref_failures = 0

    async def __call__(
        self, method: str, path: str, *, params: Any = None, json: Any = None
    ) -> Any:
        self.calls.append((method, path, json))
        if method == "POST" and path.endswith("/git/blobs"):
            return {"sha": "b" + base64.b64decode(json["content"]).decode() or "empty"}
        if method == "GET" and "/git/ref/heads/" in path:
            return {"object": {"sha": "c0"}}
        if method == "GET" and path.endswith("/git/commits/c0"):
            return {"tree": {"sha": "t0"}}
        if method == "GET" and path.endswith("/git/trees/t0"):
            return {"tree": [{"path": p, "type": "blob"} for p in self.existing]}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": "t0" if self.same_tree else "t1"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "c1"}
        if method == "PATCH":
            if self.ref_failures:
                self.ref_failures -= 1
                return {"error": "GitHub returned 422: not fast-forward", "status": 422}
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def trees(self) -> list[list[dict[str, Any]]]:
        return [
            j["tree"]
            for m, p, j in self.calls
            if m == "POST" and p.endswith("/git/trees")
        ]


@pytest.fixture
def fake(monkeypatch) -> FakeGit:
    git = FakeGit({"old/index.html", "keep.html"})
    monkeypatch.setattr(github_api, "request", git)
    return git


def test_a_file_set_is_one_commit_with_nojekyll(fake: FakeGit) -> None:
    sha = asyncio.run(
        github_pages.push_files(
            TARGET, {"a.html": b"a", "b/c.html": b"c"}, message="Publish"
        )
    )
    assert sha == "c1"
    (tree,) = fake.trees()
    assert {e["path"] for e in tree} == {".nojekyll", "a.html", "b/c.html"}
    assert sum(1 for c in fake.calls if c[1].endswith("/git/commits")) == 1


def test_deletions_skip_paths_that_are_already_gone(fake: FakeGit) -> None:
    asyncio.run(
        github_pages.push_files(
            TARGET, {}, message="x", delete=["old/index.html", "never-existed.html"]
        )
    )
    (tree,) = fake.trees()
    removed = [e["path"] for e in tree if e["sha"] is None]
    assert removed == ["old/index.html"]


def test_a_path_being_written_is_never_also_deleted(fake: FakeGit) -> None:
    asyncio.run(
        github_pages.push_files(
            TARGET, {"keep.html": b"new"}, message="x", delete=["keep.html"]
        )
    )
    (tree,) = fake.trees()
    assert [e for e in tree if e["sha"] is None] == []


def test_an_unchanged_tree_makes_no_commit(monkeypatch) -> None:
    git = FakeGit(set(), same_tree=True)
    monkeypatch.setattr(github_api, "request", git)
    assert (
        asyncio.run(github_pages.push_files(TARGET, {"a": b"a"}, message="x")) is None
    )
    assert not any(c[0] == "PATCH" for c in git.calls)


def test_a_lost_ref_race_rebuilds_on_the_new_tip(fake: FakeGit) -> None:
    fake.ref_failures = 1
    assert (
        asyncio.run(github_pages.push_files(TARGET, {"a": b"a"}, message="x")) == "c1"
    )
    assert sum(1 for c in fake.calls if c[0] == "PATCH") == 2


def test_files_already_at_the_tip_are_not_uploaded_again(monkeypatch) -> None:
    git = FakeGit(set())
    unchanged = github_pages.git_blob_sha(b"same")
    git.existing = {"same.html"}

    async def tip_tree(method: str, path: str, **kw: Any) -> Any:
        if method == "GET" and path.endswith("/git/trees/t0"):
            git.calls.append((method, path, None))
            return {
                "tree": [{"path": "same.html", "type": "blob", "sha": unchanged}]
                + [
                    {
                        "path": ".nojekyll",
                        "type": "blob",
                        "sha": github_pages.git_blob_sha(b""),
                    }
                ]
            }
        return await git(method, path, **kw)

    monkeypatch.setattr(github_api, "request", tip_tree)
    asyncio.run(
        github_pages.push_files(
            TARGET, {"same.html": b"same", "new.html": b"new"}, message="x"
        )
    )
    (tree,) = git.trees()
    assert [e["path"] for e in tree] == ["new.html"]
    uploads = [c for c in git.calls if c[1].endswith("/git/blobs")]
    assert len(uploads) == 1


def test_publishing_nothing_new_makes_no_tree_at_all(monkeypatch) -> None:
    git = FakeGit(set())

    async def tip_tree(method: str, path: str, **kw: Any) -> Any:
        if method == "GET" and path.endswith("/git/trees/t0"):
            return {
                "tree": [
                    {"path": p, "type": "blob", "sha": github_pages.git_blob_sha(c)}
                    for p, c in ((".nojekyll", b""), ("a.html", b"a"))
                ]
            }
        return await git(method, path, **kw)

    monkeypatch.setattr(github_api, "request", tip_tree)
    assert (
        asyncio.run(github_pages.push_files(TARGET, {"a.html": b"a"}, message="x"))
        is None
    )
    assert git.trees() == []


def test_git_blob_sha_matches_git() -> None:
    # `printf 'hello\n' | git hash-object --stdin`
    assert (
        github_pages.git_blob_sha(b"hello\n")
        == "ce013625030ba8dba906f756967f9e9ca394464a"
    )


def test_site_url_for_a_user_site_and_a_project_site() -> None:
    assert PagesRepo("Al", "al.github.io", "main").site_url == "https://Al.github.io/"
    assert TARGET.site_url == "https://alice.github.io/site/"


def test_scan_masks_secrets_and_flags_home_paths() -> None:
    hits = scan_text(f'token = "{FAKE_TOKEN}" in C:\\Users\\alice\\x')
    secret = next(h for h in hits if h.label == "GitHub token")
    assert secret.blocking and secret.excerpt == "ghp_…"
    assert FAKE_TOKEN not in repr(hits)
    assert any(h.kind == "path" and not h.blocking for h in hits)
    assert scan_text("") == []
