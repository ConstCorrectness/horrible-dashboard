"""This node's half of hassault account data (`backend/modules/hassault/account_sync.py`).

The game server is faked at `account_sync.request`, the one seam every call goes
through, so these pin the node's decisions: when it proxies, that it imports
once, and which settings travel.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.modules.games import server_auth
from backend.modules.hassault import account_sync


class FakeServer:
    def __init__(self, has_service: bool = True) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.has_service = has_service
        self.settings: dict = {}
        self.inventory = [{"instanceId": "srv-1", "skinId": "x"}]

    async def __call__(self, method, path, *, json=None, params=None):
        self.calls.append((method, path, json if json is not None else params))
        if not self.has_service:
            return 404, {"detail": "Not Found"}
        if path == "/progression" or path == "/drops":
            return 200, {"level": 1, "available": 0}
        if path == "/import":
            return 200, {"imported": True}
        if path == "/inventory":
            return 200, self.inventory
        if path == "/settings" and method == "GET":
            return 200, {"values": self.settings}
        if path == "/settings" and method == "PUT":
            self.settings.update(json["values"])
            return 200, {"values": self.settings}
        return 404, None


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    account_sync._ready.clear()
    yield
    account_sync._ready.clear()


def sign_in(monkeypatch, account: str | None) -> None:
    monkeypatch.setattr(
        server_auth,
        "signed_in_account",
        lambda: (
            None
            if account is None
            else {"id": account, "display_name": account, "handle": account}
        ),
    )


def test_signed_out_never_asks_the_server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(account_sync, "request", fake)
    sign_in(monkeypatch, None)
    assert asyncio.run(account_sync.active_account()) is None
    assert fake.calls == []


def test_signed_in_proxies_and_imports_once(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(account_sync, "request", fake)
    sign_in(monkeypatch, "ada")
    from backend.app import app

    client = TestClient(app)
    assert client.get("/api/hassault/skins/inventory").json() == fake.inventory
    assert client.get("/api/hassault/skins/inventory").json() == fake.inventory
    imports = [c for c in fake.calls if c[1] == "/import"]
    assert len(imports) == 1
    assert imports[0][2]["nodeId"]
    # And never again from this machine, even in a new process.
    account_sync._ready.clear()
    client.get("/api/hassault/skins/inventory")
    assert len([c for c in fake.calls if c[1] == "/import"]) == 1


def test_an_old_server_leaves_the_node_answering(monkeypatch):
    fake = FakeServer(has_service=False)
    monkeypatch.setattr(account_sync, "request", fake)
    sign_in(monkeypatch, "ada")
    from backend.app import app

    body = TestClient(app).get("/api/hassault/skins/inventory").json()
    assert isinstance(body, list) and body != fake.inventory


def test_settings_pull_seeds_an_empty_account_then_the_server_wins(monkeypatch):
    from backend.modules.settings.routes import get_value, set_value

    fake = FakeServer()
    monkeypatch.setattr(account_sync, "request", fake)
    sign_in(monkeypatch, "ada")
    set_value("hassault.sensitivity", 3.0)
    set_value("hassault.graphics", "this machine")  # machine-scoped: stays home

    asyncio.run(account_sync.pull_settings())
    assert fake.settings.get("hassault.sensitivity") == 3.0
    assert "hassault.graphics" not in fake.settings

    fake.settings["hassault.sensitivity"] = 1.25
    asyncio.run(account_sync.pull_settings())
    assert get_value("hassault.sensitivity", None) == 1.25


def test_a_push_needs_the_service_and_an_account_key(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        account_sync.threading,
        "Thread",
        lambda target, daemon: type(
            "T", (), {"start": lambda self: sent.append("x")}
        )(),
    )
    sign_in(monkeypatch, "ada")
    account_sync.push_setting("hassault.sensitivity", 2)
    assert sent == [], "not known to have the service yet"
    account_sync._ready["ada"] = True
    account_sync.push_setting("hassault.graphics", "x")
    assert sent == [], "machine-scoped"
    account_sync.push_setting("hassault.sensitivity", 2)
    assert sent == ["x"]
