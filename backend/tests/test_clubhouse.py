import json

import pytest
from fastapi import HTTPException
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

    async def fake_get(path, token, user_id, device_id=None, params=None):
        # get_following was removed upstream; get_cofollows replaces it and is a
        # GET with query params, not a POST body.
        assert path == "/get_cofollows"
        assert params["user_id"] == 4242
        return {"users": [{"user_id": 7, "username": "bob", "name": "Bob"}]}

    monkeypatch.setattr(routes, "_ch_authed_get", fake_get)
    body = client.get("/api/clubhouse/following").json()
    assert body["users"][0]["username"] == "bob"


def test_channels_surfaces_feed_failure(client, tmp_path, monkeypatch) -> None:
    """A dead feed must not render as an empty room list."""
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        raise HTTPException(status_code=502, detail="Clubhouse: unavailable")

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.get("/api/clubhouse/channels")
    assert res.status_code == 502


def test_channels_keeps_partial_pages(client, tmp_path, monkeypatch) -> None:
    """Page one succeeding and a later page failing keeps what we already have."""
    _connect(tmp_path)
    calls: list[int] = []

    async def fake_post(path, payload, token, user_id, device_id=None):
        calls.append(1)
        if len(calls) > 1:
            raise HTTPException(status_code=502, detail="Clubhouse: unavailable")
        return {
            "items": [{"channel": {"channel": "abc", "topic": "Still here"}}],
            "next_cursor": "c2",
        }

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    body = client.get("/api/clubhouse/channels").json()
    assert [c["topic"] for c in body["channels"]] == ["Still here"]


def test_notifications_use_activities_cursor(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_get(path, token, user_id, device_id=None, params=None):
        assert path == "/get_activities"
        assert params == {"next_cursor": "abc"}
        return {"activities": [{"notification_id": 1}], "next_cursor": "def"}

    monkeypatch.setattr(routes, "_ch_authed_get", fake_get)
    body = client.get("/api/clubhouse/notifications?next_cursor=abc").json()
    assert body["notifications"] == [{"notification_id": 1}]
    assert body["next_cursor"] == "def"


@pytest.mark.parametrize(
    ("enabled", "permission", "expected"),
    [
        (True, "everyone", 2),
        (True, "followed_by_speakers", 3),
        (True, "open_mic", 0),
        # Disabled is not a flag any more — it is the LOCKED value.
        (False, "everyone", 1),
    ],
)
def test_handraise_settings_map_to_wire_ints(
    client, tmp_path, monkeypatch, enabled, permission, expected
) -> None:
    """The old API's 1 meant "everyone"; here it means LOCKED, so the mapping
    must be explicit rather than a passed-through number."""
    _connect(tmp_path)
    sent: dict[str, object] = {}

    async def fake_post(path, payload, token, user_id, device_id=None):
        sent.update({"path": path, **payload})
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/my-room/handraise_settings",
        json={"is_enabled": enabled, "handraise_permission": permission},
    )
    assert res.status_code == 200
    assert sent["path"] == "/update_handraise_queue_setting"
    assert sent["handraise_queue_setting"] == expected


def test_handraise_queue_reads_queue(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_get(path, token, user_id, device_id=None, params=None):
        assert path == "/get_handraise_queue"
        assert params == {"channel": "my-room"}
        return {"handraises": [{"user_id": 9, "name": "Ada"}]}

    monkeypatch.setattr(routes, "_ch_authed_get", fake_get)
    body = client.get("/api/clubhouse/channels/my-room/handraise_queue").json()
    assert body["handraises"][0]["name"] == "Ada"


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
        # `/mute_speaker`, not `/update_is_muted`: the latter is gone upstream and
        # answers 404, so the mute button silently did nothing.
        assert (path, token, user_id, device_id) == ("/mute_speaker", "T", 4242, "D")
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
            "/become_speaker",
            "T",
            4242,
            "D",
        )
        # accept_speaker_invite was removed upstream; become_speaker takes the
        # channel alone — you accept the stage, not a particular moderator's
        # invitation, so no user id is carried at all.
        assert payload == {"channel": "my-channel"}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/my-channel/accept_speaker", json={"user_id": 12345}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_create_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/create_channel", "T", 4242, "D")
        assert payload == {
            "topic": "Testing Create Room",
            "is_private": False,
            "is_social_mode": False,
            "privacy_level": "public",
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        }
        return {
            "success": True,
            "channel_id": 111,
            "channel": "new-channel",
            "token": "AGORA-TOKEN",
        }

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels",
        json={
            "topic": "Testing Create Room",
            "is_private": False,
            "is_social_mode": False,
        },
    )
    assert res.status_code == 200
    assert res.json()["channel"] == "new-channel"
    assert res.json()["user_id"] == 4242


def test_follow_user(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/follow", "T", 4242, "D")
        assert payload == {"user_id": 99, "source": "feed"}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/users/99/follow")
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_unfollow_user(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/unfollow", "T", 4242, "D")
        assert payload == {"user_id": 99}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/users/99/unfollow")
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_invite_user(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == (
            "/invite_to_existing_channel",
            "T",
            4242,
            "D",
        )
        assert payload == {"channel": "my-chan", "user_id": 77}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/channels/my-chan/invite", json={"user_id": 77})
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_search_users(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/search_users", "T", 4242, "D")
        assert payload == {
            "query": "alice",
            "followers_only": False,
            "following_only": False,
            "cofollows_only": False,
        }
        return {"users": [{"user_id": 100, "name": "Alice"}]}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.get("/api/clubhouse/users/search?query=alice")
    assert res.status_code == 200
    assert res.json()["users"][0]["name"] == "Alice"


def test_uninvite_speaker(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == (
            "/uninvite_speaker",
            "T",
            4242,
            "D",
        )
        assert payload == {"channel": "test-room", "user_id": 999}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/test-room/uninvite_speaker", json={"user_id": 999}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_make_moderator(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/make_moderator", "T", 4242, "D")
        assert payload == {"channel": "test-room", "user_id": 999}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post(
        "/api/clubhouse/channels/test-room/make_moderator", json={"user_id": 999}
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_block_from_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == (
            "/block_from_channel",
            "T",
            4242,
            "D",
        )
        assert payload == {"channel": "test-room", "user_id": 999}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/channels/test-room/block", json={"user_id": 999})
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_end_channel(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert (path, token, user_id, device_id) == ("/end_channel", "T", 4242, "D")
        assert payload == {"channel": "test-room"}
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/channels/test-room/end")
    assert res.status_code == 200
    assert res.json() == {"success": True}


def test_update_bio(client, tmp_path, monkeypatch) -> None:
    _connect(tmp_path)

    async def fake_post(path, payload, token, user_id, device_id=None):
        assert path == "/update_bio"
        assert payload["bio"] == "New bio here"
        return {"success": True}

    monkeypatch.setattr(routes, "_ch_authed_post", fake_post)
    res = client.post("/api/clubhouse/me/bio", json={"bio": "New bio here"})
    assert res.status_code == 200
    assert res.json() == {"success": True}
