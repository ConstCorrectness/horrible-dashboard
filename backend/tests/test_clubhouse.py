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

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/get_feed_v3", "T", 4242, "D")
        return {
            "items": [
                {
                    "channel": {
                        "channel": "abc",
                        "topic": "Late night code",
                        "num_speakers": 2,
                        "num_all": 9,
                        "social_club": {"name": "Builders"},
                        "users": [{"user_id": 1, "name": "Ada", "is_speaker": True}],
                    }
                }
            ],
        }

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
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

    async def fake_run_helper(
        action: str, phone_number: str, extra_arg: str = ""
    ) -> dict:
        path = (
            "/start_phone_number_auth"
            if action == "start"
            else "/complete_phone_number_auth"
        )
        payload = {"phone_number": phone_number}
        if action == "complete":
            payload["verification_code"] = extra_arg
        calls.append((path, payload))
        return responses[path]

    monkeypatch.setattr(routes, "_run_helper", fake_run_helper)
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


def test_start_auth_succeeds_even_if_rc_token_present(
    client: TestClient, monkeypatch
) -> None:
    calls = _mock_ch(
        monkeypatch,
        {
            "/start_phone_number_auth": {
                "success": True,
                "send_rc_token": True,
            }
        },
    )
    res = client.post(
        "/api/clubhouse/auth/start", json={"phone_number": "+15551234567"}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert calls == [("/start_phone_number_auth", {"phone_number": "+15551234567"})]


def test_start_auth_fails_if_api_reports_failure(
    client: TestClient, monkeypatch
) -> None:
    calls = _mock_ch(
        monkeypatch,
        {
            "/start_phone_number_auth": {
                "success": False,
                "error_message": "Too many attempts",
            }
        },
    )
    res = client.post(
        "/api/clubhouse/auth/start", json={"phone_number": "+15551234567"}
    )
    assert res.status_code == 400
    assert "Too many attempts" in res.json()["detail"]
    assert calls == [("/start_phone_number_auth", {"phone_number": "+15551234567"})]


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


def test_join_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/join_channel", "T", 4242, "D")
        assert payload == {"channel": "my-channel"}
        return {
            "success": True,
            "channel_id": 999,
            "channel": "my-channel",
            "token": "AGORA-RTC-TOKEN",
            "rtm_token": "AGORA-RTM-TOKEN",
            "pubnub_token": "PN-TOKEN",
        }

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/channels/my-channel/join")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["channel"] == "my-channel"
    assert body["token"] == "AGORA-RTC-TOKEN"
    assert body["rtm_token"] == "AGORA-RTM-TOKEN"
    assert body["pubnub_token"] == "PN-TOKEN"
    assert body["user_id"] == 4242


def test_leave_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/leave_channel", "T", 4242, "D")
        assert payload == {"channel": "my-channel"}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/channels/my-channel/leave")
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_active_ping(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/active_ping", "T", 4242, "D")
        assert payload == {"channel": "my-channel"}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/channels/my-channel/ping")
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_mute_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/update_is_muted", "T", 4242, "D")
        assert payload == {"channel": "my-channel", "is_muted": True}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/my-channel/mute", json={"is_muted": True}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_hand_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/audience_reply", "T", 4242, "D")
        assert payload == {
            "channel": "my-channel",
            "raise_hands": True,
            "unraise_hands": False,
        }
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/my-channel/hand", json={"raise_hands": True}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_accept_speaker(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == (
            "/accept_speaker_invite",
            "T",
            4242,
            "D",
        )
        assert payload == {"channel": "my-channel", "user_id": 12345}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/my-channel/accept_speaker", json={"user_id": 12345}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}
