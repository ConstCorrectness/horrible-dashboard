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
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.share_relay import app as relay_app
from backend.share_relay.tokens import Registry


@pytest.fixture(autouse=True)
def clean_relay(monkeypatch):
    """A fresh registry and room table per test -- both are process globals."""
    monkeypatch.setattr(relay_app, "registry", Registry())
    monkeypatch.setenv("SHARE_RELAY_PUBLIC_URL", "https://share.example.com")
    monkeypatch.delenv("SHARE_RELAY_KEY", raising=False)
    yield


@pytest.fixture
async def client():
    transport = ASGITransport(app=relay_app.app)
    async with AsyncClient(transport=transport, base_url="http://relay") as c:
        yield c


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
async def test_mint_returns_a_view_url_on_the_public_origin(client) -> None:
    res = await client.post("/streams", json={"title": "Standup"})
    assert res.status_code == 200
    body = res.json()
    assert body["view_url"] == f"https://share.example.com/s/{body['token']}"
    assert body["ingest_url"].endswith(f"/whip/{body['token']}")
    assert body["has_passphrase"] is False


@pytest.mark.anyio
async def test_minting_is_gated_when_a_key_is_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("SHARE_RELAY_KEY", "s3cret")
    assert (await client.post("/streams", json={})).status_code == 401
    ok = await client.post("/streams", json={}, headers={"X-Relay-Key": "s3cret"})
    assert ok.status_code == 200


@pytest.mark.anyio
async def test_whip_then_whep_pairs_offers_with_answers(client) -> None:
    token = (await client.post("/streams", json={})).json()["token"]

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
            played = await client.post(f"/whep/{token}", content=view_sdp)
            assert played.status_code == 201
            # The answer must actually carry the video the viewer asked for.
            assert "m=video" in played.text
        finally:
            await view.close()
    finally:
        await pub.close()
        await relay_app.rooms.drop(token)


@pytest.mark.anyio
async def test_watching_before_the_host_starts_is_409_not_404(client) -> None:
    # A host mints a link and sends it, then starts sharing a minute later. If
    # that window answered "no such stream" the host would look broken.
    token = (await client.post("/streams", json={})).json()["token"]
    view, sdp = await viewer_offer()
    try:
        res = await client.post(f"/whep/{token}", content=sdp)
        assert res.status_code == 409
    finally:
        await view.close()


@pytest.mark.anyio
async def test_a_passphrase_is_required_before_any_sdp_is_processed(client) -> None:
    token = (await client.post("/streams", json={"passphrase": "open sesame"})).json()[
        "token"
    ]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        view, sdp = await viewer_offer()
        try:
            denied = await client.post(f"/whep/{token}", content=sdp)
            assert denied.status_code == 403

            allowed = await client.post(
                f"/whep/{token}",
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
    token = (await client.post("/streams", json={})).json()["token"]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        assert (await client.delete(f"/streams/{token}")).json()["revoked"] is True

        view, sdp = await viewer_offer()
        try:
            assert (await client.post(f"/whep/{token}", content=sdp)).status_code == 404
        finally:
            await view.close()
        assert (await client.get(f"/streams/{token}")).status_code == 404
    finally:
        await pub.close()


@pytest.mark.anyio
async def test_a_full_stream_is_refused_rather_than_degraded(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(relay_app.registry, "max_viewers_per_stream", 0)
    token = (await client.post("/streams", json={})).json()["token"]
    pub, offer = await publisher_offer()
    try:
        await client.post(f"/whip/{token}", content=offer)
        view, sdp = await viewer_offer()
        try:
            assert (await client.post(f"/whep/{token}", content=sdp)).status_code == 503
        finally:
            await view.close()
    finally:
        await pub.close()
        await relay_app.rooms.drop(token)


@pytest.mark.anyio
async def test_an_empty_body_is_rejected_before_aiortc_sees_it(client) -> None:
    token = (await client.post("/streams", json={})).json()["token"]
    assert (await client.post(f"/whip/{token}", content="")).status_code == 400


@pytest.mark.anyio
async def test_the_viewer_page_escapes_the_host_supplied_title(client) -> None:
    token = (
        await client.post("/streams", json={"title": "<img src=x onerror=alert(1)>"})
    ).json()["token"]
    page = await client.get(f"/s/{token}")
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
    token = (await client.post("/streams", json={"title": "Secret standup"})).json()[
        "token"
    ]
    body = (await client.get("/")).text
    assert token not in body
    assert "Secret standup" not in body


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
    token = (await client.post("/streams", json={})).json()["token"]
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
    token = (await client.post("/streams", json={"passphrase": "p"})).json()["token"]
    res = await client.options(
        f"/whep/{token}",
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
    token = (await client.post("/streams", json={})).json()["token"]
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
    from backend.share_relay import app as relay_module

    monkeypatch.setattr(relay_module.restreams, "start", _raise_no_ffmpeg)
    token = (await client.post("/streams", json={})).json()["token"]
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

    from backend.share_relay import app as relay_module

    monkeypatch.setattr(relay_module.restreams, "stop", fake_stop)
    token = (await client.post("/streams", json={})).json()["token"]
    await client.delete(f"/streams/{token}")
    assert stopped == [token]
