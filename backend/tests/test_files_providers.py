"""The virtual-root seam: scheme detection, dispatch, and read-only rejection.

The single most important test in here is `test_windows_drive_letters_are_not_schemes`.
`C:/Users/x` matches a naive scheme regex, and if it were treated as a URI every file on
a Windows workspace root would be routed into a provider instead of the filesystem.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.files import providers
from backend.modules.files.models import DirListing, FileContent, FileEntry, RootInfo


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_providers():
    providers.reset()
    yield
    providers.reset()


class _FakeProvider:
    """A minimal in-memory provider, standing in for Drive."""

    scheme = "fake"
    read_only = True

    def __init__(self) -> None:
        self.list_calls: list[tuple[str, bool]] = []

    async def roots(self) -> list[RootInfo]:
        return [RootInfo(name="Fake Mount", path="fake:/root")]

    async def list(self, path: str, *, fresh: bool = False) -> DirListing:
        self.list_calls.append((path, fresh))
        return DirListing(
            path=path,
            entries=[
                FileEntry(
                    name="Report.doc",
                    path="fake:/abc",
                    kind="file",
                    size=None,
                    mtime=1.0,
                )
            ],
        )

    async def read(self, path: str) -> FileContent:
        return FileContent(path=path, content="hello", truncated=False)


@pytest.fixture
def fake_provider() -> _FakeProvider:
    provider = _FakeProvider()
    providers.register(provider)
    return provider


# --- scheme detection -------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "C:/Users/Horrible/notes.txt",
        "C:\\Users\\Horrible\\notes.txt",
        "c:/tmp",
        "D:/data/file.py",
    ],
)
def test_windows_drive_letters_are_not_schemes(path: str):
    """A one-character prefix is a drive letter, not a URI scheme. Getting this wrong
    routes every Windows file into a provider."""
    assert providers.scheme_of(path) is None


@pytest.mark.parametrize(
    "path", ["/home/user/notes.txt", "notes.txt", "./rel/path", ""]
)
def test_ordinary_paths_have_no_scheme(path: str):
    assert providers.scheme_of(path) is None


@pytest.mark.parametrize(
    ("path", "scheme"),
    [
        ("gdrive:/root", "gdrive"),
        ("gdrive:/1a2b3c", "gdrive"),
        ("fake:/x", "fake"),
        ("s3+v2:/bucket", "s3+v2"),
    ],
)
def test_uri_schemes_are_detected(path: str, scheme: str):
    assert providers.scheme_of(path) == scheme


def test_an_unregistered_scheme_is_not_virtual():
    """It falls through to normal resolution, where the traversal boundary rejects it —
    a 403 that describes the real problem, rather than a 404 from a missing provider."""
    assert providers.scheme_of("gdrive:/x") == "gdrive"
    assert providers.is_virtual("gdrive:/x") is False
    assert providers.provider_for("gdrive:/x") is None


# --- dispatch ---------------------------------------------------------------


def test_roots_appends_virtual_roots(client: TestClient, fake_provider):
    body = client.get("/api/files/roots").json()
    assert {"name": "Fake Mount", "path": "fake:/root"} in body
    # Real roots survive alongside it.
    assert any(not r["path"].startswith("fake:") for r in body)


def test_roots_are_local_only_without_a_provider(client: TestClient):
    body = client.get("/api/files/roots").json()
    assert all(providers.scheme_of(r["path"]) is None for r in body)


def test_list_dispatches_to_the_provider(client: TestClient, fake_provider):
    body = client.get("/api/files/list", params={"path": "fake:/root"}).json()
    assert body["entries"][0]["name"] == "Report.doc"
    assert fake_provider.list_calls == [("fake:/root", False)]


def test_fresh_is_forwarded_to_the_provider(client: TestClient, fake_provider):
    client.get("/api/files/list", params={"path": "fake:/root", "fresh": "true"})
    assert fake_provider.list_calls == [("fake:/root", True)]


def test_read_dispatches_to_the_provider(client: TestClient, fake_provider):
    body = client.get("/api/files/read", params={"path": "fake:/abc"}).json()
    assert body["content"] == "hello"


def test_a_broken_provider_does_not_hide_the_other_roots(client: TestClient):
    class Broken:
        scheme = "broken"
        read_only = True

        async def roots(self):
            raise RuntimeError("mount unavailable")

        async def list(self, path, *, fresh=False):  # pragma: no cover
            raise NotImplementedError

        async def read(self, path):  # pragma: no cover
            raise NotImplementedError

    providers.register(Broken())
    res = client.get("/api/files/roots")
    assert res.status_code == 200


# --- read-only rejection ----------------------------------------------------


def test_write_routes_reject_a_virtual_path(client: TestClient, fake_provider):
    cases = [
        ("post", "/api/files/create", {"path": "fake:/x", "kind": "file"}),
        ("put", "/api/files/write", {"path": "fake:/x", "content": "no"}),
        ("post", "/api/files/rename", {"path": "fake:/x", "new_path": "fake:/y"}),
        ("post", "/api/files/delete", {"path": "fake:/x"}),
    ]
    for method, url, body in cases:
        res = getattr(client, method)(url, json=body)
        assert res.status_code == 403, f"{method} {url}"
        assert "read-only" in res.json()["detail"]


def test_rename_rejects_a_virtual_destination(
    client: TestClient, fake_provider, tmp_path
):
    """Renaming *into* a read-only mount is as impossible as out of one, and the
    source-only check would have missed it."""
    res = client.post(
        "/api/files/rename",
        json={"path": str(tmp_path / "real.txt"), "new_path": "fake:/y"},
    )
    assert res.status_code == 403


def test_git_status_on_a_virtual_root_is_not_an_error(
    client: TestClient, fake_provider
):
    """The tree asks this of every root on each refresh; erroring would log noise
    forever and mask a genuine git failure."""
    body = client.get("/api/files/git-status", params={"path": "fake:/root"}).json()
    assert body["is_repo"] is False


# --- local behaviour is unchanged -------------------------------------------


def test_local_paths_still_work_with_a_provider_registered(
    client: TestClient, fake_provider, tmp_path, monkeypatch
):
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(tmp_path))
    (tmp_path / "hello.txt").write_text("local content", encoding="utf-8")

    listing = client.get("/api/files/list", params={"path": str(tmp_path)}).json()
    assert [e["name"] for e in listing["entries"]] == ["hello.txt"]

    read = client.get(
        "/api/files/read", params={"path": str(tmp_path / "hello.txt")}
    ).json()
    assert read["content"] == "local content"
    # The provider was never consulted for a real path.
    assert fake_provider.list_calls == []


def test_the_traversal_boundary_still_rejects_escapes(
    client: TestClient, fake_provider, tmp_path, monkeypatch
):
    monkeypatch.setenv("HORRIBLE_WORKSPACE_ROOTS", str(tmp_path))
    res = client.get(
        "/api/files/read", params={"path": str(tmp_path / ".." / "outside.txt")}
    )
    assert res.status_code in (403, 404)
