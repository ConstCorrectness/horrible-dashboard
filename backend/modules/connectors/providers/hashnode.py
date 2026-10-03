"""The Hashnode connector: a personal access token and one publication, and the
GraphQL mutation Scrive's outbox makes to cross-post a page as a whole article.

What the design has to respect:

- **The token goes bare in `Authorization`** — no `Bearer`. With the prefix Hashnode
  does not say the token is wrong; the request just acts as nobody.
- **GraphQL answers 200 with `errors`.** A status check is not enough, so `graphql`
  reads the body and raises `SendError` on any error. One whose code says the token
  was refused is reported like an HTTP 401 (reconnect); the rest are the platform's
  own message about the post.
- **A post goes to a publication, by id.** Connecting asks for the publication's host
  (`you.hashnode.dev`, or a custom domain) when the account has more than one, and
  stores its id and URL in the credential, so a send never has to look it up.
- **At most five tags**, each `{slug, name}`.
- `originalArticleURL` is the canonical link back to the Pages post.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from backend.modules.connectors import store
from backend.modules.connectors.providers import social_http
from backend.modules.connectors.store import Credential
from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

CONNECTOR_ID = "hashnode"
SERVICE = "Hashnode"
API = "https://gql.hashnode.com"
MAX_TAGS = 5

_ME = """query Me {
  me {
    id
    username
    publications(first: 20) { edges { node { id title url } } }
  }
}"""

_PUBLISH = """mutation Publish($input: PublishPostInput!) {
  publishPost(input: $input) { post { id url slug } }
}"""

_AUTH_CODES = {"UNAUTHENTICATED", "FORBIDDEN"}


def tag_slug(tag: str) -> str:
    """A tag's slug as Hashnode keys it: lowercase words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


async def graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """One GraphQL call; its `data`, or `SendError` with what Hashnode said."""
    res = await social_http.call(
        "POST",
        API,
        service=SERVICE,
        headers={"Authorization": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
    )
    try:
        body = res.json() or {}
    except ValueError as exc:
        # An infrastructure failure answers an HTML page with a 200 now and then.
        raise social_http.SendError(
            f"{SERVICE} answered something that is not JSON", transient=True
        ) from exc
    errors = body.get("errors") or []
    if errors:
        first = (
            errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        )
        code = str((first.get("extensions") or {}).get("code") or "")
        message = str(first.get("message") or "unknown error")
        if code in _AUTH_CODES:
            raise social_http.SendError(
                f"{SERVICE} rejected the stored token — reconnect {SERVICE} from the home "
                f"page. ({message})",
                status=401,
            )
        # An answer, not a lost request: the post was not created.
        raise social_http.SendError(f"{SERVICE}: {message}", status=400)
    return dict(body.get("data") or {})


def _token() -> tuple[str, dict[str, Any]]:
    cred = store.load(CONNECTOR_ID)
    if not cred or not cred.access_token:
        raise social_http.SendError(
            "Hashnode isn't connected — connect it from the home page.", status=401
        )
    return cred.access_token, dict(cred.account or {})


def publication() -> dict[str, str]:
    """The connected publication: `{id, title, url}` ('' values when not connected)."""
    cred = store.load(CONNECTOR_ID)
    account = (cred.account if cred else None) or {}
    return {
        "id": str(account.get("publication_id") or ""),
        "title": str(account.get("publication_title") or ""),
        "url": str(account.get("publication_url") or ""),
    }


async def publish_post(post: dict[str, Any]) -> tuple[str, str]:
    """Publish to the connected publication; answers `(id, url)`."""
    token, account = _token()
    publication_id = str(account.get("publication_id") or "")
    if not publication_id:
        raise social_http.SendError(
            "No Hashnode publication is chosen — reconnect Hashnode and name one.",
            status=400,
        )
    data = await graphql(
        _PUBLISH, {"input": {**post, "publicationId": publication_id}}, token
    )
    created = ((data.get("publishPost") or {}).get("post")) or {}
    return str(created.get("id") or ""), str(created.get("url") or "")


def _host(url: str) -> str:
    raw = url.strip().lower()
    return (urlparse(raw if "://" in raw else f"https://{raw}").hostname or "").rstrip(
        "."
    )


def choose_publication(
    publications: list[dict[str, Any]], host: str
) -> tuple[dict[str, Any] | None, str]:
    """The publication `host` names, or the only one; else `(None, why)`."""
    if not publications:
        return None, "This Hashnode account has no publication (blog) to post to yet."
    if host:
        want = _host(host)
        for pub in publications:
            if _host(str(pub.get("url") or "")) == want:
                return pub, ""
        listed = ", ".join(_host(str(p.get("url") or "")) for p in publications)
        return None, f"No publication at {want}. This account has: {listed}."
    if len(publications) == 1:
        return publications[0], ""
    listed = ", ".join(_host(str(p.get("url") or "")) for p in publications)
    return None, f"This account has several publications; name one: {listed}."


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
            label=str(account.get("label") or "Hashnode"),
        ),
        scopes=["publish"],
    )


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [
            {
                "name": "api_key",
                "label": "Personal access token",
                "secret": True,
                "required": True,
                "help": (
                    "Generate one at hashnode.com → Account settings → Developer. Scrive "
                    "uses it only to publish articles you approve in its outbox."
                ),
            },
            {
                "name": "host",
                "label": "Publication (optional)",
                "placeholder": "you.hashnode.dev",
                # Public — prefilled, so a reconfigure shows which one is chosen.
                "value": _host(publication()["url"]) if publication()["url"] else "",
                "help": (
                    "The blog's address, such as you.hashnode.dev or a custom domain. "
                    "Needed only when the account has more than one."
                ),
            },
        ],
    }


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    token = (values.get("api_key") or "").strip()
    host = (values.get("host") or "").strip()
    if not token:
        cred = store.load(CONNECTOR_ID)
        if not cred or not cred.access_token:
            return {"error": "a Hashnode personal access token is required"}
        if not host:
            return {"connected": True}
        token = cred.access_token  # changing the publication keeps the token
    try:
        data = await graphql(_ME, {}, token)
    except social_http.SendError as exc:
        return {"error": str(exc)}
    me = data.get("me") or {}
    if not me:
        # No error and no user: the token did not authenticate (see the docstring).
        return {"error": "Hashnode did not accept that token."}
    edges = ((me.get("publications") or {}).get("edges")) or []
    publications = [e.get("node") or {} for e in edges if isinstance(e, dict)]
    chosen, why = choose_publication(publications, host)
    if chosen is None:
        return {"error": why}
    username = str(me.get("username") or "")
    account = {
        "id": str(me.get("id") or username or CONNECTOR_ID),
        "label": f"@{username} · {_host(str(chosen.get('url') or ''))}",
        "publication_id": str(chosen.get("id") or ""),
        "publication_title": str(chosen.get("title") or ""),
        "publication_url": str(chosen.get("url") or ""),
    }
    store.save(
        CONNECTOR_ID,
        Credential(access_token=token, scopes=["publish"], account=account),
    )
    return {
        "connected": True,
        "account": {"id": account["id"], "label": account["label"]},
    }


async def _disconnect() -> None:
    store.clear(CONNECTOR_ID)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Hashnode",
        kind="api-key",
        icon="hashnode",
        blurb="Cross-post articles you approve in Scrive's outbox, linked back to the original.",
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[],
        guide=None,
        configured=None,
    )
