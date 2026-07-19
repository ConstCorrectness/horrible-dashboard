"""The GitHub repo-viewer routes: projection, caching, and the truncated-tree fallback."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors.providers import github_routes, github_tools


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_cache():
    github_routes.clear_cache()
    yield
    github_routes.clear_cache()


@pytest.fixture
def api(monkeypatch):
    """Stub the GitHub API. Returns the call log so tests can assert on cache hits."""
    calls: list[str] = []
    responses: dict[str, Any] = {}

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append(path)
        return responses.get(path, {"error": "GitHub returned 404: Not Found"})

    monkeypatch.setattr(github_tools, "_request", fake_request)
    return type("Api", (), {"calls": calls, "responses": responses})()


REPO = {
    "full_name": "octocat/hello",
    "description": "greetings",
    "private": False,
    "language": "Python",
    "stargazers_count": 7,
    "default_branch": "main",
    "html_url": "https://github.com/octocat/hello",
}


# --- repos ------------------------------------------------------------------


def test_list_repos_projects_the_summary(client: TestClient, api):
    api.responses["/user/repos"] = [REPO]
    body = client.get("/api/connectors/github/repos").json()
    assert body[0]["full_name"] == "octocat/hello"
    assert body[0]["default_branch"] == "main"
    assert body[0]["stars"] == 7


def test_a_second_call_is_served_from_cache(client: TestClient, api):
    api.responses["/user/repos"] = [REPO]
    client.get("/api/connectors/github/repos")
    client.get("/api/connectors/github/repos")
    assert api.calls.count("/user/repos") == 1


def test_fresh_bypasses_the_cache(client: TestClient, api):
    api.responses["/user/repos"] = [REPO]
    client.get("/api/connectors/github/repos")
    client.get("/api/connectors/github/repos", params={"fresh": "true"})
    assert api.calls.count("/user/repos") == 2


def test_search_repos_reads_the_items_envelope(client: TestClient, api):
    api.responses["/search/repositories"] = {"items": [REPO]}
    body = client.get(
        "/api/connectors/github/search/repos", params={"q": "hello"}
    ).json()
    assert [r["full_name"] for r in body] == ["octocat/hello"]


def test_an_empty_search_does_not_call_github(client: TestClient, api):
    assert (
        client.get("/api/connectors/github/search/repos", params={"q": "  "}).json()
        == []
    )
    assert api.calls == []


def test_branches_are_names_only(client: TestClient, api):
    api.responses["/repos/octocat/hello/branches"] = [{"name": "main"}, {"name": "dev"}]
    body = client.get("/api/connectors/github/repos/octocat/hello/branches").json()
    assert body == ["main", "dev"]


# --- tree -------------------------------------------------------------------


def test_tree_maps_blob_and_tree_to_file_and_dir(client: TestClient, api):
    """The viewer's tree speaks the files module's vocabulary, not git's."""
    api.responses["/repos/octocat/hello/git/trees/main"] = {
        "tree": [
            {"path": "src", "type": "tree"},
            {"path": "src/app.py", "type": "blob", "size": 120},
        ]
    }
    body = client.get(
        "/api/connectors/github/repos/octocat/hello/tree", params={"ref": "main"}
    ).json()
    assert body["entries"] == [
        {"path": "src", "kind": "dir", "size": None},
        {"path": "src/app.py", "kind": "file", "size": 120},
    ]
    assert body["truncated"] is False


def test_a_truncated_tree_is_reported_so_the_viewer_can_fall_back(
    client: TestClient, api
):
    """A repo too big for one request must not silently render a partial tree."""
    api.responses["/repos/octocat/hello/git/trees/main"] = {
        "tree": [],
        "truncated": True,
    }
    body = client.get(
        "/api/connectors/github/repos/octocat/hello/tree", params={"ref": "main"}
    ).json()
    assert body["truncated"] is True


def test_contents_is_the_lazy_fallback(client: TestClient, api):
    api.responses["/repos/octocat/hello/contents/src"] = [
        {"path": "src/app.py", "type": "file", "size": 10},
        {"path": "src/lib", "type": "dir"},
    ]
    body = client.get(
        "/api/connectors/github/repos/octocat/hello/contents",
        params={"path": "src", "ref": "main"},
    ).json()
    assert [e["kind"] for e in body["entries"]] == ["file", "dir"]


def test_contents_on_a_file_is_a_400(client: TestClient, api):
    api.responses["/repos/octocat/hello/contents/README.md"] = {"type": "file"}
    res = client.get(
        "/api/connectors/github/repos/octocat/hello/contents",
        params={"path": "README.md"},
    )
    assert res.status_code == 400


# --- file + readme ----------------------------------------------------------


def test_read_file_decodes_content(client: TestClient, api):
    api.responses["/repos/octocat/hello/contents/src/app.py"] = {
        "encoding": "base64",
        "content": base64.b64encode(b"print('hi')").decode(),
        "html_url": "https://github.com/octocat/hello/blob/main/src/app.py",
    }
    body = client.get(
        "/api/connectors/github/repos/octocat/hello/file",
        params={"path": "src/app.py", "ref": "main"},
    ).json()
    assert body["content"] == "print('hi')"


def test_reading_a_directory_is_a_400(client: TestClient, api):
    api.responses["/repos/octocat/hello/contents/src"] = [
        {"name": "app.py", "type": "file"}
    ]
    res = client.get(
        "/api/connectors/github/repos/octocat/hello/file", params={"path": "src"}
    )
    assert res.status_code == 400


def test_readme_is_decoded(client: TestClient, api):
    api.responses["/repos/octocat/hello/readme"] = {
        "path": "README.md",
        "encoding": "base64",
        "content": base64.b64encode(b"# Hello").decode(),
    }
    body = client.get("/api/connectors/github/repos/octocat/hello/readme").json()
    assert body["content"] == "# Hello"


def test_a_missing_readme_is_a_404_not_a_gateway_error(client: TestClient, api):
    """Plenty of repos have no README; that's an ordinary state for the viewer."""
    res = client.get("/api/connectors/github/repos/octocat/hello/readme")
    assert res.status_code == 404


# --- errors -----------------------------------------------------------------


def test_not_connected_is_a_409(client: TestClient, monkeypatch):
    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        return github_tools._NOT_CONNECTED

    monkeypatch.setattr(github_tools, "_request", fake_request)
    res = client.get("/api/connectors/github/repos")
    assert res.status_code == 409
    assert "isn't connected" in res.json()["detail"]


def test_a_rate_limit_is_reported_verbatim(client: TestClient, monkeypatch):
    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        return {"error": "GitHub rate limit hit — wait a minute and try again."}

    monkeypatch.setattr(github_tools, "_request", fake_request)
    res = client.get("/api/connectors/github/repos")
    assert res.status_code == 502
    assert "rate limit" in res.json()["detail"]


def test_an_error_is_not_cached(client: TestClient, api):
    """Caching a failure would make a transient outage stick for the whole TTL."""
    client.get("/api/connectors/github/repos")
    api.responses["/user/repos"] = [REPO]
    body = client.get("/api/connectors/github/repos").json()
    assert body[0]["full_name"] == "octocat/hello"
