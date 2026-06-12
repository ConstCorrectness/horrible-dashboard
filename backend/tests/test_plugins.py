import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app

PLUGIN_ID = "hello-widget"

MANIFEST = {
    "id": PLUGIN_ID,
    "name": "Hello Widget",
    "version": "0.1.0",
    "description": "A demo widget.",
    "author": "horrible",
    "entry": "dist/index.js",
    "sdkVersion": 1,
    "requiredCapabilities": [],
    "permissions": ["storage"],
}

ENTRY_JS = "export default { setup: () => ({}) };\n"


def _write_package(root: Path, manifest: dict) -> Path:
    pkg = root / manifest["id"]
    (pkg / "dist").mkdir(parents=True)
    (pkg / "horrible-plugin.json").write_text(json.dumps(manifest))
    (pkg / "dist" / "index.js").write_text(ENTRY_JS, newline="\n")
    return pkg


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch) -> Path:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _write_package(catalog, MANIFEST)
    monkeypatch.setenv("HORRIBLE_PLUGIN_CATALOG", str(catalog))
    return catalog


@pytest.fixture
def client(tmp_path, monkeypatch, catalog_dir) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "data"))
    return TestClient(app)


def test_catalog_lists_packages(client: TestClient) -> None:
    res = client.get("/api/plugins/catalog")
    assert res.status_code == 200
    plugins = res.json()["plugins"]
    assert len(plugins) == 1
    assert plugins[0]["id"] == PLUGIN_ID
    assert plugins[0]["sdkVersion"] == 1
    assert plugins[0]["entry"] == "dist/index.js"


def test_catalog_skips_invalid_manifest(client: TestClient, catalog_dir: Path) -> None:
    bad = catalog_dir / "broken"
    bad.mkdir()
    (bad / "horrible-plugin.json").write_text("{not json")
    res = client.get("/api/plugins/catalog")
    assert [p["id"] for p in res.json()["plugins"]] == [PLUGIN_ID]


def test_catalog_skips_id_directory_mismatch(
    client: TestClient, catalog_dir: Path
) -> None:
    impostor = dict(MANIFEST, id="other-id")
    pkg = catalog_dir / "impostor"
    pkg.mkdir()
    (pkg / "horrible-plugin.json").write_text(json.dumps(impostor))
    res = client.get("/api/plugins/catalog")
    assert [p["id"] for p in res.json()["plugins"]] == [PLUGIN_ID]


def test_catalog_rejects_traversal_entry(client: TestClient, catalog_dir: Path) -> None:
    evil = dict(MANIFEST, id="evil", entry="../../../etc/passwd")
    pkg = catalog_dir / "evil"
    pkg.mkdir()
    (pkg / "horrible-plugin.json").write_text(json.dumps(evil))
    res = client.get("/api/plugins/catalog")
    assert [p["id"] for p in res.json()["plugins"]] == [PLUGIN_ID]


def test_installed_empty_by_default(client: TestClient) -> None:
    res = client.get("/api/plugins/installed")
    assert res.status_code == 200
    assert res.json() == {"plugins": []}


def test_install_and_list(client: TestClient) -> None:
    res = client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    assert res.status_code == 200
    assert res.json()["manifest"]["id"] == PLUGIN_ID
    assert res.json()["enabled"] is True

    res = client.get("/api/plugins/installed")
    plugins = res.json()["plugins"]
    assert len(plugins) == 1
    assert plugins[0]["manifest"]["version"] == "0.1.0"
    assert plugins[0]["enabled"] is True


def test_install_unknown_plugin_404(client: TestClient) -> None:
    res = client.post("/api/plugins/install", json={"id": "nope"})
    assert res.status_code == 404


def test_install_invalid_id_422(client: TestClient) -> None:
    res = client.post("/api/plugins/install", json={"id": "../evil"})
    assert res.status_code == 422


def test_reinstall_updates_package(client: TestClient, catalog_dir: Path) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    bumped = dict(MANIFEST, version="0.2.0")
    (catalog_dir / PLUGIN_ID / "horrible-plugin.json").write_text(json.dumps(bumped))

    res = client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    assert res.status_code == 200
    assert res.json()["manifest"]["version"] == "0.2.0"


def test_enable_disable_round_trip(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})

    res = client.put(f"/api/plugins/{PLUGIN_ID}/enabled", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["enabled"] is False
    assert client.get("/api/plugins/installed").json()["plugins"][0]["enabled"] is False

    res = client.put(f"/api/plugins/{PLUGIN_ID}/enabled", json={"enabled": True})
    assert client.get("/api/plugins/installed").json()["plugins"][0]["enabled"] is True


def test_enable_uninstalled_404(client: TestClient) -> None:
    res = client.put(f"/api/plugins/{PLUGIN_ID}/enabled", json={"enabled": False})
    assert res.status_code == 404


def test_uninstall(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    res = client.delete(f"/api/plugins/{PLUGIN_ID}")
    assert res.status_code == 200
    assert client.get("/api/plugins/installed").json() == {"plugins": []}


def test_uninstall_unknown_404(client: TestClient) -> None:
    res = client.delete(f"/api/plugins/{PLUGIN_ID}")
    assert res.status_code == 404


def test_asset_served_with_js_mime(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    res = client.get(f"/api/plugins/{PLUGIN_ID}/assets/dist/index.js")
    assert res.status_code == 200
    assert res.text == ENTRY_JS
    # Load-bearing on Windows: the registry can map .js to text/plain, and
    # browsers reject ES module imports with a non-JS MIME type.
    assert res.headers["content-type"].startswith("text/javascript")


def test_asset_missing_404(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    res = client.get(f"/api/plugins/{PLUGIN_ID}/assets/dist/missing.js")
    assert res.status_code == 404


@pytest.mark.parametrize(
    "asset_path",
    [
        "../state.json",
        "..%2Fstate.json",
        "../../other/package/horrible-plugin.json",
        "/etc/passwd",
        "c:/windows/win.ini",
    ],
)
def test_asset_traversal_rejected(client: TestClient, asset_path: str) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    res = client.get(f"/api/plugins/{PLUGIN_ID}/assets/{asset_path}")
    assert res.status_code == 404


def test_storage_round_trip(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    value = {"count": 3, "nested": {"ok": True}}

    res = client.put(f"/api/plugins/{PLUGIN_ID}/storage/counter", json={"value": value})
    assert res.status_code == 200
    assert res.json() == {"key": "counter", "value": value}

    res = client.get(f"/api/plugins/{PLUGIN_ID}/storage/counter")
    assert res.status_code == 200
    assert res.json()["value"] == value

    res = client.delete(f"/api/plugins/{PLUGIN_ID}/storage/counter")
    assert res.status_code == 200
    assert client.get(f"/api/plugins/{PLUGIN_ID}/storage/counter").status_code == 404


def test_storage_missing_key_404(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    res = client.get(f"/api/plugins/{PLUGIN_ID}/storage/nope")
    assert res.status_code == 404


def test_storage_requires_install(client: TestClient) -> None:
    res = client.put(f"/api/plugins/{PLUGIN_ID}/storage/counter", json={"value": 1})
    assert res.status_code == 404


def test_storage_invalid_key_422(client: TestClient) -> None:
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    res = client.put(f"/api/plugins/{PLUGIN_ID}/storage/{'k' * 200}", json={"value": 1})
    assert res.status_code == 422


def test_storage_isolated_per_plugin(client: TestClient, catalog_dir: Path) -> None:
    other = dict(MANIFEST, id="other-plugin", name="Other")
    _write_package(catalog_dir, other)
    client.post("/api/plugins/install", json={"id": PLUGIN_ID})
    client.post("/api/plugins/install", json={"id": "other-plugin"})

    client.put(f"/api/plugins/{PLUGIN_ID}/storage/k", json={"value": "mine"})
    res = client.get("/api/plugins/other-plugin/storage/k")
    assert res.status_code == 404
