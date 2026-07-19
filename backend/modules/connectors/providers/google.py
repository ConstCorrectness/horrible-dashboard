"""The Google connector: loopback authorization-code + PKCE, credential custody, refresh.

**Why not the device flow** (unlike GitHub): Google's limited-input device flow only
permits a small allowlist of scopes (`email`/`profile`/`openid` + YouTube) —
`drive.readonly` is not among them. That's exactly why the game server's Google
sign-in only ever asks for `email profile`. Anything useful needs the redirect flow.

**Bring your own client.** v1 is explicitly BYO: you point this at your own Google Cloud
project, so the data never leaves your machine and you aren't sharing a quota with
anyone. Supply the client id and secret in the app — see `config.py`; no env editing
required, though `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` still win if set.

**Publish the consent screen to "In Production".** This is the difference between
configuring once and re-authorizing every week: an app left in *Testing* has all its
refresh tokens expire after **7 days**. Publishing removes that, and you can publish
*without* verification — you get an "unverified app" interstitial and a 100-user cap,
neither of which matters for a personal node. Verification plus the annual third-party
CASA assessment only become necessary to ship `drive.readonly` (a *restricted* scope)
publicly to strangers.

The client type should be **Desktop app**: Google treats that client secret as
non-confidential and wildcards the loopback port, so the redirect URI registration
doesn't have to track our dev port.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from backend.modules.connectors import config, oauth, store
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


ID_ENV = "GOOGLE_CLIENT_ID"
SECRET_ENV = "GOOGLE_CLIENT_SECRET"


def client_id() -> str:
    """Public by design, so a setting is fine."""
    return config.client_id(CONNECTOR_ID, ID_ENV)


def client_secret() -> str:
    """`GOOGLE_CLIENT_SECRET`, else the encrypted secrets store — **never a setting**:
    `GET /api/settings` hands the whole bag to the browser."""
    return config.client_secret(CONNECTOR_ID, SECRET_ENV)


def _configured() -> bool:
    return config.is_configured(CONNECTOR_ID, id_env=ID_ENV, secret_env=SECRET_ENV)


def _configure_step() -> dict[str, Any]:
    """The form that stands in for "not configured on this node"."""
    return config.configure_step(
        CONNECTOR_ID,
        id_env=ID_ENV,
        secret_env=SECRET_ENV,
        id_help=(
            "From a Google Cloud OAuth client of type Desktop app, with the Drive API "
            "enabled. Publish the consent screen to In Production — an app left in "
            "Testing expires its refresh tokens after 7 days."
        ),
        secret_help="Stored encrypted on this node and never sent to the browser.",
    )


def _missing_config() -> dict[str, Any] | None:
    """A `form` step when the client credentials are missing, else None.

    Deliberately a form and not an `{"error": …}`: an error string tells the user what
    they'd have to go do in a terminal, whereas a form lets them do it here.
    """
    return None if _configured() else _configure_step()


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


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    # `reconfigure` forces the credential form even when the node is already set up —
    # that's how you rotate a client secret or point at a different Cloud project.
    if options.get("reconfigure"):
        return _configure_step()
    if missing := _missing_config():
        return missing
    return oauth.begin_redirect(
        CONNECTOR_ID, authorize_url=_authorize_url, exchange=_exchange
    )


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    """Persist the client credentials, then chain straight into the OAuth step.

    Chaining is what makes this feel like one flow: the user fills in the form and lands
    on Google's consent screen, rather than having to press Connect a second time.
    """
    if error := config.apply_config(
        CONNECTOR_ID, values, id_env=ID_ENV, secret_env=SECRET_ENV
    ):
        return {"error": error}
    return await _begin({})


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
        submit=_submit,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=SCOPES,
        guide=guide_loader(CONNECTOR_ID),
        configured=_configured,
    )
