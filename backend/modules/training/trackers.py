"""Experiment-tracker credentials, and the `trackers` connector that holds them.

`TrainingArguments.report_to` is a list of third parties a run streams metrics
to. Two of them need a credential — Weights & Biases wants an API key, MLflow a
tracking URI that routinely embeds one — and a credential is exactly what must
**not** be a setting: `GET /api/settings` hands the whole bag to the browser, so
a key stored there is a key that has left the machine. They live in the
connectors' Fernet-encrypted secret store instead, the same place the search
module keeps its API keys.

**One connector, not one per tracker.** The `search` connector is the precedent:
a tile per vendor would be four tiles for one capability. Unlike `search` this
one contributes **no agent tools at all**, so the "connector id must equal the
agent-tool prefix" rule has nothing to bind — there is no `trackers.*` group,
because "log this run to W&B" is not something the agent should be doing behind
your back; it is a property of a recipe you wrote.

**Local metrics remain authoritative regardless.** The generated recipe always
installs `ht.callback()`, so the chart pane works offline and with `report_to`
set to `["none"]`. A tracker is additive — never the only place your numbers
went.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

logger = logging.getLogger(__name__)

CONNECTOR_ID = "trackers"

_WANDB_KEY = "training:wandb_api_key"
_MLFLOW_URI = "training:mlflow_tracking_uri"

#: Honoured when set, so a CI box or a container can supply the key the usual
#: way. The stored credential wins only when the environment says nothing.
WANDB_ENV = "WANDB_API_KEY"
MLFLOW_ENV = "MLFLOW_TRACKING_URI"


def _get(name: str) -> str:
    from backend.modules.database.secrets_store import get_secret_or_none

    try:
        return (get_secret_or_none(name) or "").strip()
    except Exception as exc:  # noqa: BLE001 — an unreadable store is "no key"
        logger.info("training: could not read %s (%s)", name, exc)
        return ""


def _set(name: str, value: str) -> None:
    from backend.modules.database.secrets_store import delete_secret, upsert_secret

    if value:
        upsert_secret(name, value)
    else:
        delete_secret(name)


def wandb_key() -> str:
    return os.environ.get(WANDB_ENV, "").strip() or _get(_WANDB_KEY)


def mlflow_uri() -> str:
    return os.environ.get(MLFLOW_ENV, "").strip() or _get(_MLFLOW_URI)


def has_wandb_key() -> bool:
    return bool(wandb_key())


def env_for(trackers: list[str]) -> dict[str, str]:
    """Environment variables a run needs for the trackers it asked for.

    Merged into the kernel's / script runner's environment at **spawn** time, so
    the credential reaches the training process without ever being written into
    a notebook, a project file, or an HTTP response. A tracker that wasn't
    selected contributes nothing: connecting the tile must not silently start
    shipping every run somewhere.
    """
    env: dict[str, str] = {}
    if "wandb" in trackers and (key := wandb_key()):
        env[WANDB_ENV] = key
    if "mlflow" in trackers and (uri := mlflow_uri()):
        env[MLFLOW_ENV] = uri
    return env


# --- the connector ------------------------------------------------------------


def _form_step() -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [
            {
                "name": "wandb_api_key",
                "label": "Weights & Biases API key",
                "secret": True,
                # Never prefilled, even on reconfigure: a secret handed back to
                # the browser is a secret this module no longer protects.
                "value": "",
                "help": (
                    "Configured. Leave blank to keep it."
                    if _get(_WANDB_KEY)
                    else "From wandb.ai/authorize. Optional — only needed if a "
                    "recipe selects W&B."
                ),
            },
            {
                "name": "mlflow_tracking_uri",
                "label": "MLflow tracking URI",
                "secret": True,
                "value": "",
                "help": (
                    "Configured. Leave blank to keep it."
                    if _get(_MLFLOW_URI)
                    else "e.g. https://user:token@mlflow.example.com. Secret "
                    "because a tracking URI routinely embeds credentials."
                ),
            },
        ],
    }


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return _form_step()


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    """Store whichever fields were filled in.

    Blank means "leave it alone", never "clear it": the form cannot prefill a
    secret, so blank is the resting state of a field that already has one and
    treating it as a delete would wipe a working key on every reconfigure.
    """
    for name, key in (
        (_WANDB_KEY, "wandb_api_key"),
        (_MLFLOW_URI, "mlflow_tracking_uri"),
    ):
        value = (values.get(key) or "").strip()
        if value:
            _set(name, value)
    status = _status()
    if not status.connected:
        return {
            "error": (
                "Nothing was entered. Training works without this — local metrics "
                "are collected either way, and tensorboard and trackio need no key."
            )
        }
    return {"connected": True, "account": {"id": CONNECTOR_ID, "label": _label()}}


def _configured() -> list[str]:
    found = []
    if wandb_key():
        found.append("wandb")
    if mlflow_uri():
        found.append("mlflow")
    return found


def _label() -> str:
    names = {"wandb": "Weights & Biases", "mlflow": "MLflow"}
    return ", ".join(names[c] for c in _configured())


def _status() -> ConnectorStatus:
    configured = _configured()
    if not configured:
        return ConnectorStatus(connected=False)
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(id=CONNECTOR_ID, label=_label()),
        scopes=configured,
    )


async def _disconnect() -> None:
    _set(_WANDB_KEY, "")
    _set(_MLFLOW_URI, "")


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Experiment trackers",
        kind="api-key",
        icon="chart",
        blurb=(
            "Credentials for Weights & Biases and MLflow, so a training recipe can "
            "stream its metrics to them as well as to this node."
        ),
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[
            ConnectorScope(
                id="wandb",
                label="Weights & Biases",
                description="API key from wandb.ai/authorize",
            ),
            ConnectorScope(
                id="mlflow",
                label="MLflow",
                description="Tracking URI, which usually embeds a credential",
            ),
        ],
        configured=None,
    )
