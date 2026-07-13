"""Game-server identity: JWT sessions, dev-token fallback, GitHub/Google accounts."""

from __future__ import annotations

import time

import jwt as pyjwt

from backend.games_server import auth, store


def test_jwt_roundtrip() -> None:
    token = auth.issue_jwt("github:1", "octocat")
    claims = auth.verify_jwt(token)
    assert claims is not None
    assert claims["sub"] == "github:1"
    assert claims["name"] == "octocat"


def test_expired_jwt_is_rejected() -> None:
    secret = auth._jwt_secret()
    expired = pyjwt.encode(
        {"sub": "x", "exp": int(time.time()) - 10}, secret, algorithm=auth.JWT_ALG
    )
    assert auth.verify_jwt(expired) is None


def test_tampered_jwt_is_rejected() -> None:
    token = auth.issue_jwt("a", "a")
    assert auth.verify_jwt(token + "x") is None


def test_resolve_token_prefers_jwt_then_dev() -> None:
    token = auth.issue_jwt("github:1", "octocat")
    resolved = auth.resolve_token(token)
    assert resolved == {"account_id": "github:1", "display_name": "octocat"}
    # A non-JWT string is a dev token: it *is* the account id.
    assert auth.resolve_token("bob") == {"account_id": "bob", "display_name": "bob"}
    assert auth.resolve_token("") is None


def test_dev_auth_can_be_disabled_but_jwt_still_works(monkeypatch) -> None:
    monkeypatch.setenv("GAMES_ALLOW_DEV_AUTH", "0")
    assert auth.resolve_token("bob") is None
    token = auth.issue_jwt("github:1", "octocat")
    assert auth.resolve_token(token)["account_id"] == "github:1"


def test_finish_github_creates_account_and_signs_jwt() -> None:
    out = auth._finish_github({"id": 99, "login": "octocat"})
    assert out["account"] == {"id": "github:99", "display_name": "octocat"}
    claims = auth.verify_jwt(out["token"])
    assert claims["sub"] == "github:99"
    # The account is now persisted for the leaderboard.
    assert store.get_account("github:99")["display_name"] == "octocat"
    # The handle is auto-derived and locked from the GitHub login.
    assert store.get_account("github:99")["handle"] == "octocat"


def test_finish_google_creates_account_and_signs_jwt() -> None:
    out = auth._finish_google(
        {"id": "108", "email": "mildred.bakes@gmail.com", "name": "Mildred"}
    )
    assert out["account"] == {"id": "google:108", "display_name": "Mildred"}
    claims = auth.verify_jwt(out["token"])
    assert claims["sub"] == "google:108"
    assert store.get_account("google:108")["display_name"] == "Mildred"
    # Handle comes from the email's local part, not the display name.
    assert store.get_account("google:108")["handle"] == "mildred-bakes"


def test_finish_google_falls_back_to_email_local_part() -> None:
    out = auth._finish_google({"id": "109", "email": "bosun.salt@gmail.com"})
    assert out["account"]["display_name"] == "bosun.salt"
    # Two different Gmail accounts are two distinct players.
    assert out["account"]["id"] != auth._finish_google({"id": "108"})["account"]["id"]


def test_ensure_handle_is_locked_after_first_sign_in() -> None:
    # A GitHub user renaming their login doesn't rewrite an already-locked handle.
    auth._finish_github({"id": 500, "login": "first_name"})
    auth._finish_github({"id": 500, "login": "renamed"})
    assert store.get_account("github:500")["handle"] == "first_name"


def test_ensure_handle_resolves_collisions() -> None:
    # Two Google accounts with the same email local part get distinct handles.
    auth._finish_google({"id": "600", "email": "sam@gmail.com"})
    auth._finish_google({"id": "601", "email": "sam@example.com"})
    handles = {
        store.get_account("google:600")["handle"],
        store.get_account("google:601")["handle"],
    }
    assert handles == {"sam", "sam2"}


class MockWS:
    def __init__(self, messages_to_send=None):
        self.messages_to_send = messages_to_send or []
        self.sent_messages = []
        self.closed = False

    async def send(self, msg):
        self.sent_messages.append(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages_to_send:
            raise StopAsyncIteration
        return self.messages_to_send.pop(0)

    async def close(self):
        self.closed = True


def test_player_conn_handles_auth_error(monkeypatch) -> None:
    import asyncio
    import json
    import pytest
    from backend.modules.games.client import _PlayerConn

    async def go() -> None:
        mock_ws = MockWS(
            [json.dumps({"type": "error", "code": "auth", "message": "invalid token"})]
        )

        async def mock_ws_connect(url):
            return mock_ws

        monkeypatch.setattr("backend.modules.games.client.ws_connect", mock_ws_connect)

        conn = _PlayerConn("ws://dummy", "invalid-token", None, lambda _msg: None)

        with pytest.raises(ValueError) as excinfo:
            await conn.start()

        assert "Game server authentication failed" in str(excinfo.value)
        assert "invalid token" in str(excinfo.value)
        assert mock_ws.closed

    asyncio.run(go())


def test_player_conn_handles_conn_close(monkeypatch) -> None:
    import asyncio
    import pytest
    from backend.modules.games.client import _PlayerConn

    async def go() -> None:
        mock_ws = MockWS([])  # closes immediately

        async def mock_ws_connect(url):
            return mock_ws

        monkeypatch.setattr("backend.modules.games.client.ws_connect", mock_ws_connect)

        conn = _PlayerConn("ws://dummy", "some-token", None, lambda _msg: None)

        with pytest.raises(ValueError) as excinfo:
            await conn.start()

        assert "Connection closed before authentication completed" in str(excinfo.value)
        assert mock_ws.closed

    asyncio.run(go())
