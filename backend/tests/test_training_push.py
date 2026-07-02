"""Cloud push: kernel-metadata generation, mocked Kaggle push, mocked Drive
create-vs-update, and the google token store."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.training import google_auth, projects
from backend.modules.training.models import EnvironmentRefModel
from backend.modules.training.push import get_target, list_targets
from backend.modules.training.push.base import PushError
from backend.modules.training.push.colab_push import ColabPush
from backend.modules.training.push.kaggle_push import KagglePush, kernel_metadata


@pytest.fixture
def project(tmp_path):
    settings = Path(os.environ["HORRIBLE_DATA_DIR"]) / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "training.projectsRoot": str(tmp_path / "projects"),
                "training.kaggle.username": "horrible",
            }
        )
    )
    proj = projects.create_project(
        "Pokemon TCG",
        [
            EnvironmentRefModel(
                provider="kaggle", kind="competition", id="pokemon-tcg"
            ),
            EnvironmentRefModel(provider="kaggle", kind="dataset", id="org/cards"),
            EnvironmentRefModel(provider="huggingface", kind="dataset", id="hf/other"),
        ],
        "3.12",
    )
    (Path(proj.root) / "main.ipynb").write_text("{}", encoding="utf-8")
    return proj


def test_registry() -> None:
    ids = {t["target"] for t in list_targets()}
    assert ids == {"kaggle", "colab"}
    with pytest.raises(PushError, match="unknown push target"):
        get_target("nope")


def test_kernel_metadata_sources(project) -> None:
    meta = kernel_metadata(project, "horrible")
    assert meta["id"] == f"horrible/{project.id}"
    assert meta["competition_sources"] == ["pokemon-tcg"]
    assert meta["dataset_sources"] == ["org/cards"]  # HF ref excluded
    assert meta["code_file"] == "main.ipynb" and meta["enable_gpu"] is True


def test_kaggle_push_writes_metadata_and_calls_api(project, monkeypatch) -> None:
    from backend.modules.training.push import kaggle_push as kp

    calls: list[str] = []
    monkeypatch.setattr(
        kp,
        "_api",
        lambda: SimpleNamespace(kernels_push=lambda root: calls.append(root)),
    )
    result = KagglePush().push(
        project, Path(project.root) / "main.ipynb", lambda _l: None
    )
    assert calls == [project.root]
    meta = json.loads((Path(project.root) / "kernel-metadata.json").read_text())
    assert meta["id"] == f"horrible/{project.id}"
    assert result.url == f"https://www.kaggle.com/code/horrible/{project.id}"
    assert result.status == "pushed"


def test_kaggle_status(project, monkeypatch) -> None:
    from backend.modules.training.push import kaggle_push as kp

    monkeypatch.setattr(
        kp,
        "_api",
        lambda: SimpleNamespace(
            kernels_status=lambda kid: SimpleNamespace(
                status="complete", failureMessage=None
            )
        ),
    )
    result = KagglePush().status(project)
    assert result.status == "complete"


class _FakeDriveFiles:
    def __init__(self, log: list) -> None:
        self.log = log

    def create(self, body=None, media_body=None, fields=None):
        self.log.append(("create", body["name"]))
        return SimpleNamespace(execute=lambda: {"id": "file123"})

    def update(self, fileId=None, media_body=None, fields=None):
        self.log.append(("update", fileId))
        return SimpleNamespace(execute=lambda: {"id": fileId})


def test_colab_push_create_then_update(project, monkeypatch) -> None:
    from backend.modules.training.push import colab_push as cp

    log: list = []
    monkeypatch.setattr(
        cp, "_drive", lambda: SimpleNamespace(files=lambda: _FakeDriveFiles(log))
    )
    monkeypatch.setattr(
        "googleapiclient.http.MediaFileUpload",
        lambda *a, **k: SimpleNamespace(),
        raising=False,
    )
    target = ColabPush()
    nb = Path(project.root) / "main.ipynb"
    first = target.push(project, nb, lambda _l: None)
    assert log[0][0] == "create"
    assert first.url.endswith("/drive/file123")
    second = target.push(project, nb, lambda _l: None)
    assert log[1] == ("update", "file123")
    assert second.url == first.url
    assert target.status(project).status == "pushed"


def test_google_token_store_roundtrip(tmp_path, monkeypatch) -> None:
    assert google_auth.status() == {"connected": False}
    google_auth._token_path().parent.mkdir(parents=True, exist_ok=True)
    google_auth._token_path().write_text(
        '{"refresh_token": "secret"}', encoding="utf-8"
    )
    assert google_auth.status() == {"connected": True}
    google_auth.disconnect()
    assert google_auth.status() == {"connected": False}


def test_google_status_route_never_leaks_token(monkeypatch) -> None:
    google_auth._token_path().parent.mkdir(parents=True, exist_ok=True)
    google_auth._token_path().write_text(
        '{"refresh_token": "sekret"}', encoding="utf-8"
    )
    client = TestClient(app)
    res = client.get("/api/training/google/status")
    assert res.status_code == 200
    assert res.json() == {"connected": True}
    assert "sekret" not in res.text
    google_auth.disconnect()


def test_auth_start_requires_client_config() -> None:
    with pytest.raises(PushError, match="not configured"):
        google_auth.auth_start()
