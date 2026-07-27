"""The `search` connector: one tile that holds every search provider's API key.

**One connector, not one per provider.** A connector's `id` must equal the namespace
of the agent tools it enables, because the orchestrator derives a tool's group from
its name prefix. Four connectors would therefore mean `tavily.search`, `brave.search`
… — four tool groups, four catalog blurbs and four `load_tools` round-trips for a
single capability, and no provider-agnostic tool for the model to call. One connector
called `search` gives one `search.*` group whose provider is an implementation detail
the user can switch without the agent noticing.

The connect form asks for every provider's key at once, each optional. There is no
`select` field type in the connect-form vocabulary, so provider *choice* couldn't
live here anyway — and it shouldn't: a vendor's name is public, so it's an ordinary
setting (`search.provider`). Only the key is secret.

**`connected` means "you added paid providers", not "search works".** The tools are
registered unconditionally and SearXNG and the DuckDuckGo fallback need no key, so
search answers queries with this tile untouched. Conflating the two would push people
into paying for something they already have.
"""

from __future__ import annotations

from typing import Any

from backend.modules.connectors.guides import guide_loader
from backend.modules.search import credentials
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

# MUST equal the agent-tool prefix — see the module docstring and `Connector.id`.
CONNECTOR_ID = "search"


def _field(provider: str) -> dict[str, Any]:
    stored = bool(credentials.get_key(provider))
    if credentials.key_from_env(provider):
        help_text = f"Set by {credentials.env_var(provider)} — edit the environment to change it."
    elif stored:
        help_text = "Configured. Leave blank to keep the existing key."
    else:
        help_text = credentials.SIGNUP_HINTS.get(provider, "")
    return {
        "name": f"{provider}_api_key",
        "label": f"{credentials.LABELS[provider]} API key",
        "secret": True,
        "placeholder": "",
        # Deliberately never prefilled, even on reconfigure — a secret this module
        # hands back to the browser is a secret it no longer protects.
        "value": "",
        "help": help_text,
    }


def _form_step() -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [_field(p) for p in credentials.KEYED_PROVIDERS],
    }


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return _form_step()


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    """Persist whichever keys were filled in.

    A blank field means "leave it alone", never "clear it" — the form can't prefill a
    secret, so blank is the default state of a field whose key is already stored, and
    treating it as a delete would wipe a working key on every reconfigure.
    """
    saved: list[str] = []
    for provider in credentials.KEYED_PROVIDERS:
        value = (values.get(f"{provider}_api_key") or "").strip()
        if value:
            credentials.set_key(provider, value)
            saved.append(provider)

    status = _status()
    if not status.connected:
        return {
            "error": (
                "No API key was entered. Search still works without one — SearXNG "
                "and the keyless fallback need no key."
            )
        }
    return {
        "connected": True,
        "account": {
            "id": CONNECTOR_ID,
            "label": status.account.label if status.account else "",
        },
    }


def _status() -> ConnectorStatus:
    keyed = credentials.configured_providers()
    if not keyed:
        return ConnectorStatus(connected=False)
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=CONNECTOR_ID,
            label=", ".join(credentials.LABELS[p] for p in keyed),
        ),
        scopes=keyed,
    )


async def _disconnect() -> None:
    credentials.clear_keys()


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Web Search",
        kind="api-key",
        icon="search",
        # Doubles as the agent's tool-group blurb in `list_tool_groups`, which is why
        # it names the capability rather than the vendors.
        blurb=(
            "Search the live web, read pages, and query this node's own crawled "
            "index of ML sites and API docs."
        ),
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[
            ConnectorScope(
                id=provider,
                label=credentials.LABELS[provider],
                description=credentials.SIGNUP_HINTS.get(provider, ""),
            )
            for provider in credentials.KEYED_PROVIDERS
        ],
        guide=guide_loader(CONNECTOR_ID),
        # Deliberately None: the keys *are* the credential, so there is no separate
        # client-config step. Setting it would render a "Configure" button that has
        # nothing to configure.
        configured=None,
    )
