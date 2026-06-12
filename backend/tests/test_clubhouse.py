import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.clubhouse import routes

FAKE_PROFILE = {
    "user_id": 4242,
    "username": "horrible",
    "name": "Horrible",
    "photo_url": "https://example.com/p.jpg",
}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    return TestClient(app)


def _connect(tmp_path) -> None:
    (tmp_path / "clubhouse-auth.json").write_text(
        json.dumps({"auth_token": "T", "user_id": 4242, "device_id": "D"})
    )


def test_channels_requires_connection(client: TestClient) -> None:
    assert client.get("/api/clubhouse/channels").status_code == 409


def test_channels_returns_parsed_rooms(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_get(path, token, user_id, device_id=None, params=None):
        assert (path, token, user_id, device_id) == ("/get_channels", "T", 4242, "D")
        return {
            "success": True,
            "channels": [
                {
                    "channel": "abc",
                    "topic": "Late night code",
                    "num_speakers": 2,
                    "num_all": 9,
                    "club": {"name": "Builders"},
                    "users": [{"user_id": 1, "name": "Ada", "is_speaker": True}],
                }
            ],
        }

    monkeypatch.setattr(routes, "_ch_authed_get", fake_get)
    body = client.get("/api/clubhouse/channels").json()
    assert body["channels"][0]["topic"] == "Late night code"
    assert body["channels"][0]["club"]["name"] == "Builders"
    assert body["channels"][0]["users"][0]["name"] == "Ada"


def test_following_returns_users(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert path == "/get_following"
        assert payload["user_id"] == 4242
        return {"users": [{"user_id": 7, "username": "bob", "name": "Bob"}]}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    body = client.get("/api/clubhouse/following").json()
    assert body["users"][0]["username"] == "bob"


def _mock_ch(monkeypatch, responses: dict[str, dict]) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def fake_post(path: str, payload: dict) -> dict:
        calls.append((path, payload))
        return responses[path]

    monkeypatch.setattr(routes, "_ch_post", fake_post)
    return calls


def test_status_disconnected_by_default(client: TestClient) -> None:
    res = client.get("/api/clubhouse/status")
    assert res.status_code == 200
    assert res.json()["connected"] is False


def test_start_auth_passes_phone_through(client: TestClient, monkeypatch) -> None:
    calls = _mock_ch(monkeypatch, {"/start_phone_number_auth": {"success": True}})
    res = client.post(
        "/api/clubhouse/auth/start", json={"phone_number": "+15551234567"}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert calls == [("/start_phone_number_auth", {"phone_number": "+15551234567"})]


def test_start_auth_rejects_bad_phone(client: TestClient) -> None:
    res = client.post("/api/clubhouse/auth/start", json={"phone_number": "555-1234"})
    assert res.status_code == 422


def test_complete_auth_persists_and_never_leaks_token(
    client: TestClient, monkeypatch
) -> None:
    _mock_ch(
        monkeypatch,
        {
            "/complete_phone_number_auth": {
                "auth_token": "SECRET-TOKEN",
                "refresh_token": "SECRET-REFRESH",
                "user_profile": FAKE_PROFILE,
            }
        },
    )
    res = client.post(
        "/api/clubhouse/auth/complete",
        json={"phone_number": "+15551234567", "verification_code": "1234"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    assert body["username"] == "horrible"
    assert "SECRET" not in res.text

    status = client.get("/api/clubhouse/status")
    assert status.json()["connected"] is True
    assert "SECRET" not in status.text


def test_complete_auth_wrong_code_is_400(client: TestClient, monkeypatch) -> None:
    _mock_ch(monkeypatch, {"/complete_phone_number_auth": {"success": False}})
    res = client.post(
        "/api/clubhouse/auth/complete",
        json={"phone_number": "+15551234567", "verification_code": "0000"},
    )
    assert res.status_code == 400


def test_connect_with_token_validates_and_stores(
    client: TestClient, monkeypatch
) -> None:
    calls: list[tuple] = []

    async def fake_authed_post(path, payload, token, user_id, device_id=None):
        calls.append((path, token, user_id, device_id))
        return {"user_profile": FAKE_PROFILE}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_authed_post)
    res = client.post(
        "/api/clubhouse/auth/token",
        json={"auth_token": "TKN", "user_id": 4242, "device_id": "DEV-1"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    assert body["username"] == "horrible"
    assert "TKN" not in res.text  # token never echoed back
    # validated against /me with the supplied credentials
    assert calls == [("/me", "TKN", 4242, "DEV-1")]

    status = client.get("/api/clubhouse/status")
    assert status.json()["connected"] is True


def test_connect_with_token_rejects_bad_token(client: TestClient, monkeypatch) -> None:
    from fastapi import HTTPException

    async def fake_authed_post(path, payload, token, user_id, device_id=None):
        raise HTTPException(status_code=401, detail="Clubhouse: unauthorized")

    monkeypatch.setattr(routes, "_ch_authed_post", fake_authed_post)
    res = client.post(
        "/api/clubhouse/auth/token", json={"auth_token": "bad", "user_id": 1}
    )
    assert res.status_code == 401
    assert client.get("/api/clubhouse/status").json()["connected"] is False


def test_disconnect(client: TestClient, monkeypatch) -> None:
    _mock_ch(
        monkeypatch,
        {
            "/complete_phone_number_auth": {
                "auth_token": "T",
                "user_profile": FAKE_PROFILE,
            }
        },
    )
    client.post(
        "/api/clubhouse/auth/complete",
        json={"phone_number": "+15551234567", "verification_code": "1234"},
    )
    res = client.delete("/api/clubhouse/auth")
    assert res.json()["connected"] is False
    assert client.get("/api/clubhouse/status").json()["connected"] is False
