"""The Google connector: loopback authorization-code + PKCE, credential custody, refresh.

**Why not the device flow** (unlike GitHub): Google's limited-input device flow only
permits a small allowlist of scopes (`email`/`profile`/`openid` + YouTube) —
`drive.readonly` is not among them. That's exactly why the game server's Google
sign-in only ever asks for `email profile`. Anything useful needs the redirect flow.

**Bring your own client.** An unverified Google app is capped at 100 hand-added test
users, and — the sharper edge — refresh tokens issued by an app in *Testing* expire
after **7 days**. Publishing an app that uses `drive.readonly` (a *restricted* scope)
requires Google verification plus an annual third-party CASA security assessment. So
v1 is explicitly BYO: you point this at your own Google Cloud project, where you are
your own sole test user and the data never leaves your machine.

The client type should be **Desktop app**: Google treats that client secret as
non-confidential and wildcards the loopback port, so the redirect URI registration
doesn't have to track our dev port.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlencode

from backend.modules.connectors import oauth, store
from backend.modules.connectors.guides import guide_loader
from backend.modules.connectors.store import Credential
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

CONNECTOR_ID = "google"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Drive's own `about` endpoint reports the signed-in user under `drive.readonly`, so
# we can label the account without asking for `openid`/`email` on top.
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

SCOPES = [
    ConnectorScope(
        id=DRIVE_SCOPE,
        label="Read your Google Drive",
        description=(
            "Search your files and read their contents, so the agent can answer from "
            "them and sync them into a library. Read-only — nothing is modified."
        ),
    ),
]


def client_id() -> str:
    """Public by design, so a setting is fine."""
    from backend.modules.settings.routes import get_value

    return str(
        os.environ.get("GOOGLE_CLIENT_ID", "")
        or get_value("connectors.google.clientId", "")
    )


def client_secret() -> str:
    """`GOOGLE_CLIENT_SECRET`, else the encrypted secrets store — **never a setting**:
    `GET /api/settings` hands the whole bag to the browser."""
    from backend.modules.database.secrets_store import get_secret_or_none

    return str(
        os.environ.get("GOOGLE_CLIENT_SECRET", "")
        or get_secret_or_none("google_client_secret")
        or ""
    )


def _missing_config() -> dict[str, Any] | None:
    if not client_id() or not client_secret():
        return {
            "error": (
                "Google isn't configured on this node. Create a Google Cloud OAuth "
                "client (type: Desktop app) with the Drive API enabled, then set "
                "GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET (or the "
                "connectors.google.clientId setting and a google_client_secret secret)."
            )
        }
    return None


def _authorize_url(state: str, challenge: str) -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
        "response_type": "code",
        "scope": DRIVE_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # offline + consent is the only reliable way to be handed a refresh token;
        # without it a reconnect often returns none and the connection dies in an hour.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def _fetch_account(access_token: str) -> dict[str, Any]:
    """Label the connection. Best-effort: a connector that works but can't name the
    account is better than a failed sign-in."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                ABOUT_URL,
                params={"fields": "user"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            res.raise_for_status()
            user = (res.json() or {}).get("user") or {}
    except httpx.HTTPError:
        return {"id": "", "label": "Google account"}
    return {
        "id": str(user.get("permissionId") or user.get("emailAddress") or ""),
        "label": str(
            user.get("emailAddress") or user.get("displayName") or "Google account"
        ),
        "avatar_url": user.get("photoLink"),
    }


def _credential_from(
    data: dict[str, Any], *, account: dict[str, Any] | None
) -> Credential:
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(data.get("access_token") or ""),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=str(data.get("scope") or DRIVE_SCOPE).split(),
        account=account or {},
    )


async def _exchange(code: str, verifier: str) -> Credential | dict[str, Any]:
    """Turn the authorization code into a credential. Runs entirely in this process —
    the token never goes near the browser."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id(),
                    "client_secret": client_secret(),
                    "code": code,
                    "code_verifier": verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
                },
            )
            data = res.json()
            if res.status_code >= 400 or data.get("error"):
                return {
                    "error": data.get("error_description")
                    or data.get("error")
                    or "Google refused the code"
                }
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Google: {exc}"}

    account = await _fetch_account(str(data.get("access_token") or ""))
    cred = _credential_from(data, account=account)
    if not cred.refresh_token:
        # Not fatal — the access token works for an hour — but say so, because the
        # connection will quietly stop working after that.
        cred.account.setdefault("label", "Google account")
    return cred


async def _refresh(cred: Credential) -> Credential | dict[str, Any]:
    import httpx

    if not cred.refresh_token:
        return {"error": "no refresh token — reconnect Google"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id(),
                    "client_secret": client_secret(),
                    "refresh_token": cred.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            data = res.json()
            if res.status_code >= 400 or data.get("error"):
                return {
                    "error": data.get("error_description")
                    or data.get("error")
                    or "refresh failed"
                }
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Google: {exc}"}
    # Google omits refresh_token on a refresh response; ensure_fresh carries the old
    # one forward, so don't synthesise one here.
    return _credential_from(data, account=cred.account)


async def _begin(_options: dict[str, Any]) -> dict[str, Any]:
    if missing := _missing_config():
        return missing
    return oauth.begin_redirect(
        CONNECTOR_ID, authorize_url=_authorize_url, exchange=_exchange
    )


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=True, error=error)
    if cred is None:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    # A credential with no refresh token dies within the hour and can't be renewed —
    # surface it as a broken connection rather than letting it fail mid-task later.
    problem = (
        None
        if cred.refresh_token
        else "Google didn't return a refresh token — reconnect to restore access"
    )
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "Google account"),
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
        error=problem,
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


async def token() -> str | None:
    """A live access token for the Drive tools, refreshed if it's near expiry."""
    cred = await oauth.ensure_fresh(CONNECTOR_ID, _refresh)
    return cred.access_token if cred else None


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Google",
        kind="oauth",
        icon="google",
        blurb="Search and read your Google Drive files.",
        status=_status,
        begin=_begin,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=SCOPES,
        guide=guide_loader(CONNECTOR_ID),
    )
