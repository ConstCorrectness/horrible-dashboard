"""Client-credential custody for OAuth connectors: resolve, present, persist.

Every OAuth connector needs the same three things — a client id, sometimes a client
secret, and a way for the user to supply both without hand-editing the environment.
This module owns that contract so `google.py` and `github.py` don't each reinvent it.

**Where each half lives, and why they differ.** A client id is public by design (it
ships in every authorize URL), so it's an ordinary setting. A client secret is not:
`GET /api/settings` hands the whole settings bag to the browser, so a secret stored
there would be readable by any page the app renders. Secrets go to the Fernet-encrypted
`secrets.db` instead, under `<connector>_client_secret`, and are **never** echoed back —
not in a form prefill, not in a status payload.

**Precedence is env → stored.** An operator who pins `GOOGLE_CLIENT_ID` in the
environment means it; the UI must not silently shadow it with a stored value that has
no effect. So an env-pinned field renders with an explanatory `help` line and its
submitted value is discarded rather than persisted — persisting it would record a
setting that never gets read.
"""

from __future__ import annotations

import os
from typing import Any


def _setting_key(connector_id: str) -> str:
    return f"connectors.{connector_id}.clientId"


def _secret_key(connector_id: str) -> str:
    return f"{connector_id}_client_secret"


def client_id(connector_id: str, env_var: str) -> str:
    """The effective client id: environment first, then the stored setting."""
    from backend.modules.settings.routes import get_value

    return str(os.environ.get(env_var, "") or get_value(_setting_key(connector_id), ""))


def client_secret(connector_id: str, env_var: str) -> str:
    """The effective client secret: environment first, then the encrypted store.

    Never a setting — see the module docstring.
    """
    from backend.modules.database.secrets_store import get_secret_or_none

    return str(
        os.environ.get(env_var, "")
        or get_secret_or_none(_secret_key(connector_id))
        or ""
    )


def id_from_env(env_var: str) -> bool:
    return bool(os.environ.get(env_var, ""))


def secret_from_env(env_var: str) -> bool:
    return bool(os.environ.get(env_var, ""))


def is_configured(
    connector_id: str, *, id_env: str, secret_env: str | None = None
) -> bool:
    """Whether this node has everything the connector needs to start a flow."""
    if not client_id(connector_id, id_env):
        return False
    if secret_env is not None and not client_secret(connector_id, secret_env):
        return False
    return True


def configure_step(
    connector_id: str,
    *,
    id_env: str,
    secret_env: str | None = None,
    id_label: str = "Client ID",
    secret_label: str = "Client secret",
    id_help: str = "",
    secret_help: str = "",
) -> dict[str, Any]:
    """A `form` step asking for this connector's client credentials.

    Returned in place of an error when the connector isn't configured, so the popover
    walks the user into a form instead of dead-ending on a string they can't act on.
    """
    fields: list[dict[str, Any]] = [
        {
            "name": "client_id",
            "label": id_label,
            "placeholder": "",
            # Public — safe to prefill so a reconfigure doesn't force a retype.
            "value": client_id(connector_id, id_env),
            "help": (
                f"Set by {id_env} — edit the environment to change it."
                if id_from_env(id_env)
                else id_help
            ),
        }
    ]
    if secret_env is not None:
        fields.append(
            {
                "name": "client_secret",
                "label": secret_label,
                "secret": True,
                "placeholder": "",
                # Deliberately never prefilled, even on reconfigure.
                "value": "",
                "help": (
                    f"Set by {secret_env} — edit the environment to change it."
                    if secret_from_env(secret_env)
                    else secret_help
                ),
            }
        )
    return {"step": "form", "fields": fields}


def apply_config(
    connector_id: str,
    values: dict[str, str],
    *,
    id_env: str,
    secret_env: str | None = None,
) -> str | None:
    """Persist submitted client credentials. Returns an error message, or None on success.

    Env-pinned values are ignored rather than stored (see the module docstring). A blank
    secret on a connector that already has one is treated as "leave it alone", so a
    reconfigure that only changes the id doesn't wipe the secret — the form can't
    prefill it, so blank cannot mean "clear it".
    """
    from backend.modules.database.secrets_store import upsert_secret
    from backend.modules.settings.routes import set_value

    if not id_from_env(id_env):
        submitted_id = (values.get("client_id") or "").strip()
        if not submitted_id:
            return "A client ID is required."
        set_value(_setting_key(connector_id), submitted_id)

    if secret_env is not None and not secret_from_env(secret_env):
        submitted_secret = (values.get("client_secret") or "").strip()
        if submitted_secret:
            upsert_secret(_secret_key(connector_id), submitted_secret)
        elif not client_secret(connector_id, secret_env):
            return "A client secret is required."

    return None
