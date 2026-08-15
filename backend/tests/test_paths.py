"""Where the node's files land, per launcher and per OS.

The bug this module exists to prevent is silent: a path that resolves differently
depending on which launcher started the backend looks like data loss, not like a
crash. So these tests pin the *resolution rules* rather than any one path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend import paths


@pytest.fixture
def packaged(monkeypatch):
    """A build that is not a checkout: no repo, no env overrides."""
    monkeypatch.setattr(paths, "repo_root", lambda: None)
    for var in (
        "HORRIBLE_DATA_DIR",
        "HORRIBLE_CONFIG_DIR",
        "HORRIBLE_CACHE_DIR",
        "HORRIBLE_LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


def _fake_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(paths, "_home", lambda: tmp_path)
    return tmp_path


def test_env_override_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path / "elsewhere"


def test_override_is_read_per_call(monkeypatch, tmp_path):
    # Not cached at import: a test (or `dash`) that repoints the data dir mid-process
    # must be answered honestly, which is why resolution stays pure.
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "one"))
    assert paths.data_dir() == tmp_path / "one"
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "two"))
    assert paths.data_dir() == tmp_path / "two"


def test_blank_override_is_not_an_override(monkeypatch, tmp_path):
    # An empty or whitespace value is what an unset `.env` line yields; treating it
    # as a path would put the data dir at the process cwd.
    monkeypatch.setenv("HORRIBLE_DATA_DIR", "   ")
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)
    assert paths.data_dir() == tmp_path / ".data"


def test_checkout_keeps_its_data_in_the_tree(monkeypatch, tmp_path):
    monkeypatch.delenv("HORRIBLE_DATA_DIR", raising=False)
    monkeypatch.delenv("HORRIBLE_LOG_DIR", raising=False)
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)
    assert paths.data_dir() == tmp_path / ".data"
    assert paths.log_dir() == tmp_path / "logs"


def test_this_backend_is_a_checkout():
    # The detection itself, unmocked: these tests run from the repo, so it must say
    # so — a false negative here would silently relocate a developer's whole node.
    root = paths.repo_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert (root / "backend" / "paths.py").is_file()


def test_repo_root_is_anchored_on_the_source_not_the_cwd(monkeypatch, tmp_path):
    # The whole point: `pnpm dev`, the Tauri supervisor and a packaged shortcut all
    # start the backend with a different cwd.
    paths.repo_root.cache_clear()
    monkeypatch.chdir(tmp_path)
    try:
        assert paths.repo_root() == Path(paths.__file__).resolve().parent.parent
    finally:
        paths.repo_root.cache_clear()


@pytest.mark.usefixtures("packaged")
def test_packaged_windows_uses_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    data = paths.data_dir()
    assert data == tmp_path / "Local" / paths.APP_NAME
    # Roaming profiles are copied at logon; multi-gigabyte GGUFs must not be.
    assert "Roaming" not in str(data)


@pytest.mark.usefixtures("packaged")
def test_packaged_macos_uses_application_support(monkeypatch, tmp_path):
    home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert (
        paths.data_dir()
        == home / "Library" / "Application Support" / "HorribleDashboard"
    )
    assert paths.cache_dir() == home / "Library" / "Caches" / "HorribleDashboard"
    assert paths.log_dir() == home / "Library" / "Logs" / "HorribleDashboard"


@pytest.mark.usefixtures("packaged")
def test_packaged_linux_follows_xdg(monkeypatch, tmp_path):
    home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert paths.data_dir() == home / ".local/share" / paths.APP_NAME_UNIX
    assert paths.cache_dir() == home / ".cache" / paths.APP_NAME_UNIX
    assert paths.log_dir() == home / ".local/state" / paths.APP_NAME_UNIX / "logs"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir() == tmp_path / "xdg" / paths.APP_NAME_UNIX


@pytest.mark.usefixtures("packaged")
def test_relative_xdg_value_is_ignored(monkeypatch, tmp_path):
    # The XDG spec says a relative value must be ignored, not resolved against the
    # cwd — which is exactly the failure mode this whole module is about.
    home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert paths.data_dir() == home / ".local/share" / paths.APP_NAME_UNIX


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_config_dir_is_never_inside_the_data_dir(monkeypatch, tmp_path, platform):
    """The load-bearing invariant, on every platform and in a checkout.

    `config_dir()` holds the Fernet master key and `data_dir()` holds the database
    it decrypts. The threat model is the data dir being copied somewhere it
    shouldn't — a synced folder, a backup, a screen share — so the two must not
    nest, and the conventional per-OS config locations for this app all would.
    """
    home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("HORRIBLE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("HORRIBLE_DATA_DIR", raising=False)

    for repo in (None, tmp_path / "checkout"):
        monkeypatch.setattr(paths, "repo_root", lambda repo=repo: repo)
        config = paths.config_dir()
        data = paths.data_dir()
        assert config == home / ".horrible"
        assert not config.is_relative_to(data)
        assert not data.is_relative_to(config)


def test_secrets_key_path_tracks_the_config_dir(monkeypatch, tmp_path):
    from backend.modules.database import secrets_store

    monkeypatch.delenv("SECRETS_KEY_PATH", raising=False)
    monkeypatch.setenv("HORRIBLE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert secrets_store.get_key_path() == tmp_path / "cfg" / "secrets.key"


def test_describe_reports_every_root():
    described = paths.describe()
    assert set(described) == {"data", "config", "cache", "logs", "repo"}
    assert described["data"] == str(paths.data_dir())


def test_describe_roots_reports_why_each_root_resolved(monkeypatch, tmp_path):
    """The settings section renders `source`, and it is the point of the payload:
    a path alone cannot tell an override the user set from an OS default."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HORRIBLE_LOG_DIR", raising=False)
    # The conftest isolates config with an override, which would itself report
    # `environment`; cleared so the assertion below is about the *rules*.
    monkeypatch.delenv("HORRIBLE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path)

    payload = paths.describe_roots()
    roots = {root["id"]: root for root in payload["roots"]}
    assert set(roots) == {"data", "config", "cache", "logs"}
    assert payload["repo"] == str(tmp_path)

    assert roots["data"]["source"] == "environment"
    assert roots["data"]["envVar"] == "HORRIBLE_DATA_DIR"
    assert roots["logs"]["source"] == "checkout"
    # Config has no checkout branch at all, so a checkout cannot make it one.
    assert roots["config"]["source"] == "platform"


def test_describe_roots_reports_existence(monkeypatch, tmp_path):
    # "Not created yet" is normal — a root appears on first write — but the pane
    # must not offer to open a directory that isn't there.
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "made"))
    (tmp_path / "made").mkdir()
    monkeypatch.setenv("HORRIBLE_CACHE_DIR", str(tmp_path / "never"))
    roots = {root["id"]: root for root in paths.describe_roots()["roots"]}
    assert roots["data"]["exists"] is True
    assert roots["cache"]["exists"] is False


def test_paths_route_serves_the_long_form():
    from fastapi.testclient import TestClient

    from backend.app import app

    body = TestClient(app).get("/api/paths").json()
    assert {root["id"] for root in body["roots"]} == {"data", "config", "cache", "logs"}
    assert all(root["path"] and root["note"] for root in body["roots"])
