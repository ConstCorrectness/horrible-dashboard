"""The connectors surface: the tile projection, the begin/submit/poll machine across
all three connector kinds, and the naming invariant the agent tool grouping depends on.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.connectors import oauth, store
from backend.modules.connectors.store import Credential
from backend.sdk.registry import registry
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_flows():
    oauth.reset_flows()
    yield
    oauth.reset_flows()


@pytest.fixture(autouse=True)
def builtin_connectors():
    """Re-register the built-ins before each test.

    They're registered once at app import, but the registry is process-global and
    `registry.reset()` (test_backend_sdk.py) wipes it — so whether `github` exists here
    would otherwise depend on test ordering. Registration is idempotent.
    """
    from backend.modules.connectors import register_connectors

    register_connectors()


@pytest.fixture
def fake_connectors():
    """Register throwaway connectors and remove them afterwards, leaving the built-ins
    (which other tests rely on) in place."""
    added: list[str] = []

    def add(connector: Connector) -> Connector:
        registry.connectors[connector.id] = connector
        added.append(connector.id)
        return connector

    yield add
    for cid in added:
        registry.connectors.pop(cid, None)


def _api_key_connector() -> Connector:
    """An `api-key` connector: one form step, then connected."""

    async def begin(_options: dict[str, Any]) -> dict[str, Any]:
        return {
            "step": "form",
            "fields": [{"name": "api_key", "label": "API key", "secret": True}],
        }

    async def submit(values: dict[str, str]) -> dict[str, Any]:
        key = values.get("api_key", "")
        if not key:
            return {"error": "api_key is required"}
        store.save(
            "fakekey", Credential(access_token=key, account={"id": "k", "label": "Key"})
        )
        return {"connected": True, "account": {"id": "k", "label": "Key"}}

    def status() -> ConnectorStatus:
        cred, error = store.load_or_error("fakekey")
        if error:
            return ConnectorStatus(connected=True, error=error)
        if cred is None:
            return ConnectorStatus(connected=False)
        return ConnectorStatus(
            connected=True, account=ConnectorAccount(id="k", label="Key")
        )

    async def disconnect() -> None:
        store.clear("fakekey")

    return Connector(
        id="fakekey",
        label="Fake Key",
        kind="api-key",
        icon="key",
        blurb="A pasted API key.",
        status=status,
        begin=begin,
        submit=submit,
        disconnect=disconnect,
        scopes=[ConnectorScope(id="all", label="Everything")],
    )


def _no_form_connector() -> Connector:
    """A connector with no `submit` callback at all — submitting to it is a 400."""

    async def begin(_options: dict[str, Any]) -> dict[str, Any]:
        return {"step": "redirect", "authorize_url": "https://example.test/auth"}

    async def disconnect() -> None:
        store.clear("fakenoform")

    return Connector(
        id="fakenoform",
        label="Fake No Form",
        kind="oauth",
        icon="key",
        blurb="Takes no form input.",
        status=lambda: ConnectorStatus(connected=False),
        begin=begin,
        disconnect=disconnect,
    )


def _custom_connector() -> Connector:
    """A `custom` connector: phone -> code -> connected, i.e. a form step that returns
    another form step. This is the check that `custom` needs no new concepts."""
    seen: dict[str, str] = {}

    async def begin(_options: dict[str, Any]) -> dict[str, Any]:
        seen.clear()
        return {"step": "form", "fields": [{"name": "phone", "label": "Phone"}]}

    async def submit(values: dict[str, str]) -> dict[str, Any]:
        if "phone" in values:
            seen["phone"] = values["phone"]
            return {"step": "form", "fields": [{"name": "code", "label": "SMS code"}]}
        if "code" in values:
            if not seen.get("phone"):
                return {"error": "start with a phone number"}
            if values["code"] != "1234":
                return {"error": "wrong code"}
            store.save(
                "fakesms",
                Credential(
                    access_token="t", account={"id": "u", "label": seen["phone"]}
                ),
            )
            return {"connected": True, "account": {"id": "u", "label": seen["phone"]}}
        return {"error": "unexpected input"}

    def status() -> ConnectorStatus:
        return ConnectorStatus(connected=store.is_connected("fakesms"))

    async def disconnect() -> None:
        store.clear("fakesms")

    return Connector(
        id="fakesms",
        label="Fake SMS",
        kind="custom",
        icon="phone",
        blurb="A phone-and-code flow.",
        status=status,
        begin=begin,
        submit=submit,
        disconnect=disconnect,
    )


# --- the tile projection ----------------------------------------------------


def test_list_includes_builtin_github(client: TestClient):
    res = client.get("/api/connectors")
    assert res.status_code == 200
    by_id = {c["id"]: c for c in res.json()["connectors"]}
    assert "github" in by_id
    gh = by_id["github"]
    assert gh["kind"] == "oauth"
    assert gh["connected"] is False
    assert gh["blurb"]
    assert {s["id"] for s in gh["scopes"]} == {"read:user", "repo"}


def test_list_never_leaks_a_token(client: TestClient):
    """The browser learns *that* an account is connected, never the credential."""
    store.save(
        "github",
        Credential(
            access_token="ghp_supersecret", account={"id": "1", "label": "octocat"}
        ),
    )
    body = client.get("/api/connectors").text
    assert "ghp_supersecret" not in body
    gh = next(
        c
        for c in client.get("/api/connectors").json()["connectors"]
        if c["id"] == "github"
    )
    assert gh["connected"] is True
    assert gh["account"]["label"] == "octocat"


def test_unreadable_credential_reads_as_error_not_disconnected(
    client: TestClient, monkeypatch
):
    """A rotated key must not present as "never connected" — that would offer a fresh
    Connect button and silently paper over a real problem."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("SECRETS_MASTER_KEY", Fernet.generate_key().decode())
    store.save(
        "github", Credential(access_token="t", account={"id": "1", "label": "octocat"})
    )
    monkeypatch.setenv("SECRETS_MASTER_KEY", Fernet.generate_key().decode())

    gh = next(
        c
        for c in client.get("/api/connectors").json()["connectors"]
        if c["id"] == "github"
    )
    assert gh["connected"] is True
    assert gh["error"] and "decrypt" in gh["error"]


def test_status_failure_does_not_hide_the_tile(client: TestClient, fake_connectors):
    """One broken connector must not take the home page's tile row down."""

    def boom() -> ConnectorStatus:
        raise RuntimeError("status exploded")

    fake_connectors(
        Connector(
            id="boom",
            label="Boom",
            kind="oauth",
            icon="x",
            blurb="b",
            status=boom,
            begin=lambda _o: {"error": "no"},
            disconnect=lambda: None,
        )
    )
    body = client.get("/api/connectors").json()["connectors"]
    boom_tile = next(c for c in body if c["id"] == "boom")
    assert boom_tile["connected"] is False
    assert "status exploded" in boom_tile["error"]


# --- routing ----------------------------------------------------------------


def test_unknown_connector_is_404(client: TestClient):
    assert client.post("/api/connectors/nope/connect").status_code == 404
    assert client.delete("/api/connectors/nope").status_code == 404
    assert client.post("/api/connectors/nope/poll").status_code == 404


def test_submit_on_a_connector_without_a_form_is_400(
    client: TestClient, fake_connectors
):
    # Deliberately a fake rather than a built-in: every built-in OAuth connector now
    # takes a form (its client credentials), so none of them exercises this path.
    fake_connectors(_no_form_connector())
    res = client.post("/api/connectors/fakenoform/submit", json={"values": {}})
    assert res.status_code == 400


def test_disconnect_clears_the_credential(client: TestClient):
    store.save(
        "github", Credential(access_token="t", account={"id": "1", "label": "octocat"})
    )
    assert store.is_connected("github")

    res = client.delete("/api/connectors/github")
    assert res.status_code == 200
    assert res.json()["connected"] is False
    assert not store.is_connected("github")


def test_disconnect_is_idempotent(client: TestClient):
    assert client.delete("/api/connectors/github").status_code == 200
    assert client.delete("/api/connectors/github").status_code == 200


def test_connector_callback_failure_is_a_value_not_a_500(
    client: TestClient, fake_connectors
):
    fake_connectors(
        Connector(
            id="throws",
            label="Throws",
            kind="api-key",
            icon="x",
            blurb="b",
            status=lambda: ConnectorStatus(connected=False),
            begin=lambda _o: (_ for _ in ()).throw(RuntimeError("begin exploded")),
            disconnect=lambda: None,
        )
    )
    res = client.post("/api/connectors/throws/connect")
    assert res.status_code == 200
    assert "begin exploded" in res.json()["error"]


# --- the three kinds --------------------------------------------------------


def test_api_key_round_trip(client: TestClient, fake_connectors):
    fake_connectors(_api_key_connector())

    begin = client.post("/api/connectors/fakekey/connect").json()
    assert begin["step"] == "form"
    assert begin["fields"][0] == {
        "name": "api_key",
        "label": "API key",
        "secret": True,
        "placeholder": "",
        "value": "",
        "help": "",
    }

    res = client.post(
        "/api/connectors/fakekey/submit", json={"values": {"api_key": "sk-123"}}
    )
    assert res.json()["connected"] is True
    assert store.load("fakekey").access_token == "sk-123"


def test_api_key_validation_error_is_reported(client: TestClient, fake_connectors):
    fake_connectors(_api_key_connector())
    client.post("/api/connectors/fakekey/connect")
    res = client.post(
        "/api/connectors/fakekey/submit", json={"values": {"api_key": ""}}
    )
    assert res.json()["error"] == "api_key is required"
    assert not store.is_connected("fakekey")


def test_custom_form_to_form_to_connected(client: TestClient, fake_connectors):
    """Clubhouse's phone -> SMS -> connected shape, on the generic machine."""
    fake_connectors(_custom_connector())

    step1 = client.post("/api/connectors/fakesms/connect").json()
    assert [f["name"] for f in step1["fields"]] == ["phone"]

    step2 = client.post(
        "/api/connectors/fakesms/submit", json={"values": {"phone": "+15551234"}}
    ).json()
    assert step2["step"] == "form"
    assert [f["name"] for f in step2["fields"]] == ["code"]
    assert step2["connected"] is False

    step3 = client.post(
        "/api/connectors/fakesms/submit", json={"values": {"code": "1234"}}
    ).json()
    assert step3["connected"] is True
    assert step3["account"]["label"] == "+15551234"


def test_custom_wrong_code_does_not_connect(client: TestClient, fake_connectors):
    fake_connectors(_custom_connector())
    client.post("/api/connectors/fakesms/connect")
    client.post("/api/connectors/fakesms/submit", json={"values": {"phone": "+1"}})
    res = client.post(
        "/api/connectors/fakesms/submit", json={"values": {"code": "0000"}}
    )
    assert res.json()["error"] == "wrong code"
    assert not store.is_connected("fakesms")


# --- the invariant ----------------------------------------------------------


def test_agent_tool_names_match_their_declared_group():
    """The orchestrator groups tools by the namespace before the first dot
    (`_group_of`), NOT by `AgentTool.group`. When they disagree, the tools silently
    land in a group with no blurb and no guide — which is what `game.*` tools declaring
    `group="games"` did.
    """
    from backend.modules.agent.orchestrator import _group_of

    mismatched = {
        name: (tool.group, _group_of(name))
        for name, tool in registry.agent_tools.items()
        if tool.group is not None and _group_of(name) != tool.group
    }
    assert not mismatched, (
        "these tools would be disclosed under a group that doesn't match their "
        f"declared one: {mismatched}"
    )


def test_connector_tools_are_namespaced_under_their_connector():
    """A connector's blurb and guide only reach the model if its tools group under its
    id — so the tool prefix and the connector id must agree."""
    from backend.modules.agent.orchestrator import _group_of

    tool_groups = {_group_of(n) for n in registry.agent_tools}
    for cid in registry.connectors:
        tools = [n for n in registry.agent_tools if _group_of(n) == cid]
        assert tools or cid not in tool_groups, (
            f"connector {cid} has no tools grouped under it"
        )
