"""The Google connector: config resolution, the PKCE authorize URL, code exchange,
refresh, and status.

httpx is stubbed with MockTransport, so nothing here talks to Google.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from backend.modules.connectors import oauth, store
from backend.modules.connectors.providers import google
from backend.modules.connectors.store import Credential


@pytest.fixture(autouse=True)
def clean():
    oauth.reset_flows()
    store.clear("google")
    yield
    oauth.reset_flows()
    store.clear("google")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")


def _mock_httpx(monkeypatch, handler):
    """Route every httpx.AsyncClient through `handler`."""
    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


# --- configuration ----------------------------------------------------------


def test_begin_without_config_asks_for_credentials(monkeypatch):
    """An unconfigured node gets a *form*, not an error string: this connector can't
    work without the user's own Cloud project, and the form is where they say so."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    res = asyncio.run(google.build().begin({}))

    assert res["step"] == "form"
    assert [f["name"] for f in res["fields"]] == ["client_id", "client_secret"]
    # The guidance has to say what to actually go and make, and warn about the
    # 7-day refresh-token expiry that a Testing-status consent screen imposes.
    help_text = " ".join(f["help"] for f in res["fields"])
    assert "Desktop app" in help_text
    assert "In Production" in help_text


def test_client_secret_is_never_read_from_settings(monkeypatch):
    """`GET /api/settings` returns the whole bag to the browser, so a secret must
    never resolve from there — only env or the encrypted secrets store."""
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    from backend.modules.settings.routes import set_value

    set_value("connectors.google.clientSecret", "leaked-via-settings")
    assert google.client_secret() == ""


def test_client_secret_reads_from_the_secrets_store(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    from backend.modules.database.secrets_store import upsert_secret

    upsert_secret("google_client_secret", "from-store")
    assert google.client_secret() == "from-store"


def test_client_id_may_come_from_settings(monkeypatch):
    """A client id is public by design, so the BYO escape hatch is a plain setting."""
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    from backend.modules.settings.routes import set_value

    set_value("connectors.google.clientId", "from-setting")
    assert google.client_id() == "from-setting"


# --- the authorize URL ------------------------------------------------------


def test_authorize_url_carries_pkce_and_offline_consent(configured):
    from urllib.parse import parse_qs, urlparse

    step = asyncio.run(google.build().begin({}))
    assert step["step"] == "redirect"
    q = parse_qs(urlparse(step["authorize_url"]).query)

    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["code_challenge"][0]
    assert q["state"] and q["state"][0]
    assert q["response_type"] == ["code"]
    assert q["scope"] == [google.DRIVE_SCOPE]
    # Without offline+consent Google reliably returns no refresh token, and the
    # connection silently dies an hour later.
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    # Loopback, never the request Host — one registered URI for dev/prod/Tauri.
    assert q["redirect_uri"] == [oauth.redirect_uri("google")]
    assert "127.0.0.1" in q["redirect_uri"][0]


def test_authorize_url_never_carries_the_client_secret(configured):
    step = asyncio.run(google.build().begin({}))
    assert "csecret" not in step["authorize_url"]


# --- the code exchange ------------------------------------------------------


def _token_handler(token_body: dict[str, Any], *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(status, json=token_body)
        if "drive/v3/about" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "user": {"emailAddress": "rob@example.com", "permissionId": "p1"}
                },
            )
        return httpx.Response(404, json={})

    return handler


def test_exchange_stores_the_credential_and_labels_the_account(configured, monkeypatch):
    _mock_httpx(
        monkeypatch,
        _token_handler(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3599,
                "scope": google.DRIVE_SCOPE,
            }
        ),
    )
    connector = google.build()
    state = (
        asyncio.run(connector.begin({}))["authorize_url"]
        .split("state=")[1]
        .split("&")[0]
    )

    res = asyncio.run(oauth.finish_redirect("google", code="c", state=state))

    assert res["connected"] is True
    assert res["account"]["label"] == "rob@example.com"
    cred = store.load("google")
    assert cred.access_token == "at"
    assert cred.refresh_token == "rt"
    assert cred.expires_at and cred.expires_at > time.time()


def test_exchange_failure_is_reported_and_stores_nothing(configured, monkeypatch):
    _mock_httpx(
        monkeypatch,
        _token_handler(
            {"error": "invalid_grant", "error_description": "bad code"}, status=400
        ),
    )
    connector = google.build()
    state = (
        asyncio.run(connector.begin({}))["authorize_url"]
        .split("state=")[1]
        .split("&")[0]
    )

    res = asyncio.run(oauth.finish_redirect("google", code="c", state=state))

    assert "bad code" in res["error"]
    assert not store.is_connected("google")


def test_account_lookup_failure_still_connects(configured, monkeypatch):
    """A connector that works but can't name the account beats a failed sign-in."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt"}
            )
        return httpx.Response(500, json={})

    _mock_httpx(monkeypatch, handler)
    connector = google.build()
    state = (
        asyncio.run(connector.begin({}))["authorize_url"]
        .split("state=")[1]
        .split("&")[0]
    )

    res = asyncio.run(oauth.finish_redirect("google", code="c", state=state))
    assert res["connected"] is True
    assert store.load("google").access_token == "at"


# --- refresh ----------------------------------------------------------------


def test_token_refreshes_when_expired(configured, monkeypatch):
    _mock_httpx(
        monkeypatch, _token_handler({"access_token": "fresh", "expires_in": 3599})
    )
    store.save(
        "google",
        Credential(
            access_token="stale", refresh_token="rt", expires_at=time.time() - 1
        ),
    )

    assert asyncio.run(google.token()) == "fresh"
    # Google omits refresh_token on refresh; losing it would break the *next* refresh.
    assert store.load("google").refresh_token == "rt"


def test_refresh_failure_yields_no_token(configured, monkeypatch):
    _mock_httpx(
        monkeypatch,
        _token_handler(
            {"error": "invalid_grant", "error_description": "revoked"}, status=400
        ),
    )
    store.save(
        "google",
        Credential(
            access_token="stale", refresh_token="rt", expires_at=time.time() - 1
        ),
    )
    assert asyncio.run(google.token()) is None


def test_token_is_none_when_disconnected():
    assert asyncio.run(google.token()) is None


# --- status -----------------------------------------------------------------


def test_status_disconnected_by_default():
    assert google.build().status().connected is False


def test_status_reports_the_account_and_scopes():
    store.save(
        "google",
        Credential(
            access_token="at",
            refresh_token="rt",
            scopes=[google.DRIVE_SCOPE],
            account={"id": "p1", "label": "rob@example.com"},
        ),
    )
    status = google.build().status()
    assert status.connected is True
    assert status.account.label == "rob@example.com"
    assert status.scopes == [google.DRIVE_SCOPE]
    assert status.error is None


def test_status_flags_a_credential_with_no_refresh_token():
    """It works for an hour then dies with no way to renew — that's a broken
    connection, and saying so beats failing mid-task later."""
    store.save("google", Credential(access_token="at", refresh_token=None))
    status = google.build().status()
    assert status.connected is True
    assert "refresh token" in status.error


def test_disconnect_clears_the_credential_and_any_flow(configured):
    store.save("google", Credential(access_token="at"))
    asyncio.run(google.build().begin({}))
    asyncio.run(google.build().disconnect())
    assert not store.is_connected("google")
    assert asyncio.run(oauth.poll_flow("google"))["error"] == "no sign-in in progress"


# --- registration -----------------------------------------------------------


def test_connector_id_matches_its_tool_namespace():
    """The orchestrator groups tools by name prefix, so this is what ties the Drive
    tools to the connector's blurb and guide."""
    from backend.modules.agent.orchestrator import _group_of
    from backend.modules.connectors import register_connectors
    from backend.sdk.registry import registry

    register_connectors()
    tools = [n for n in registry.agent_tools if n.startswith("google.")]
    assert tools
    assert all(_group_of(n) == "google" for n in tools)


def test_guide_is_loadable():
    from backend.modules.connectors import register_connectors
    from backend.sdk.registry import registry

    register_connectors()
    guide = registry.connectors["google"].resolve_guide()
    assert guide and "driveSearch" in guide
