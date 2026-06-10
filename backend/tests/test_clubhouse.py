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
