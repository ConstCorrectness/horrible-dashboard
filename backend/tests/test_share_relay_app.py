"""The relay's HTTP surface, exercised with real aiortc peers.

The SDP exchange is done for real -- a publisher peer connection offers, the
relay answers, a viewer peer connection offers, the relay answers again -- because
the failure this file exists to catch is the one where the answer comes back
syntactically fine and carries no media. Asserting on a mocked SDP string would
prove only that the mock was returned.

ICE is deliberately *not* driven to completion. Two peers inside one process on a
CI box do not reliably connect, and media flow is not what these routes are
responsible for: they are responsible for pairing an offer with an answer and for
saying no to the right people.

Every stream has two keys: a short **code** (watch) and a long **token**
(publish). Viewer-facing routes take the code, WHIP takes the token, and the
tests below that matter most are the ones proving neither stands in for the
other.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.share_relay import app as relay_app
from backend.share_relay.ratelimit import DEFAULT_MISS_LIMIT, MissLimiter
from backend.share_relay.tokens import DEFAULT_MAX_VIEWERS, Registry

#: Every test here drives a real peer connection, so the whole module needs the
#: optional `webrtc` extra (`uv sync --extra webrtc`). Skipping is the honest
#: outcome without it: a hard ImportError here reads as "the relay is broken" on
#: a machine that simply never installed the media stack, and that misreading
#: cost a real investigation.
pytest.importorskip("aiortc", reason="needs the `webrtc` extra")


@pytest.fixture(autouse=True)
def clean_relay(monkeypatch):
    """A fresh registry, room table and limiter per test -- all process globals."""
    monkeypatch.setattr(relay_app, "registry", Registry())
    monkeypatch.setattr(relay_app, "limiter", MissLimiter())
    monkeypatch.setenv("SHARE_RELAY_PUBLIC_URL", "https://share.example.com")
    monkeypatch.delenv("SHARE_RELAY_KEY", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=relay_app.app)
    async with AsyncClient(transport=transport, base_url="http://relay") as c:
        yield c


async def mint(client, **body) -> dict:
    res = await client.post("/streams", json=body)
    assert res.status_code == 200
    return res.json()


async def publisher_offer():
    """A real WHIP offer carrying one video track."""
    from aiortc import RTCPeerConnection
    from aiortc.mediastreams import VideoStreamTrack

    pc = RTCPeerConnection()
    pc.addTrack(VideoStreamTrack())
    await pc.setLocalDescription(await pc.createOffer())
    return pc, pc.localDescription.sdp


async def viewer_offer():
    """A real WHEP offer that asks to receive."""
    from aiortc import RTCPeerConnection

    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    await pc.setLocalDescription(await pc.createOffer())
    return pc, pc.localDescription.sdp


@pytest.mark.anyio
async def test_mint_returns_short_and_long_urls_on_the_public_origin(client) -> None:
    body = await mint(client, title="Standup")
    assert body["short_url"] == f"https://share.example.com/{body['code']}"
    assert body["view_url"] == f"https://share.example.com/s/{body['code']}"
    assert body["ingest_url"].endswith(f"/whip/{body['token']}")
    assert len(body["code"]) == 8
    assert body["has_passphrase"] is False


@pytest.mark.anyio
async def test_no_viewer_facing_url_carries_the_token(client) -> None:
    body = await mint(client)
    assert body["token"] not in body["short_url"]
    assert body["token"] not in body["view_url"]


@pytest.mark.anyio
async def test_minting_is_gated_when_a_key_is_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_KEY", "s3cret")
    assert (await client.post("/streams", json={})).status_code == 401
    ok = await client.post("/streams", json={}, headers={"X-Relay-Key": "s3cret"})
    assert ok.status_code == 200


@pytest.mark.anyio
async def test_whip_then_whep_pairs_offers_with_answers(client) -> None:
    body = await mint(client)
    token, code = body["token"], body["code"]

    pub, offer = await publisher_offer()
    try:
        res = await client.post(f"/whip/{token}", content=offer)
        assert res.status_code == 201
        assert res.text.startswith("v=")
        assert res.headers["content-type"].startswith("application/sdp")
        # No `fly-replay-src`: that header is what Fly adds to a request it has
        # replayed, not something an app sends to steer routing. Emitting it
        # looked like session affinity and provided none -- which invited a
        # multi-machine deploy where a viewer's WHEP lands on a machine that has
        # never heard of the token.
        assert "fly-replay-src" not in res.headers

        status = (await client.get(f"/streams/{token}")).json()
        assert status["live"] is True

        view, view_sdp = await viewer_offer()
        try:
            played = await client.post(f"/whep/{code}", content=view_sdp)
            assert played.status_code == 201
            # The answer must actually carry the video the viewer asked for.
            assert "m=video" in played.text
        finally:
            await view.close()
    finally:
        await pub.close()
        await relay_app.rooms.drop(token)


# --- the two keys are not interchangeable -------------------------------------


@pytest.mark.anyio
async def test_a_viewer_cannot_publish_with_the_code_from_their_link(client) -> None:
    """THE regression. The viewer URL used to carry the publish token, and
    `Room.publish` replaces the current publisher -- so anyone watching could push
    their own video into the host's stream. The code must open nothing on WHIP."""
    code = (await mint(client))["code"]
    pub, offer = await publisher_offer()
    try:
        assert (await client.post(f"/whip/{code}", content=offer)).status_code == 404
    finally:
        await pub.close()


@pytest.mark.anyio
async def test_the_viewer_page_does_not_contain_the_token(client) -> None:
    body = await mint(client, title="Standup")
    for path in (f"/{body['code']}", f"/s/{body['code']}"):
        page = await client.get(path)
        assert page.status_code == 200
        assert body["token"] not in page.text
        assert body["code"] in page.text


@pytest.mark.anyio
async def test_the_token_does_not_open_viewer_routes(client) -> None:
    token = (await mint(client))["token"]
    view, sdp = await viewer_offer()
    try:
        assert (await client.post(f"/whep/{token}", content=sdp)).status_code == 404
    finally:
        await view.close()
    page = await client.get(f"/s/{token}")
    assert '"found": false' in page.text


# --- the short link ------------------------------------------------------------


@pytest.mark.anyio
async def test_the_short_link_serves_the_viewer_page_in_place(client) -> None:
    code = (await mint(client, title="Standup"))["code"]
    page = await client.get(f"/{code}")
    assert page.status_code == 200
    assert "Standup" in page.text
    assert '"found": true' in page.text


@pytest.mark.anyio
async def test_a_code_survives_being_typed_by_a_person(client) -> None:
    # Upper case, a hyphen, and the Crockford look-alikes: a code is read aloud
    # and copied off QR codes, so each of these is the same link.
    code = (await mint(client, title="Standup"))["code"]
    typed = code.upper()[:4] + "-" + code.upper()[4:]
    page = await client.get(f"/{typed}")
    assert '"found": true' in page.text


@pytest.mark.anyio
async def test_the_short_route_does_not_shadow_the_others(client) -> None:
    assert (await client.get("/health")).json()["service"] == "horrible-share"
    assert (await client.get("/")).status_code == 200


@pytest.mark.anyio
async def test_non_codes_are_404_and_not_charged_as_guesses(client) -> None:
    # A browser fetches /favicon.ico beside every page; charging that as a miss
    # would lock out a viewer who merely reloaded.
    for _ in range(DEFAULT_MISS_LIMIT + 5):
        assert (await client.get("/favicon.ico")).status_code == 404
    code = (await mint(client))["code"]
    assert (await client.get(f"/{code}")).status_code == 200


# --- guessing is slow ----------------------------------------------------------


@pytest.mark.anyio
async def test_too_many_wrong_codes_are_refused(client) -> None:
    code = (await mint(client))["code"]
    for _ in range(DEFAULT_MISS_LIMIT):
        page = await client.get("/zzzzzzzz")
        assert page.status_code == 200
        assert '"found": false' in page.text
    assert (await client.get("/zzzzzzzz")).status_code == 429
    # Blocked means blocked -- including the real code, or the limit is a filter
    # a guesser simply walks past once they land on a hit.
    assert (await client.get(f"/{code}")).status_code == 429
    view, sdp = await viewer_offer()
    try:
        assert (await client.post(f"/whep/{code}", content=sdp)).status_code == 429
    finally:
        await view.close()


@pytest.mark.anyio
async def test_wrong_passphrases_count_as_misses(client) -> None:
    code = (await mint(client, passphrase="open sesame"))["code"]
    view, sdp = await viewer_offer()
    try:
        for _ in range(DEFAULT_MISS_LIMIT):
            res = await client.post(
                f"/whep/{code}", content=sdp, headers={"X-Share-Passphrase": "nope"}
            )
            assert res.status_code == 403
        res = await client.post(
            f"/whep/{code}", content=sdp, headers={"X-Share-Passphrase": "open sesame"}
        )
        assert res.status_code == 429
    finally:
        await view.close()


def test_fly_client_ip_is_trusted_only_on_fly(monkeypatch) -> None:
    from backend.share_relay.ratelimit import client_ip

    headers = {"fly-client-ip": "203.0.113.9"}
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    # Off Fly it is a header the caller wrote -- a fresh "address" per request.
    assert client_ip(headers, "10.0.0.1") == "10.0.0.1"
    monkeypatch.setenv("FLY_APP_NAME", "horrible-share")
    assert client_ip(headers, "10.0.0.1") == "203.0.113.9"


# --- the rest of the surface ---------------------------------------------------


@pytest.mark.anyio
async def test_watching_before_the_host_starts_is_409_not_404(client) -> None:
    # A host mints a link and sends it, then starts sharing a minute later. If
    # that window answered "no such stream" the host would look broken.
    code = (await mint(client))["code"]
    view, sdp = await viewer_offer()
    try:
        res = await client.post(f"/whep/{code}", content=sdp)
        assert res.status_code == 409
    finally:
        await view.close()


@pytest.mark.anyio
async def test_a_passphrase_is_required_before_any_sdp_is_processed(client) -> None:
    body = await mint(client, passphrase="open sesame")
    token, code = body["token"], body["code"]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        view, sdp = await viewer_offer()
        try:
            denied = await client.post(f"/whep/{code}", content=sdp)
            assert denied.status_code == 403

            allowed = await client.post(
                f"/whep/{code}",
                content=sdp,
                headers={"X-Share-Passphrase": "open sesame"},
            )
            assert allowed.status_code == 201
        finally:
            await view.close()
    finally:
        await pub.close()
        await relay_app.rooms.drop(token)


@pytest.mark.anyio
async def test_revoking_kills_the_link_immediately(client) -> None:
    body = await mint(client)
    token, code = body["token"], body["code"]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        assert (await client.delete(f"/streams/{token}")).json()["revoked"] is True

        view, sdp = await viewer_offer()
        try:
            assert (await client.post(f"/whep/{code}", content=sdp)).status_code == 404
        finally:
            await view.close()
        assert (await client.get(f"/streams/{token}")).status_code == 404
        # The short link dies with it, not at expiry.
        assert '"found": false' in (await client.get(f"/{code}")).text
    finally:
        await pub.close()


@pytest.mark.anyio
async def test_a_full_stream_is_refused_rather_than_degraded(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(relay_app.registry, "max_viewers_per_stream", 0)
    body = await mint(client)
    token, code = body["token"], body["code"]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        view, sdp = await viewer_offer()
        try:
            assert (await client.post(f"/whep/{code}", content=sdp)).status_code == 503
        finally:
            await view.close()
    finally:
        await pub.close()
        await relay_app.rooms.drop(token)


@pytest.mark.anyio
async def test_an_empty_body_is_rejected_before_aiortc_sees_it(client) -> None:
    token = (await mint(client))["token"]
    assert (await client.post(f"/whip/{token}", content="")).status_code == 400


@pytest.mark.anyio
async def test_the_viewer_page_escapes_the_host_supplied_title(client) -> None:
    code = (await mint(client, title="<img src=x onerror=alert(1)>"))["code"]
    page = await client.get(f"/s/{code}")
    assert page.status_code == 200
    assert "<img src=x" not in page.text
    assert "&lt;img src=x" in page.text


@pytest.mark.anyio
async def test_a_dead_link_still_renders_an_explanation(client) -> None:
    # A status code is not an answer for the person holding a stale URL.
    page = await client.get("/s/not-a-real-token")
    assert page.status_code == 200
    assert '"found": false' in page.text.replace("'", '"')


@pytest.mark.anyio
async def test_the_index_names_no_streams(client) -> None:
    body = await mint(client, title="Secret standup")
    index = (await client.get("/")).text
    assert body["token"] not in index
    assert body["code"] not in index
    assert "Secret standup" not in index


@pytest.mark.anyio
async def test_health_reports_whether_minting_is_open(client, monkeypatch) -> None:
    assert (await client.get("/health")).json()["gated"] is False
    monkeypatch.setenv("SHARE_RELAY_KEY", "k")
    assert (await client.get("/health")).json()["gated"] is True


@pytest.mark.anyio
async def test_whip_answers_a_cross_origin_preflight(client) -> None:
    """The host's browser is always on another origin, so WHIP must survive CORS.

    Caught live rather than here first: an ASGI test client does not enforce CORS,
    so every route test passed while a real browser's preflight was refused and
    the public link carried no video with only a console message to say so.
    """
    token = (await mint(client))["token"]
    res = await client.options(
        f"/whip/{token}",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code in (200, 204)
    assert res.headers["access-control-allow-origin"] == "*"


@pytest.mark.anyio
async def test_the_passphrase_header_survives_preflight(client) -> None:
    # A preflight that does not name the header fails, and the browser reports it
    # as an indistinguishable network error.
    code = (await mint(client, passphrase="p"))["code"]
    res = await client.options(
        f"/whep/{code}",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-share-passphrase",
        },
    )
    assert res.status_code in (200, 204)
    allowed = res.headers.get("access-control-allow-headers", "").lower()
    assert "x-share-passphrase" in allowed


@pytest.mark.anyio
async def test_restreaming_needs_a_live_stream(client) -> None:
    # A link with nothing published yet: 409, the same distinction WHEP makes
    # between "no such stream" and "not started".
    token = (await mint(client))["token"]
    res = await client.post(
        f"/restream/{token}", json={"target": "rtmp://x/app/k", "label": "Twitch"}
    )
    assert res.status_code == 409


@pytest.mark.anyio
async def test_restreaming_is_gated_on_the_relay_key_not_the_token(
    client, monkeypatch
) -> None:
    """Unlike WHIP. The token is the credential for publishing *to* this relay;
    starting an outbound broadcast is an operator action and the body carries a
    stream key."""
    monkeypatch.setenv("SHARE_RELAY_KEY", "s3cret")
    token = (
        await client.post("/streams", json={}, headers={"X-Relay-Key": "s3cret"})
    ).json()["token"]
    res = await client.post(
        f"/restream/{token}", json={"target": "rtmp://x/app/k", "label": "Twitch"}
    )
    assert res.status_code == 401


@pytest.mark.anyio
async def test_a_relay_without_ffmpeg_says_so_rather_than_500ing(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(relay_app.restreams, "start", _raise_no_ffmpeg)
    token = (await mint(client))["token"]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        res = await client.post(
            f"/restream/{token}", json={"target": "rtmp://x/app/k", "label": "Twitch"}
        )
        assert res.status_code == 503
        assert "ffmpeg" in res.json()["detail"]
    finally:
        await pub.close()
        await relay_app.rooms.drop(token)


async def _raise_no_ffmpeg(*_a, **_k):
    raise RuntimeError("ffmpeg is not on PATH. The relay needs it to restream to RTMP.")


@pytest.mark.anyio
async def test_health_reports_whether_this_relay_can_restream(client) -> None:
    # So a node can say "this relay has no ffmpeg" instead of offering a button
    # that always fails.
    body = (await client.get("/health")).json()
    assert "can_restream" in body


@pytest.mark.anyio
async def test_revoking_stops_the_restream_too(client, monkeypatch) -> None:
    """Otherwise a revoked link keeps broadcasting -- the link dies and the
    stream carries on to Twitch, which is the worst possible reading of 'stop'."""
    stopped: list[str] = []

    async def fake_stop(token):
        stopped.append(token)
        return True

    monkeypatch.setattr(relay_app.restreams, "stop", fake_stop)
    token = (await mint(client))["token"]
    await client.delete(f"/streams/{token}")
    assert stopped == [token]


def test_max_viewers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ceiling is a deployment knob, and a bad value never stops the boot.

    A relay that refuses to start because somebody typed a word into an env var
    would take down every link on it to protect a number that has a perfectly
    good default.
    """
    monkeypatch.delenv("SHARE_RELAY_MAX_VIEWERS", raising=False)
    assert relay_app._max_viewers() == DEFAULT_MAX_VIEWERS

    monkeypatch.setenv("SHARE_RELAY_MAX_VIEWERS", "12")
    assert relay_app._max_viewers() == 12

    monkeypatch.setenv("SHARE_RELAY_MAX_VIEWERS", "lots")
    assert relay_app._max_viewers() == DEFAULT_MAX_VIEWERS

    # Zero would refuse every viewer, which reads as a broken link rather than a
    # setting -- so it is floored, not honoured.
    monkeypatch.setenv("SHARE_RELAY_MAX_VIEWERS", "0")
    assert relay_app._max_viewers() == 1
