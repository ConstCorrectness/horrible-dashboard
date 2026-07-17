"""The OAuth machinery: state/PKCE on the redirect flow, the device flow, and the
refresh path.

These cover the properties that fail silently and dangerously if they regress — a
callback that skips the state check, a refresh burst that spends the refresh token
several times over, a callback that redirects into the app instead of ending in place.

Async is driven with `asyncio.run` to match the rest of the suite (test_games_auth.py).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors import oauth, store
from backend.modules.connectors.store import Credential
from backend.sdk.registry import registry
from backend.sdk.types import Connector, ConnectorStatus


@pytest.fixture(autouse=True)
def clean_flows():
    oauth.reset_flows()
    yield
    oauth.reset_flows()


@pytest.fixture
def redirect_connector():
    """A registered connector whose code exchange is a stub, so the flow and the
    callback route can be driven end to end. Returns `(connector, exchange_calls)`."""
    calls: list[tuple[str, str]] = []

    async def exchange(code: str, verifier: str) -> Credential | dict[str, Any]:
        calls.append((code, verifier))
        if code == "bad":
            return {"error": "provider rejected the code"}
        return Credential(
            access_token="tok-secret", account={"id": "1", "label": "user"}
        )

    connector = Connector(
        id="fakeoauth",
        label="Fake OAuth",
        kind="oauth",
        icon="x",
        blurb="b",
        status=lambda: ConnectorStatus(connected=store.is_connected("fakeoauth")),
        begin=lambda _o: oauth.begin_redirect(
            "fakeoauth",
            authorize_url=lambda state, challenge: (
                f"https://provider.example/auth?state={state}&code_challenge={challenge}"
            ),
            exchange=exchange,
        ),
        poll=lambda: oauth.poll_flow("fakeoauth"),
        disconnect=lambda: store.clear("fakeoauth"),
    )
    registry.connectors["fakeoauth"] = connector
    yield connector, calls
    registry.connectors.pop("fakeoauth", None)
    store.clear("fakeoauth")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _start(connector: Connector) -> str:
    """Begin a redirect flow and return the minted state."""
    step = connector.begin({})
    assert step["step"] == "redirect"
    return step["authorize_url"].split("state=")[1].split("&")[0]


# --- redirect URI -----------------------------------------------------------


def test_redirect_uri_is_loopback_not_request_host(monkeypatch):
    """It must come from the backend's own port, never the request's Host — behind the
    dev Vite proxy a Host-derived URI resolves to :5173 and the provider rejects it."""
    monkeypatch.delenv("HORRIBLE_DEV_BACKEND_PORT", raising=False)
    monkeypatch.setenv("HORRIBLE_BACKEND_PORT", "8000")
    assert (
        oauth.redirect_uri("google")
        == "http://127.0.0.1:8000/api/connectors/google/callback"
    )


def test_redirect_uri_follows_the_dev_port_override(monkeypatch):
    monkeypatch.setenv("HORRIBLE_DEV_BACKEND_PORT", "8100")
    assert "127.0.0.1:8100" in oauth.redirect_uri("google")


# --- PKCE -------------------------------------------------------------------


def test_pkce_pair_is_s256_and_unpadded():
    import base64
    import hashlib

    verifier, challenge = oauth.new_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected
    assert "=" not in challenge and "=" not in verifier


def test_pkce_pair_is_fresh_each_time():
    assert oauth.new_pkce_pair()[0] != oauth.new_pkce_pair()[0]


# --- the redirect flow ------------------------------------------------------


def test_state_mismatch_is_rejected(redirect_connector):
    connector, calls = redirect_connector
    _start(connector)

    result = asyncio.run(oauth.finish_redirect("fakeoauth", code="c", state="forged"))
    assert "state mismatch" in result["error"]
    assert calls == [], "a forged state must never reach the code exchange"
    assert not store.is_connected("fakeoauth")


def test_forged_callback_does_not_cancel_the_real_flow(redirect_connector):
    connector, _ = redirect_connector
    state = _start(connector)

    async def go():
        await oauth.finish_redirect("fakeoauth", code="c", state="forged")
        # The genuine callback must still work afterwards.
        return await oauth.finish_redirect("fakeoauth", code="c", state=state)

    assert asyncio.run(go())["connected"] is True


def test_missing_state_is_rejected(redirect_connector):
    connector, _ = redirect_connector
    _start(connector)
    result = asyncio.run(oauth.finish_redirect("fakeoauth", code="c", state=""))
    assert "state mismatch" in result["error"]


def test_callback_without_a_flow_is_rejected(redirect_connector):
    result = asyncio.run(oauth.finish_redirect("fakeoauth", code="c", state="whatever"))
    assert result["error"] == "no sign-in in progress"


def test_expired_flow_is_rejected(redirect_connector):
    connector, calls = redirect_connector
    state = _start(connector)
    oauth._flows["fakeoauth"].expires_at = time.time() - 1

    result = asyncio.run(oauth.finish_redirect("fakeoauth", code="c", state=state))
    assert "timed out" in result["error"]
    assert calls == []


def test_state_is_single_use(redirect_connector):
    connector, _ = redirect_connector
    state = _start(connector)

    async def go():
        first = await oauth.finish_redirect("fakeoauth", code="c", state=state)
        await oauth.poll_flow("fakeoauth")  # consumes the finished flow
        replay = await oauth.finish_redirect("fakeoauth", code="c", state=state)
        return first, replay

    first, replay = asyncio.run(go())
    assert first["connected"] is True
    assert replay["error"] == "no sign-in in progress"


def test_verifier_reaches_the_exchange(redirect_connector):
    connector, calls = redirect_connector
    state = _start(connector)

    asyncio.run(oauth.finish_redirect("fakeoauth", code="thecode", state=state))
    assert len(calls) == 1
    code, verifier = calls[0]
    assert code == "thecode"
    assert verifier, "the PKCE verifier must be handed to the exchange"


def test_exchange_failure_does_not_persist(redirect_connector):
    connector, _ = redirect_connector
    state = _start(connector)

    result = asyncio.run(oauth.finish_redirect("fakeoauth", code="bad", state=state))
    assert "provider rejected" in result["error"]
    assert not store.is_connected("fakeoauth")


# --- the callback route -----------------------------------------------------


def test_callback_returns_html_and_does_not_redirect(
    client: TestClient, redirect_connector
):
    """The app's origin stays out of the OAuth loop: the callback ends in place and the
    app tab polls. A redirect here would reintroduce the dev-proxy mismatch."""
    connector, _ = redirect_connector
    state = _start(connector)

    res = client.get(
        f"/api/connectors/fakeoauth/callback?code=c&state={state}",
        follow_redirects=False,
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "close this tab" in res.text
    assert store.is_connected("fakeoauth")


def test_callback_never_puts_the_token_in_the_page(
    client: TestClient, redirect_connector
):
    connector, _ = redirect_connector
    state = _start(connector)
    res = client.get(f"/api/connectors/fakeoauth/callback?code=c&state={state}")
    assert "tok-secret" not in res.text
    assert store.load("fakeoauth").access_token == "tok-secret"


def test_callback_rejects_bad_state_with_a_page(client: TestClient, redirect_connector):
    connector, _ = redirect_connector
    _start(connector)
    res = client.get("/api/connectors/fakeoauth/callback?code=c&state=forged")
    assert res.status_code == 400
    assert "state mismatch" in res.text
    assert not store.is_connected("fakeoauth")


def test_callback_for_unknown_connector_is_404(client: TestClient):
    res = client.get("/api/connectors/nope/callback?code=c&state=s")
    assert res.status_code == 404


# --- polling ----------------------------------------------------------------


def test_poll_reports_pending_then_the_result(redirect_connector):
    connector, _ = redirect_connector
    state = _start(connector)

    async def go():
        pending = await oauth.poll_flow("fakeoauth")
        await oauth.finish_redirect("fakeoauth", code="c", state=state)
        return pending, await oauth.poll_flow("fakeoauth")

    pending, done = asyncio.run(go())
    assert pending == {"pending": True}
    assert done["connected"] is True


def test_poll_with_no_flow_reports_ground_truth(redirect_connector):
    assert "error" in asyncio.run(oauth.poll_flow("fakeoauth"))
    store.save("fakeoauth", Credential(access_token="t"))
    assert asyncio.run(oauth.poll_flow("fakeoauth"))["connected"] is True


# --- the device flow --------------------------------------------------------


def test_device_flow_polls_until_authorized():
    attempts = {"n": 0}

    async def poll(device_code: str) -> Credential | dict[str, Any]:
        assert device_code == "dev-code"
        attempts["n"] += 1
        if attempts["n"] < 3:
            return {"pending": True}
        return Credential(access_token="at", account={"id": "1", "label": "octocat"})

    async def go():
        oauth.begin_device(
            "fakedev",
            user_code="ABCD-1234",
            verification_uri="https://example/device",
            device_code="dev-code",
            poll=poll,
        )
        return [await oauth.poll_flow("fakedev") for _ in range(3)]

    first, second, third = asyncio.run(go())
    assert first == {"pending": True}
    assert second == {"pending": True}
    assert third["connected"] is True
    assert third["account"]["label"] == "octocat"
    assert store.load("fakedev").access_token == "at"
    store.clear("fakedev")


def test_device_flow_start_returns_the_user_facing_step():
    async def poll(_d: str) -> dict[str, Any]:
        return {"pending": True}

    step = oauth.begin_device(
        "fakedev",
        user_code="ABCD-1234",
        verification_uri="https://example/device",
        device_code="d",
        poll=poll,
        interval=7,
    )
    assert step["step"] == "device"
    assert step["user_code"] == "ABCD-1234"
    assert step["interval"] == 7


def test_device_flow_error_clears_the_flow():
    async def poll(_device_code: str) -> dict[str, Any]:
        return {"error": "access_denied"}

    async def go():
        oauth.begin_device(
            "fakedev", user_code="A", verification_uri="u", device_code="d", poll=poll
        )
        return await oauth.poll_flow("fakedev"), await oauth.poll_flow("fakedev")

    first, second = asyncio.run(go())
    assert first["error"] == "access_denied"
    assert second["error"] == "no sign-in in progress"


# --- refresh ----------------------------------------------------------------


def test_ensure_fresh_leaves_a_live_token_alone():
    refreshed = {"n": 0}

    async def refresh(_cred: Credential) -> Credential:
        refreshed["n"] += 1
        return Credential(access_token="new")

    store.save(
        "fakeref",
        Credential(
            access_token="live", refresh_token="r", expires_at=time.time() + 3600
        ),
    )
    cred = asyncio.run(oauth.ensure_fresh("fakeref", refresh))
    assert cred.access_token == "live"
    assert refreshed["n"] == 0
    store.clear("fakeref")


def test_ensure_fresh_refreshes_inside_the_window():
    """Inside REFRESH_WINDOW_S of expiry the token is still technically valid, but a
    call that starts just under the wire would land just over it."""

    async def refresh(_cred: Credential) -> Credential:
        return Credential(access_token="new", expires_at=time.time() + 3600)

    store.save(
        "fakeref",
        Credential(access_token="old", refresh_token="r", expires_at=time.time() + 30),
    )
    cred = asyncio.run(oauth.ensure_fresh("fakeref", refresh))
    assert cred.access_token == "new"
    assert store.load("fakeref").access_token == "new"
    store.clear("fakeref")


def test_ensure_fresh_keeps_a_refresh_token_the_provider_omitted():
    """Google returns no refresh_token on a refresh response. Dropping it would make
    the *next* refresh impossible — the connection would silently die an hour later."""

    async def refresh(_cred: Credential) -> Credential:
        return Credential(access_token="new", expires_at=time.time() + 3600)

    store.save(
        "fakeref",
        Credential(
            access_token="old",
            refresh_token="keep-me",
            expires_at=time.time() - 1,
            scopes=["a"],
            account={"id": "1", "label": "u"},
        ),
    )
    asyncio.run(oauth.ensure_fresh("fakeref", refresh))
    stored = store.load("fakeref")
    assert stored.refresh_token == "keep-me"
    assert stored.account == {"id": "1", "label": "u"}
    assert stored.scopes == ["a"]
    store.clear("fakeref")


def test_ensure_fresh_stores_a_rotated_refresh_token():
    async def refresh(_cred: Credential) -> Credential:
        return Credential(
            access_token="new", refresh_token="rotated", expires_at=time.time() + 3600
        )

    store.save(
        "fakeref",
        Credential(
            access_token="old", refresh_token="old-r", expires_at=time.time() - 1
        ),
    )
    asyncio.run(oauth.ensure_fresh("fakeref", refresh))
    assert store.load("fakeref").refresh_token == "rotated"
    store.clear("fakeref")


def test_concurrent_callers_refresh_once():
    """A parallel tool burst must not each spend the refresh token — providers rotate
    it, so the later exchanges would fail and log the user out."""
    calls = {"n": 0}

    async def refresh(_cred: Credential) -> Credential:
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return Credential(
            access_token=f"new{calls['n']}", expires_at=time.time() + 3600
        )

    store.save(
        "fakeref",
        Credential(access_token="old", refresh_token="r", expires_at=time.time() - 1),
    )

    async def go():
        return await asyncio.gather(
            *(oauth.ensure_fresh("fakeref", refresh) for _ in range(5))
        )

    creds = asyncio.run(go())
    assert calls["n"] == 1, "the expiry must be re-checked inside the lock"
    assert {c.access_token for c in creds} == {"new1"}
    store.clear("fakeref")


def test_ensure_fresh_on_a_disconnected_connector_is_none():
    assert asyncio.run(oauth.ensure_fresh("never-connected", None)) is None


def test_ensure_fresh_survives_a_failed_refresh():
    async def refresh(_cred: Credential) -> dict[str, Any]:
        return {"error": "refresh token revoked"}

    store.save(
        "fakeref",
        Credential(access_token="old", refresh_token="r", expires_at=time.time() - 1),
    )
    assert asyncio.run(oauth.ensure_fresh("fakeref", refresh)) is None
    store.clear("fakeref")


def test_credential_without_expiry_never_refreshes():
    """GitHub OAuth App user tokens don't expire; ensure_fresh must be a no-op."""
    called = {"n": 0}

    async def refresh(_cred: Credential) -> Credential:
        called["n"] += 1
        return Credential(access_token="new")

    store.save("fakeref", Credential(access_token="forever", refresh_token="r"))
    cred = asyncio.run(oauth.ensure_fresh("fakeref", refresh))
    assert cred.access_token == "forever"
    assert called["n"] == 0
    store.clear("fakeref")
