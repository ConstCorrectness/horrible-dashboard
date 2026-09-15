"""Standalone **share relay**: WHIP ingest, SFU fan-out, WHEP playback, viewer page.

Run separately from a node's own backend:

    uv sync --extra webrtc
    uv run uvicorn backend.share_relay.app:app --port 9100

This is the public half of the `share` module. A host node mints a link, pushes
its already-encoded screen capture in over **WHIP** (one HTTP POST of an SDP
offer -- the standard every broadcaster speaks), and anyone holding the URL pulls
it back out over **WHEP**. Nothing here authenticates a *person*: a public viewer
holds a short watch code and gets pixels, and never a grant.

**The code watches; the token publishes.** Viewer-facing routes (`/<code>`,
`/s/<code>`, `/whep/<code>`, `/chat/<code>`) take the code, and only WHIP takes
the token. Never let one stand in for the other -- see `tokens.py` for the hijack
that sharing them allowed. That is precisely what keeps
this service a dumb pipe rather than something security-critical -- see
docs/architecture/share-relay.mdx.

Two deployment facts that are easy to get wrong:

- **A stream is sticky to one machine.** The registry and the rooms live in
  process memory, so a viewer whose WHEP request lands on a different machine
  than the host's WHIP finds nothing. On Fly that is `fly-replay` keyed on the
  token; the header is emitted here so the routing rule sits next to its reason.
- **Minting is gated, watching is not.** `SHARE_RELAY_KEY` (env only, never a
  setting) is required to *create* a stream, so a stranger cannot use the relay
  as free video hosting. Leaving it unset makes minting open, which is fine on a
  laptop and wrong for anything public -- so the app says so at boot.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.share_relay import viewer
from backend.share_relay.chat import Chat
from backend.share_relay.chat import parse as parse_chat
from backend.share_relay import ice
from backend.share_relay.fanout import Rooms
from backend.share_relay.restream import Restreams, ffmpeg_available
from backend.share_relay.ratelimit import MissLimiter, client_ip
from backend.share_relay.tokens import (
    DEFAULT_MAX_VIEWERS,
    Registry,
    Stream,
    normalize_code,
)

logger = logging.getLogger(__name__)


def _max_viewers() -> int:
    """The per-stream viewer ceiling, overridable per deployment.

    An env var rather than a constant because the right number is a property of
    the *machine*, not of this code: it is set by how many software encodes the
    box can carry at the host's capture resolution. A garbage value falls back to
    the default rather than raising — a relay that refuses to boot because
    somebody typed `SHARE_RELAY_MAX_VIEWERS=lots` is worse than one that serves
    six.
    """
    raw = os.environ.get("SHARE_RELAY_MAX_VIEWERS", "").strip()
    if not raw:
        return DEFAULT_MAX_VIEWERS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "SHARE_RELAY_MAX_VIEWERS=%r is not a number; using %d",
            raw,
            DEFAULT_MAX_VIEWERS,
        )
        return DEFAULT_MAX_VIEWERS
    if value < 1:
        # Zero would refuse every viewer, which reads as "the link is broken"
        # rather than as a deliberate setting. Say so instead of serving it.
        logger.warning("SHARE_RELAY_MAX_VIEWERS=%d is below 1; using 1", value)
        return 1
    return value


registry = Registry(max_viewers_per_stream=_max_viewers())
rooms = Rooms()
chat = Chat()
restreams = Restreams()
limiter = MissLimiter()


def _public_base() -> str:
    """The origin viewers reach this relay on, for building link URLs."""
    configured = os.environ.get("SHARE_RELAY_PUBLIC_URL", "").rstrip("/")
    return configured or "http://localhost:9100"


def _ingest_key() -> str:
    return os.environ.get("SHARE_RELAY_KEY", "")


def require_ingest_key(x_relay_key: str = Header(default="")) -> None:
    """Gate stream creation. A no-op when no key is configured."""
    key = _ingest_key()
    if not key:
        return
    if x_relay_key != key:
        raise HTTPException(status_code=401, detail="bad or missing relay key")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not _ingest_key():
        logger.warning(
            "SHARE_RELAY_KEY is unset: anyone who can reach this relay can mint a "
            "stream on it. Set it before exposing this service publicly."
        )
    if not ice.stun_server():
        logger.warning(
            "No STUN configured (SHARE_RELAY_STUN): this relay will gather host "
            "candidates only, which are private addresses on a hosting platform. "
            "Signaling will succeed and no media will flow."
        )
    if ice.turn_is_incomplete():
        logger.warning(
            "SHARE_RELAY_TURN_URL is set without SHARE_RELAY_TURN_USER/PASS, so "
            "TURN is being ignored. Viewers behind a symmetric NAT will not connect."
        )
    yield
    await restreams.stop_all()
    await rooms.close_all()


app = FastAPI(title="horrible-dashboard share relay", lifespan=lifespan)

# Every caller is cross-origin, by construction. The host's browser lives on its
# own node's origin -- a different one for every user, and often a bare LAN IP --
# and the viewer page is the only thing ever served from the relay itself. There
# is no allowlist that could be written here, so the answer is `*`.
#
# That is not a hole, because **the origin was never the credential**: the token
# is, and it is unguessable and revocable. CORS protects a browser from a page
# using *its* ambient authority (cookies, sessions) against another site; this
# relay has no ambient authority to borrow -- no cookies, no sessions, and
# `allow_credentials` stays off so none can be sent. Getting this wrong is not
# subtle in one direction and invisible in the other: without it the host's WHIP
# POST is blocked by the browser and the public link silently carries no video.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    # The two custom headers this API actually reads. A preflight that does not
    # name them fails, and a browser reports that as a generic network error.
    allow_headers=["Content-Type", "X-Share-Passphrase", "X-Relay-Key"],
    # WHIP hands the publisher a resource URL in `Location`; a cross-origin
    # response hides every header not named here.
    expose_headers=["Location"],
)


class MintIn(BaseModel):
    title: str = ""
    ttl_s: int | None = None
    passphrase: str = ""


class MintOut(BaseModel):
    #: Publish authority. Returned only to the minting node (this route is
    #: key-gated) and never part of anything a viewer is handed.
    token: str
    #: Watch authority: what every viewer-facing URL carries.
    code: str
    #: The URL a human opens -- `/<code>`. This is what the host copies and sends.
    short_url: str
    #: The long-form equivalent, `/s/<code>`.
    view_url: str
    #: Where the host POSTs its WHIP offer.
    ingest_url: str
    expires_at: float
    has_passphrase: bool


class StatusOut(BaseModel):
    token: str
    live: bool
    viewers: int
    expires_at: float
    has_passphrase: bool
    title: str = ""


class RestreamIn(BaseModel):
    #: The full `rtmp://host/app/KEY`. Carries a stream key, so this route is
    #: gated on the relay key and the value is never logged or echoed back.
    target: str
    #: A safe name for logs and status ("Twitch"). Never the URL.
    label: str = "RTMP"


class RestreamOut(BaseModel):
    live: bool
    label: str = ""
    error: str | None = None


class RelayInfo(BaseModel):
    service: str = "horrible-share"
    streams: int = 0
    rooms: int = 0
    restreams: int = 0
    #: Whether this relay can restream at all. Reported so a node can say
    #: "this relay has no ffmpeg" instead of offering a button that always fails.
    can_restream: bool = False
    #: Whether minting needs a key. Reported plainly so an operator can see at a
    #: glance that they left it open.
    gated: bool = False
    max_viewers_per_stream: int = 0
    #: STUN/TURN presence, never the credential. Reported because a relay that
    #: gathers only host candidates answers every WHIP offer perfectly and then
    #: carries no media -- so "is ICE configured" has to be answerable without
    #: reproducing the failure.
    ice: dict = {}


@app.get("/health", response_model=RelayInfo)
async def health() -> RelayInfo:
    return RelayInfo(
        streams=len(registry),
        rooms=len(rooms),
        restreams=len(restreams),
        can_restream=ffmpeg_available(),
        gated=bool(_ingest_key()),
        max_viewers_per_stream=registry.max_viewers_per_stream,
        ice=ice.describe(),
    )


@app.post(
    "/streams", response_model=MintOut, dependencies=[Depends(require_ingest_key)]
)
async def mint(body: MintIn) -> MintOut:
    """Mint a link. Called by a host node, never by a browser."""
    registry.sweep()
    stream = registry.mint(
        title=body.title, ttl_s=body.ttl_s, passphrase=body.passphrase
    )
    base = _public_base()
    return MintOut(
        token=stream.token,
        code=stream.code,
        short_url=f"{base}/{stream.code}",
        view_url=f"{base}/s/{stream.code}",
        ingest_url=f"{base}/whip/{stream.token}",
        expires_at=stream.expires_at,
        has_passphrase=bool(stream.passphrase_hash),
    )


def _ip(request: Request | WebSocket) -> str:
    return client_ip(request.headers, request.client.host if request.client else None)


def _resolve_code(raw: str, ip: str) -> Stream:
    """The stream a viewer-facing route names, charging a wrong guess to `ip`."""
    if limiter.blocked(ip):
        raise HTTPException(status_code=429, detail="too many attempts; slow down")
    stream = registry.get_by_code(raw)
    if stream is None:
        if normalize_code(raw) is not None:
            limiter.miss(ip)
        raise HTTPException(status_code=404, detail="no such stream")
    return stream


def _resolve(token: str) -> Stream:
    stream = registry.get(token)
    if stream is None:
        # One answer for unknown, revoked and expired -- see `Registry.get`.
        raise HTTPException(status_code=404, detail="no such stream")
    return stream


@app.get(
    "/streams/{token}",
    response_model=StatusOut,
    dependencies=[Depends(require_ingest_key)],
)
async def status(token: str) -> StatusOut:
    stream = _resolve(token)
    room = rooms.get(token)
    return StatusOut(
        token=token,
        live=bool(room and room.live),
        viewers=room.viewers if room else 0,
        expires_at=stream.expires_at,
        has_passphrase=bool(stream.passphrase_hash),
        title=stream.title,
    )


@app.delete("/streams/{token}", dependencies=[Depends(require_ingest_key)])
async def revoke(token: str) -> dict[str, bool]:
    """Kill a link and drop everyone watching it. Idempotent."""
    was_live = registry.revoke(token)
    await restreams.stop(token)
    await rooms.drop(token)
    return {"revoked": was_live}


def _sdp_response(
    answer: str, token: str, extra: dict[str, str] | None = None
) -> Response:
    """A WHIP/WHEP answer, built rather than decorated.

    Built explicitly because assigning `Content-Type` onto FastAPI's injected
    `Response` **appends** to whatever the response class already set, yielding
    `text/plain; charset=utf-8, application/sdp` -- malformed, and rejected by any
    client that actually checks the type it was handed.

    No stickiness header is emitted, and that is deliberate rather than an
    omission. An earlier version set `fly-replay-src`, which does nothing: that
    is the header Fly *adds to* a request it has already replayed, not one an app
    sends to steer routing. Steering requires returning `fly-replay:
    instance=<machine>`, which this app cannot do yet because a token does not
    record which machine minted it. Until it does the app runs on **one machine**
    (see fly.share.toml) -- and a wrong header that looks like it pins traffic is
    worse than none, because it invites exactly the multi-machine deploy that
    silently breaks.
    """
    headers: dict[str, str] = {}
    headers.update(extra or {})
    return Response(
        content=answer, media_type="application/sdp", status_code=201, headers=headers
    )


async def _sdp_body(request: Request) -> str:
    body = (await request.body()).decode("utf-8", "replace").strip()
    if not body:
        raise HTTPException(status_code=400, detail="empty SDP")
    return body


@app.post("/whip/{token}")
async def whip(token: str, request: Request) -> Response:
    """Host -> relay. The body is an SDP offer; the reply is the answer.

    Deliberately **not** gated on the relay key. The token is the credential
    here: it was minted by a node that held the key, and requiring the key again
    would mean shipping it to the host's *browser*, which is the one place it
    must never be.

    Addressed by the token and **never** the watch code. `Room.publish` replaces
    whoever is publishing, so a route a viewer can address is a route a viewer
    can use to swap the host's screen for their own video -- which is exactly
    what the viewer URL allowed while it carried this token.
    """
    stream = _resolve(token)
    offer = await _sdp_body(request)
    room = rooms.get_or_create(token)
    answer = await room.publish(offer, ice.ice_servers())
    stream.live = True
    return _sdp_response(answer, token, extra={"Location": f"/whip/{token}"})


@app.delete("/whip/{token}")
async def whip_stop(token: str) -> dict[str, bool]:
    """Host stops sending. The link stays valid -- re-publishing reuses it."""
    stream = registry.get(token)
    if stream is not None:
        stream.live = False
    # The restream is fed from this room's track; leaving it running would push a
    # dead track to a platform that shows it as a frozen frame rather than an end.
    await restreams.stop(token)
    await rooms.drop(token)
    return {"stopped": True}


@app.post("/whep/{code}")
async def whep(
    code: str,
    request: Request,
    x_share_passphrase: str = Header(default=""),
) -> Response:
    """Viewer -> relay. The body is an SDP offer; the reply is the answer.

    A wrong passphrase is charged as a miss too. It is a guess like any other,
    and each check is a PBKDF2 run, so an unlimited stream of them is also a
    cheap way to spend the relay's CPU.
    """
    ip = _ip(request)
    stream = _resolve_code(code, ip)
    token = stream.token
    if not stream.check_passphrase(x_share_passphrase):
        limiter.miss(ip)
        raise HTTPException(status_code=403, detail="passphrase required")
    room = rooms.get(token)
    if room is None or not room.live:
        # 409 rather than 404: the link is real and the stream simply has not
        # started. The viewer page polls on this, and answering "no such stream"
        # would make a host who minted a link a minute early look broken.
        raise HTTPException(status_code=409, detail="stream is not live yet")
    if registry.at_capacity(stream):
        raise HTTPException(status_code=503, detail="this stream is full")

    offer = await _sdp_body(request)
    answer = await room.subscribe(offer, ice.ice_servers())
    stream.viewers = room.viewers
    return _sdp_response(answer, token)


def _viewer_response(raw: str, ip: str) -> HTMLResponse:
    """The page a stranger opens. Self-contained: no CDN, no build step.

    A dead code still renders a page rather than a bare 404, because the person
    holding a stale link needs a sentence explaining it, not a status code.
    """
    if limiter.blocked(ip):
        return HTMLResponse(viewer.render_limited(), status_code=429)
    stream = registry.get_by_code(raw)
    if stream is None and normalize_code(raw) is not None:
        limiter.miss(ip)
    room = rooms.get(stream.token) if stream else None
    html = viewer.render(
        code=stream.code if stream else "",
        title=stream.title if stream else "",
        found=stream is not None,
        needs_passphrase=bool(stream and stream.passphrase_hash),
        live=bool(room and room.live),
    )
    return HTMLResponse(html)


@app.get("/s/{code}", response_class=HTMLResponse)
async def viewer_page(code: str, request: Request) -> HTMLResponse:
    return _viewer_response(code, _ip(request))


@app.post(
    "/restream/{token}",
    response_model=RestreamOut,
    dependencies=[Depends(require_ingest_key)],
)
async def start_restream(token: str, body: RestreamIn) -> RestreamOut:
    """Begin pushing a live stream out to RTMP.

    Gated on the relay key rather than the token, unlike WHIP. The token is the
    credential for *publishing to* this relay; starting an outbound broadcast on
    somebody else's behalf is an operator action, and the body carries a stream
    key.
    """
    _resolve(token)
    room = rooms.get(token)
    if room is None or not room.live:
        raise HTTPException(status_code=409, detail="stream is not live yet")
    track = room.proxy("video")
    if track is None:
        raise HTTPException(status_code=409, detail="this stream carries no video")
    try:
        push = await restreams.start(token, track, body.target, body.label)
    except RuntimeError as exc:
        # ffmpeg missing. A plain 503 with the reason, not a 500: the relay is
        # working, it simply cannot do this one thing.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RestreamOut(live=push.live, label=push.label, error=push.error)


@app.get(
    "/restream/{token}",
    response_model=RestreamOut,
    dependencies=[Depends(require_ingest_key)],
)
async def restream_status(token: str) -> RestreamOut:
    push = restreams.get(token)
    if push is None:
        return RestreamOut(live=False)
    return RestreamOut(live=push.live, label=push.label, error=push.error)


@app.delete("/restream/{token}", dependencies=[Depends(require_ingest_key)])
async def stop_restream(token: str) -> dict[str, bool]:
    return {"stopped": await restreams.stop(token)}


@app.websocket("/chat/{code}")
async def chat_ws(ws: WebSocket, code: str) -> None:
    """Viewer chat for one stream.

    Accepted for any *usable* code, including one whose stream has not started:
    people open a link early and talk while they wait, and closing the socket on
    them would look like the link was broken. A dead code is closed with a code
    rather than left hanging, so the page can say so.
    """
    ip = _ip(ws)
    if limiter.blocked(ip):
        await ws.close(code=4429)
        return
    stream = registry.get_by_code(code)
    if stream is None:
        if normalize_code(code) is not None:
            limiter.miss(ip)
        await ws.close(code=4404)
        return
    token = stream.token
    await ws.accept()
    room = chat.room(token)
    await room.join(ws)
    try:
        while True:
            raw = await ws.receive_text()
            if not room.allowed(ws):
                continue
            message = parse_chat(raw)
            if message is None:
                continue
            await room.broadcast({"kind": "chat", **message})
    except WebSocketDisconnect:
        pass
    finally:
        await room.leave(ws)
        if not room.sockets:
            chat.drop(token)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(viewer.render_index())


# Registered LAST, and that is load-bearing: a bare `/{code}` would otherwise
# claim `/health` and every other single-segment route declared after it.
@app.get("/{code}", response_class=HTMLResponse)
async def short_link(code: str, request: Request) -> HTMLResponse:
    """The short link itself. Serves the viewer page in place, no redirect, so the
    URL a viewer sees stays the one they were given."""
    if normalize_code(code) is None:
        # Not a code at all (`/favicon.ico`, a probe). Not a guess, so not charged.
        raise HTTPException(status_code=404, detail="not found")
    return _viewer_response(code, _ip(request))
