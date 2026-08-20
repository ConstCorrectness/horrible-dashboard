import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_settings_default_empty(client: TestClient) -> None:
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json() == {"values": {}, "secretKeys": []}


def test_put_then_get_roundtrip(client: TestClient) -> None:
    res = client.put("/api/settings/observability.recentCount", json={"value": 2})
    assert res.status_code == 200
    assert res.json() == {"value": 2}

    # A second key of a different type coexists in the bag.
    client.put("/api/settings/hello-widget.greeting", json={"value": "hi"})

    values = client.get("/api/settings").json()["values"]
    assert values == {"observability.recentCount": 2, "hello-widget.greeting": "hi"}


def test_delete_resets_key(client: TestClient) -> None:
    client.put("/api/settings/observability.recentCount", json={"value": 9})
    res = client.delete("/api/settings/observability.recentCount")
    assert res.status_code == 200
    assert res.json() == {"values": {}, "secretKeys": []}
    assert client.get("/api/settings").json() == {"values": {}, "secretKeys": []}


def test_delete_missing_key_is_noop(client: TestClient) -> None:
    res = client.delete("/api/settings/never.set")
    assert res.status_code == 200
    assert res.json() == {"values": {}, "secretKeys": []}


def test_secret_shaped_values_are_never_served(client: TestClient) -> None:
    """`GET /api/settings` hands the whole bag to whatever asked — including
    third-party plugins, which load unsandboxed. A token in that response is a
    credential given away."""
    from backend.modules.settings.routes import get_value

    client.put("/api/settings/training.hf.token", json={"value": "hf_realtoken"})
    client.put("/api/settings/training.google.clientSecret", json={"value": "shhh"})
    client.put("/api/settings/training.google.clientId", json={"value": "public-id"})

    body = client.get("/api/settings").json()
    assert body["values"]["training.hf.token"] == ""
    assert body["values"]["training.google.clientSecret"] == ""
    # A client *id* is public and must keep working as an ordinary setting.
    assert body["values"]["training.google.clientId"] == "public-id"
    # "Set" is knowable; the value is not.
    assert sorted(body["secretKeys"]) == [
        "training.google.clientSecret",
        "training.hf.token",
    ]
    assert "hf_realtoken" not in res_text(client)

    # The backend still reads the real value — redaction is on the way out only.
    assert get_value("training.hf.token", "") == "hf_realtoken"


def res_text(client: TestClient) -> str:
    return client.get("/api/settings").text


def test_unset_secret_is_not_listed(client: TestClient) -> None:
    client.put("/api/settings/training.hf.token", json={"value": ""})
    assert client.get("/api/settings").json()["secretKeys"] == []


def test_put_rejects_bad_key(client: TestClient) -> None:
    # Slashes/leading dots aren't valid setting keys.
    res = client.put("/api/settings/..bad", json={"value": 1})
    assert res.status_code == 422


def test_put_rejects_missing_value(client: TestClient) -> None:
    res = client.put("/api/settings/observability.recentCount", json={})
    assert res.status_code == 422


def test_concurrent_writes_do_not_clobber_each_other(client: TestClient) -> None:
    """Two overlapping PUTs must both survive.

    Each write is a read-modify-write of the whole bag, and the routes are sync
    `def`, so FastAPI runs them on the threadpool: without a lock both requests
    read the pre-change bag and the second one writes it back missing the first
    one's key — 200 on both, one setting silently gone. First-run setup writes
    the name, the theme and `desktop.oobeComplete` from one click, which is how a
    completed wizard came back on the next launch.
    """
    from concurrent.futures import ThreadPoolExecutor

    keys = [f"race.key{i}" for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda k: client.put(f"/api/settings/{k}", json={"value": k}), keys
            )
        )
    assert all(r.status_code == 200 for r in results)

    values = client.get("/api/settings").json()["values"]
    assert values == {k: k for k in keys}
