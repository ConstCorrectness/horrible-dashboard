"""The Hugging Face connector: config, the device flow, refresh, status, and the
read-only shape of its tools.

httpx is stubbed with MockTransport, so nothing here talks to Hugging Face.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from backend.modules.connectors import oauth, store
from backend.modules.connectors.providers import huggingface, huggingface_tools
from backend.modules.connectors.store import Credential


@pytest.fixture(autouse=True)
def clean():
    oauth.reset_flows()
    store.clear("huggingface")
    yield
    oauth.reset_flows()
    store.clear("huggingface")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_CLIENT_ID", "hf-client-id")


def _mock_httpx(monkeypatch, handler):
    """Route every httpx.AsyncClient through `handler`."""
    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


def _connected(**overrides: Any) -> Credential:
    cred = Credential(
        access_token="hf_oauth_live",
        refresh_token="hf_refresh",
        expires_at=time.time() + 3600,
        scopes=["profile", "read-repos", "inference-api"],
        account={"id": "u1", "label": "octonaut"},
    )
    for key, value in overrides.items():
        setattr(cred, key, value)
    return cred


# --- configuration ----------------------------------------------------------


def test_begin_without_config_asks_for_a_client_id_only(monkeypatch):
    """A public OAuth app has no secret, so the form must not ask for one — asking
    would send the user looking for a field Hugging Face never showed them."""
    monkeypatch.delenv("HUGGINGFACE_CLIENT_ID", raising=False)
    res = asyncio.run(huggingface.build().begin({}))

    assert res["step"] == "form"
    assert [f["name"] for f in res["fields"]] == ["client_id"]
    assert "without a client secret" in res["fields"][0]["help"]


def test_setup_help_gives_a_navigation_path_not_a_bare_url():
    """Every HF settings page is auth-gated — logged out it serves a login form, so a
    deep link reads as a dead end (it did, in review). The help must name the clicks."""
    help_text = huggingface._configure_step()["fields"][0]["help"]
    assert "Connected Apps" in help_text
    assert "settings/applications/new" not in help_text


def test_scopes_are_read_only():
    """The connector must never request write/manage — a confused agent could
    otherwise delete a model. This is the guard on that."""
    ids = {s.id for s in huggingface.SCOPES}
    assert ids == {"profile", "read-repos", "inference-api"}
    assert not any("write" in s or "manage" in s for s in ids)


# --- the device flow --------------------------------------------------------


def test_begin_returns_a_device_step(monkeypatch, configured):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/device"
        return httpx.Response(
            200,
            json={
                "device_code": "dev-123",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://huggingface.co/oauth/device",
                "interval": 5,
                "expires_in": 600,
            },
        )

    _mock_httpx(monkeypatch, handler)
    res = asyncio.run(huggingface.build().begin({}))

    assert res["step"] == "device"
    assert res["user_code"] == "ABCD-EFGH"
    assert res["verification_uri"] == "https://huggingface.co/oauth/device"


def test_device_error_is_reported_not_raised(monkeypatch, configured):
    """An app created without one of our scopes fails here — the message has to reach
    the user, because the fix is in their app settings."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_scope",
                "error_description": "scope inference-api is not enabled",
            },
        )

    _mock_httpx(monkeypatch, handler)
    res = asyncio.run(huggingface.build().begin({}))

    assert "inference-api is not enabled" in res["error"]


def test_poll_pending_then_connected(monkeypatch, configured):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/device":
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://huggingface.co/oauth/device",
                    "interval": 1,
                    "expires_in": 600,
                },
            )
        if request.url.path == "/oauth/token":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(
                200,
                json={
                    "access_token": "hf_oauth_abc",
                    "refresh_token": "hf_refresh",
                    "expires_in": 28800,
                    "scope": "profile read-repos inference-api",
                },
            )
        assert request.url.path == "/api/whoami-v2"
        return httpx.Response(
            200, json={"id": "u1", "name": "octonaut", "avatarUrl": "https://a/v.png"}
        )

    _mock_httpx(monkeypatch, handler)
    connector = huggingface.build()

    async def run():
        await connector.begin({})
        first = await connector.poll()
        second = await connector.poll()
        return first, second

    first, second = asyncio.run(run())
    assert first == {"pending": True}
    assert second["connected"] is True
    assert second["account"]["label"] == "octonaut"

    cred = store.load("huggingface")
    assert cred.access_token == "hf_oauth_abc"
    assert cred.refresh_token == "hf_refresh"
    # Load-bearing: unlike GitHub's, these tokens lapse, so an expiry must be recorded
    # or `ensure_fresh` would never refresh.
    assert cred.expires_at and cred.expires_at > time.time()


# --- refresh ----------------------------------------------------------------


def test_expired_token_is_refreshed(monkeypatch, configured):
    store.save("huggingface", _connected(expires_at=time.time() - 10))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        body = request.content.decode()
        assert "grant_type=refresh_token" in body
        # A public app authenticates with the client id alone — no secret to present.
        assert "client_secret" not in body
        return httpx.Response(
            200, json={"access_token": "hf_oauth_fresh", "expires_in": 28800}
        )

    _mock_httpx(monkeypatch, handler)
    assert asyncio.run(huggingface.token()) == "hf_oauth_fresh"
    # The refresh response omits the refresh token; it must survive anyway or the
    # *next* refresh has nothing to present.
    assert store.load("huggingface").refresh_token == "hf_refresh"


def test_failed_refresh_yields_no_token(monkeypatch, configured):
    store.save("huggingface", _connected(expires_at=time.time() - 10))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    _mock_httpx(monkeypatch, handler)
    assert asyncio.run(huggingface.token()) is None


# --- status -----------------------------------------------------------------


def test_status_flags_an_expiring_credential_with_no_refresh_token():
    store.save("huggingface", _connected(refresh_token=None))
    status = huggingface.build().status()
    assert status.connected is True
    assert "reconnect" in (status.error or "")


def test_status_is_clean_when_refreshable():
    store.save("huggingface", _connected())
    status = huggingface.build().status()
    assert status.connected is True
    assert status.error is None
    assert status.account.label == "octonaut"


# --- tools ------------------------------------------------------------------


def test_tools_report_a_missing_connection_as_a_value(monkeypatch):
    """Every tool has to degrade to an instruction the user can act on, not a raise."""
    store.clear("huggingface")
    for tool in huggingface_tools._TOOLS:
        result = asyncio.run(
            tool.handler({"query": "q", "repo": "a/b", "path": "R.md"})
        )
        assert "isn't connected" in result["error"], tool.name


def test_dataset_reads_use_the_datasets_path(monkeypatch, configured):
    """Models and datasets are separate namespaces; a dataset read that forgets the
    `datasets/` prefix silently 404s or, worse, hits a same-named model."""
    store.save("huggingface", _connected())
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, text="# card")

    _mock_httpx(monkeypatch, handler)
    res = asyncio.run(
        huggingface_tools._read_file(
            {"repo": "squad/v2", "path": "README.md", "type": "dataset"}
        )
    )
    assert seen["path"] == "/datasets/squad/v2/resolve/main/README.md"
    assert res["content"] == "# card"


def test_binary_file_is_refused_rather_than_returned_as_noise(monkeypatch, configured):
    store.save("huggingface", _connected())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00\x01\x02weights" * 100,
            headers={"content-type": "application/octet-stream"},
        )

    _mock_httpx(monkeypatch, handler)
    res = asyncio.run(
        huggingface_tools._read_file({"repo": "a/b", "path": "model.safetensors"})
    )
    assert "binary" in res["error"]


def test_gated_repo_error_names_the_licence(monkeypatch, configured):
    store.save("huggingface", _connected())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Access to model is restricted"})

    _mock_httpx(monkeypatch, handler)
    res = asyncio.run(
        huggingface_tools._read_file({"repo": "meta/llama", "path": "config.json"})
    )
    assert "gated" in res["error"]
