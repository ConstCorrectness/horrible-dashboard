"""The dev.to (Forem) connector: an API key, and the one call Scrive's outbox makes to
cross-post a page as a whole article.

An **api-key** connector: dev.to issues personal keys (Settings → Extensions → DEV
Community API Keys) and has no OAuth for writing articles. The key goes in an
`api-key` header, not `Authorization`.

What the design has to respect:

- **`canonical_url` points home.** The article is a copy; the canonical URL tells
  search engines the Pages post is the original. The payload's default is
  `{{post.url}}`, filled in at approval.
- **At most four tags**, lowercase letters and digits only (dev.to strips the rest).
  `devto_tag` normalises them; preflight refuses a fifth.
- **`published: false` makes a dev.to draft**, which the person finishes on dev.to.
  Either way the call is made only for an approved outbox row.
"""

from __future__ import annotations

import re
from typing import Any

from backend.modules.connectors import store
from backend.modules.connectors.providers import social_http
from backend.modules.connectors.store import Credential
from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

CONNECTOR_ID = "devto"
SERVICE = "dev.to"
API = "https://dev.to/api"
MAX_TAGS = 4


def devto_tag(tag: str) -> str:
    """A tag as dev.to stores it: lowercase letters and digits."""
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def _headers(key: str) -> dict[str, str]:
    return {"api-key": key, "Accept": "application/vnd.forem.api-v1+json"}


def _key() -> str:
    cred = store.load(CONNECTOR_ID)
    if not cred or not cred.access_token:
        raise social_http.SendError(
            "dev.to isn't connected — connect it from the home page.", status=401
        )
    return cred.access_token


async def me(key: str) -> dict[str, Any]:
    res = await social_http.call(
        "GET", f"{API}/users/me", service=SERVICE, headers=_headers(key)
    )
    return dict(res.json() or {})


async def create_article(article: dict[str, Any]) -> tuple[str, str]:
    """Create the article; answers `(id, url)`."""
    res = await social_http.call(
        "POST",
        f"{API}/articles",
        service=SERVICE,
        headers=_headers(_key()),
        json={"article": article},
    )
    body = res.json() or {}
    return str(body.get("id") or ""), str(body.get("url") or "")


# --- the connector -----------------------------------------------------------


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=False, error=error)
    if not cred or not cred.access_token:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or CONNECTOR_ID),
            label=str(account.get("label") or "dev.to"),
        ),
        scopes=["articles"],
    )


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [
            {
                "name": "api_key",
                "label": "dev.to API key",
                "secret": True,
                "required": True,
                "help": (
                    "Generate one at dev.to → Settings → Extensions → DEV Community API "
                    "Keys. Scrive uses it only to post articles you approve in its outbox."
                ),
            }
        ],
    }


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    key = (values.get("api_key") or "").strip()
    if not key:
        # Blank on a reconfigure keeps the stored key (a secret field cannot prefill).
        if store.is_connected(CONNECTOR_ID):
            return {"connected": True}
        return {"error": "a dev.to API key is required"}
    try:
        user = await me(key)
    except social_http.SendError as exc:
        # Verified before it is called connected: a key that does not work is worse
        # than none, because the failure would surface only at send time.
        return {"error": str(exc)}
    username = str(user.get("username") or "")
    account = {
        "id": str(user.get("id") or username or CONNECTOR_ID),
        "label": f"@{username}" if username else "dev.to",
    }
    store.save(
        CONNECTOR_ID, Credential(access_token=key, scopes=["articles"], account=account)
    )
    return {"connected": True, "account": account}


async def _disconnect() -> None:
    store.clear(CONNECTOR_ID)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="dev.to",
        kind="api-key",
        icon="devto",
        blurb="Cross-post articles you approve in Scrive's outbox, linked back to the original.",
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[],
        # No agent tools, so no guide: the outbox runner is its only caller.
        guide=None,
        configured=None,
    )
