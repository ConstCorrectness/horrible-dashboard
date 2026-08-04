"""Training routes over TestClient, exercised through a plugin-registered fake
provider (which doubles as proof of the sdk seam). Venv bootstrap is stubbed so
tests never shell out to uv."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.training import envs, projects, routes
from backend.modules.training.models import EnvironmentRefModel
from backend.modules.training.providers.base import (
    FetchResult,
    ProviderError,
    ScaffoldResult,
    code_cell,
    md_cell,
)
from backend.sdk.registry import registry as sdk_registry


class FakeProvider:
    provider = "fake"
    label = "Fake"
    kinds = ("competition",)

    def __init__(self) -> None:
        self.fetched: list[str] = []

    def search(self, query, kind, limit):
        if "pokemon" not in query.lower():
            return []
        return [self.resolve("pokemon-tcg", kind)]

    def resolve(self, ref_id, kind):
        if ref_id != "pokemon-tcg":
            raise ProviderError(f"competition not found: {ref_id}")
        return EnvironmentRefModel(
            provider="fake",
            kind="competition",
            id="pokemon-tcg",
            title="Pokemon TCG",
            url="https://example.test/pokemon-tcg",
        )

    def fetch(self, ref, dest: Path, progress):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "train.csv").write_text("a,b\n1,2\n")
        self.fetched.append(ref.id)
        progress("downloaded train.csv", 1.0)
        return FetchResult(files=["train.csv"], bytes=8)

    def scaffold(self, ref, project):
        return ScaffoldResult(
            cells=[md_cell(f"# {ref.title}"), code_cell("print('hi')")],
            requirements=["pandas"],
        )


@pytest.fixture
def fake_provider():
    provider = FakeProvider()
    sdk_registry.training_providers["fake"] = provider
    yield provider
    sdk_registry.training_providers.clear()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_provider) -> TestClient:
    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"training.projectsRoot": str(tmp_path / "projects")})
    )
    # Keep route tests off the real uv binary.
    monkeypatch.setattr(
        envs, "bootstrap", lambda project, reqs, progress: progress("venv ready")
    )
    monkeypatch.setattr(
        envs, "install", lambda project, pkgs, progress: progress("installed")
    )
    return TestClient(app)


def _create(client: TestClient) -> dict:
    res = client.post(
        "/api/training/projects",
        json={"provider": "fake", "ref": "pokemon-tcg", "kind": "competition"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _settle(timeout: float = 60.0) -> None:
    """Wait for the route's background workers to actually finish.

    This replaces a poll-until-a-deadline helper, which was a race dressed up as a
    test: it asked "has the file appeared in the last 15 seconds of wall clock?"
    and so passed on an idle machine and failed on a loaded one — with a failure
    that read like a product bug rather than a scheduling one. `join_workers`
    returns the instant the thread finishes, so the assertions below are about the
    work, not about how busy the CPU was.

    The timeout that remains is a deadlock backstop: crossing it means a worker
    genuinely never finished, which is worth failing on.
    """
    assert routes.join_workers(timeout), "training background worker never finished"


def test_providers_lists_fake(client: TestClient) -> None:
    res = client.get("/api/training/providers")
    assert res.status_code == 200
    ids = [p["provider"] for p in res.json()["providers"]]
    assert "fake" in ids and "kaggle" in ids


def test_search_and_resolve(client: TestClient) -> None:
    res = client.get("/api/training/providers/fake/search", params={"q": "pokemon tcg"})
    assert res.status_code == 200
    assert res.json()["results"][0]["id"] == "pokemon-tcg"

    res = client.post(
        "/api/training/providers/fake/resolve", json={"id": "pokemon-tcg"}
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Pokemon TCG"

    res = client.post("/api/training/providers/fake/resolve", json={"id": "nope"})
    assert res.status_code == 404

    res = client.get("/api/training/providers/nope/search", params={"q": "x"})
    assert res.status_code == 404


def test_create_project_scaffolds_notebook(client: TestClient) -> None:
    body = _create(client)
    assert body["id"] == "pokemon-tcg"
    assert body["refs"][0]["provider"] == "fake"

    project = projects.get_project("pokemon-tcg")
    assert project is not None
    nb_path = Path(project.root) / "main.ipynb"
    assert nb_path.is_file()
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    assert nb["metadata"]["horrible"]["projectId"] == "pokemon-tcg"
    sources = ["".join(c["source"]) for c in nb["cells"]]
    assert any("Pokemon TCG" in s for s in sources)
    assert all(c.get("id") for c in nb["cells"])  # nbformat 4.5 ids

    listed = client.get("/api/training/projects").json()["projects"]
    assert [p["id"] for p in listed] == ["pokemon-tcg"]


def test_fetch_populates_data(client: TestClient, fake_provider: FakeProvider) -> None:
    _create(client)
    res = client.post("/api/training/projects/pokemon-tcg/fetch")
    assert res.status_code == 202

    _settle()

    assert fake_provider.fetched == ["pokemon-tcg"]
    project = projects.get_project("pokemon-tcg")
    assert project is not None
    assert (Path(project.root) / "data" / "train.csv").is_file()
    # Re-read rather than reusing `project`: the fetch worker writes the flag
    # through `_mark`, which re-reads under a lock, so the snapshot above is stale
    # by design.
    fetched = projects.get_project("pokemon-tcg")
    assert fetched is not None, "project vanished while the fetch worker ran"
    assert fetched.data_ready


def test_notebook_get_and_put(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/training/projects/pokemon-tcg/notebook")
    assert res.status_code == 200
    doc = res.json()
    assert doc["path"] == "main.ipynb"
    assert doc["cells"][0]["cell_type"] == "markdown"

    doc["cells"].append(
        {"id": "", "cell_type": "code", "source": "1 + 1", "outputs": []}
    )
    res = client.put("/api/training/projects/pokemon-tcg/notebook", json=doc)
    assert res.status_code == 200
    assert res.json()["cells"][-1]["source"] == "1 + 1"

    res = client.get(
        "/api/training/projects/pokemon-tcg/notebook",
        params={"path": "../../etc/passwd"},
    )
    assert res.status_code == 400


def test_deps_endpoint_validates(client: TestClient) -> None:
    _create(client)
    res = client.post("/api/training/projects/pokemon-tcg/deps", json={"packages": []})
    assert res.status_code == 400
    res = client.post(
        "/api/training/projects/pokemon-tcg/deps", json={"packages": ["torch"]}
    )
    assert res.status_code == 202


def test_delete_project(client: TestClient) -> None:
    _create(client)
    res = client.delete("/api/training/projects/pokemon-tcg")
    assert res.status_code == 200 and res.json()["deleted"] is True
    assert client.get("/api/training/projects/pokemon-tcg").status_code == 404


def test_unknown_project_404s(client: TestClient) -> None:
    assert client.get("/api/training/projects/nope").status_code == 404
    assert client.post("/api/training/projects/nope/fetch").status_code == 404
