"""The GitHub connector: device-flow sign-in, credential custody, and status.

**Why the device flow.** GitHub OAuth Apps don't support PKCE, so an
authorization-code flow would force us to ship a client secret in an app that runs on
the user's machine — which is not a secret. The device flow needs no secret and no
redirect URI, so it also works headless and under Tauri unchanged.

Unlike the game server's sign-in (which asks for `read:user`, discards the token, and
keeps only the profile), this connector asks for scopes that let the *agent* do work
and holds onto the token — that's the whole point of the integration.
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

CONNECTOR_ID = "github"

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

# `read:user` identifies the account; `repo` is what makes code search and file reads
# work against private repositories. GitHub has no read-only variant of `repo` for
# OAuth Apps — it's all-or-nothing, which is worth being upfront about in the UI.
SCOPES = [
    ConnectorScope(
        id="read:user",
        label="Read your profile",
        description="Your username and avatar, to show which account is connected.",
    ),
    ConnectorScope(
        id="repo",
        label="Read your repositories",
        description=(
            "Search code, read files, and list issues — including private repos. "
            "GitHub has no read-only repo scope, so this also permits writes; the "
            "agent still asks before any write."
        ),
    ),
]

_SCOPE_PARAM = "read:user repo"


ID_ENV = "GITHUB_CLIENT_ID"


def client_id() -> str:
    """`GITHUB_CLIENT_ID`, else the `connectors.github.clientId` setting.

    A client id is public by design, so a shipped default is safe; the setting is the
    bring-your-own-app escape hatch.
    """
    return config.client_id(CONNECTOR_ID, ID_ENV)


def _configured() -> bool:
    # No secret: the device flow needs only the client id.
    return config.is_configured(CONNECTOR_ID, id_env=ID_ENV)


def _configure_step() -> dict[str, Any]:
    return config.configure_step(
        CONNECTOR_ID,
        id_env=ID_ENV,
        id_help=(
            "From an OAuth App at github.com/settings/developers, with Device Flow "
            "enabled. No client secret needed — the device flow doesn't use one."
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
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach GitHub: {exc}"}

    if data.get("error") or not data.get("device_code"):
        return {
            "error": data.get("error_description")
            or data.get("error")
            or "GitHub refused the sign-in"
        }

    return oauth.begin_device(
        CONNECTOR_ID,
        user_code=str(data["user_code"]),
        verification_uri=str(
            data.get("verification_uri") or "https://github.com/login/device"
        ),
        device_code=str(data["device_code"]),
        poll=_poll_once,
        interval=float(data.get("interval") or 5),
        expires_in=float(data.get("expires_in") or oauth.FLOW_TTL_S),
    )


async def _poll_once(device_code: str) -> Credential | dict[str, Any]:
    """One token poll. `{pending: True}` until the user finishes at github.com."""
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
            res.raise_for_status()
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

            profile = await client.get(
                USER_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/vnd.github+json",
                },
            )
            profile.raise_for_status()
            user = profile.json()
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach GitHub: {exc}"}

    granted = str(data.get("scope") or "").replace(",", " ").split()
    # GitHub OAuth App user tokens don't expire by default, so expires_at stays None
    # and `ensure_fresh` is a no-op. A GitHub *App* (not OAuth App) would return
    # expires_in here, hence reading it rather than assuming.
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(access),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=granted or _SCOPE_PARAM.split(),
        account={
            "id": str(user.get("id") or ""),
            "label": str(user.get("login") or user.get("name") or "GitHub user"),
            "avatar_url": user.get("avatar_url"),
        },
    )


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=True, error=error)
    if cred is None:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "GitHub user"),
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


async def token() -> str | None:
    """The access token for the agent tools, refreshed if needed. None when the
    connector isn't connected."""
    cred = await oauth.ensure_fresh(CONNECTOR_ID, None)
    return cred.access_token if cred else None


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="GitHub",
        kind="oauth",
        icon="github",
        blurb="Search code and repositories, read files, and manage issues on GitHub.",
        status=_status,
        begin=_begin,
        submit=_submit,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=SCOPES,
        guide=guide_loader(CONNECTOR_ID),
        configured=_configured,
    )
