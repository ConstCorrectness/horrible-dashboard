"""The test suite never reaches the shared Atlas cluster.

Found on 2026-09-16: dozens of junk records in the live `presence` directory, one per
test that booted the app — each with a fresh person key from its temp data dir,
published because `backend/__init__.py` had loaded the real credentials from `.env`.
The guard is in `conftest.py` (`pytest_configure` + `isolate_data_dir`).
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from backend import atlas
from backend.tests.conftest import ATLAS_ENV_VARS


def test_every_atlas_variable_is_blank():
    assert {name: os.environ.get(name) for name in ATLAS_ENV_VARS} == {
        name: "" for name in ATLAS_ENV_VARS
    }
    assert atlas.cluster_uri() is None
    assert atlas.client() is None
    assert atlas.collection("presence") is None


def test_dotenv_cannot_put_the_credentials_back():
    """`_load_dotenv` skips keys already in the environment — an empty string is
    present, so re-reading the real `.env` (if this checkout has one) changes
    nothing."""
    import backend

    backend._load_dotenv()
    assert atlas.cluster_uri() is None


def test_booting_the_app_publishes_presence_to_nothing(monkeypatch):
    """The exact path that polluted the directory: lifespan → `directory.publish`.
    It still runs; it must find no cluster to write to."""
    from backend.modules.social import directory

    real_publish = directory.publish
    seen: dict[str, object] = {}

    async def spy() -> bool:
        seen["collection"] = atlas.collection(directory.COLLECTION)
        seen["published"] = await real_publish()
        return bool(seen["published"])

    monkeypatch.setattr(directory, "publish", spy)
    from backend.app import app

    with TestClient(app):
        pass

    assert "collection" in seen, (
        "startup no longer publishes presence; update this test"
    )
    assert seen["collection"] is None
    assert seen["published"] is False
