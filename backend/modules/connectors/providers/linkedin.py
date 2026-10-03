"""The LinkedIn connector: authorization code with a client secret (bring your own
app), and the calls Scrive's outbox makes to share a post as an **article link card**.

**Bring your own app.** Create an app at linkedin.com/developers, add the products
"Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn", and register the
redirect URL this node shows in the configure form (exact match, port included).

What the design has to respect:

- **No long-form articles.** LinkedIn's API cannot publish an Article; what it can do
  is a post whose content is a link card (title, description, thumbnail) pointing at
  the Pages post. That is what the outbox sends.
- **Tokens last about 60 days and usually cannot be refreshed** (refresh tokens are
  only for approved partner apps). The connector's status says how many days are left,
  and preflight warns a week ahead; a refresh token is used when LinkedIn grants one.
- **Commentary is "little text"**: `| { } @ [ ] ( ) < > # \\ * _ ~` are syntax and
  must be backslash-escaped, or LinkedIn drops or mangles the text (`little_escape`).
- **The API is versioned by month** (`LinkedIn-Version: YYYYMM`), each version
  supported for about a year. The version is pinned in the `scrive.linkedin.version`
  setting so an upgrade is a deliberate change, not a surprise.
"""

from __future__ import annotations

import re
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

CONNECTOR_ID = "linkedin"
SERVICE = "LinkedIn"
ID_ENV = "LINKEDIN_CLIENT_ID"
SECRET_ENV = "LINKEDIN_CLIENT_SECRET"
VERSION_SETTING = "scrive.linkedin.version"
DEFAULT_VERSION = "202606"

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
API = "https://api.linkedin.com"

SCOPE_IDS = ["openid", "profile", "w_member_social"]
SCOPES = [
    ConnectorScope(
        id="w_member_social",
        label="Share posts as you",
        description="Link posts you approve in Scrive's outbox — never anything else.",
    ),
    ConnectorScope(id="profile", label="Know which member is connected"),
]

#: Little-text syntax characters (LinkedIn's "little" format for commentary).
_LITTLE = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def little_escape(text: str) -> str:
    """`text` with every little-text syntax character backslash-escaped."""
    return _LITTLE.sub(r"\\\1", text)


def client_id() -> str:
    return config.client_id(CONNECTOR_ID, ID_ENV)


def client_secret() -> str:
    return config.client_secret(CONNECTOR_ID, SECRET_ENV)


def _configured() -> bool:
    return config.is_configured(CONNECTOR_ID, id_env=ID_ENV, secret_env=SECRET_ENV)


def _configure_step() -> dict[str, Any]:
    return config.configure_step(
        CONNECTOR_ID,
        id_env=ID_ENV,
        secret_env=SECRET_ENV,
        id_help=(
            "From a LinkedIn developer app with 'Sign In with LinkedIn using OpenID "
            "Connect' and 'Share on LinkedIn'. Register this redirect URL exactly: "
            f"{oauth.redirect_uri(CONNECTOR_ID)}"
        ),
        secret_help="Stored encrypted on this node and never sent to the browser.",
    )


def api_version() -> str:
    from backend.modules.settings.routes import get_value

    return str(get_value(VERSION_SETTING, DEFAULT_VERSION) or DEFAULT_VERSION)


def _authorize_url(state: str, _challenge: str) -> str:
    # LinkedIn's member flow is a confidential-client code flow; it takes no PKCE.
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
        "state": state,
        "scope": " ".join(SCOPE_IDS),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _credential_from(data: dict[str, Any], account: dict[str, Any]) -> Credential:
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(data.get("access_token") or ""),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=str(data.get("scope") or " ".join(SCOPE_IDS)).replace(",", " ").split(),
        account=account,
    )


async def _token_request(form: dict[str, str]) -> dict[str, Any]:
    res = await social_http.call(
        "POST",
        TOKEN_URL,
        service=SERVICE,
        data={**form, "client_id": client_id(), "client_secret": client_secret()},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return res.json()


async def fetch_account(access_token: str) -> dict[str, Any]:
    """`{id, label, avatar_url}`; `id` is the member id the author URN is built from."""
    try:
        res = await social_http.call(
            "GET",
            f"{API}/v2/userinfo",
            service=SERVICE,
            token=access_token,
            timeout=15.0,
        )
        info = res.json() or {}
    except (social_http.SendError, ValueError):
        return {"id": "", "label": "LinkedIn member"}
    return {
        "id": str(info.get("sub") or ""),
        "label": str(info.get("name") or "LinkedIn member"),
        "avatar_url": info.get("picture"),
    }


async def _exchange(code: str, _verifier: str) -> Credential | dict[str, Any]:
    try:
        data = await _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
            }
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    account = await fetch_account(str(data.get("access_token") or ""))
    if not account.get("id"):
        return {"error": "LinkedIn did not say which member signed in"}
    return _credential_from(data, account)


async def _refresh(cred: Credential) -> Credential | dict[str, Any]:
    if not cred.refresh_token:
        return {"error": "LinkedIn tokens cannot be refreshed — reconnect LinkedIn"}
    try:
        data = await _token_request(
            {"grant_type": "refresh_token", "refresh_token": cred.refresh_token}
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    return _credential_from(data, cred.account)


def days_left(cred: Credential | None = None) -> float | None:
    """Days until the stored token expires; `None` when not connected or no expiry."""
    if cred is None:
        cred, _ = store.load_or_error(CONNECTOR_ID)
    if cred is None or cred.expires_at is None:
        return None
    return (cred.expires_at - time.time()) / 86400


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("reconfigure") or not _configured():
        return _configure_step()
    return oauth.begin_redirect(
        CONNECTOR_ID, authorize_url=_authorize_url, exchange=_exchange
    )


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    if error := config.apply_config(
        CONNECTOR_ID, values, id_env=ID_ENV, secret_env=SECRET_ENV
    ):
        return {"error": error}
    return await _begin({})


def expiry_note(days: float | None, refreshable: bool) -> str | None:
    """What the status line says about the token's lifetime (None: nothing to say)."""
    if days is None or refreshable:
        return None
    if days <= 0:
        return "The LinkedIn sign-in has expired — reconnect to post again."
    whole = max(1, round(days))
    if days < 7:
        return f"The LinkedIn sign-in expires in {whole} day{'s' if whole != 1 else ''} — reconnect soon."
    return None


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=True, error=error)
    if cred is None:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    days = days_left(cred)
    label = str(account.get("label") or "LinkedIn member")
    if days is not None and days > 0 and not cred.refresh_token:
        label += f" · expires in {round(days)} days"
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=label,
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
        error=expiry_note(days, bool(cred.refresh_token)),
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


async def token() -> str | None:
    cred = await oauth.ensure_fresh(CONNECTOR_ID, _refresh)
    if cred and cred.is_expired(window_s=0):
        return None
    return cred.access_token if cred else None


def author_urn() -> str:
    cred, _ = store.load_or_error(CONNECTOR_ID)
    member = str((cred.account or {}).get("id") or "") if cred else ""
    return f"urn:li:person:{member}" if member else ""


# --- posting (called by the Scrive outbox runner) -------------------------------------


def _rest_headers() -> dict[str, str]:
    return {
        "LinkedIn-Version": api_version(),
        "X-Restli-Protocol-Version": "2.0.0",
    }


async def _live_token() -> str:
    access = await token()
    if not access:
        raise social_http.SendError(
            "LinkedIn isn't connected, or its sign-in expired — reconnect it from the "
            "home page."
        )
    return access


async def initialize_image_upload() -> tuple[str, str]:
    """`(upload_url, image_urn)` for one image owned by the connected member."""
    res = await social_http.call(
        "POST",
        f"{API}/rest/images",
        service=SERVICE,
        token=await _live_token(),
        params={"action": "initializeUpload"},
        headers=_rest_headers(),
        json={"initializeUploadRequest": {"owner": author_urn()}},
    )
    value = (res.json() or {}).get("value") or {}
    return str(value.get("uploadUrl") or ""), str(value.get("image") or "")


async def upload_image(upload_url: str, data: bytes) -> None:
    await social_http.call(
        "PUT",
        upload_url,
        service=SERVICE,
        token=await _live_token(),
        content=data,
        headers={"Content-Type": "application/octet-stream"},
        timeout=120.0,
    )


async def create_post(
    commentary: str,
    *,
    link: str = "",
    title: str = "",
    description: str = "",
    thumbnail: str = "",
) -> str:
    """Share a post (a link card when `link` is set); answers the post's URN.

    `commentary` is sent as given — escape it with `little_escape` first.
    """
    body: dict[str, Any] = {
        "author": author_urn(),
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if link:
        article: dict[str, Any] = {"source": link, "title": title or link}
        if description:
            article["description"] = description
        if thumbnail:
            article["thumbnail"] = thumbnail
        body["content"] = {"article": article}
    res = await social_http.call(
        "POST",
        f"{API}/rest/posts",
        service=SERVICE,
        token=await _live_token(),
        headers=_rest_headers(),
        json=body,
    )
    return str(res.headers.get("x-restli-id") or res.headers.get("x-linkedin-id") or "")


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="LinkedIn",
        kind="oauth",
        icon="linkedin",
        blurb="Share posts you approve in Scrive's outbox, as link cards.",
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
