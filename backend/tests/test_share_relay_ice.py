"""The relay's ICE configuration.

This exists because of a failure with no symptom: a relay that gathers only host
candidates answers every WHIP offer with a perfectly valid SDP, reports the
stream live, and never carries a frame. It works flawlessly on one machine --
where a host candidate is the right answer -- and never in production.
"""

from __future__ import annotations

import re

import pytest

from backend.share_relay import ice


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "SHARE_RELAY_STUN",
        "SHARE_RELAY_TURN_URL",
        "SHARE_RELAY_TURN_USER",
        "SHARE_RELAY_TURN_PASS",
        "SHARE_RELAY_TURN_FOR_VIEWERS",
        "CLOUDFLARE_TURN_KEY_ID",
        "CLOUDFLARE_TURN_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    ice.reset_cache()
    yield
    ice.reset_cache()


def test_stun_is_on_by_default() -> None:
    """The out-of-the-box deploy must gather a reflexive candidate. Defaulting to
    nothing would make "it deployed fine and shows no video" the default."""
    assert ice.stun_server() == ice.DEFAULT_STUN
    assert ice.viewer_ice() == [{"urls": [f"stun:{ice.DEFAULT_STUN}"]}]


def test_the_stun_scheme_is_added_not_expected(monkeypatch) -> None:
    # Same semantics as the frontend's `buildIceConfig` and the node's
    # `_ice_servers`: bare host:port in, `stun:` added here. Three readers of one
    # idea that disagreed about the scheme would fail as "ICE does not connect".
    monkeypatch.setenv("SHARE_RELAY_STUN", "stun.example.com:3478")
    assert ice.viewer_ice()[0]["urls"] == ["stun:stun.example.com:3478"]


def test_stun_can_be_disabled_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_STUN", "")
    assert ice.stun_server() == ""
    assert ice.viewer_ice() == []


def test_turn_needs_every_part(monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    assert ice.turn_is_incomplete() is True

    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    assert ice.turn_is_incomplete() is True

    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")
    assert ice.turn_is_incomplete() is False


def test_viewers_do_not_get_turn_by_default(monkeypatch) -> None:
    """A TURN credential in a page anyone can open makes the operator's bandwidth
    free for the whole internet, so it is opt-in."""
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")

    assert all("turn" not in str(entry) for entry in ice.viewer_ice())

    monkeypatch.setenv("SHARE_RELAY_TURN_FOR_VIEWERS", "1")
    assert any("turn" in str(entry) for entry in ice.viewer_ice())


def test_describe_reports_presence_never_the_credential(monkeypatch) -> None:
    # `/health` is unauthenticated for reads in practice; it must answer "is TURN
    # configured" without answering "what is the password".
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "relayuser")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "hunter2")

    described = ice.describe()
    assert described["turn"] is True
    assert "hunter2" not in str(described)
    assert "relayuser" not in str(described)


def test_ice_servers_builds_real_aiortc_objects(monkeypatch) -> None:
    pytest.importorskip("aiortc", reason="needs the `webrtc` extra")
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")

    servers = ice.ice_servers()
    assert [s.urls for s in servers] == [
        [f"stun:{ice.DEFAULT_STUN}"],
        ["turn:relay.example.com:3478"],
    ]
    assert servers[1].username == "u"


def test_incomplete_turn_is_dropped_rather_than_offered(monkeypatch) -> None:
    # A malformed entry is worse than a missing one: it can take the whole
    # configuration down with it rather than degrading to STUN.
    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:relay.example.com:3478")
    servers = ice.ice_servers()
    assert len(servers) == 1
    assert servers[0].urls == [f"stun:{ice.DEFAULT_STUN}"]


# --- Cloudflare TURN ----------------------------------------------------------
#
# The live relay reported `turn: false`, and Fly routes no inbound UDP to the
# ports aiortc gathers on -- so a STUN-only relay connected or not depending on
# the other end's NAT. These pin the minted-credential path that fixes it.

CF_RESPONSE = {
    "iceServers": [
        {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
        {
            "urls": [
                "turn:turn.cloudflare.com:3478?transport=udp",
                "turn:turn.cloudflare.com:53?transport=udp",
                "turn:turn.cloudflare.com:3478?transport=tcp",
                "turns:turn.cloudflare.com:5349?transport=tcp",
                "turns:turn.cloudflare.com:443?transport=tcp",
            ],
            "username": "minted-user",
            "credential": "minted-secret",
        },
    ]
}


def cloudflare(monkeypatch, handler):
    """Configure Cloudflare TURN and return a client routed to `handler`."""
    import httpx

    monkeypatch.setenv("CLOUDFLARE_TURN_KEY_ID", "key-id")
    monkeypatch.setenv("CLOUDFLARE_TURN_API_TOKEN", "api-token")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.anyio
async def test_cloudflare_credentials_are_minted_and_cached(monkeypatch) -> None:
    import json

    import httpx

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json=CF_RESPONSE)

    async with cloudflare(monkeypatch, handler) as client:
        await ice.refresh(client)
        await ice.refresh(client)

    assert len(calls) == 1, (
        "a fresh credential must be reused, not re-minted per viewer"
    )
    req = calls[0]
    assert str(req.url).endswith("/turn/keys/key-id/credentials/generate-ice-servers")
    assert req.headers["Authorization"] == "Bearer api-token"
    assert json.loads(req.content)["ttl"] == ice.CLOUDFLARE_TTL_S
    assert ice.turn_source() == "cloudflare"


@pytest.mark.anyio
async def test_cloudflare_turn_reaches_viewers_without_the_static_opt_in(
    monkeypatch,
) -> None:
    """It expires, so publishing it in a public page costs a bounded amount --
    unlike a static credential, which still needs SHARE_RELAY_TURN_FOR_VIEWERS."""
    import httpx

    async with cloudflare(
        monkeypatch, lambda r: httpx.Response(201, json=CF_RESPONSE)
    ) as c:
        await ice.refresh(c)

    turn = [s for s in ice.viewer_ice() if "username" in s]
    assert len(turn) == 1
    assert turn[0]["credential"] == "minted-secret"
    # Browsers block port 53; with no trickle channel a candidate there is only
    # a timeout in front of every connection.
    assert not any(re.search(r":53(\?|$)", url) for url in turn[0]["urls"])
    # ...and only port 53: the TLS URL on 5349 must survive the filter.
    assert "turns:turn.cloudflare.com:5349?transport=tcp" in turn[0]["urls"]


@pytest.mark.anyio
async def test_the_relay_itself_uses_udp_turn_first(monkeypatch) -> None:
    pytest.importorskip("aiortc", reason="needs the `webrtc` extra")
    import httpx

    async with cloudflare(
        monkeypatch, lambda r: httpx.Response(201, json=CF_RESPONSE)
    ) as c:
        await ice.refresh(c)

    servers = ice.ice_servers()
    turn = [s for s in servers if s.username]
    # aiortc uses only the first TURN URL it understands, so we choose it.
    assert turn[0].urls == ["turn:turn.cloudflare.com:3478?transport=udp"]


@pytest.mark.anyio
async def test_a_failed_mint_falls_back_and_waits_before_retrying(monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("SHARE_RELAY_TURN_URL", "turn:static.example.com:3478")
    monkeypatch.setenv("SHARE_RELAY_TURN_USER", "u")
    monkeypatch.setenv("SHARE_RELAY_TURN_PASS", "p")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with cloudflare(monkeypatch, handler) as client:
        await ice.refresh(client)
        await ice.refresh(client)

    assert calls == 1, "a failing API must not be hammered once per connection"
    assert ice.turn_source() == "static"


@pytest.mark.anyio
async def test_a_credential_near_expiry_is_reminted(monkeypatch) -> None:
    import httpx

    now = [1000.0]
    monkeypatch.setattr(ice, "_now", lambda: now[0])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(201, json=CF_RESPONSE)

    async with cloudflare(monkeypatch, handler) as client:
        await ice.refresh(client)
        now[0] += ice.CLOUDFLARE_TTL_S - ice.CLOUDFLARE_REFRESH_BELOW_S + 1
        await ice.refresh(client)

    assert calls == 2


@pytest.mark.anyio
async def test_health_names_the_source_never_the_minted_secret(monkeypatch) -> None:
    import httpx

    async with cloudflare(
        monkeypatch, lambda r: httpx.Response(201, json=CF_RESPONSE)
    ) as c:
        await ice.refresh(c)

    described = ice.describe()
    assert described["turn"] is True
    assert described["turn_source"] == "cloudflare"
    assert "minted-secret" not in str(described)
    assert "minted-user" not in str(described)
