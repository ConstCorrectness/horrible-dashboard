"""Notebook publishing: preflight, the GitHub/Kaggle targets, and the records.

GitHub is mocked at `github_api.request` and scopes at `github.granted_scopes` — the
two seams the targets actually call — so these tests say what is sent, in what order,
and what is refused before anything is sent at all.

Secret-shaped strings are **built at runtime**. A literal token-looking value in a
committed test file is exactly what repository secret scanners exist to flag.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.modules.connectors.providers import github, github_api

FAKE_GITHUB_TOKEN = "ghp" + "_" + "A1b2" * 9


@pytest.fixture
def nb_root(tmp_path) -> Path:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "notebooks"
    root.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"notebook.root": str(root)}))
    return root


@pytest.fixture
def client(nb_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


def _notebook(root: Path, cells: list[Any]) -> str:
    # A unique name per test: the publication records share one app.db.
    name = f"nb_{uuid.uuid4().hex[:8]}.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.cells = cells
    nbformat.write(nb, str(root / name))
    return name


@pytest.fixture
def clean_nb(nb_root) -> str:
    code = nbformat.v4.new_code_cell("print(sum(range(10)))")
    code.outputs = [nbformat.v4.new_output("stream", name="stdout", text="45\n")]
    return _notebook(nb_root, [nbformat.v4.new_markdown_cell("# Churn analysis"), code])


@pytest.fixture
def secret_nb(nb_root) -> str:
    code = nbformat.v4.new_code_cell(f'token = "{FAKE_GITHUB_TOKEN}"')
    code.outputs = [
        nbformat.v4.new_output(
            "stream", name="stdout", text="saved to /home/alice/project/out.csv\n"
        )
    ]
    return _notebook(nb_root, [nbformat.v4.new_markdown_cell("# Churn analysis"), code])


class FakeGitHub:
    """Scripted GitHub. `routes` maps (METHOD, path) to a response or a list of them."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    async def __call__(
        self, method: str, path: str, *, params: Any = None, json: Any = None
    ) -> Any:
        self.calls.append((method, path, json))
        if (method, path) in self.routes:
            answer = self.routes[(method, path)]
            if isinstance(answer, list):
                return answer.pop(0) if len(answer) > 1 else answer[0]
            return answer
        if method == "GET" and "/contents/" in path:
            return {"error": "GitHub returned 404: Not Found", "status": 404}
        if method == "PUT" and "/contents/" in path:
            return {"content": {}}
        raise AssertionError(f"unexpected GitHub call: {method} {path}")


def _github(monkeypatch, routes: dict, scopes: list[str] | None = None) -> FakeGitHub:
    fake = FakeGitHub(routes)
    monkeypatch.setattr(github_api, "request", fake)
    monkeypatch.setattr(
        github,
        "granted_scopes",
        lambda: ["read:user", "repo", "gist"] if scopes is None else scopes,
    )
    return fake


GIST = {
    "id": "abc123",
    "html_url": "https://gist.github.com/alice/abc123",
    "owner": {"login": "alice"},
}


# --- preflight ------------------------------------------------------------------


def test_preflight_finds_the_secret_and_the_home_path(client, secret_nb) -> None:
    res = client.post("/api/notebook/publish/preflight", json={"path": secret_nb})
    assert res.status_code == 200, res.text
    body = res.json()
    labels = {(f["kind"], f["where"], f["label"]) for f in body["findings"]}
    assert ("secret", "source", "GitHub token") in labels
    assert any(kind == "path" and where == "output" for kind, where, _ in labels)
    assert body["blocking"] >= 1


def test_a_finding_never_echoes_the_whole_secret(client, secret_nb) -> None:
    res = client.post("/api/notebook/publish/preflight", json={"path": secret_nb})
    assert FAKE_GITHUB_TOKEN not in res.text


def test_stripping_outputs_clears_findings_that_lived_in_outputs(
    client, secret_nb
) -> None:
    body = client.post(
        "/api/notebook/publish/preflight",
        json={"path": secret_nb, "strip_outputs": True},
    ).json()
    assert not [f for f in body["findings"] if f["where"] == "output"]
    # The source still carries the token; stripping outputs does not hide that.
    assert body["blocking"] >= 1


def test_stripping_never_touches_the_users_file(client, nb_root, secret_nb) -> None:
    client.post(
        "/api/notebook/publish/preflight",
        json={"path": secret_nb, "strip_outputs": True},
    )
    on_disk = nbformat.read(str(nb_root / secret_nb), as_version=4)
    assert on_disk.cells[1].outputs


# --- the gate -------------------------------------------------------------------


def test_a_secret_blocks_publishing_before_any_network_call(
    client, secret_nb, monkeypatch
) -> None:
    fake = _github(monkeypatch, {})
    body = client.post(
        "/api/notebook/publish", json={"path": secret_nb, "target": "gist"}
    ).json()
    assert body["ok"] is False
    assert "secret" in body["error"]
    assert fake.calls == []


def test_acknowledging_lets_it_through(client, secret_nb, monkeypatch) -> None:
    fake = _github(monkeypatch, {("POST", "/gists"): GIST})
    body = client.post(
        "/api/notebook/publish",
        json={"path": secret_nb, "target": "gist", "acknowledged": True},
    ).json()
    assert body["ok"] is True
    assert [c[:2] for c in fake.calls] == [("POST", "/gists")]


def test_unknown_target_is_404(client, clean_nb) -> None:
    res = client.post(
        "/api/notebook/publish", json={"path": clean_nb, "target": "myspace"}
    )
    assert res.status_code == 404


def test_a_target_refuses_a_visibility_it_cannot_honour(client, clean_nb) -> None:
    res = client.post(
        "/api/notebook/publish",
        json={"path": clean_nb, "target": "pages", "visibility": "secret"},
    )
    assert res.status_code == 400


# --- gist -----------------------------------------------------------------------


def test_gist_create_then_update_in_place(client, clean_nb, monkeypatch) -> None:
    fake = _github(
        monkeypatch,
        {("POST", "/gists"): GIST, ("PATCH", "/gists/abc123"): GIST},
    )
    first = client.post(
        "/api/notebook/publish", json={"path": clean_nb, "target": "gist"}
    ).json()
    assert first["ok"] is True, first
    publication = first["publication"]
    assert publication["url"] == "https://nbviewer.org/gist/alice/abc123"
    assert publication["extra_url"] == "https://gist.github.com/alice/abc123"
    sent = fake.calls[0][2]
    assert sent["public"] is False
    assert sent["description"] == "Churn analysis"
    assert list(sent["files"]) == [clean_nb]

    client.post("/api/notebook/publish", json={"path": clean_nb, "target": "gist"})
    assert fake.calls[-1][:2] == ("PATCH", "/gists/abc123")

    listed = client.get("/api/notebook/publish", params={"path": clean_nb}).json()
    assert [p["target"] for p in listed["publications"]] == ["gist"]


def test_changing_visibility_makes_a_new_gist_and_says_so(
    client, clean_nb, monkeypatch
) -> None:
    fake = _github(
        monkeypatch,
        {
            ("POST", "/gists"): [GIST, {**GIST, "id": "def456"}],
            ("DELETE", "/gists/abc123"): {},
        },
    )
    client.post("/api/notebook/publish", json={"path": clean_nb, "target": "gist"})
    body = client.post(
        "/api/notebook/publish",
        json={"path": clean_nb, "target": "gist", "visibility": "public"},
    ).json()
    assert body["publication"]["remote_id"] == "def456"
    assert ("DELETE", "/gists/abc123") in [c[:2] for c in fake.calls]
    assert "new gist" in body["note"]


def test_a_sign_in_without_gist_scope_says_reconnect(
    client, clean_nb, monkeypatch
) -> None:
    fake = _github(monkeypatch, {}, scopes=["read:user", "repo"])
    body = client.post(
        "/api/notebook/publish", json={"path": clean_nb, "target": "gist"}
    ).json()
    assert body["ok"] is False
    assert "Reconnect GitHub" in body["error"]
    assert fake.calls == []

    targets = client.get("/api/notebook/publish", params={"path": clean_nb}).json()[
        "targets"
    ]
    gist = next(t for t in targets if t["id"] == "gist")
    assert gist["available"] is False
    assert "Reconnect" in gist["reason"]


def test_unpublishing_deletes_the_gist_and_the_record(
    client, clean_nb, monkeypatch
) -> None:
    fake = _github(
        monkeypatch, {("POST", "/gists"): GIST, ("DELETE", "/gists/abc123"): {}}
    )
    client.post("/api/notebook/publish", json={"path": clean_nb, "target": "gist"})
    res = client.delete(
        "/api/notebook/publish", params={"path": clean_nb, "target": "gist"}
    )
    assert res.json() == {"ok": True, "error": ""}
    assert fake.calls[-1][:2] == ("DELETE", "/gists/abc123")
    listed = client.get("/api/notebook/publish", params={"path": clean_nb}).json()
    assert listed["publications"] == []


# --- pages ----------------------------------------------------------------------


def test_pages_creates_the_repo_and_publishes_rendered_html(
    client, clean_nb, monkeypatch
) -> None:
    not_found = {"error": "GitHub returned 404: Not Found", "status": 404}
    fake = _github(
        monkeypatch,
        {
            ("GET", "/user"): {"login": "alice"},
            ("GET", "/repos/alice/notebooks"): not_found,
            ("POST", "/user/repos"): {"private": False, "default_branch": "main"},
            ("GET", "/repos/alice/notebooks/pages"): not_found,
            ("POST", "/repos/alice/notebooks/pages"): {},
        },
    )
    body = client.post(
        "/api/notebook/publish",
        json={"path": clean_nb, "target": "pages", "visibility": "public"},
    ).json()
    assert body["ok"] is True, body
    slug = clean_nb.removesuffix(".ipynb").replace("_", "-")
    assert body["publication"]["url"] == f"https://alice.github.io/notebooks/{slug}/"

    puts = {path: payload for method, path, payload in fake.calls if method == "PUT"}
    assert "/repos/alice/notebooks/contents/.nojekyll" in puts
    html = base64.b64decode(
        puts[f"/repos/alice/notebooks/contents/{slug}/index.html"]["content"]
    )
    assert b"Churn analysis" in html
    assert b"<html" in html.lower()


def test_pages_refuses_a_private_repo(client, clean_nb, monkeypatch) -> None:
    _github(
        monkeypatch,
        {
            ("GET", "/user"): {"login": "alice"},
            ("GET", "/repos/alice/notebooks"): {
                "private": True,
                "default_branch": "main",
            },
        },
    )
    body = client.post(
        "/api/notebook/publish",
        json={"path": clean_nb, "target": "pages", "visibility": "public"},
    ).json()
    assert body["ok"] is False
    assert "private" in body["error"]


# --- kaggle ---------------------------------------------------------------------


def test_kaggle_publishes_without_gpu_and_can_only_be_forgotten(
    client, clean_nb, monkeypatch
) -> None:
    from backend.modules.training.models import PushResultModel
    from backend.modules.training.push import kaggle_push

    captured: dict[str, Any] = {}

    def fake_push(self, project, notebook, progress, *, private=True, enable_gpu=True):
        captured.update(
            project_id=project.id,
            private=private,
            enable_gpu=enable_gpu,
            staged=Path(notebook).is_file(),
        )
        return PushResultModel(
            target="kaggle", url=f"https://www.kaggle.com/code/alice/{project.id}"
        )

    monkeypatch.setattr(kaggle_push, "has_credentials", lambda: True)
    monkeypatch.setattr(kaggle_push.KagglePush, "push", fake_push)

    body = client.post(
        "/api/notebook/publish",
        json={"path": clean_nb, "target": "kaggle", "visibility": "public"},
    ).json()
    assert body["ok"] is True, body
    assert captured == {
        "project_id": "churn-analysis",
        "private": False,
        "enable_gpu": False,
        "staged": True,
    }

    refused = client.delete(
        "/api/notebook/publish", params={"path": clean_nb, "target": "kaggle"}
    ).json()
    assert refused["ok"] is False
    assert "Forget" in refused["error"]

    forgotten = client.delete(
        "/api/notebook/publish",
        params={"path": clean_nb, "target": "kaggle", "forget": True},
    ).json()
    assert forgotten["ok"] is True


# --- html -----------------------------------------------------------------------


def test_saving_html_writes_beside_the_notebook(client, nb_root, clean_nb) -> None:
    res = client.post("/api/notebook/publish/html", json={"path": clean_nb})
    assert res.status_code == 200, res.text
    saved = res.json()["path"]
    assert saved == clean_nb.replace(".ipynb", ".html")
    assert "Churn analysis" in (nb_root / saved).read_text(encoding="utf-8")


def test_html_download_is_an_attachment(client, clean_nb) -> None:
    res = client.get("/api/notebook/publish/html", params={"path": clean_nb})
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    assert "Churn analysis" in res.text
