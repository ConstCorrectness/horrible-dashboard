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


def test_web_signin_flow_end_to_end(monkeypatch) -> None:
    """The redirect (authorization-code) flow: start -> browser /login 302 -> provider
    /callback exchange -> the node pulls the JWT with its private retrieval code."""
    from starlette.testclient import TestClient

    from backend.games_server import app as app_mod
    from backend.games_server import auth as auth_mod

    monkeypatch.setattr(auth_mod, "web_config_error", lambda provider: None)
    monkeypatch.setattr(
        auth_mod,
        "web_authorize_url",
        lambda provider, state, redirect_uri: (
            f"https://prov.example/auth?state={state}"
        ),
    )

    async def fake_exchange(provider, code, redirect_uri):
        assert code == "abc123"
        return {
            "token": "TOK",
            "account": {"id": "github:1", "display_name": "octocat"},
        }

    monkeypatch.setattr(auth_mod, "web_exchange", fake_exchange)
    client = TestClient(app_mod.app)

    start = client.post("/auth/github/web/start").json()
    assert start["login_url"] and start["retrieval_code"]
    lid = start["login_url"].split("lid=")[1]
    code = start["retrieval_code"]

    # Poll before authorization: pending. The public lid can't fetch the token.
    assert client.post(
        "/auth/github/web/poll", json={"retrieval_code": code}
    ).json() == {"pending": True}

    # The browser is redirected on to the provider, carrying our state.
    login = client.get(f"/auth/github/login?lid={lid}", follow_redirects=False)
    assert login.status_code == 302
    assert f"state={lid}" in login.headers["location"]

    # The provider redirects back to the callback; we exchange + stash the result.
    cb = client.get(f"/auth/github/callback?code=abc123&state={lid}")
    assert cb.status_code == 200
    assert "Signed in as octocat" in cb.text

    # The node's private poll now yields the token — exactly once.
    poll = client.post("/auth/github/web/poll", json={"retrieval_code": code}).json()
    assert poll["token"] == "TOK"
    assert poll["account"]["display_name"] == "octocat"
    assert (
        "error"
        in client.post("/auth/github/web/poll", json={"retrieval_code": code}).json()
    )


def test_web_signin_start_reports_config_error(monkeypatch) -> None:
    """An unconfigured provider fails fast at start, before any provider round-trip."""
    from starlette.testclient import TestClient

    from backend.games_server import app as app_mod
    from backend.games_server import auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "web_config_error",
        lambda provider: "GAMES_GITHUB_CLIENT_SECRET is not configured",
    )
    client = TestClient(app_mod.app)
    start = client.post("/auth/github/web/start").json()
    assert "GAMES_GITHUB_CLIENT_SECRET" in start["error"]


def test_client_secrets_are_never_read_from_settings(monkeypatch) -> None:
    """`GET /api/settings` returns the whole bag to the browser, and a bundled game
    server shares `$HORRIBLE_DATA_DIR/settings.json` with its node — so a secret parked
    there would be readable by any page the node serves. Env only, no fallback."""
    from backend.modules.settings.routes import set_value

    for env in (
        "GAMES_GITHUB_CLIENT_SECRET",
        "GAMES_GOOGLE_CLIENT_SECRET",
        "GAMES_GOOGLE_WEB_CLIENT_SECRET",
    ):
        monkeypatch.delenv(env, raising=False)
    set_value("games.github.clientSecret", "leaked-via-settings")
    set_value("games.google.clientSecret", "leaked-via-settings")
    set_value("games.google.webClientSecret", "leaked-via-settings")

    assert auth._github_client_secret() == ""
    assert auth._google_client_secret() == ""
    assert auth._google_web_client_secret() == ""

    # Env is the one source that works.
    monkeypatch.setenv("GAMES_GITHUB_CLIENT_SECRET", "from-env")
    monkeypatch.setenv("GAMES_GOOGLE_CLIENT_SECRET", "from-env")
    assert auth._github_client_secret() == "from-env"
    assert auth._google_client_secret() == "from-env"


def test_client_ids_may_come_from_settings(monkeypatch) -> None:
    """The other half of the rule: an id is public by design, so it stays settable."""
    from backend.modules.settings.routes import set_value

    for env in (
        "GAMES_GITHUB_CLIENT_ID",
        "GAMES_GOOGLE_CLIENT_ID",
        "GAMES_GOOGLE_WEB_CLIENT_ID",
    ):
        monkeypatch.delenv(env, raising=False)
    set_value("games.github.clientId", "gh-id")
    set_value("games.google.clientId", "tv.apps.google")
    set_value("games.google.webClientId", "web.apps.google")

    assert auth._github_client_id() == "gh-id"
    assert auth._google_client_id() == "tv.apps.google"
    assert auth._google_web_client_id() == "web.apps.google"


def test_web_config_error_names_the_env_var_for_a_missing_secret(monkeypatch) -> None:
    """The message has to point at the env var, not a setting the server won't read."""
    monkeypatch.setattr(auth, "_github_client_id", lambda: "iv1.deadbeef")
    monkeypatch.setattr(auth, "_github_client_secret", lambda: "")
    error = auth.web_config_error("github")
    assert error and "GAMES_GITHUB_CLIENT_SECRET" in error


def test_web_poll_rejects_unknown_retrieval_code() -> None:
    from starlette.testclient import TestClient

    from backend.games_server import app as app_mod

    client = TestClient(app_mod.app)
    poll = client.post("/auth/github/web/poll", json={"retrieval_code": "nope"}).json()
    assert "error" in poll


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


def test_auth_providers_reports_flow_availability(monkeypatch) -> None:
    """GET /auth/providers reflects the configured credentials: GitHub's device flow
    needs only the client id; its web flow (and both Google flows) need the secret too.
    The UI uses this to disable a provider button up front instead of opening a popup
    that immediately fails."""
    from starlette.testclient import TestClient

    from backend.games_server import app as app_mod
    from backend.games_server import auth as auth_mod

    client = TestClient(app_mod.app)

    # Only a GitHub client id: device flow available, web flow not; Google nothing.
    monkeypatch.setattr(auth_mod, "_github_client_id", lambda: "iv1.deadbeef")
    monkeypatch.setattr(auth_mod, "_github_client_secret", lambda: "")
    monkeypatch.setattr(auth_mod, "_google_client_id", lambda: "")
    monkeypatch.setattr(auth_mod, "_google_client_secret", lambda: "")
    assert client.get("/auth/providers").json() == {
        "github": {"device": True, "web": False},
        "google": {"device": False, "web": False},
    }

    # Full GitHub credentials: both flows.
    monkeypatch.setattr(auth_mod, "_github_client_secret", lambda: "s3cret")
    assert client.get("/auth/providers").json()["github"] == {
        "device": True,
        "web": True,
    }

    # Google's device credentials do NOT enable its web flow: a limited-input client
    # has no redirect URI, so claiming `web` here is what sent users to a
    # redirect_uri_mismatch page. Only the separate web client turns it on.
    monkeypatch.setattr(auth_mod, "_google_client_id", lambda: "tv.apps.google")
    monkeypatch.setattr(auth_mod, "_google_client_secret", lambda: "tv-secret")
    monkeypatch.setattr(auth_mod, "_google_web_client_id", lambda: "")
    monkeypatch.setattr(auth_mod, "_google_web_client_secret", lambda: "")
    assert client.get("/auth/providers").json()["google"] == {
        "device": True,
        "web": False,
    }

    monkeypatch.setattr(auth_mod, "_google_web_client_id", lambda: "web.apps.google")
    monkeypatch.setattr(auth_mod, "_google_web_client_secret", lambda: "web-secret")
    assert client.get("/auth/providers").json()["google"] == {
        "device": True,
        "web": True,
    }


def test_google_web_flow_never_uses_the_device_client(monkeypatch) -> None:
    """The regression that produced `Error 400: redirect_uri_mismatch`: the redirect
    flow reused Google's "TVs and Limited Input devices" client, which cannot have a
    redirect URI registered. Without a web client the flow must report itself
    unconfigured (so the caller falls back to the device flow), and with one it must
    authorize against *that* client id."""
    from urllib.parse import parse_qs, urlparse

    from backend.games_server import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_google_client_id", lambda: "tv.apps.google")
    monkeypatch.setattr(auth_mod, "_google_client_secret", lambda: "tv-secret")
    monkeypatch.setattr(auth_mod, "_google_web_client_id", lambda: "")
    monkeypatch.setattr(auth_mod, "_google_web_client_secret", lambda: "")

    error = auth_mod.web_config_error("google")
    assert error and "GAMES_GOOGLE_WEB_CLIENT_ID" in error

    monkeypatch.setattr(auth_mod, "_google_web_client_id", lambda: "web.apps.google")
    monkeypatch.setattr(auth_mod, "_google_web_client_secret", lambda: "web-secret")
    assert auth_mod.web_config_error("google") is None

    url = auth_mod.web_authorize_url(
        "google", "state123", "https://games.example/auth/google/callback"
    )
    params = parse_qs(urlparse(url).query)
    assert params["client_id"] == ["web.apps.google"]
    assert params["redirect_uri"] == ["https://games.example/auth/google/callback"]
