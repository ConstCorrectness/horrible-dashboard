"""Node-side sign-in to the central game server.

The node holds the JWT the game server issues after a GitHub or Google sign-in,
persisted server-side (`.data/games_token.json`) and **never returned to the
browser** — the clubhouse/`google_auth.py` token pattern. The node presents it on
`/game-ws`; without it, play falls back to the dev token.

The device flows themselves run on the game server (it has the client ids/secrets);
the node just proxies start/poll so the browser talks to one origin (no CORS), and
captures the issued token when it arrives.

There is a third way in alongside the two OAuth providers: **email + password**
(`local_signup` / `local_login`), which needs no OAuth configuration at all, so a
server with no client ids set can still sign people up. It ends in the same place —
one JWT, held here — and the credential is never recorded (see `_local_auth`).

The account carries a **username**: the game server's globally unique `handle`,
which is what HorribleAssault plays you as. `signed_in_username()` is the gate the
match channel consults; a client-supplied name is never identity.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from backend.modules.games.client import resolve_http_base, resolve_server_url
from backend.modules.settings.routes import get_value
from backend import jsonstore, paths


def _token_path() -> Path:
    return paths.data_dir() / "games_token.json"


def _read() -> dict[str, Any] | None:
    path = _token_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def get_token() -> str | None:
    data = _read()
    return str(data["token"]) if data and data.get("token") else None


def _is_expired(token: str) -> bool:
    """Whether a stored JWT is past its `exp`.

    The signature is deliberately *not* verified: only the game server holds the
    signing secret, and this file is written by nothing but our own sign-in flow,
    so the trust boundary here is the filesystem. All we need is the expiry, and
    reading it locally is what stops the node reporting "signed in" for a token
    the play socket will reject as `invalid token` — a 30-day-old session used to
    look live right up until the moment you tried to play.
    """
    import jwt

    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return True  # unreadable is as good as expired
    exp = claims.get("exp")
    if exp is None:
        return False  # no expiry claim — nothing to have passed
    return time.time() >= float(exp)


def signed_in_account() -> dict[str, Any] | None:
    """The account this node is signed in as (`{id, display_name, handle}`), or None.

    None covers three cases that all mean the same thing to a caller: no token
    file, a malformed one, and an expired session.
    """
    data = _read()
    if not data or not data.get("token"):
        return None
    if _is_expired(str(data["token"])):
        return None
    account = data.get("account") or {}
    account_id = str(account.get("id") or "")
    if not account_id:
        return None
    return {
        "id": account_id,
        "display_name": str(account.get("display_name") or account_id),
        "handle": account.get("handle"),
    }


def signed_in_name() -> str | None:
    account = signed_in_account()
    return account["display_name"] if account else None


def signed_in_username() -> str | None:
    """The account's globally unique username (the game server's `handle`), or None
    when signed out or not yet enlisted. This — never a client-supplied string — is
    who a player is in HorribleAssault."""
    account = signed_in_account()
    handle = account.get("handle") if account else None
    return str(handle) if handle else None


def sign_out() -> None:
    _token_path().unlink(missing_ok=True)


def _http_base() -> str:
    """The game server's HTTP base for sign-in, derived from the same ws:// URL the
    node plays against (resolve_server_url) — see that function on why they must match."""
    return resolve_http_base()


def _unreachable_error() -> dict[str, str]:
    """The `{error}` shape the browser already understands (see signInWithGitHub),
    for when the central game server isn't running — a friendly message beats a 500.

    The port here is **9090**, which is what `scripts/dev.mjs` actually starts the
    bundled server on. This used to say 9200 — the Fly *internal* port from
    fly.toml, which nothing local ever listens on — so following the instruction
    produced a server the node still could not reach.
    """
    return {
        "error": (
            f"game server unreachable at {_http_base()} — start it with "
            "`uv run uvicorn backend.games_server.app:app --port 9090`"
        )
    }


async def _auth_start(provider: str) -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{_http_base()}/auth/{provider}/start")
            res.raise_for_status()
            return res.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _unreachable_error()
    except httpx.HTTPStatusError as exc:
        try:
            err_data = exc.response.json()
            if "error" in err_data:
                return {"error": err_data["error"]}
            if "detail" in err_data:
                return {"error": f"Game server error: {err_data['detail']}"}
        except Exception:
            pass
        return {
            "error": f"Game server returned error status {exc.response.status_code}"
        }
    except httpx.HTTPError as exc:
        return {"error": f"Failed to communicate with game server: {exc}"}


async def _auth_poll(provider: str, device_code: str) -> dict[str, Any]:
    """Proxy one poll to the server. On success, persist the token server-side and
    return only the account (never the raw token) to the browser."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/auth/{provider}/poll",
                json={"device_code": device_code},
            )
            res.raise_for_status()
            data = res.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _unreachable_error()
    except httpx.HTTPStatusError as exc:
        try:
            err_data = exc.response.json()
            if "error" in err_data:
                return {"error": err_data["error"]}
            if "detail" in err_data:
                return {"error": f"Game server error: {err_data['detail']}"}
        except Exception:
            pass
        return {
            "error": f"Game server returned error status {exc.response.status_code}"
        }
    except httpx.HTTPError as exc:
        return {"error": f"Failed to communicate with game server: {exc}"}
    if data.get("token"):
        jsonstore.write_text(_token_path(), json.dumps(data))
        return {"signed_in": True, "account": data.get("account")}
    return data  # {pending: true} or {error: ...}


async def auth_providers() -> dict[str, Any]:
    """Which sign-in flows the connected game server supports, and **which server
    that is**.

    `{"server": url, "flows": {provider: {device, web}}}`. `flows` is `{}` when the
    server can't say — an older one without the endpoint, or an unreachable one —
    which means "unknown": the UI keeps the buttons enabled and the click-time
    errors take over.

    `server` is reported because a disabled sign-in button is otherwise
    unexplainable from the browser. The URL is resolved from `GAMES_SERVER_URL`
    **before** the `games.serverUrl` setting (see `resolve_server_url`), so under
    `pnpm dev` this node targets the bundled local game server — which ships with
    no OAuth credentials and therefore reports every provider unavailable. The
    browser cannot work that out on its own: the setting it can read says
    something else entirely.
    """
    import httpx

    server = resolve_server_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(f"{_http_base()}/auth/providers")
            res.raise_for_status()
            data = res.json()
            return {"server": server, "flows": data if isinstance(data, dict) else {}}
    except Exception:  # noqa: BLE001 — availability is advisory, never an error
        return {"server": server, "flows": {}}


# ---- web (authorization-code) sign-in --------------------------------------
#
# The redirect flow: the node asks the game server to open a login, keeps the
# private `retrieval_code` here (never handed to the browser), and later pulls the
# minted JWT with it. The browser only ever gets the `authorize_url` to open.

# provider -> {retrieval_code, expires_at}. One interactive sign-in at a time.
_pending_web: dict[str, dict[str, Any]] = {}


async def web_login_start(provider: str) -> dict[str, Any]:
    """Begin the redirect flow. Returns `{authorize_url}` for the browser to open, or
    `{error}`. The private retrieval code is stashed node-side for the poll."""
    import time

    import httpx

    if provider not in ("github", "google"):
        return {"error": f"unknown provider {provider!r}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{_http_base()}/auth/{provider}/web/start")
            res.raise_for_status()
            data = res.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _unreachable_error()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to start sign-in: {exc}"}
    if data.get("error") or not data.get("login_url") or not data.get("retrieval_code"):
        return {
            "error": data.get("error")
            or "sign-in unavailable — web OAuth is not configured on the game server"
        }
    _pending_web[provider] = {
        "retrieval_code": data["retrieval_code"],
        "expires_at": time.time() + float(data.get("expires_in") or 900),
    }
    return {"authorize_url": data["login_url"]}


async def web_login_poll(provider: str) -> dict[str, Any]:
    """Poll the pending redirect sign-in. `{pending: True}` until the user authorizes,
    then persist the JWT and return `{signed_in, account}`; `{error}` otherwise."""
    import time

    import httpx

    pending = _pending_web.get(provider)
    if not pending:
        return {"error": "no sign-in in progress"}
    if time.time() > pending["expires_at"]:
        _pending_web.pop(provider, None)
        return {"error": "sign-in timed out"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/auth/{provider}/web/poll",
                json={"retrieval_code": pending["retrieval_code"]},
            )
            res.raise_for_status()
            data = res.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _unreachable_error()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to complete sign-in: {exc}"}
    if data.get("pending"):
        return {"pending": True}
    if data.get("token"):
        jsonstore.write_text(_token_path(), json.dumps(data))
        _pending_web.pop(provider, None)
        return {"signed_in": True, "account": data.get("account")}
    _pending_web.pop(provider, None)
    return {"error": data.get("error") or "sign-in failed"}


# ---- local (email + password) sign-in ---------------------------------------
#
# Same custody rule as the OAuth flows: the game server mints the JWT, the node
# keeps it, and the browser is handed only `{signed_in, account}`.
#
# The password does pass through this process on its way to the game server —
# unavoidable while the browser talks to one origin (the alternative is a
# cross-origin POST straight to the game server, which breaks CORS *and* would put
# the minted token in the browser). What matters is that it is never written down:
# `/api/games/auth/local` and `/auth/local` are both in `_REDACT_BODY_PREFIXES`
# (backend/modules/telemetry/instrument.py), so neither the inbound middleware nor
# the outbound httpx hook records these bodies.


async def _local_auth(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a credential to the game server and capture the session it returns."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(f"{_http_base()}/auth/local/{action}", json=payload)
            res.raise_for_status()
            data = res.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _unreachable_error()
    except httpx.HTTPStatusError as exc:
        try:
            err_data = exc.response.json()
            if "error" in err_data:
                return {"error": err_data["error"]}
        except Exception:
            pass
        return {
            "error": f"Game server returned error status {exc.response.status_code}"
        }
    except httpx.HTTPError as exc:
        return {"error": f"Failed to communicate with game server: {exc}"}
    if data.get("token"):
        jsonstore.write_text(_token_path(), json.dumps(data))
        return {"signed_in": True, "account": data.get("account")}
    return {"error": data.get("error") or "sign-in failed"}


async def local_signup(email: str, password: str, username: str = "") -> dict[str, Any]:
    return await _local_auth(
        "signup", {"email": email, "password": password, "username": username}
    )


async def local_login(email: str, password: str) -> dict[str, Any]:
    return await _local_auth("login", {"email": email, "password": password})


# ---- account / username ------------------------------------------------------


async def fetch_account() -> dict[str, Any] | None:
    """The signed-in account, read fresh from the game server (`GET /me`).

    Used to pick up a username this node's stored token predates, or one changed
    from another machine. Returns None when signed out or the server is
    unreachable — callers fall back to the locally cached account.
    """
    import httpx

    if signed_in_account() is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{_http_base()}/me", headers=_bearer())
            res.raise_for_status()
            data = res.json()
    except Exception:  # noqa: BLE001 — a refresh is advisory, never fatal
        return None
    account = data.get("account")
    if not isinstance(account, dict):
        return None
    _merge_account(account)
    return account


@jsonstore.serialized(_token_path)
def _merge_account(account: dict[str, Any]) -> None:
    """Write a refreshed account back into the token file, keeping the token.

    Serialized because it is a read-modify-write: overlapping with a sign-in
    that replaces the whole document, the loser's *token* is the field that
    goes missing."""
    data = _read()
    if not data:
        return
    data["account"] = account
    jsonstore.write_text(_token_path(), json.dumps(data))


async def set_username(handle: str) -> dict[str, Any]:
    """Claim or rename the username, then cache the updated account locally."""
    import httpx

    if signed_in_account() is None:
        return {"error": "sign in first"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/account/handle",
                json={"handle": handle},
                headers=_bearer(),
            )
            res.raise_for_status()
            data = res.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _unreachable_error()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to set username: {exc}"}
    if data.get("error"):
        return {"error": data["error"]}
    account = data.get("account")
    if isinstance(account, dict):
        _merge_account(account)
    return {"ok": True, "account": account}


async def github_start() -> dict[str, Any]:
    return await _auth_start("github")


async def github_poll(device_code: str) -> dict[str, Any]:
    return await _auth_poll("github", device_code)


async def google_start() -> dict[str, Any]:
    return await _auth_start("google")


async def google_poll(device_code: str) -> dict[str, Any]:
    return await _auth_poll("google", device_code)


def _play_token() -> str:
    """The token this node plays under: the signed-in JWT if present, else the dev
    token — the same resolution `client._settings` uses for `/game-ws`."""
    if token := get_token():
        return token
    return str(get_value("games.devToken", "player") or "player")


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {_play_token()}"}


async def replays_list(
    game_id: str | None, scope: str = "mine", limit: int = 50
) -> dict[str, Any]:
    """Proxy the server's replay index (`mine` needs our token; `public` doesn't)."""
    import httpx

    params: dict[str, Any] = {"scope": scope, "limit": limit}
    if game_id:
        params["game_id"] = game_id
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"{_http_base()}/replays", params=params, headers=_bearer()
            )
            res.raise_for_status()
            return res.json()
    except httpx.HTTPError:
        return _unreachable_error()


async def replay_get(replay_id: str) -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"{_http_base()}/replays/{replay_id}", headers=_bearer()
            )
            res.raise_for_status()
            return res.json()
    except httpx.HTTPError:
        return _unreachable_error()


async def replay_publish(replay_id: str) -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/replays/{replay_id}/publish", headers=_bearer()
            )
            res.raise_for_status()
            return res.json()
    except httpx.HTTPError:
        return _unreachable_error()


# ---- profiles ----------------------------------------------------------------
#
# Proxied through the node for the same two reasons sign-in is: the browser talks
# to one origin (no CORS), and the bearer token never leaves this process. Media
# rides through as well rather than being loaded cross-origin off the game server —
# one origin is one thing to reason about, and `/media/{sha}` is immutable so the
# relay costs one request per image for the life of that image.


async def profile_get(handle: str) -> dict[str, Any]:
    """Somebody else's profile, by username. Unauthenticated at the far end."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{_http_base()}/profile/{handle}")
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def profile_cards(handles: list[str]) -> dict[str, Any]:
    """Avatar/level/status for a whole roster in one call.

    Batched because the alternative is one request per friend on every render of a
    pane that opens by default — and this node is the only thing that knows which
    handles it wants, so the fan-in has to happen here rather than in the browser.
    """
    import httpx

    if not handles:
        return {"cards": {}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"{_http_base()}/profiles/cards", json={"handles": handles}
            )
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def profile_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Update the signed-in account's own profile."""
    import httpx

    if signed_in_account() is None:
        return {"error": "sign in first"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/profile", json=patch, headers=_bearer()
            )
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def profile_upload(kind: str, mime: str, data: bytes) -> dict[str, Any]:
    """Relay an image upload. The size and type gates live on the game server (it
    owns the volume); this only carries the bytes and the bearer."""
    import httpx

    if signed_in_account() is None:
        return {"error": "sign in first"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                f"{_http_base()}/profile/media",
                params={"kind": kind},
                content=data,
                headers={**_bearer(), "Content-Type": mime},
            )
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def profile_media(sha: str) -> tuple[bytes, str] | None:
    """Fetch a stored image so the node can serve it to its own browser."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(f"{_http_base()}/media/{sha}")
            if res.status_code != 200:
                return None
            return res.content, res.headers.get("content-type", "image/png")
    except httpx.HTTPError:
        return None


async def comments_list(handle: str, before: float | None = None) -> dict[str, Any]:
    import httpx

    params: dict[str, Any] = {}
    if before is not None:
        params["before"] = before
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"{_http_base()}/profile/{handle}/comments", params=params
            )
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def comment_add(handle: str, body: str) -> dict[str, Any]:
    import httpx

    if signed_in_account() is None:
        return {"error": "sign in to leave a comment"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{_http_base()}/profile/{handle}/comments",
                json={"body": body},
                headers=_bearer(),
            )
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def comment_hide(comment_id: str) -> dict[str, Any]:
    import httpx

    if signed_in_account() is None:
        return {"error": "sign in first"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.delete(
                f"{_http_base()}/profile/comments/{comment_id}", headers=_bearer()
            )
            res.raise_for_status()
            return dict(res.json())
    except httpx.HTTPError:
        return _unreachable_error()


async def leaderboard(game_id: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_http_base()}/leaderboard", params={"game_id": game_id}
        )
        res.raise_for_status()
        return res.json()


async def challenge_leaderboard(game_id: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.get(
            f"{_http_base()}/challenges/leaderboard", params={"game_id": game_id}
        )
        res.raise_for_status()
        return res.json()
