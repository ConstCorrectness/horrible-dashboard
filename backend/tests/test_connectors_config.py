"""Client-credential custody: precedence, persistence, and the secret never leaking.

The load-bearing property here is that a client *secret* never crosses back to the
browser — not in a form prefill, not in a tile payload. `GET /api/settings` hands the
whole settings bag to any page the app renders, so a secret stored there would be
readable; these tests pin both halves of that rule.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors import config
from backend.modules.connectors.providers import github, google

ID_ENV = "GOOGLE_CLIENT_ID"
SECRET_ENV = "GOOGLE_CLIENT_SECRET"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def builtin_connectors():
    from backend.modules.connectors import register_connectors

    register_connectors()


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    """Start every test from an unconfigured node."""
    for var in (ID_ENV, SECRET_ENV, "GITHUB_CLIENT_ID"):
        monkeypatch.delenv(var, raising=False)


# --- precedence -------------------------------------------------------------


def test_env_beats_the_stored_setting(monkeypatch):
    from backend.modules.settings.routes import set_value

    set_value("connectors.google.clientId", "from-setting")
    assert config.client_id("google", ID_ENV) == "from-setting"

    monkeypatch.setenv(ID_ENV, "from-env")
    assert config.client_id("google", ID_ENV) == "from-env"


def test_env_beats_the_stored_secret(monkeypatch):
    from backend.modules.database.secrets_store import upsert_secret

    upsert_secret("google_client_secret", "from-store")
    assert config.client_secret("google", SECRET_ENV) == "from-store"

    monkeypatch.setenv(SECRET_ENV, "from-env")
    assert config.client_secret("google", SECRET_ENV) == "from-env"


def test_an_env_pinned_value_is_not_persisted(monkeypatch):
    """Storing it would record a setting that is never read — a lie the UI would then
    display back as if it were in effect."""
    from backend.modules.settings.routes import get_value, set_value

    set_value("connectors.google.clientId", "")
    monkeypatch.setenv(ID_ENV, "pinned")

    assert (
        config.apply_config(
            "google",
            {"client_id": "ignored", "client_secret": "s"},
            id_env=ID_ENV,
            secret_env=SECRET_ENV,
        )
        is None
    )
    assert get_value("connectors.google.clientId", "") == ""


def test_an_env_pinned_field_says_so():
    step = config.configure_step("google", id_env=ID_ENV, secret_env=SECRET_ENV)
    assert ID_ENV not in step["fields"][0]["help"]

    import os

    os.environ[ID_ENV] = "pinned"
    try:
        step = config.configure_step("google", id_env=ID_ENV, secret_env=SECRET_ENV)
        assert ID_ENV in step["fields"][0]["help"]
    finally:
        del os.environ[ID_ENV]


# --- persistence ------------------------------------------------------------


def test_apply_config_splits_id_and_secret_by_storage():
    """The id is public and goes to settings; the secret must not."""
    from backend.modules.database.secrets_store import get_secret_or_none
    from backend.modules.settings.routes import _read, get_value

    assert (
        config.apply_config(
            "google",
            {"client_id": "cid-123", "client_secret": "shh-456"},
            id_env=ID_ENV,
            secret_env=SECRET_ENV,
        )
        is None
    )

    assert get_value("connectors.google.clientId", "") == "cid-123"
    assert get_secret_or_none("google_client_secret") == "shh-456"
    # The whole settings bag, which the browser can read, must not contain the secret.
    assert "shh-456" not in repr(_read())


def test_a_blank_secret_leaves_an_existing_one_alone():
    """The form can't prefill a secret, so blank means "unchanged", not "clear it" —
    otherwise editing only the client id would silently break the connector."""
    from backend.modules.database.secrets_store import get_secret_or_none

    config.apply_config(
        "google",
        {"client_id": "cid", "client_secret": "original"},
        id_env=ID_ENV,
        secret_env=SECRET_ENV,
    )
    config.apply_config(
        "google",
        {"client_id": "cid-updated", "client_secret": ""},
        id_env=ID_ENV,
        secret_env=SECRET_ENV,
    )
    assert get_secret_or_none("google_client_secret") == "original"


def test_missing_credentials_are_reported_not_stored():
    assert config.apply_config("google", {}, id_env=ID_ENV, secret_env=SECRET_ENV)


# --- the secret never comes back --------------------------------------------


def test_the_configure_form_never_prefills_the_secret():
    from backend.modules.database.secrets_store import upsert_secret

    upsert_secret("google_client_secret", "top-secret")
    config.apply_config("google", {"client_id": "cid"}, id_env=ID_ENV)

    step = config.configure_step("google", id_env=ID_ENV, secret_env=SECRET_ENV)
    by_name = {f["name"]: f for f in step["fields"]}
    # The public half is prefilled so a reconfigure doesn't force a retype...
    assert by_name["client_id"]["value"] == "cid"
    # ...the secret half never is.
    assert by_name["client_secret"]["value"] == ""
    assert "top-secret" not in repr(step)


def test_the_tile_payload_reports_booleans_not_credentials(client: TestClient):
    from backend.modules.database.secrets_store import upsert_secret

    upsert_secret("google_client_secret", "top-secret")
    config.apply_config("google", {"client_id": "cid"}, id_env=ID_ENV)

    body = client.get("/api/connectors").json()
    tile = next(c for c in body["connectors"] if c["id"] == "google")

    assert tile["configurable"] is True
    assert tile["configured"] is True
    assert "top-secret" not in repr(body)
    assert "cid" not in repr(tile.get("account"))


# --- chaining ---------------------------------------------------------------


def test_submitting_credentials_chains_into_the_oauth_step():
    """The point of chaining: fill the form, land on consent — not "press Connect again"."""
    step = asyncio.run(
        google.build().submit({"client_id": "cid", "client_secret": "shh"})
    )
    assert step["step"] == "redirect"
    assert step["authorize_url"].startswith("https://accounts.google.com/")
    assert "client_id=cid" in step["authorize_url"]
    # The secret is used to mint the URL but never appears in it.
    assert "shh" not in step["authorize_url"]


def test_github_needs_only_a_client_id():
    """The device flow uses no secret, so asking for one would be a dead end."""
    step = asyncio.run(github.build().begin({}))
    assert step["step"] == "form"
    assert [f["name"] for f in step["fields"]] == ["client_id"]


def test_reconfigure_reopens_the_form_on_a_configured_node():
    config.apply_config(
        "google",
        {"client_id": "cid", "client_secret": "shh"},
        id_env=ID_ENV,
        secret_env=SECRET_ENV,
    )
    assert google.build().configured() is True

    step = asyncio.run(google.build().begin({"reconfigure": True}))
    assert step["step"] == "form"
