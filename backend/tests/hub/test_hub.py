"""The hosted hub end to end against a fake game server + instance: sign-in,
sessions, the proxy (HTTP, streaming, WebSocket), origin and ticket checks."""

from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.hub.app import Hub
from backend.hub.config import HubConfig
from backend.tests.hub.conftest import sign_in


def test_signed_out_gets_401_everywhere(make_hub) -> None:
    client = make_hub()
    assert client.get("/hub/me").status_code == 401
    assert client.get("/api/echo").status_code == 401
    assert client.post("/hub/ws-ticket").status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_wrong_password_surfaces_the_error_and_sets_no_cookie(make_hub) -> None:
    client = make_hub()
    res = client.post(
        "/hub/auth/local/login", json={"email": "a@example.com", "password": "nope"}
    )
    assert res.json() == {"error": "wrong email or password"}
    assert "hd_session" not in client.cookies


def test_sign_in_sets_an_httponly_session_and_never_returns_the_jwt(make_hub) -> None:
    client = make_hub()
    res = client.post(
        "/hub/auth/local/login",
        json={"email": "alice@example.com", "password": "right"},
    )
    body = res.json()
    assert body == {
        "signed_in": True,
        "account": {"id": "acc-alice", "handle": "alice", "display_name": "Alice"},
    }
    assert "jwt-" not in res.text
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("hd_session=")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/hub/me").json()["account"]["handle"] == "alice"


def test_session_cookie_is_secure_by_default(make_hub) -> None:
    client = make_hub(cookie_secure=True)
    res = client.post(
        "/hub/auth/local/login", json={"email": "a@example.com", "password": "right"}
    )
    assert "Secure" in res.headers["set-cookie"]


def test_logout_ends_the_session(make_hub) -> None:
    client = make_hub()
    sign_in(client)
    sid = client.cookies.get("hd_session")
    client.post("/hub/logout")
    client.cookies.set("hd_session", sid)  # replaying the old cookie
    assert client.get("/hub/me").status_code == 401


def test_invite_only_refuses_unlisted_accounts(make_hub) -> None:
    client = make_hub(allowed_accounts=("bob",))
    res = client.post(
        "/hub/auth/local/login",
        json={"email": "alice@example.com", "password": "right"},
    )
    assert "invite-only" in res.json()["error"]
    assert client.get("/hub/me").status_code == 401
    ok = client.post(
        "/hub/auth/local/login", json={"email": "bob@example.com", "password": "right"}
    )
    assert ok.json()["signed_in"] is True


def test_proxy_adds_identity_and_drops_the_session_cookie(make_hub, upstream) -> None:
    client = make_hub()
    sign_in(client)
    res = client.post("/api/echo?x=1", content=b"a" * 70_000)
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "POST"
    assert body["query"] == "x=1"
    assert body["body_len"] == 70_000
    assert body["cookie"] is None
    assert json.loads(body["user"]) == {
        "id": "acc-alice",
        "handle": "alice",
        "display_name": "Alice",
    }
    # The instance was handed the user's games session once it came up.
    assert upstream.seen["games_session"] == {
        "token": "jwt-alice@example.com",
        "account": {"id": "acc-alice", "handle": "alice", "display_name": "Alice"},
    }


def test_proxy_streams_and_strips_instance_cookies(make_hub) -> None:
    client = make_hub()
    sign_in(client)
    sid = client.cookies.get("hd_session")
    res = client.get("/api/stream")
    assert res.text == "".join(f"chunk{i}\n" for i in range(5))
    assert "set-cookie" not in res.headers
    assert client.cookies.get("hd_session") == sid


def test_hub_to_instance_control_is_not_reachable_from_the_browser(make_hub) -> None:
    client = make_hub()
    sign_in(client)
    res = client.put("/api/hosted/games-session", json={"token": "x", "account": {}})
    assert res.status_code == 404


def test_ws_proxies_frames_both_ways(make_hub) -> None:
    client = make_hub()
    sign_in(client)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["event"] == "hello"
        assert json.loads(hello["user"])["id"] == "acc-alice"
        ws.send_text("ping")
        assert ws.receive_text() == "echo:ping"
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_bytes() == b"echo:\x00\x01"


def test_ws_rejects_a_foreign_origin(make_hub) -> None:
    client = make_hub()
    sign_in(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/ws", headers={"origin": "https://evil.example"}
        ) as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_ws_accepts_same_origin_and_allowlisted_origins(make_hub) -> None:
    client = make_hub(allowed_origins=("https://dash.vercel.app",))
    sign_in(client)
    for origin in ("http://testserver", "https://dash.vercel.app"):
        with client.websocket_connect("/ws", headers={"origin": origin}) as ws:
            assert ws.receive_json()["event"] == "hello"


def test_ws_ticket_is_single_use(make_hub) -> None:
    client = make_hub(allowed_origins=("https://dash.vercel.app",))
    sign_in(client)
    ticket = client.post("/hub/ws-ticket").json()["ticket"]
    client.cookies.clear()  # a cross-origin page has no hub cookie on the upgrade
    headers = {"origin": "https://dash.vercel.app"}
    with client.websocket_connect(f"/ws?ticket={ticket}", headers=headers) as ws:
        assert ws.receive_json()["event"] == "hello"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws?ticket={ticket}", headers=headers) as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_two_users_get_separate_sessions(make_hub) -> None:
    # One TestClient (its event loop owns the hub's HTTP client), two browsers'
    # worth of cookies swapped in and out.
    client = make_hub()
    sign_in(client, "alice@example.com")
    alice = client.cookies.get("hd_session")
    client.cookies.clear()
    sign_in(client, "bob@example.com")
    bob = client.cookies.get("hd_session")
    assert alice != bob
    assert client.get("/hub/me").json()["account"]["id"] == "acc-bob"
    client.cookies.clear()
    client.cookies.set("hd_session", alice)
    assert client.get("/hub/me").json()["account"]["id"] == "acc-alice"
    assert json.loads(client.get("/api/echo").json()["user"])["id"] == "acc-alice"


def test_idle_users_excludes_open_sockets(tmp_path) -> None:
    class NoSpawner:
        async def ensure(self, user): ...
        async def stop(self, user_id): ...
        async def close(self): ...

    from backend.hub.store import HubStore

    hub = Hub(
        HubConfig(db_path=tmp_path / "h.db", idle_minutes=1),
        HubStore(tmp_path / "h.db"),
        NoSpawner(),
    )
    hub.touch("idle")
    hub.touch("busy")
    hub.open_socket("busy")
    later = hub._last_seen["idle"] + 120
    assert hub.idle_users(now=later) == ["idle"]
    hub.close_socket("busy")
    assert sorted(hub.idle_users(now=hub._last_seen["busy"] + 120)) == ["busy", "idle"]


def test_spa_index_is_revalidated_and_assets_are_served(make_hub, tmp_path) -> None:
    """Found running a real deploy: a cached index.html kept the previous build's
    bundle running after the frontend was rebuilt."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>hd</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    client = make_hub(web_dist=dist)
    for path in ("/", "/some/client/route"):
        res = client.get(path)
        assert res.status_code == 200
        assert "<title>hd</title>" in res.text
        assert res.headers["cache-control"] == "no-cache"
    assert client.get("/assets/app-abc123.js").text == "console.log(1)"
    # API paths are never swallowed by the SPA fallback.
    assert client.get("/api/anything").status_code == 401


def test_a_failing_spawner_reads_as_starting_not_500(make_hub, monkeypatch) -> None:
    client = make_hub()
    sign_in(client)
    hub = client.app.state.hub

    async def boom(user):
        raise RuntimeError("412 unable to start machine from current state")

    # Let the post-sign-in warm-up finish (it would re-cache the instance behind
    # our back), then forget what it cached so the request reaches the spawner.
    import time

    deadline = time.monotonic() + 10
    while hub._tasks and time.monotonic() < deadline:
        time.sleep(0.05)
    monkeypatch.setattr(hub.spawner, "ensure", boom)
    hub.mark_unready("acc-alice")
    res = client.get("/api/echo")
    assert res.status_code == 503
    assert res.json() == {"starting": True}


def test_ws_to_a_booting_instance_is_refused_promptly(make_hub, monkeypatch) -> None:
    """Fly's proxy drops an upgrade unanswered for ~10 s; holding one longer left
    dead upgrades to be accepted all at once when the instance came up."""
    import backend.hub.app as hub_app

    assert hub_app.WS_READY_SECONDS < 10
    client = make_hub()
    sign_in(client)
    hub = client.app.state.hub

    async def never_ready(user, instance, timeout):
        assert timeout == hub_app.WS_READY_SECONDS
        return False

    monkeypatch.setattr(hub, "wait_ready", never_ready)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert exc.value.code == 1013


def test_an_existing_session_loses_access_when_not_on_the_list(make_hub) -> None:
    """Going invite-only must cut off sessions minted before it, not wait out
    their 30-day cookies."""
    client = make_hub(allowed_accounts=("bob",))
    store = client.app.state.hub.store
    # Alice signed in while the hub was open to everyone.
    alice = store.upsert_user({"id": "acc-alice", "handle": "alice"}, "jwt")
    client.cookies.set("hd_session", store.create_session(alice.id, 3600))
    ticket = store.create_ticket(alice.id)
    assert client.get("/hub/me").status_code == 401
    assert client.get("/api/echo").status_code == 401
    assert client.post("/hub/ws-ticket").status_code == 401
    for path in ("/ws", f"/ws?ticket={ticket}"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(path) as ws:
                ws.receive_json()
        assert exc.value.code == 4401
    # Matching by id works as well as by handle.
    open_by_id = make_hub(allowed_accounts=("acc-alice",))
    sign_in(open_by_id)
    assert open_by_id.get("/hub/me").json()["account"]["id"] == "acc-alice"
