"""The node's half of the public link: minting, revoking, and what leaks.

The test that matters most here is the negative one. `ingest_url` is publish
authority -- anyone holding it can push their own video into the host's stream --
and the session model is broadcast verbatim to every guest. So the interesting
question is not "does minting work" but "can a guest ever see the ingest URL".
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.share import link as link_api
from backend.modules.share.session import ShareManager


class FakeHandle:
    def __init__(self) -> None:
        self.token = "tok-abc"
        self.view_url = "https://share.example.com/s/tok-abc"
        self.ingest_url = "https://share.example.com/whip/tok-abc"
        self.expires_at = 4102444800.0


@pytest.fixture
def manager(monkeypatch):
    """A clean manager and a stubbed identity.

    The same shape `test_share_routes.py` uses: the real manager is process-global
    and `self_profile()` reads the social tables, which an isolated data dir does
    not have.
    """
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.routes.share_manager", mgr)
    monkeypatch.setattr("backend.modules.share.session.share_manager", mgr)
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    class FakeProfile:
        person_id = "hostperson"
        display_name = "Host"

    monkeypatch.setattr(
        "backend.modules.social.roster.self_profile", lambda: FakeProfile()
    )
    return mgr


@pytest.fixture
def client(monkeypatch, manager):
    """A node with a scripted relay, so nothing here needs a network."""
    minted: list[dict] = []

    async def fake_mint(*, title, ttl_s=None, passphrase=""):
        minted.append({"title": title, "ttl_s": ttl_s, "passphrase": passphrase})
        return FakeHandle()

    async def fake_revoke(token):
        minted.append({"revoked": token})
        return True

    monkeypatch.setattr(link_api, "mint", fake_mint)
    monkeypatch.setattr(link_api, "revoke", fake_revoke)
    c = TestClient(app)
    c.minted = minted  # type: ignore[attr-defined]
    c.manager = manager  # type: ignore[attr-defined]
    return c


def _start(client) -> None:
    res = client.post("/api/share/session", json={"title": "Standup", "mode": "both"})
    assert res.status_code == 200


def test_minting_needs_a_session(client) -> None:
    res = client.post("/api/share/link", json={})
    assert res.status_code == 200
    assert "Start a session" in res.json()["error"]


def test_minting_publishes_only_the_view_url_to_the_session(client) -> None:
    _start(client)
    res = client.post("/api/share/link", json={})
    body = res.json()
    assert body["view_url"].endswith("/s/tok-abc")
    assert body["ingest_url"].endswith("/whip/tok-abc")

    # The broadcast model carries the public URL and nothing else about the link.
    state = client.get("/api/share").json()
    assert state["hosting"]["link"] == body["view_url"]
    assert "tok-abc" not in str(state["hosting"].get("ingest_url", ""))
    assert "whip" not in str(state["hosting"])


def test_the_ingest_url_is_not_in_the_session_model_at_all(client) -> None:
    # The one that would be a real breach: `_tell_guests` sends this model
    # verbatim over the fabric, so a field added here reaches every guest.
    _start(client)
    client.post("/api/share/link", json={})
    session = client.manager.hosting
    assert session is not None
    assert "whip" not in session.model_dump_json()


def test_the_host_can_read_its_own_ingest_url_back(client) -> None:
    # The host's tab can reload mid-session and has to resume publishing.
    _start(client)
    client.post("/api/share/link", json={})
    again = client.get("/api/share/link").json()
    assert again["ingest_url"].endswith("/whip/tok-abc")


def test_minting_twice_revokes_the_first_link(client) -> None:
    # Two live links for one session means a revoke that only half works.
    _start(client)
    client.post("/api/share/link", json={})
    client.post("/api/share/link", json={})
    assert any(entry.get("revoked") == "tok-abc" for entry in client.minted)


def test_revoking_clears_the_link_from_the_session(client) -> None:
    _start(client)
    client.post("/api/share/link", json={})
    assert client.delete("/api/share/link").json()["ok"] is True
    assert client.get("/api/share").json()["hosting"]["link"] == ""
    assert client.get("/api/share/link").json()["ingest_url"] == ""


def test_stopping_a_session_revokes_its_link(client) -> None:
    # A public URL that outlives the session it was minted for is the failure the
    # whole expiry story exists to bound -- so stopping must not rely on expiry.
    _start(client)
    client.post("/api/share/link", json={})
    client.delete("/api/share/session")
    assert any(entry.get("revoked") == "tok-abc" for entry in client.minted)
    assert client.manager.link_ingest == ""


def test_the_passphrase_is_passed_through_but_never_echoed(client) -> None:
    _start(client)
    res = client.post("/api/share/link", json={"passphrase": "hunter2"})
    assert client.minted[-1]["passphrase"] == "hunter2"
    assert "hunter2" not in res.text
    assert "hunter2" not in client.get("/api/share").text


def test_an_unconfigured_relay_reports_a_fixable_error(manager, monkeypatch) -> None:
    # The real minting path, not the fake: no relay URL configured is the state a
    # fresh install is in, and it must read as instructions rather than a 500.
    client = TestClient(app)
    client.post("/api/share/session", json={"title": "Standup", "mode": "both"})
    res = client.post("/api/share/link", json={})
    assert res.status_code == 200
    assert "relay" in res.json()["error"].lower()


# --- the relay liveness poll -------------------------------------------------
#
# The bug this covers: the relay keeps its registry in one process's memory, so
# an OOM kill or a redeploy drops every token while the host's peer connection
# goes on believing it is publishing. Nothing on the media path raises. The pane
# said "relaying" over a link that served an expired page to every viewer.
#
# The assertion that carries the weight is the `unknown` one: a relay we could
# not reach must never be reported as a relay that disowned the token, because
# those two call for opposite reactions from the host.


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _relay_answering(monkeypatch, response, *, url: str = "https://relay.example.com"):
    """Point `link.stream_status` at a scripted relay. `response` may raise."""
    monkeypatch.setattr(link_api, "relay_base", lambda: url)

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def get(self, *a, **k):
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(link_api.httpx, "AsyncClient", FakeClient)


def test_relay_holding_media_reads_as_live(monkeypatch) -> None:
    _relay_answering(
        monkeypatch, _FakeResponse(200, {"live": True, "viewers": 3, "expires_at": 1.0})
    )
    status = asyncio.run(link_api.stream_status("tok-abc"))
    assert status["state"] == "live"
    assert status["live"] is True
    assert status["viewers"] == 3


def test_a_valid_token_with_no_publisher_is_idle_not_gone(monkeypatch) -> None:
    # The link still works; the picture is missing. Telling the host to mint a
    # new one here would be advice that fixes nothing.
    _relay_answering(monkeypatch, _FakeResponse(200, {"live": False, "viewers": 0}))
    status = asyncio.run(link_api.stream_status("tok-abc"))
    assert status["state"] == "idle"
    assert status["live"] is False
    assert status["detail"]


def test_a_404_means_the_relay_disowned_the_token(monkeypatch) -> None:
    # Exactly the OOM case: the relay is up and has never heard of this token.
    _relay_answering(monkeypatch, _FakeResponse(404))
    status = asyncio.run(link_api.stream_status("tok-abc"))
    assert status["state"] == "gone"
    assert "mint a new one" in status["detail"].lower()


def test_an_unreachable_relay_is_unknown_and_never_gone(monkeypatch) -> None:
    # THE test in this block. A flaky hop between node and relay must not be
    # rendered as "your link is dead" -- that sends the host off to re-mint and
    # re-share a URL that was fine all along.
    _relay_answering(monkeypatch, link_api.httpx.ConnectError("no route"))
    status = asyncio.run(link_api.stream_status("tok-abc"))
    assert status["state"] == "unknown"
    assert status["state"] != "gone"
    assert status["live"] is False


def test_a_rejected_key_is_unknown_too(monkeypatch) -> None:
    # The relay declined to answer, which says nothing about the stream.
    _relay_answering(monkeypatch, _FakeResponse(401))
    status = asyncio.run(link_api.stream_status("tok-abc"))
    assert status["state"] == "unknown"


def test_no_relay_configured_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(link_api, "relay_base", lambda: "")
    status = asyncio.run(link_api.stream_status("tok-abc"))
    assert status["state"] == "unknown"


def test_the_status_route_reports_a_dead_link_to_the_pane(client, monkeypatch) -> None:
    _start(client)
    client.post("/api/share/link", json={})

    async def fake_status(token):
        assert token == "tok-abc"
        return {
            "state": "gone",
            "live": False,
            "viewers": 0,
            "detail": "Mint a new one.",
        }

    monkeypatch.setattr(link_api, "stream_status", fake_status)
    body = client.get("/api/share/link/status").json()
    assert body["state"] == "gone"
    assert body["live"] is False
    assert body["detail"]


def test_the_status_route_without_a_link_says_unknown(client) -> None:
    # Not "gone": there is nothing to be gone. A session with no link minted is
    # the ordinary fabric-only case and must not render as a fault.
    _start(client)
    body = client.get("/api/share/link/status").json()
    assert body["state"] == "unknown"
