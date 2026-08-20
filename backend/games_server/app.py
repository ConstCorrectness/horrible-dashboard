"""Standalone game-server app. Run separately from a node's own backend:

    uv run uvicorn backend.games_server.app:app --port 9200

A node connects one authenticated socket per account to `/game-ws` and speaks the
`{"type": ...}` protocol in `models.py`. The `GameHub` is process-global so every
connection shares the same lobby of tables.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from backend.games_engine.base import list_games
from backend.games_server import auth, crypto, store
from backend.games_server.hub import GameHub

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    store.init_db()  # accounts + ratings + results + task bank
    from backend.games_server import task_bank

    task_bank.ensure_builtin()  # load the bundled bug-hunt starter set
    hub.town.start_loop()  # AgentTown's world clock (tick cadence from env)
    hub.matchmaker.start_loop()  # ranked queue sweep
    yield
    hub.matchmaker.stop_loop()
    hub.town.stop_loop()


app = FastAPI(title="horrible-dashboard game server", lifespan=_lifespan)

# One lobby shared by every connection to this process. (AgentTown's tick cadence
# is env-tunable via TOWN_TICK_SECONDS — see town.py.)
hub = GameHub()


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "games": [g.id for g in list_games()]}


@app.get("/games")
def games() -> dict[str, object]:
    return {
        "games": [
            {
                "id": g.id,
                "name": g.name,
                "min_players": g.min_players,
                "max_players": g.max_players,
            }
            for g in list_games()
        ]
    }


@app.get("/leaderboard")
def leaderboard(game_id: str = "tictactoe", limit: int = 50) -> dict[str, object]:
    return {"game_id": game_id, "entries": store.leaderboard(game_id, limit)}


@app.get("/challenges/leaderboard")
def challenge_leaderboard(
    game_id: str = "tictactoe", limit: int = 50
) -> dict[str, object]:
    return {"game_id": game_id, "entries": store.challenge_leaderboard(game_id, limit)}


# ---- replays ----------------------------------------------------------------


def _viewer(authorization: str | None) -> str | None:
    """Resolve an optional `Authorization: Bearer <token>` to an account id (JWT or
    dev token — same resolution as `/game-ws` auth)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    resolved = auth.resolve_token(authorization[7:].strip())
    return resolved["account_id"] if resolved else None


@app.get("/replays")
def replays_index(
    game_id: str | None = None,
    scope: str = "public",
    limit: int = 50,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Replay summaries: `scope=public` is the public replay browser; `scope=mine`
    lists the caller's own matches (participants always see theirs)."""
    if scope == "mine":
        viewer = _viewer(authorization)
        if viewer is None:
            return {"replays": [], "error": "sign in required"}
        entries = store.list_replays(
            game_id=game_id, account_id=viewer, limit=min(limit, 200)
        )
    else:
        entries = store.list_replays(
            game_id=game_id, public_only=True, limit=min(limit, 200)
        )
    return {"replays": entries}


@app.get("/replays/{replay_id}")
def replay_get(
    replay_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """One replay with its full event log — participants always; others only once
    published. Not-found and not-allowed are indistinguishable on purpose."""
    replay = store.get_replay(replay_id, viewer=_viewer(authorization))
    if replay is None:
        return {"error": "replay not found"}
    return {"replay": replay}


@app.post("/replays/{replay_id}/publish")
def replay_publish(
    replay_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Open a replay up to the public browser. Participants only."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    if not store.publish_replay(replay_id, viewer):
        return {"error": "replay not found"}
    return {"ok": True}


class _DevicePoll(BaseModel):
    device_code: str


@app.get("/auth/providers")
async def auth_providers() -> dict[str, Any]:
    """Which OAuth providers/flows this server is configured for
    (`{provider: {device, web}}`) — lets a client grey out a sign-in button with an
    explanation instead of opening a popup that immediately fails."""
    return auth.providers_available()


@app.post("/auth/github/start")
async def github_start() -> dict[str, Any]:
    """Begin GitHub device-flow sign-in. Returns the user_code + verification_uri the
    player enters at github.com."""
    try:
        return await auth.github_device_start()
    except ValueError as exc:
        return {"error": str(exc)}


@app.post("/auth/github/poll")
async def github_poll(body: _DevicePoll) -> dict[str, Any]:
    """Poll once for the token. `{pending: true}` until authorized, then `{token, account}`."""
    try:
        return await auth.github_device_poll(body.device_code)
    except Exception as exc:  # network / provider error — report, don't crash
        logger.warning("github poll failed: %s", exc)
        return {"error": str(exc)}


@app.post("/auth/google/start")
async def google_start() -> dict[str, Any]:
    """Begin Google device-flow sign-in (code entered at google.com/device)."""
    try:
        return await auth.google_device_start()
    except ValueError as exc:
        return {"error": str(exc)}


@app.post("/auth/google/poll")
async def google_poll(body: _DevicePoll) -> dict[str, Any]:
    """Poll once for the token. `{pending: true}` until authorized, then `{token, account}`."""
    try:
        return await auth.google_device_poll(body.device_code)
    except Exception as exc:  # network / provider error — report, don't crash
        logger.warning("google poll failed: %s", exc)
        return {"error": str(exc)}


# ---- local (email + password) sign-in ---------------------------------------
#
# These MUST stay above `/auth/{provider}/web/start` below: that route's `provider`
# is a path parameter, FastAPI matches in declaration order, and a `/auth/local/...`
# request would otherwise be swallowed by it. Same hazard the node's routes.py
# documents for its own `/auth/{provider}` block.
#
# Neither body is ever recorded by the observability panel — `/auth/local` is in
# `_REDACT_BODY_PREFIXES` (backend/modules/telemetry/instrument.py), because the
# request carries a plaintext password and the response carries a fresh JWT.


class _LocalSignup(BaseModel):
    email: str
    password: str
    username: str = ""


class _LocalLogin(BaseModel):
    email: str
    password: str


class _SetHandle(BaseModel):
    handle: str


class _BindPerson(BaseModel):
    person_id: str
    person_public_key: str
    sig: str


@app.post("/auth/local/signup")
async def local_signup(body: _LocalSignup) -> dict[str, Any]:
    """Create an email+password account. `{token, account}` on success, `{error}`
    with a user-facing message otherwise."""
    try:
        return auth.signup_local(body.email, body.password, body.username)
    except ValueError as exc:
        return {"error": str(exc)}


@app.post("/auth/local/login")
async def local_login(body: _LocalLogin) -> dict[str, Any]:
    """Check an email+password. The failure message is identical for an unknown
    address and a wrong password — on purpose, so this can't confirm who has an
    account here."""
    try:
        return auth.login_local(body.email, body.password)
    except ValueError as exc:
        return {"error": str(exc)}


@app.get("/me")
async def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """The bearer's account, read fresh. The node calls this to learn a handle its
    stored token predates, and to pick up a username changed on another machine."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    return {"account": auth.account_payload(viewer)}


@app.post("/account/handle")
async def set_handle_route(
    body: _SetHandle, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Claim or rename the caller's username — the globally unique `handle` the
    ladder and HorribleAssault both display."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    outcome = auth.set_account_handle(viewer, body.handle)
    if outcome == "taken":
        return {"error": "that username is taken"}
    if outcome == "invalid":
        return {"error": "a username is 3-20 characters of a-z, 0-9, - or _"}
    return {"ok": True, "account": auth.account_payload(viewer)}


@app.post("/account/person")
async def bind_person_route(
    body: _BindPerson, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Bind the caller's account to their peer-fabric **person** identity.

    This is what makes `@handle` a way to reach someone: the game server is the
    only uniqueness authority every node agrees on, so it is where the mapping
    handle → person_id has to live.

    The bearer token proves *account*; the signature proves *person*. Both are
    required — a bearer alone would let anyone claim to be any person, and a
    signature alone would let anyone bind a person to any account. The signed
    challenge includes the account id so a signature can't be replayed from
    elsewhere onto a different account.
    """
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    if store.fingerprint_person(body.person_public_key) != body.person_id:
        return {"error": "person_id does not match the public key"}
    challenge = store.person_challenge(viewer, body.person_id)
    if not crypto.verify(body.person_public_key, challenge, body.sig):
        return {"error": "signature did not verify"}
    outcome = store.bind_person(viewer, body.person_id, body.person_public_key)
    if outcome == "taken":
        return {"error": "that identity is already bound to another account"}
    if outcome != "ok":
        return {"error": outcome}
    return {"ok": True, "account": auth.account_payload(viewer)}


@app.get("/directory/resolve")
async def directory_resolve(handle: str) -> dict[str, Any]:
    """`@handle` → the public directory entry, so a node can add them as a friend.

    Unauthenticated on purpose: a handle is already public (it is on the ladder),
    and requiring sign-in to look one up would mean you cannot be found by someone
    who has not signed in yet — which is exactly the person trying to add you.
    """
    entry = store.account_by_handle(handle)
    return {"entry": entry} if entry else {"error": "no such username"}


@app.get("/directory/search")
async def directory_search(q: str, limit: int = 10) -> dict[str, Any]:
    """Prefix-search usernames. Short queries return nothing rather than everyone."""
    return {
        "results": store.search_handles(q, limit),
        "min_prefix": store.MIN_SEARCH_PREFIX,
    }


class _PersonLookup(BaseModel):
    person_ids: list[str] = []


# ---- profiles: the part other people can see -------------------------------
#
# Your *own* profile still rides the `/game-ws` `profile_get`/`profile_set` frames —
# it is live state you already hold a socket for. Everyone else's is HTTP, and that
# split is the point: a profile you can only read over a game socket is a profile
# you can only read while playing, which is why nobody could see anyone's bio.


class _ProfilePatch(BaseModel):
    """Every field optional and patch-style: absent means "leave it alone".

    Clearing artwork is an explicit empty string, never a null — otherwise "don't
    touch my background" and "remove my background" would be the same request.
    """

    avatar: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    background_url: str | None = None
    background_id: str | None = None
    status_text: str | None = None
    showcase: list[dict[str, Any]] | None = None


class _CommentBody(BaseModel):
    body: str = ""


class _CardLookup(BaseModel):
    handles: list[str] = []


@app.post("/profiles/cards")
async def profile_cards_route(body: _CardLookup) -> dict[str, Any]:
    """Avatar, level and status for many people at once — what a *list* needs.

    POST rather than GET for the same two reasons as `/directory/people`: a
    roster's worth of names is too long for a query string, and who someone's
    friends are does not belong in a request log.

    Declared before `/profile/{handle}` is irrelevant here (different prefix), but
    it is still batched-only: there is deliberately no way to walk it.
    """
    return {"cards": store.profile_cards(body.handles)}


@app.get("/profile/{handle}")
async def get_profile_route(handle: str) -> dict[str, Any]:
    """Somebody else's profile, by username. Unauthenticated — a profile is public,
    the same way the ladder that shows their rating is."""
    profile = store.profile_by_handle(handle)
    if profile is None:
        return {"error": "no such player"}
    return {"profile": profile}


@app.post("/profile")
async def patch_profile_route(
    body: _ProfilePatch, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Update the caller's own profile."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    return {
        "profile": store.upsert_profile(
            viewer,
            avatar=body.avatar,
            bio=body.bio,
            avatar_url=body.avatar_url,
            background_url=body.background_url,
            background_id=body.background_id,
            status_text=body.status_text,
            showcase=body.showcase,
        )
    }


@app.post("/profile/media")
async def upload_media_route(
    request: Request,
    kind: str = "avatar",
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Upload a profile image. Body is the raw bytes; `Content-Type` declares the
    format and is checked against the bytes themselves.

    Raw body rather than multipart because there is exactly one file and no other
    fields — multipart would add a parser (and a dependency) to carry nothing.
    """
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    if kind not in ("avatar", "background"):
        return {"error": "kind must be 'avatar' or 'background'"}
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    # Read with the cap in hand: an unbounded `await request.body()` would buffer
    # whatever a caller sends before we ever get to reject it.
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > store.MEDIA_MAX_BYTES:
            return {"error": f"image is larger than {store.MEDIA_MAX_BYTES // 1024} KB"}
    return store.store_media(viewer, kind, mime, data)


@app.get("/media/{sha}")
async def get_media_route(sha: str) -> Response:
    """Serve a stored image. Content-addressed, so it is immutable and cacheable
    forever — a profile view costs one request the first time and none after."""
    found = store.get_media(sha)
    if found is None:
        return Response(status_code=404)
    data, mime = found
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=31536000, immutable", "ETag": sha},
    )


@app.get("/profile/{handle}/comments")
async def list_comments_route(
    handle: str, before: float | None = None
) -> dict[str, Any]:
    """A profile's comment wall, newest first."""
    profile = store.profile_by_handle(handle)
    if profile is None:
        return {"error": "no such player"}
    return {
        "comments": store.list_comments(profile["account_id"], before=before),
        "page": store.COMMENT_PAGE,
    }


@app.post("/profile/{handle}/comments")
async def add_comment_route(
    handle: str, body: _CommentBody, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Leave a comment on someone's wall. Signing in is required — an anonymous
    wall post is a spam vector with no upside."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    profile = store.profile_by_handle(handle)
    if profile is None:
        return {"error": "no such player"}
    comment = store.add_comment(profile["account_id"], viewer, body.body)
    if comment is None:
        return {"error": "comment was empty"}
    return {"comment": comment}


@app.delete("/profile/comments/{comment_id}")
async def hide_comment_route(
    comment_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Hide a comment. The wall's owner or the comment's author may; nobody else."""
    viewer = _viewer(authorization)
    if viewer is None:
        return {"error": "sign in required"}
    if not store.hide_comment(comment_id, viewer):
        return {"error": "not yours to remove"}
    return {"ok": True}


@app.post("/directory/people")
async def directory_people(body: _PersonLookup) -> dict[str, Any]:
    """Fabric `person_id`s → their ladder accounts, for the ones that have one.

    POST rather than GET despite being a read: a roster's worth of person ids is
    both too long for a query string and exactly the kind of thing that should not
    sit in server logs and browser history. Nothing here is secret — it is the same
    public slice `/directory/resolve` serves — but a list of *who someone's friends
    are* is a different disclosure from any single entry in it, and URLs leak by
    default.

    Unauthenticated for the same reason `/directory/resolve` is: the entries are
    public, and the caller is a node reconciling its own roster.
    """
    found = store.accounts_by_person(list(body.person_ids or []))
    return {"people": found, "max": store.MAX_PERSON_LOOKUP}


# ---- web (authorization-code) sign-in --------------------------------------
#
# The redirect flow behind the browser "Sign in" button. A pending login carries
# two secrets, mirroring the device flow: `login_id` is public (it rides in the
# browser URL as the OAuth `state`), while `retrieval_code` is private — only the
# node ever holds it, and only it can pull the minted JWT. So even someone who sees
# the browser URL cannot lift the token.

_WEB_LOGIN_TTL_S = 900  # 15 min, matching the device-code lifetime


class _WebLogin:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.login_id = secrets.token_urlsafe(16)
        self.retrieval_code = secrets.token_urlsafe(32)
        self.created_at = time.time()
        # {token, account} on success, {error} on failure, None until the callback.
        self.result: dict[str, Any] | None = None


# login_id -> entry. Process-global (the server is single-process per machine).
_web_logins: dict[str, _WebLogin] = {}


def _purge_web_logins() -> None:
    now = time.time()
    for lid in [
        k for k, v in _web_logins.items() if now - v.created_at > _WEB_LOGIN_TTL_S
    ]:
        _web_logins.pop(lid, None)


def _public_base(request: Request) -> str:
    """The externally-reachable base URL, used to build the OAuth redirect_uri so it
    matches the provider's registered callback. `GAMES_PUBLIC_URL` pins it; otherwise
    we derive it from the request and force https off localhost (Fly terminates TLS,
    so the inbound request scheme can read as http)."""
    env = os.environ.get("GAMES_PUBLIC_URL")
    if env:
        return env.rstrip("/")
    base = str(request.base_url).rstrip("/")
    if "localhost" not in base and "127.0.0.1" not in base:
        base = base.replace("http://", "https://", 1)
    return base


def _web_login_page(message: str) -> HTMLResponse:
    """The tiny page the popup lands on; it reports status and closes itself."""
    safe = message.replace("<", "&lt;").replace(">", "&gt;")
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Sign-in</title></head>"
        "<body style='font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        f"<div style='text-align:center;max-width:24rem;padding:1rem'><p style='font-size:1.05rem'>{safe}</p>"
        "<p style='color:#8b949e;font-size:0.85rem'>You can close this tab.</p></div>"
        "<script>setTimeout(function(){window.close();},1500);</script>"
        "</body></html>"
    )
    return HTMLResponse(html)


@app.post("/auth/{provider}/web/start")
async def web_start(provider: str, request: Request) -> dict[str, Any]:
    """Begin the redirect flow. Returns the `login_url` to open in the browser and the
    private `retrieval_code` the node polls with."""
    if provider not in ("github", "google"):
        return {"error": f"unknown provider {provider!r}"}
    cfg_error = auth.web_config_error(provider)
    if cfg_error:
        return {"error": cfg_error}
    _purge_web_logins()
    entry = _WebLogin(provider)
    _web_logins[entry.login_id] = entry
    login_url = f"{_public_base(request)}/auth/{provider}/login?lid={entry.login_id}"
    return {
        "login_url": login_url,
        "retrieval_code": entry.retrieval_code,
        "expires_in": _WEB_LOGIN_TTL_S,
    }


@app.get("/auth/{provider}/login")
async def web_login(provider: str, lid: str, request: Request) -> Any:
    """Redirect the browser on to the provider's consent page (302). `lid` is the
    pending login's public id, replayed as the OAuth `state`."""
    entry = _web_logins.get(lid)
    if entry is None or entry.provider != provider:
        return _web_login_page("This sign-in link is invalid or has expired.")
    redirect_uri = f"{_public_base(request)}/auth/{provider}/callback"
    try:
        url = auth.web_authorize_url(provider, entry.login_id, redirect_uri)
    except ValueError as exc:
        return _web_login_page(str(exc))
    return RedirectResponse(url, status_code=302)


@app.get("/auth/{provider}/callback")
async def web_callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> HTMLResponse:
    """The provider's redirect target: exchange the code and stash the result under the
    pending login (keyed by `state`) for the node to retrieve."""
    entry = _web_logins.get(state)
    if entry is None or entry.provider != provider:
        return _web_login_page("Sign-in session not found or expired.")
    if error:
        entry.result = {"error": error}
        return _web_login_page(f"Sign-in was cancelled ({error}).")
    redirect_uri = f"{_public_base(request)}/auth/{provider}/callback"
    try:
        entry.result = await auth.web_exchange(provider, code, redirect_uri)
        name = (entry.result.get("account") or {}).get("display_name") or "you"
        return _web_login_page(f"Signed in as {name}.")
    except Exception as exc:
        logger.warning("web callback exchange failed: %s", exc)
        entry.result = {"error": str(exc)}
        return _web_login_page("Sign-in failed. Return to the app and try again.")


class _WebPoll(BaseModel):
    retrieval_code: str


@app.post("/auth/{provider}/web/poll")
async def web_poll(provider: str, body: _WebPoll) -> dict[str, Any]:
    """The node polls here with its private `retrieval_code`. `{pending: true}` until the
    callback lands, then `{token, account}` (consumed once) or `{error}`."""
    _purge_web_logins()
    for lid, entry in list(_web_logins.items()):
        if entry.provider == provider and secrets.compare_digest(
            entry.retrieval_code, body.retrieval_code
        ):
            if entry.result is None:
                return {"pending": True}
            _web_logins.pop(lid, None)
            return entry.result
    return {"error": "sign-in session not found or expired"}


@app.get("/hassault/maps")
def hassault_maps() -> dict[str, Any]:
    """Maps a rated match can be played on.

    Bundled only, and that is the point rather than a shortfall: a map that exists
    on one player's disk cannot be adjudicated by anybody else.
    """
    from backend.games_server import hassault_rooms

    return {"maps": hassault_rooms.referee.maps()}


@app.websocket("/hassault-ws")
async def hassault_ws(websocket: WebSocket) -> None:
    """A rated HorribleAssault match, simulated **here**.

    The same `MatchServer` the node runs, on a machine no player controls — which
    is what makes the result worth recording. See `hassault_rooms` for why this is
    the trust boundary and storage never was.

    The wire is the node's own `hassault` channel envelope
    (`{channel, event, data}`), so a client speaks one protocol whether the room
    is on its own node or here. **Identity is not on it**: the account comes from
    the token in the query string, exactly as `/game-ws` takes it, and a `name`
    in the join payload is ignored the same way `channel.py` ignores it.
    """
    from backend.games_server import hassault_rooms

    token = websocket.query_params.get("token", "")
    session = auth.resolve_token(token)
    if session is None:
        # Closed before `accept` where possible: an unauthenticated socket should
        # never reach the room registry, and a 1008 is a reason rather than a
        # silent drop.
        await websocket.close(code=1008, reason="sign in to play a rated match")
        return

    await websocket.accept()
    conn = hassault_rooms.SeatConn(
        websocket, session["account_id"], session["display_name"]
    )
    referee = hassault_rooms.referee
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("channel") != "hassault":
                continue
            event = str(msg.get("event") or "")
            data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
            if event == "join":
                try:
                    welcome = await referee.join(
                        conn,
                        str(data.get("map") or ""),
                        str(data.get("room") or "") or None,
                    )
                except (ValueError, LookupError) as exc:
                    await conn.send_json(
                        {
                            "channel": "hassault",
                            "event": "error",
                            "data": {"message": str(exc), "code": "join_refused"},
                        }
                    )
                    continue
                await conn.send_json(
                    {"channel": "hassault", "event": "welcome", "data": welcome}
                )
            elif event == "input":
                referee.apply_input(conn, data)
            elif event == "respawn":
                entry = referee.server.player_for(conn)
                if entry is not None:
                    room, player = entry
                    # The *room* decides — it holds the respawn clock. A client
                    # asking is a request, exactly as it is on a node.
                    room.respawn(player)
            elif event == "leave":
                result = await referee.leave(conn)
                if result is not None:
                    # Sent back before the socket goes: this is how the player's
                    # node learns what happened, and it is the *server's* account
                    # of it — the node records it under `authority="server"`
                    # because it was told, not because it worked anything out.
                    await conn.send_json(
                        {"channel": "hassault", "event": "result", "data": result}
                    )
    except WebSocketDisconnect:
        pass
    finally:
        conn.closed = True
        # Recorded here too: a player who closed the game is a player whose
        # session ended, and the common case is a disconnect rather than a
        # polite `leave`.
        try:
            await referee.leave(conn)
        except Exception:
            logger.exception("hassault: leaving on disconnect failed")


@app.websocket("/game-ws")
async def game_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    session = hub.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                await hub.handle(session, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(session)
