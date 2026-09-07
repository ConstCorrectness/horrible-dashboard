"""NVIDIA: the NGC model catalog, and NIM endpoints as a chat provider.

Two things this node could not do before. It could probe an NVIDIA card
(`nvidia-smi`, in the hardware module) and that was the whole relationship — no
model catalog, no hosted inference, no synthetic-data engine.

An **api-key** connector rather than OAuth: NGC issues personal API keys and has no
device flow, so there is nothing to redirect to.

**A NIM endpoint is one `PROVIDERS` entry with `dialect="openai"`, not a new
dialect.** NIM speaks the OpenAI chat API. A bespoke dialect would mean six new
branches in `providers.py` and would silently lose the `tool_choice="required"`
retry, which is gated on `info.dialect == "openai"` — the exact mistake the
llama.cpp integration documents having avoided. So the connector's job is to hold
the key and list what is available; serving is the existing openai path.

The key is one credential with two uses, and they are different endpoints:
`https://api.ngc.nvidia.com` for the catalog, `https://integrate.api.nvidia.com/v1`
for inference. Both take the same `Bearer`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.modules.connectors import store
from backend.modules.connectors.guides import guide_loader
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

logger = logging.getLogger(__name__)

CONNECTOR_ID = "nvidia"

#: The hosted inference endpoint. OpenAI-compatible, which is the whole reason
#: this needs no new dialect.
NIM_BASE = "https://integrate.api.nvidia.com/v1"

#: The catalog. A different host from inference, same key.
NGC_BASE = "https://api.ngc.nvidia.com/v2"

TIMEOUT_S = 20.0


class NvidiaError(RuntimeError):
    """NGC could not answer. Carries something worth showing the user."""


def api_key() -> str:
    """The stored NGC key, or ''. Never reaches the browser."""
    cred = store.load(CONNECTOR_ID)
    return (cred or {}).get("access_token", "") if cred else ""


def _headers() -> dict[str, str]:
    key = api_key()
    if not key:
        raise NvidiaError("no NVIDIA API key is connected")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


async def list_models(limit: int = 50) -> list[dict[str, Any]]:
    """Models this key can call, from the NIM inference endpoint.

    Deliberately the *inference* listing rather than the NGC catalog: what the user
    wants to know is "what can I actually run", and the catalog lists a great deal
    that a given key cannot reach.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.get(f"{NIM_BASE}/models", headers=_headers())
        except httpx.HTTPError as exc:
            raise NvidiaError(f"could not reach NVIDIA: {exc}") from exc
    if response.status_code == 401:
        raise NvidiaError("the NVIDIA API key was rejected")
    if response.status_code >= 400:
        raise NvidiaError(f"NVIDIA returned {response.status_code}")
    rows = (response.json() or {}).get("data") or []
    return [
        {
            "id": str(row.get("id") or ""),
            "owner": str(row.get("owned_by") or ""),
            "root": str(row.get("root") or ""),
        }
        for row in rows[:limit]
        if row.get("id")
    ]


async def search_catalog(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """NGC catalog search — containers, models and resources."""
    params = {"q": query or "*", "page-size": str(max(1, min(limit, 100)))}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.get(
                f"{NGC_BASE}/search/catalog/resources/MODEL",
                headers=_headers(),
                params=params,
            )
        except httpx.HTTPError as exc:
            raise NvidiaError(f"could not reach NGC: {exc}") from exc
    if response.status_code >= 400:
        raise NvidiaError(f"NGC returned {response.status_code}")
    results = (response.json() or {}).get("results") or []
    out: list[dict[str, Any]] = []
    for group in results:
        for entry in group.get("resources") or []:
            out.append(
                {
                    "name": str(entry.get("name") or ""),
                    "displayName": str(entry.get("displayName") or ""),
                    "publisher": str(entry.get("publisher") or ""),
                    "description": str(entry.get("description") or "")[:300],
                }
            )
    return out[:limit]


# --- the connector -----------------------------------------------------------


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=False, error=error)
    if not cred or not cred.get("access_token"):
        return ConnectorStatus(connected=False)
    account = cred.get("account") or {}
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or CONNECTOR_ID),
            label=str(account.get("label") or "NVIDIA API key"),
        ),
        scopes=["nim", "ngc"],
    )


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [
            {
                "name": "api_key",
                "label": "NGC API key",
                "secret": True,
                "required": True,
                "help": (
                    "Create one at build.nvidia.com or ngc.nvidia.com under Setup → "
                    "API Key. The same key reaches the model catalog and the hosted "
                    "NIM inference endpoints."
                ),
            }
        ],
    }


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    key = (values.get("api_key") or "").strip()
    if not key:
        # A blank field on a reconfigure means "leave it alone", never "clear it" —
        # the form cannot prefill a secret, so blank is the default state of a
        # field whose key is already stored.
        if api_key():
            return {
                "connected": True,
                "account": {"id": CONNECTOR_ID, "label": "NVIDIA"},
            }
        return {"error": "an NGC API key is required"}

    store.save(CONNECTOR_ID, {"access_token": key, "scopes": ["nim", "ngc"]})
    try:
        models = await list_models(limit=1)
    except NvidiaError as exc:
        # Verified before it is called connected: a key that does not work is
        # worse than no key, because everything downstream then blames itself.
        store.clear(CONNECTOR_ID)
        return {"error": str(exc)}

    label = f"NVIDIA ({models[0]['id']}…)" if models else "NVIDIA"
    store.save(
        CONNECTOR_ID,
        {
            "access_token": key,
            "scopes": ["nim", "ngc"],
            "account": {"id": CONNECTOR_ID, "label": label},
        },
    )
    return {"connected": True, "account": {"id": CONNECTOR_ID, "label": label}}


async def _disconnect() -> None:
    store.clear(CONNECTOR_ID)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="NVIDIA",
        kind="api-key",
        icon="nvidia",
        # Doubles as the agent's tool-group blurb in `list_tool_groups`.
        blurb=(
            "Browse the NGC model catalog and call NVIDIA's hosted NIM endpoints, "
            "which speak the OpenAI API and can be used as a chat or eval provider."
        ),
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[
            ConnectorScope(
                id="nim",
                label="Hosted inference",
                description="Call NIM models at integrate.api.nvidia.com.",
            ),
            ConnectorScope(
                id="ngc",
                label="Model catalog",
                description="Search NGC for models and resources.",
            ),
        ],
        guide=guide_loader(CONNECTOR_ID),
        configured=None,
    )
