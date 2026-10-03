"""The X connector: OAuth 2.0 authorization code + PKCE as a **public client**, and the
calls Scrive's outbox makes to post (text, threads, media).

**Bring your own app.** Create an app in the X developer portal with *Type of App:
Native App* (a public client: no secret, PKCE instead), and register the callback URL
this node shows in the configure form — X matches it exactly, port included, which is
why it is built from the backend's pinned port (`oauth.redirect_uri`) and never from a
request's Host.

**Posting costs money.** The X API has been pay-per-use since February 2026; at the
time of writing about $0.015 a post, and $0.20 for a post that contains a URL. The
outbox composer shows the estimate (`scrive/social.py`) before anything is approved,
and nothing is ever posted without a person's approval.

Scopes: `tweet.read users.read` (who is connected), `tweet.write`, `media.write`
(images and video), `offline.access` (a refresh token — X access tokens last two
hours, and X **rotates** the refresh token on every use, so the new one must be kept;
`oauth.ensure_fresh` does).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from backend.modules.connectors import config, oauth, store
from backend.modules.connectors.providers import social_http
from backend.modules.connectors.store import Credential
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

CONNECTOR_ID = "x"
SERVICE = "X"
ID_ENV = "X_CLIENT_ID"

AUTH_URL = "https://x.com/i/oauth2/authorize"
API = "https://api.x.com"
TOKEN_URL = f"{API}/2/oauth2/token"

SCOPE_IDS = ["tweet.read", "tweet.write", "users.read", "media.write", "offline.access"]
SCOPES = [
    ConnectorScope(
        id="tweet.write",
        label="Post on your behalf",
        description="Posts and threads you approve in Scrive's outbox — never anything else.",
    ),
    ConnectorScope(
        id="media.write",
        label="Upload media",
        description="Images and video attached to a post you approved.",
    ),
    ConnectorScope(
        id="users.read",
        label="Know which account is connected",
    ),
    ConnectorScope(
        id="offline.access",
        label="Stay connected",
        description="A refresh token, so a scheduled post can go out while you are away.",
    ),
]


def client_id() -> str:
    return config.client_id(CONNECTOR_ID, ID_ENV)


def _configured() -> bool:
    return config.is_configured(CONNECTOR_ID, id_env=ID_ENV)


def _configure_step() -> dict[str, Any]:
    return config.configure_step(
        CONNECTOR_ID,
        id_env=ID_ENV,
        id_label="OAuth 2.0 Client ID",
        id_help=(
            "From an X developer app of type Native App (public client). Register "
            f"this callback URL exactly: {oauth.redirect_uri(CONNECTOR_ID)}"
        ),
    )


def _authorize_url(state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
        "scope": " ".join(SCOPE_IDS),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _credential_from(data: dict[str, Any], account: dict[str, Any]) -> Credential:
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(data.get("access_token") or ""),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=str(data.get("scope") or " ".join(SCOPE_IDS)).split(),
        account=account,
    )


async def _token_request(form: dict[str, str]) -> dict[str, Any]:
    res = await social_http.call(
        "POST",
        TOKEN_URL,
        service=SERVICE,
        data={**form, "client_id": client_id()},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return res.json()


async def fetch_account(access_token: str) -> dict[str, Any]:
    """`{id, label, avatar_url, username}` of the signed-in account. Best effort."""
    try:
        res = await social_http.call(
            "GET",
            f"{API}/2/users/me",
            service=SERVICE,
            token=access_token,
            params={"user.fields": "profile_image_url,username,name"},
            timeout=15.0,
        )
        user = (res.json() or {}).get("data") or {}
    except (social_http.SendError, ValueError):
        return {"id": "", "label": "X account"}
    username = str(user.get("username") or "")
    return {
        "id": str(user.get("id") or ""),
        "label": f"@{username}" if username else str(user.get("name") or "X account"),
        "avatar_url": user.get("profile_image_url"),
        "username": username,
    }


async def _exchange(code: str, verifier: str) -> Credential | dict[str, Any]:
    try:
        data = await _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
            }
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    account = await fetch_account(str(data.get("access_token") or ""))
    return _credential_from(data, account)


async def _refresh(cred: Credential) -> Credential | dict[str, Any]:
    if not cred.refresh_token:
        return {"error": "no refresh token — reconnect X"}
    try:
        data = await _token_request(
            {"grant_type": "refresh_token", "refresh_token": cred.refresh_token}
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    # X rotates refresh tokens: the response carries the new one, and the old one is
    # dead from this moment.
    return _credential_from(data, cred.account)


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("reconfigure") or not _configured():
        return _configure_step()
    return oauth.begin_redirect(
        CONNECTOR_ID, authorize_url=_authorize_url, exchange=_exchange
    )


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    if error := config.apply_config(CONNECTOR_ID, values, id_env=ID_ENV):
        return {"error": error}
    return await _begin({})


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
            label=str(account.get("label") or "X account"),
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
        error=None
        if cred.refresh_token
        else "X returned no refresh token — reconnect, or scheduled posts will fail",
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


async def token() -> str | None:
    cred = await oauth.ensure_fresh(CONNECTOR_ID, _refresh)
    return cred.access_token if cred else None


def username() -> str:
    cred, _ = store.load_or_error(CONNECTOR_ID)
    return str((cred.account or {}).get("username") or "") if cred else ""


# --- posting (called by the Scrive outbox runner) -------------------------------------


async def _live_token() -> str:
    access = await token()
    if not access:
        raise social_http.SendError(
            "X isn't connected — connect it from the home page."
        )
    return access


async def create_post(
    text: str, *, reply_to: str | None = None, media_ids: list[str] | None = None
) -> str:
    """Post, and answer the new post's id."""
    body: dict[str, Any] = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    if media_ids:
        body["media"] = {"media_ids": media_ids}
    res = await social_http.call(
        "POST", f"{API}/2/tweets", service=SERVICE, token=await _live_token(), json=body
    )
    return str(((res.json() or {}).get("data") or {}).get("id") or "")


def post_url(post_id: str) -> str:
    handle = username()
    return f"https://x.com/{handle or 'i/web'}/status/{post_id}"


async def media_initialize(total_bytes: int, media_type: str, category: str) -> str:
    """Start a chunked upload; answers the media id the APPEND calls use."""
    res = await social_http.call(
        "POST",
        f"{API}/2/media/upload/initialize",
        service=SERVICE,
        token=await _live_token(),
        json={
            "media_type": media_type,
            "total_bytes": total_bytes,
            "media_category": category,
        },
    )
    return str(((res.json() or {}).get("data") or {}).get("id") or "")


async def media_append(media_id: str, segment_index: int, chunk: bytes) -> None:
    await social_http.call(
        "POST",
        f"{API}/2/media/upload/{media_id}/append",
        service=SERVICE,
        token=await _live_token(),
        data={"segment_index": str(segment_index)},
        files={"media": ("chunk", chunk, "application/octet-stream")},
        timeout=120.0,
    )


async def media_finalize(media_id: str) -> dict[str, Any]:
    """Finish the upload; answers `processing_info` (empty when nothing to wait for)."""
    res = await social_http.call(
        "POST",
        f"{API}/2/media/upload/{media_id}/finalize",
        service=SERVICE,
        token=await _live_token(),
    )
    return dict(((res.json() or {}).get("data") or {}).get("processing_info") or {})


async def media_status(media_id: str) -> dict[str, Any]:
    res = await social_http.call(
        "GET",
        f"{API}/2/media/upload",
        service=SERVICE,
        token=await _live_token(),
        params={"media_id": media_id, "command": "STATUS"},
    )
    return dict(((res.json() or {}).get("data") or {}).get("processing_info") or {})


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="X",
        kind="oauth",
        icon="x",
        blurb="Post threads you approve in Scrive's outbox.",
        status=_status,
        begin=_begin,
        submit=_submit,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=SCOPES,
        # No agent tools, so no guide: the outbox runner is its only caller.
        guide=None,
        configured=_configured,
    )
