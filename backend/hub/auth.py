"""Sign-in: the hub's accounts are the game server's accounts.

The flows are the ones a node already relays (`backend/modules/games/server_auth.py`)
— email + password, GitHub/Google device codes, and the web redirect — with one
difference: a node keeps the minted JWT as *the machine's* identity, while the hub
turns it into a **browser session** for that one person. The JWT stays here (it is
handed on to that user's instance, never to the browser); the browser gets an
HttpOnly cookie naming an opaque session.

Passwords pass through on their way to the game server and are never stored or
logged — the hub installs no body-capturing telemetry.
"""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

if TYPE_CHECKING:
    from backend.hub.app import Hub
    from backend.hub.config import HubConfig

router = APIRouter(prefix="/hub/auth", tags=["hub-auth"])

SESSION_COOKIE = "hd_session"
PROVIDERS = ("github", "google")
INVITE_ONLY_ERROR = (
    "This dashboard is invite-only, and your account is not on the list — ask "
    "whoever runs it to add you."
)


def _hub(request: Request) -> "Hub":
    return request.app.state.hub


async def _games(
    hub: "Hub", method: str, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One call to the game server, flattened to the `{error}` shape the sign-in UI
    already renders rather than raised."""
    url = f"{hub.config.games_http_base}{path}"
    try:
        res = await hub.http.request(method, url, json=body, timeout=20.0)
    except httpx.HTTPError:
        return {"error": f"account server unreachable at {hub.config.games_http_base}"}
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code >= 400:
        if isinstance(data, dict) and data.get("error"):
            return {"error": str(data["error"])}
        if isinstance(data, dict) and data.get("detail"):
            return {"error": f"account server: {data['detail']}"}
        return {"error": f"account server returned {res.status_code}"}
    return data if isinstance(data, dict) else {"error": "unexpected response"}


def account_allowed(config: "HubConfig", account: dict[str, Any]) -> bool:
    """Whether an account passes ``HUB_ALLOWED_ACCOUNTS`` (by id or handle); with no
    list set, every account does."""
    allow = config.allowed_accounts
    if not allow:
        return True
    return str(account.get("id") or "") in allow or (
        account.get("handle") is not None and str(account.get("handle")) in allow
    )


def _complete(hub: "Hub", data: dict[str, Any], response: Response) -> dict[str, Any]:
    """Turn a game-server `{token, account}` into a hub session; pass anything else
    (`{pending}`, `{error}`, a device-code start) through untouched."""
    token = data.get("token")
    if not token:
        return data
    account = data.get("account") or {}
    if not account_allowed(hub.config, account):
        return {"error": INVITE_ONLY_ERROR}
    user = hub.store.upsert_user(account, str(token))
    ttl = hub.config.session_days * 86400
    sid = hub.store.create_session(user.id, ttl)
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=int(ttl),
        httponly=True,
        secure=hub.config.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Start the user's instance now, while the browser is still rendering the
    # sign-in success — boot time overlaps instead of following.
    hub.warm(user)
    return {"signed_in": True, "account": user.public()}


@router.get("/providers")
async def providers(request: Request) -> dict[str, Any]:
    """Which OAuth flows the account server offers (`{}` when it can't say)."""
    data = await _games(_hub(request), "GET", "/auth/providers")
    return {"flows": {} if data.get("error") else data}


class LocalSignup(BaseModel):
    email: str
    password: str
    username: str = ""


class LocalLogin(BaseModel):
    email: str
    password: str


@router.post("/local/signup")
async def local_signup(
    body: LocalSignup, request: Request, response: Response
) -> dict[str, Any]:
    hub = _hub(request)
    data = await _games(hub, "POST", "/auth/local/signup", body.model_dump())
    return _complete(hub, data, response)


@router.post("/local/login")
async def local_login(
    body: LocalLogin, request: Request, response: Response
) -> dict[str, Any]:
    hub = _hub(request)
    data = await _games(hub, "POST", "/auth/local/login", body.model_dump())
    return _complete(hub, data, response)


# ---- device flow -------------------------------------------------------------


class DevicePoll(BaseModel):
    device_code: str


@router.post("/{provider}/start")
async def device_start(provider: str, request: Request) -> dict[str, Any]:
    if provider not in PROVIDERS:
        return {"error": f"unknown provider {provider!r}"}
    return await _games(_hub(request), "POST", f"/auth/{provider}/start")


@router.post("/{provider}/poll")
async def device_poll(
    provider: str, body: DevicePoll, request: Request, response: Response
) -> dict[str, Any]:
    if provider not in PROVIDERS:
        return {"error": f"unknown provider {provider!r}"}
    hub = _hub(request)
    data = await _games(
        hub, "POST", f"/auth/{provider}/poll", {"device_code": body.device_code}
    )
    return _complete(hub, data, response)


# ---- web (redirect) flow -------------------------------------------------------
#
# The game server's private `retrieval_code` stays here, keyed by a random
# `pending` id the browser polls with — many people sign in at once, so unlike a
# node there is no "the" pending login.


class WebPoll(BaseModel):
    pending: str


@router.post("/{provider}/web/start")
async def web_start(provider: str, request: Request) -> dict[str, Any]:
    if provider not in PROVIDERS:
        return {"error": f"unknown provider {provider!r}"}
    hub = _hub(request)
    data = await _games(hub, "POST", f"/auth/{provider}/web/start")
    if data.get("error") or not data.get("login_url") or not data.get("retrieval_code"):
        return {
            "error": data.get("error")
            or "sign-in unavailable — web OAuth is not configured on the account server"
        }
    hub.prune_pending_web()
    pending = secrets.token_urlsafe(18)
    hub.pending_web[pending] = {
        "provider": provider,
        "retrieval_code": data["retrieval_code"],
        "expires_at": time.time() + float(data.get("expires_in") or 900),
    }
    return {"authorize_url": data["login_url"], "pending": pending}


@router.post("/{provider}/web/poll")
async def web_poll(
    provider: str, body: WebPoll, request: Request, response: Response
) -> dict[str, Any]:
    hub = _hub(request)
    entry = hub.pending_web.get(body.pending)
    if not entry or entry["provider"] != provider:
        return {"error": "no sign-in in progress"}
    if time.time() > entry["expires_at"]:
        hub.pending_web.pop(body.pending, None)
        return {"error": "sign-in timed out"}
    data = await _games(
        hub,
        "POST",
        f"/auth/{provider}/web/poll",
        {"retrieval_code": entry["retrieval_code"]},
    )
    if data.get("pending"):
        return {"pending": True}
    hub.pending_web.pop(body.pending, None)
    if data.get("token"):
        return _complete(hub, data, response)
    return {"error": data.get("error") or "sign-in failed"}
