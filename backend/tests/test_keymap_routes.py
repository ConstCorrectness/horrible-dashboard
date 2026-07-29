"""Keymap override storage — see docs/architecture/keybindings.mdx."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.keymap.routes import _keymap_path, read_keymap


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_empty_when_never_written(client):
    body = client.get("/api/keymap").json()
    assert body["bindings"] == []
    assert body["schema"] == "horrible.keymap"
    assert body["version"] == 1


def test_round_trip(client, data_dir):
    bindings = [
        {
            "key": "mod+shift+p",
            "command": "shell.commandPalette",
            "when": None,
            "disabled": False,
        },
        {
            "key": "n",
            "command": "region.toggle:right",
            "when": "paneFocus == 'editor.buffer'",
            "disabled": False,
        },
    ]
    put = client.put("/api/keymap", json={"bindings": bindings})
    assert put.status_code == 200
    assert [b["key"] for b in put.json()["bindings"]] == ["mod+shift+p", "n"]

    assert (
        client.get("/api/keymap").json()["bindings"][1]["when"]
        == "paneFocus == 'editor.buffer'"
    )
    assert (data_dir / "keymap.json").is_file()


def test_disabled_entry_survives(client):
    """A rebind is an add plus a disable, and both halves have to persist — a
    dropped `disabled` entry silently resurrects the default it replaced."""
    client.put(
        "/api/keymap",
        json={
            "bindings": [
                {"key": "alt+1", "command": "workspace.switch:1"},
                {"key": "mod+1", "command": "workspace.switch:1", "disabled": True},
            ]
        },
    )
    stored = client.get("/api/keymap").json()["bindings"]
    assert stored[0]["disabled"] is False
    assert stored[1]["disabled"] is True


def test_put_pins_schema_and_version(client):
    """A client cannot talk the store into claiming another format."""
    body = client.put(
        "/api/keymap",
        json={"schema": "someone.elses", "version": 99, "bindings": []},
    ).json()
    assert body["schema"] == "horrible.keymap"
    assert body["version"] == 1


def test_delete_clears_overrides(client):
    client.put("/api/keymap", json={"bindings": [{"key": "f5", "command": "x"}]})
    assert client.delete("/api/keymap").json()["bindings"] == []
    assert client.get("/api/keymap").json()["bindings"] == []


def test_malformed_file_falls_back_to_defaults(data_dir, client):
    """A corrupt keymap must never lock the user out of the UI that would fix it."""
    path = _keymap_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert read_keymap().bindings == []
    assert client.get("/api/keymap").json()["bindings"] == []


def test_wrong_shape_falls_back_to_defaults(data_dir):
    _keymap_path().write_text(json.dumps({"bindings": "not a list"}), encoding="utf-8")
    assert read_keymap().bindings == []


def test_rejects_a_binding_missing_its_command(client):
    assert (
        client.put("/api/keymap", json={"bindings": [{"key": "f5"}]}).status_code == 422
    )
