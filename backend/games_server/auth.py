"""Identity for the game server: JWT sessions + GitHub/Google OAuth.

Two ways a node authenticates on `/game-ws`:

- **JWT** — the production path. After signing in (GitHub or Google OAuth), the node
  holds a short-lived JWT this server signed; `resolve_token` verifies it and returns
  the account. The signing secret is `GAMES_JWT_SECRET` (env) or a per-install secret
  persisted under `$HORRIBLE_DATA_DIR`.
- **Dev token** — the token *is* the account id (provider `dev`). Kept on by default
  (`GAMES_ALLOW_DEV_AUTH`) so local play and tests need no OAuth setup; set the env to
  `0` to require real sign-in.

Both providers use the **device flow** (no callback server — ideal for a
desktop/headless app): ask for a code, the user enters it at the provider's device
page, then we poll for the token and read their profile.

- **GitHub**: no client secret needed. Configure `games.github.clientId` (or
  `GAMES_GITHUB_CLIENT_ID`).
- **Google**: requires an OAuth client of type **"TVs and Limited Input devices"**,
  and (unlike GitHub) its token poll requires the client secret — for that client
  type Google treats it as non-confidential, but we still keep it server-side.
  Configure `games.google.clientId` + `games.google.clientSecret` (or
  `GAMES_GOOGLE_CLIENT_ID` / `GAMES_GOOGLE_CLIENT_SECRET`). Accounts are keyed
  `google:<sub>`, so two different Gmail accounts are two distinct players.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import jwt

from backend.games_server import store

JWT_ALG = "HS256"
JWT_TTL_S = 30 * 24 * 3600  # 30 days


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def _jwt_secret() -> str:
    """The signing secret: env override, else a persisted per-install random secret."""
    env = os.environ.get("GAMES_JWT_SECRET")
    if env:
        return env
    path = _data_dir() / "game_server_jwt_secret"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")
    return secret


def _dev_auth_allowed() -> bool:
    return os.environ.get("GAMES_ALLOW_DEV_AUTH", "1") != "0"


# ---- JWT -------------------------------------------------------------------


def issue_jwt(account_id: str, display_name: str) -> str:
    now = int(time.time())
    payload = {
        "sub": account_id,
        "name": display_name,
        "iat": now,
        "exp": now + JWT_TTL_S,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def verify_jwt(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


def resolve_token(token: str) -> dict[str, str] | None:
    """Map an auth token to `{account_id, display_name}`, or None if invalid.

    Tries a signed JWT first; falls back to dev-token (token == account id) when dev
    auth is allowed. The `accounts` table is populated at sign-in, and the leaderboard
    LEFT-JOINs it, so this stays off the SQLite path in the hot auth loop.
    """
    token = (token or "").strip()
    if not token:
        return None
    claims = verify_jwt(token)
    if claims and claims.get("sub"):
        account_id = str(claims["sub"])
        return {
            "account_id": account_id,
            "display_name": str(claims.get("name") or account_id),
        }
    if _dev_auth_allowed():
        return {"account_id": token, "display_name": token}
    return None


# ---- GitHub OAuth (device flow) -------------------------------------------

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _github_client_id() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GITHUB_CLIENT_ID", "")
        or get_value("games.github.clientId", "")
    )


async def github_device_start() -> dict[str, Any]:
    """Begin the device flow: returns the code the user enters at github.com."""
    import httpx

    client_id = _github_client_id()
    if not client_id:
        raise ValueError("games.github.clientId is not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            GITHUB_DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": "read:user"},
            headers={"Accept": "application/json"},
        )
        res.raise_for_status()
        return (
            res.json()
        )  # device_code, user_code, verification_uri, interval, expires_in


async def github_device_poll(device_code: str) -> dict[str, Any]:
    """Poll once for the access token. Returns `{pending: True}` until the user has
    authorized, then upserts the account and returns `{token, account}`."""
    import httpx

    client_id = _github_client_id()
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        res.raise_for_status()
        data = res.json()
        if data.get("error") == "authorization_pending":
            return {"pending": True}
        if "access_token" not in data:
            return {"pending": True, "error": data.get("error")}
        access = data["access_token"]
        profile = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
        )
        profile.raise_for_status()
        return _finish_github(profile.json())


def _finish_github(profile: dict[str, Any]) -> dict[str, Any]:
    """Turn a GitHub profile into an account + our JWT (pure, so it's unit-testable)."""
    subject = str(profile.get("id"))
    login = str(profile.get("login") or "")
    display_name = login or str(profile.get("name") or f"gh-{subject}")
    account_id = store.upsert_account("github", subject, display_name)
    # The GitHub login *is* the handle — it's globally unique, so no collision dance.
    store.ensure_handle(account_id, login or display_name)
    token = issue_jwt(account_id, display_name)
    return {"token": token, "account": {"id": account_id, "display_name": display_name}}


# ---- Google OAuth (device flow) ---------------------------------------------

GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _google_client_id() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GOOGLE_CLIENT_ID", "")
        or get_value("games.google.clientId", "")
    )


def _google_client_secret() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GOOGLE_CLIENT_SECRET", "")
        or get_value("games.google.clientSecret", "")
    )


async def google_device_start() -> dict[str, Any]:
    """Begin the Google device flow. Returns the same shape as the GitHub start
    (`verification_uri` normalized — Google's wire name is `verification_url`)."""
    import httpx

    client_id = _google_client_id()
    if not client_id:
        raise ValueError("games.google.clientId is not configured")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GOOGLE_DEVICE_CODE_URL,
                data={"client_id": client_id, "scope": "email profile"},
                headers={"Accept": "application/json"},
            )
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPError as exc:
        if exc.response is not None:
            try:
                err_data = exc.response.json()
                if "error_description" in err_data:
                    raise ValueError(
                        f"Google API error: {err_data['error_description']}"
                    )
                elif "error" in err_data:
                    raise ValueError(f"Google API error: {err_data['error']}")
            except (ValueError, json.JSONDecodeError):
                pass
        raise ValueError(f"Failed to communicate with Google: {exc}")

    # Normalize to the GitHub shape the node/browser already speak.
    data.setdefault(
        "verification_uri",
        data.get("verification_url") or "https://www.google.com/device",
    )
    return data  # device_code, user_code, verification_uri, interval, expires_in


async def google_device_poll(device_code: str) -> dict[str, Any]:
    """Poll once for the access token. Google signals pending with 4xx statuses
    (428 authorization_pending, 403 slow_down), so we parse rather than raise."""
    import httpx

    client_id = _google_client_id()
    client_secret = _google_client_secret()
    if not client_id or not client_secret:
        raise ValueError("games.google.clientId / clientSecret are not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data = res.json()
        if data.get("error") in ("authorization_pending", "slow_down"):
            return {"pending": True}
        if "access_token" not in data:
            return {"pending": True, "error": data.get("error")}
        try:
            profile = await client.get(
                GOOGLE_USERINFO_URL,
                headers={
                    "Authorization": f"Bearer {data['access_token']}",
                    "Accept": "application/json",
                },
            )
            profile.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to fetch Google profile: {exc}")
        return _finish_google(profile.json())


def _finish_google(profile: dict[str, Any]) -> dict[str, Any]:
    """Turn a Google profile into an account + our JWT (pure, so it's unit-testable).
    Display name prefers the profile name, then the email's local part — so two
    Gmail accounts read naturally on the ladder."""
    subject = str(profile.get("id"))
    email = str(profile.get("email") or "")
    local_part = email.split("@")[0] if email else ""
    display_name = str(profile.get("name") or local_part or f"g-{subject}")
    account_id = store.upsert_account("google", subject, display_name)
    # Google has no username, so the handle is the email's local part (before @);
    # these aren't globally unique, so ensure_handle resolves collisions.
    store.ensure_handle(account_id, local_part or display_name)
    token = issue_jwt(account_id, display_name)
    return {"token": token, "account": {"id": account_id, "display_name": display_name}}


# ---- Web (authorization-code) OAuth ----------------------------------------
#
# The redirect flow behind the browser "Sign in with GitHub/Google" button: the
# user authorizes on the provider's normal consent page (no code typing) and the
# provider redirects back to *this server's* callback. Unlike the device flow,
# the code->token exchange needs a client secret, kept here server-side.

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _github_client_secret() -> str:
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GAMES_GITHUB_CLIENT_SECRET", "")
        or get_value("games.github.clientSecret", "")
    )


def providers_available() -> dict[str, dict[str, bool]]:
    """Which sign-in flows this server can actually run, from its configured OAuth
    credentials — so a client can disable (and explain) a provider button up front
    instead of discovering the gap after it has already opened a popup.

    GitHub's device flow needs only the client id; its web flow (and both Google
    flows — Google's device token poll requires the secret too) need id + secret."""
    gh_id, gh_secret = bool(_github_client_id()), bool(_github_client_secret())
    g_ok = bool(_google_client_id()) and bool(_google_client_secret())
    return {
        "github": {"device": gh_id, "web": gh_id and gh_secret},
        "google": {"device": g_ok, "web": g_ok},
    }


def web_config_error(provider: str) -> str | None:
    """A human message if `provider`'s web flow isn't configured, else None — so the
    UI fails fast at start rather than after a round-trip to the provider."""
    if provider == "github":
        if not _github_client_id():
            return "games.github.clientId is not configured"
        if not _github_client_secret():
            return (
                "games.github.clientSecret is not configured (required for web sign-in)"
            )
        return None
    if provider == "google":
        if not _google_client_id() or not _google_client_secret():
            return "games.google.clientId / clientSecret are not configured"
        return None
    return f"unknown provider {provider!r}"


def web_authorize_url(provider: str, state: str, redirect_uri: str) -> str:
    """The provider consent URL to send the browser to (carrying our CSRF `state`)."""
    from urllib.parse import urlencode

    if provider == "github":
        client_id = _github_client_id()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user",
            "state": state,
        }
        return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    if provider == "google":
        client_id = _google_client_id()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "email profile",
            "state": state,
            "access_type": "online",
        }
        return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"
    raise ValueError(f"unknown provider {provider!r}")


async def web_exchange(provider: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange an authorization `code` for the provider token, read the profile, and
    mint our account + JWT (reuses `_finish_github`/`_finish_google`)."""
    import httpx

    if provider == "github":
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": _github_client_id(),
                    "client_secret": _github_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            res.raise_for_status()
            data = res.json()
            access = data.get("access_token")
            if not access:
                raise ValueError(
                    data.get("error_description")
                    or data.get("error")
                    or "no access_token from GitHub"
                )
            profile = await client.get(
                GITHUB_USER_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json",
                },
            )
            profile.raise_for_status()
            return _finish_github(profile.json())
    if provider == "google":
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": _google_client_id(),
                    "client_secret": _google_client_secret(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            res.raise_for_status()
            data = res.json()
            access = data.get("access_token")
            if not access:
                raise ValueError(
                    data.get("error_description")
                    or data.get("error")
                    or "no access_token from Google"
                )
            profile = await client.get(
                GOOGLE_USERINFO_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json",
                },
            )
            profile.raise_for_status()
            return _finish_google(profile.json())
    raise ValueError(f"unknown provider {provider!r}")
