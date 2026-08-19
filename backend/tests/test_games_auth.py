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
    # `handle` rides along on every sign-in payload so the node learns the username
    # without a second round-trip (see auth._session). It is **None** for a new
    # account: OAuth no longer picks a username on the person's behalf, so the
    # client sees no handle, `enlisted` is false, and the chooser renders.
    assert out["account"] == {
        "id": "github:99",
        "display_name": "octocat",
        "handle": None,
        "suggested_handle": "octocat",
    }
    claims = auth.verify_jwt(out["token"])
    assert claims["sub"] == "github:99"
    # The account is now persisted for the leaderboard.
    assert store.get_account("github:99")["display_name"] == "octocat"
    # Nothing was claimed on their behalf.
    assert store.get_account("github:99")["handle"] is None


def test_finish_google_creates_account_and_signs_jwt() -> None:
    out = auth._finish_google(
        {"id": "108", "email": "mildred.bakes@gmail.com", "name": "Mildred"}
    )
    assert out["account"] == {
        "id": "google:108",
        "display_name": "Mildred",
        "handle": None,
        # The suggestion comes from the email's local part, not the display name.
        "suggested_handle": "mildred-bakes",
    }
    claims = auth.verify_jwt(out["token"])
    assert claims["sub"] == "google:108"
    assert store.get_account("google:108")["display_name"] == "Mildred"
    assert store.get_account("google:108")["handle"] is None


def test_finish_google_falls_back_to_email_local_part() -> None:
    out = auth._finish_google({"id": "109", "email": "bosun.salt@gmail.com"})
    assert out["account"]["display_name"] == "bosun.salt"
    # Two different Gmail accounts are two distinct players.
    assert out["account"]["id"] != auth._finish_google({"id": "108"})["account"]["id"]


# ---- local (email + password) accounts --------------------------------------


def test_password_hash_roundtrip_and_is_salted() -> None:
    encoded = auth.hash_password("correct horse battery")
    assert encoded.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", encoded)
    assert not auth.verify_password("wrong horse battery", encoded)
    # Per-password salt: the same password never produces the same stored string,
    # so a stolen table can't be attacked with one precomputed pass.
    assert auth.hash_password("correct horse battery") != encoded


def test_verify_password_rejects_garbage_instead_of_raising() -> None:
    # A corrupt or foreign-scheme row must fail the login, not the request.
    for bad in ["", "not-a-hash", "bcrypt$a$b", "scrypt$x$8$1$zz$zz", "scrypt$1$2$3"]:
        assert auth.verify_password("anything", bad) is False


def test_signup_local_creates_account_with_chosen_username() -> None:
    out = auth.signup_local("Ada@Example.com", "hunter2hunter2", "ada")
    account_id = out["account"]["id"]
    # The id is a uuid, never the email — people change addresses, ids are forever.
    assert account_id.startswith("local:")
    assert "ada" not in account_id
    assert out["account"]["handle"] == "ada"
    assert auth.verify_jwt(out["token"])["sub"] == account_id
    # The email is stored lowercased, so the address is one account not two.
    assert store.get_local_credentials("ADA@EXAMPLE.COM")["account_id"] == account_id
    # The password is never stored in a readable form.
    assert (
        "hunter2hunter2"
        not in store.get_local_credentials("ada@example.com")["password_hash"]
    )


def test_signup_local_requires_a_chosen_username() -> None:
    """The username is the person's choice, not a transform of their address.

    This used to derive one silently, which is what made the chooser unreachable
    everywhere downstream: the account arrived already holding a name nobody had
    agreed to, and every screen that asks "have you chosen one yet?" said yes.
    """
    for bad in ["", "no"]:
        try:
            auth.signup_local("bosun@example.com", "longenoughpw", bad)
            raise AssertionError(f"{bad!r} should have been rejected")
        except ValueError as exc:
            assert "3-20 characters" in str(exc)
    out = auth.signup_local("bosun@example.com", "longenoughpw", "bosun")
    assert out["account"]["handle"] == "bosun"


def test_signup_local_rejects_a_taken_username() -> None:
    auth.signup_local("first@example.com", "longenoughpw", "duplicate")
    try:
        auth.signup_local("second@example.com", "longenoughpw", "duplicate")
        raise AssertionError("a taken username should have been rejected")
    except ValueError as exc:
        assert "taken" in str(exc)


def test_signup_local_rejects_bad_input_and_duplicates() -> None:
    auth.signup_local("taken@example.com", "longenoughpw", "taken-user")
    for email, password, expected in [
        ("TAKEN@example.com", "longenoughpw", "already exists"),
        ("notanemail", "longenoughpw", "email address"),
        ("fresh@example.com", "short", "at least 8"),
    ]:
        try:
            auth.signup_local(email, password, "somebody")
            raise AssertionError(f"{email} should have been rejected")
        except ValueError as exc:
            assert expected in str(exc)


def test_login_local_roundtrip() -> None:
    auth.signup_local("pilot@example.com", "longenoughpw", "pilot")
    out = auth.login_local("PILOT@example.com", "longenoughpw")
    assert out["account"]["handle"] == "pilot"
    assert auth.resolve_token(out["token"])["account_id"] == out["account"]["id"]


def test_login_local_cannot_be_used_to_enumerate_accounts() -> None:
    """A wrong password and an unknown address are indistinguishable — same message,
    and both spend the scrypt time (see auth._dummy_hash), so neither the response
    nor its latency confirms whether an address has an account here."""
    auth.signup_local("known@example.com", "longenoughpw", "known-user")
    messages = set()
    for email, password in [
        ("known@example.com", "wrongpassword"),
        ("unknown@example.com", "wrongpassword"),
    ]:
        try:
            auth.login_local(email, password)
            raise AssertionError("should have been rejected")
        except ValueError as exc:
            messages.add(str(exc))
    assert len(messages) == 1


def test_oauth_account_has_no_password_and_cannot_be_logged_into_locally() -> None:
    auth._finish_github({"id": 4242, "login": "gh-only"})
    assert store.get_local_credentials("gh-only") is None
    try:
        auth.login_local("gh-only", "anything")
        raise AssertionError("an OAuth account has no local password")
    except ValueError as exc:
        assert "wrong email or password" in str(exc)


def test_set_account_handle_renames_and_enforces_uniqueness() -> None:
    a = auth.signup_local("one@example.com", "longenoughpw", "alpha")["account"]["id"]
    b = auth.signup_local("two@example.com", "longenoughpw", "bravo")["account"]["id"]

    # Unlike ensure_handle, a deliberate rename applies even though one is set.
    assert auth.set_account_handle(a, "alpha-prime") == "ok"
    assert auth.account_payload(a)["handle"] == "alpha-prime"

    assert auth.set_account_handle(b, "alpha-prime") == "taken"
    assert auth.set_account_handle(b, "no") == "invalid"
    assert auth.set_account_handle(b, "Has Spaces") == "invalid"
    assert auth.account_payload(b)["handle"] == "bravo"


def test_local_signup_rejects_a_taken_username_without_stranding_the_account() -> None:
    auth.signup_local("first@example.com", "longenoughpw", "wanted")
    try:
        auth.signup_local("second@example.com", "longenoughpw", "wanted")
        raise AssertionError("the username was already taken")
    except ValueError as exc:
        assert "username is taken" in str(exc)


def test_local_signup_and_login_over_http(monkeypatch) -> None:
    """The routes, end to end — and specifically that `/auth/local/*` is reachable
    at all: it is declared above `/auth/{provider}/web/start`, whose path parameter
    would otherwise swallow it (FastAPI matches in declaration order)."""
    from starlette.testclient import TestClient

    from backend.games_server import app as app_mod

    client = TestClient(app_mod.app)

    created = client.post(
        "/auth/local/signup",
        json={
            "email": "http@example.com",
            "password": "longenoughpw",
            "username": "httpuser",
        },
    ).json()
    assert created["account"]["handle"] == "httpuser"
    assert created["token"]

    again = client.post(
        "/auth/local/login",
        json={"email": "http@example.com", "password": "longenoughpw"},
    ).json()
    assert again["account"]["id"] == created["account"]["id"]

    wrong = client.post(
        "/auth/local/login",
        json={"email": "http@example.com", "password": "nope"},
    ).json()
    assert "token" not in wrong and wrong["error"]

    # /me and the username rename, both bearer-authenticated.
    headers = {"Authorization": f"Bearer {created['token']}"}
    assert client.get("/me", headers=headers).json()["account"]["handle"] == "httpuser"
    renamed = client.post(
        "/account/handle", json={"handle": "renamed"}, headers=headers
    ).json()
    assert renamed["ok"] and renamed["account"]["handle"] == "renamed"
    assert client.get("/me", headers=headers).json()["account"]["handle"] == "renamed"

    # Both are refused without a token.
    assert client.get("/me").json()["error"]
    assert client.post("/account/handle", json={"handle": "x"}).json()["error"]


def test_a_claimed_username_survives_a_provider_rename() -> None:
    # A GitHub user renaming their login doesn't rewrite a username they chose.
    auth._finish_github({"id": 500, "login": "first_name"})
    auth.set_account_handle("github:500", "first-name")
    out = auth._finish_github({"id": 500, "login": "renamed"})
    assert store.get_account("github:500")["handle"] == "first-name"
    # And the returning session reports it, so they are not asked a second time.
    assert out["account"]["handle"] == "first-name"


def test_colliding_suggestions_are_offered_not_claimed() -> None:
    """Two Google accounts sharing an email local part are both *suggested* `sam`,
    and neither is claimed.

    The deliberate difference from `ensure_handle`, which resolved the collision by
    silently handing the second person `sam2`. A suggestion may collide; the claim
    is what has to be unique, and a refused claim comes with a reason they can act
    on rather than a number appended behind their back.
    """
    a = auth._finish_google({"id": "600", "email": "sam@gmail.com"})
    b = auth._finish_google({"id": "601", "email": "sam@example.com"})
    assert a["account"]["suggested_handle"] == b["account"]["suggested_handle"] == "sam"
    assert store.get_account("google:600")["handle"] is None
    assert store.get_account("google:601")["handle"] is None
    assert auth.set_account_handle("google:600", "sam") == "ok"
    assert auth.set_account_handle("google:601", "sam") == "taken"


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
        # Email+password needs no credentials, so it is unconditionally on — a
        # server with no OAuth configured at all can still sign people up.
        "local": {"password": True},
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


# ---------------------------------------------------------------------------
# The node's proxy: which flows, and *which server*
# ---------------------------------------------------------------------------


def test_the_node_reports_which_server_the_flows_describe(monkeypatch) -> None:
    """A greyed-out sign-in button is unexplainable without this.

    The node resolves its game server from `GAMES_SERVER_URL` *ahead* of the
    `games.serverUrl` setting, so under `pnpm dev` it talks to the bundled local
    server — which has no OAuth credentials and reports every provider
    unavailable. The browser cannot work that out on its own: the setting it can
    read says something else. So the resolved URL is reported alongside the flows.
    """
    import asyncio

    from backend.modules.games import server_auth

    async def fake_get(self, url, **kwargs):  # noqa: ANN001, ARG001
        class _Res:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict:
                return {
                    "github": {"device": False, "web": False},
                    "local": {"password": True},
                }

        return _Res()

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(
        server_auth, "resolve_server_url", lambda: "ws://localhost:9090"
    )

    out = asyncio.run(server_auth.auth_providers())
    assert out["server"] == "ws://localhost:9090"
    assert out["flows"]["github"] == {"device": False, "web": False}


def test_an_unreachable_game_server_still_names_itself(monkeypatch) -> None:
    """`flows: {}` means "unknown", which keeps the buttons enabled — but the URL
    is still reported, because "cannot reach X" is the single most useful thing to
    be able to say when sign-in does nothing."""
    import asyncio

    import httpx

    from backend.modules.games import server_auth

    async def boom(self, url, **kwargs):  # noqa: ANN001, ARG001
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    monkeypatch.setattr(
        server_auth, "resolve_server_url", lambda: "wss://example.invalid"
    )

    out = asyncio.run(server_auth.auth_providers())
    assert out == {"server": "wss://example.invalid", "flows": {}}
