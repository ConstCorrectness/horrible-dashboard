"""Identity for the game server: JWT sessions + GitHub OAuth.

Two ways a node authenticates on `/game-ws`:

- **JWT** — the production path. After signing in (GitHub OAuth), the node holds a
  short-lived JWT this server signed; `resolve_token` verifies it and returns the
  account. The signing secret is `GAMES_JWT_SECRET` (env) or a per-install secret
  persisted under `$HORRIBLE_DATA_DIR`.
- **Dev token** — the token *is* the account id (provider `dev`). Kept on by default
  (`GAMES_ALLOW_DEV_AUTH`) so local play and tests need no OAuth setup; set the env to
  `0` to require real sign-in.

GitHub uses the **device flow** (no client secret, no callback server — ideal for a
desktop/headless app): ask for a code, the user enters it at github.com, then we poll
for the token and read their profile. Configure `games.github.clientId` in settings.
"""

from __future__ import annotations

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
        get_value("games.github.clientId", "")
        or os.environ.get("GAMES_GITHUB_CLIENT_ID", "")
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
    display_name = str(profile.get("login") or profile.get("name") or f"gh-{subject}")
    account_id = store.upsert_account("github", subject, display_name)
    token = issue_jwt(account_id, display_name)
    return {"token": token, "account": {"id": account_id, "display_name": display_name}}
