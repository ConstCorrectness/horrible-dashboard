"""The YouTube connector: the Google OAuth client you already configured, with its own
consent for `youtube.upload`, and the resumable upload Scrive's outbox sends videos with.

**Same client, separate grant.** The client id and secret are the Google connector's
(`config.py`, one Cloud project), but the credential is stored apart under `youtube`:
connecting Drive must not silently grant the right to publish videos, and
disconnecting YouTube must not cut off Drive. Enable the YouTube Data API v3 in the
same Cloud project.

What the design has to respect:

- **An unaudited project's uploads are forced private.** Until the Cloud project
  passes YouTube's API audit, every video it uploads is locked private whatever the
  request says. The composer says so (`scrive.youtube.audited` is off by default)
  instead of pretending a public upload will be public.
- **Quota.** An upload costs 1,600 of the default 10,000 daily units: about six a day.
  Preflight counts today's uploads.
- **Resumable uploads**, so a restart in the middle of a large file continues from the
  offset YouTube confirms (`query_offset`) instead of starting again. The session URL
  is kept in the outbox row's step; YouTube keeps a session for about a week.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlencode

from backend.modules.connectors import config, oauth, store
from backend.modules.connectors.providers import google, social_http
from backend.modules.connectors.store import Credential
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

CONNECTOR_ID = "youtube"
SERVICE = "YouTube"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

SCOPES = [
    ConnectorScope(
        id=UPLOAD_SCOPE,
        label="Upload videos",
        description="Videos you approve in Scrive's outbox — never anything else.",
    ),
    ConnectorScope(id=READ_SCOPE, label="Know which channel is connected"),
]


def _configured() -> bool:
    return config.is_configured(
        google.CONNECTOR_ID, id_env=google.ID_ENV, secret_env=google.SECRET_ENV
    )


def _configure_step() -> dict[str, Any]:
    step = config.configure_step(
        google.CONNECTOR_ID,
        id_env=google.ID_ENV,
        secret_env=google.SECRET_ENV,
        id_help=(
            "The Google Cloud OAuth client (Desktop app) — the same one the Google "
            "connector uses. Enable the YouTube Data API v3 in that project."
        ),
        secret_help="Stored encrypted on this node and never sent to the browser.",
    )
    return step


def _authorize_url(state: str, challenge: str) -> str:
    params = {
        "client_id": google.client_id(),
        "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
        "response_type": "code",
        "scope": f"{UPLOAD_SCOPE} {READ_SCOPE}",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{google.AUTH_URL}?{urlencode(params)}"


def _credential_from(data: dict[str, Any], account: dict[str, Any]) -> Credential:
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(data.get("access_token") or ""),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=str(data.get("scope") or f"{UPLOAD_SCOPE} {READ_SCOPE}").split(),
        account=account,
    )


async def _token_request(form: dict[str, str]) -> dict[str, Any]:
    res = await social_http.call(
        "POST",
        google.TOKEN_URL,
        service=SERVICE,
        data={
            **form,
            "client_id": google.client_id(),
            "client_secret": google.client_secret(),
        },
        timeout=20.0,
    )
    return res.json()


async def fetch_account(access_token: str) -> dict[str, Any]:
    try:
        res = await social_http.call(
            "GET",
            f"{API}/channels",
            service=SERVICE,
            token=access_token,
            params={"part": "snippet", "mine": "true"},
            timeout=15.0,
        )
        items = (res.json() or {}).get("items") or []
    except (social_http.SendError, ValueError):
        return {"id": "", "label": "YouTube channel"}
    if not items:
        return {"id": "", "label": "No YouTube channel on this account"}
    snippet = items[0].get("snippet") or {}
    thumb = ((snippet.get("thumbnails") or {}).get("default") or {}).get("url")
    return {
        "id": str(items[0].get("id") or ""),
        "label": str(snippet.get("title") or "YouTube channel"),
        "avatar_url": thumb,
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
        return {"error": "no refresh token — reconnect YouTube"}
    try:
        data = await _token_request(
            {"grant_type": "refresh_token", "refresh_token": cred.refresh_token}
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    return _credential_from(data, cred.account)


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("reconfigure") or not _configured():
        return _configure_step()
    return oauth.begin_redirect(
        CONNECTOR_ID, authorize_url=_authorize_url, exchange=_exchange
    )


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    if error := config.apply_config(
        google.CONNECTOR_ID,
        values,
        id_env=google.ID_ENV,
        secret_env=google.SECRET_ENV,
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
    problem = None
    if not cred.refresh_token:
        problem = "Google returned no refresh token — reconnect, or scheduled uploads will fail"
    elif not account.get("id"):
        problem = "This Google account has no YouTube channel to upload to"
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "YouTube channel"),
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
        error=problem,
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


async def token() -> str | None:
    cred = await oauth.ensure_fresh(CONNECTOR_ID, _refresh)
    return cred.access_token if cred else None


# --- uploading (called by the Scrive outbox runner) -----------------------------------


async def _live_token() -> str:
    access = await token()
    if not access:
        raise social_http.SendError(
            "YouTube isn't connected — connect it from the home page."
        )
    return access


async def start_upload(metadata: dict[str, Any], size: int, content_type: str) -> str:
    """Open a resumable upload session; answers its URL (keep it: it is the resume)."""
    res = await social_http.call(
        "POST",
        UPLOAD_URL,
        service=SERVICE,
        token=await _live_token(),
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": content_type,
        },
        content=json.dumps(metadata).encode("utf-8"),
    )
    session = res.headers.get("location") or ""
    if not session:
        raise social_http.SendError("YouTube opened no upload session")
    return session


_RANGE = re.compile(r"bytes=0-(\d+)")


def _progress(res: Any) -> tuple[int, dict[str, Any] | None]:
    """`(confirmed offset, video or None)` from a chunk or status response."""
    if res.status_code == 308:
        match = _RANGE.search(res.headers.get("range") or "")
        return (int(match.group(1)) + 1 if match else 0), None
    return -1, res.json()


async def upload_chunk(
    session: str, data: bytes, start: int, total: int
) -> tuple[int, dict[str, Any] | None]:
    """Send `data` at `start`. Answers `(next offset, None)` while incomplete, and
    `(total, video)` once YouTube has the whole file."""
    end = start + len(data) - 1
    res = await social_http.call(
        "PUT",
        session,
        service=SERVICE,
        token=await _live_token(),
        content=data,
        headers={"Content-Range": f"bytes {start}-{end}/{total}"},
        timeout=300.0,
    )
    offset, video = _progress(res)
    return (total, video) if video is not None else (offset, None)


async def query_offset(session: str, total: int) -> tuple[int, dict[str, Any] | None]:
    """Ask a session how much it has — how a resumed upload knows where to restart."""
    res = await social_http.call(
        "PUT",
        session,
        service=SERVICE,
        token=await _live_token(),
        content=b"",
        headers={"Content-Range": f"bytes */{total}"},
    )
    offset, video = _progress(res)
    return (total, video) if video is not None else (offset, None)


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="YouTube",
        kind="oauth",
        icon="youtube",
        blurb="Upload videos you approve in Scrive's outbox.",
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
