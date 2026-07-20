"""The Hugging Face connector: device-flow sign-in, credential custody, refresh.

**Why the device flow** (like GitHub, unlike Google): Hugging Face supports *public*
OAuth apps — created without a client secret — and its device endpoint authenticates
with the client id alone. That's the right shape for an app running on the user's
machine: no secret to ship, no redirect URI to register, and it works headless and
under Tauri unchanged.

**But unlike GitHub, the token expires.** GitHub OAuth App user tokens live forever, so
`github.py` has no refresher. Hugging Face issues `hf_oauth_*` access tokens that expire
(8h by default) alongside a refresh token, so this connector implements `_refresh` and
`token()` goes through `oauth.ensure_fresh` with it — otherwise the connection would
quietly stop working part-way through a session.

**Bring your own app.** Log in to huggingface.co, then **Settings → Connected Apps →
Developer Applications → Create App**, choosing "no client secret". (The Hub's own docs
deep-link to `/settings/applications/new`, but every one of those settings pages is
auth-gated — logged out you get a login form, not the app, which reads as a dead link.
Hence the navigation path here rather than a URL.) A client id is public by design, so
it's an ordinary setting; there is no secret half to store.
"""

from __future__ import annotations

import time
from typing import Any

from backend.modules.connectors import config, oauth, store
from backend.modules.connectors.guides import guide_loader
from backend.modules.connectors.store import Credential
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

CONNECTOR_ID = "huggingface"

DEVICE_CODE_URL = "https://huggingface.co/oauth/device"
TOKEN_URL = "https://huggingface.co/oauth/token"
WHOAMI_URL = "https://huggingface.co/api/whoami-v2"

# `profile` names the account; `read-repos` is what makes private models and datasets
# readable; `inference-api` lets the agent run inference as the user. All read-shaped —
# Hugging Face does have `write-repos`/`manage-repos`, and this connector deliberately
# asks for neither, so a confused agent cannot delete a model.
SCOPES = [
    ConnectorScope(
        id="profile",
        label="Read your profile",
        description="Your username and avatar, to show which account is connected.",
    ),
    ConnectorScope(
        id="read-repos",
        label="Read your models and datasets",
        description=(
            "Search and read files in your repos, private ones included. Read-only — "
            "this connector never asks for write or manage access."
        ),
    ),
    ConnectorScope(
        id="inference-api",
        label="Run inference as you",
        description=(
            "Call Inference Providers on your behalf, billed to your account. Only "
            "used when you ask the agent to run a model."
        ),
    ),
]

_SCOPE_PARAM = "profile read-repos inference-api"


ID_ENV = "HUGGINGFACE_CLIENT_ID"


def client_id() -> str:
    """`HUGGINGFACE_CLIENT_ID`, else the `connectors.huggingface.clientId` setting."""
    return config.client_id(CONNECTOR_ID, ID_ENV)


def _configured() -> bool:
    # No secret: a public app's device flow needs only the client id.
    return config.is_configured(CONNECTOR_ID, id_env=ID_ENV)


def _configure_step() -> dict[str, Any]:
    return config.configure_step(
        CONNECTOR_ID,
        id_env=ID_ENV,
        id_help=(
            "Log in to huggingface.co, then Settings → Connected Apps → Developer "
            "Applications → Create App. Create it without a client secret, and enable "
            "the profile, read-repos and inference-api scopes."
        ),
    )


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    """Persist the client id, then chain straight into the device flow."""
    if error := config.apply_config(CONNECTOR_ID, values, id_env=ID_ENV):
        return {"error": error}
    return await _begin({})


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    import httpx

    if options.get("reconfigure"):
        return _configure_step()
    cid = client_id()
    if not cid:
        # A form, not an error string: the user can fix this here rather than in a shell.
        return _configure_step()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                DEVICE_CODE_URL,
                data={"client_id": cid, "scope": _SCOPE_PARAM},
                headers={"Accept": "application/json"},
            )
            data = res.json()
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Hugging Face: {exc}"}
    except ValueError:
        return {"error": "Hugging Face returned an unreadable response"}

    if data.get("error") or not data.get("device_code"):
        # `invalid_scope` here means the app was created without one of the scopes we
        # ask for — worth saying plainly, since the fix is in the app's settings.
        return {
            "error": data.get("error_description")
            or data.get("error")
            or "Hugging Face refused the sign-in"
        }

    return oauth.begin_device(
        CONNECTOR_ID,
        user_code=str(data["user_code"]),
        verification_uri=str(
            data.get("verification_uri_complete")
            or data.get("verification_uri")
            or "https://huggingface.co/oauth/device"
        ),
        device_code=str(data["device_code"]),
        poll=_poll_once,
        interval=float(data.get("interval") or 5),
        expires_in=float(data.get("expires_in") or oauth.FLOW_TTL_S),
    )


async def _fetch_account(access_token: str) -> dict[str, Any]:
    """Label the connection. Best-effort: a connector that works but can't name the
    account beats a failed sign-in."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                WHOAMI_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            res.raise_for_status()
            user = res.json() or {}
    except (httpx.HTTPError, ValueError):
        return {"id": "", "label": "Hugging Face user"}
    return {
        "id": str(user.get("id") or user.get("name") or ""),
        "label": str(user.get("name") or user.get("fullname") or "Hugging Face user"),
        "avatar_url": user.get("avatarUrl"),
    }


def _credential_from(
    data: dict[str, Any], *, account: dict[str, Any] | None
) -> Credential:
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(data.get("access_token") or ""),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=str(data.get("scope") or _SCOPE_PARAM).split(),
        account=account or {},
    )


async def _poll_once(device_code: str) -> Credential | dict[str, Any]:
    """One token poll. `{pending: True}` until the user finishes at huggingface.co."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id(),
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
            data = res.json()

            error = data.get("error")
            # `slow_down` means we polled too fast — still pending, not a failure.
            if error in ("authorization_pending", "slow_down"):
                return {"pending": True}
            if error:
                return {"error": data.get("error_description") or error}
            access = data.get("access_token")
            if not access:
                return {"pending": True}
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Hugging Face: {exc}"}
    except ValueError:
        return {"error": "Hugging Face returned an unreadable token response"}

    return _credential_from(data, account=await _fetch_account(str(access)))


async def _refresh(cred: Credential) -> Credential | dict[str, Any]:
    """Renew an expiring access token. Hugging Face tokens are short-lived, so this is
    load-bearing rather than the no-op it is for GitHub."""
    import httpx

    if not cred.refresh_token:
        return {"error": "no refresh token — reconnect Hugging Face"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id(),
                    "refresh_token": cred.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
            data = res.json()
            if res.status_code >= 400 or data.get("error"):
                return {
                    "error": data.get("error_description")
                    or data.get("error")
                    or "refresh failed"
                }
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach Hugging Face: {exc}"}
    except ValueError:
        return {"error": "Hugging Face returned an unreadable refresh response"}

    # `ensure_fresh` carries the old refresh token and account forward when the
    # response omits them, so don't synthesise either here.
    return _credential_from(data, account=cred.account)


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=True, error=error)
    if cred is None:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    # An expiring credential with no refresh token can't be renewed — surface it now
    # rather than letting it fail mid-task once the access token lapses.
    problem = (
        "Hugging Face didn't return a refresh token — reconnect to restore access"
        if cred.expires_at and not cred.refresh_token
        else None
    )
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "Hugging Face user"),
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
        error=problem,
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


async def token() -> str | None:
    """A live access token for the Hugging Face tools, refreshed if it's near expiry.
    None when the connector isn't connected."""
    cred = await oauth.ensure_fresh(CONNECTOR_ID, _refresh)
    return cred.access_token if cred else None


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Hugging Face",
        kind="oauth",
        icon="huggingface",
        blurb="Search models and datasets on the Hub, and read files from your repos.",
        status=_status,
        begin=_begin,
        submit=_submit,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=SCOPES,
        guide=guide_loader(CONNECTOR_ID),
        configured=_configured,
    )
