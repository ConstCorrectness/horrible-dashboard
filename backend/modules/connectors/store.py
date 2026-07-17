"""Credential custody for connectors.

One encrypted record per connector, held in the shared secrets store under
`connector:<id>`. The browser never sees any of this — routes return a
`ConnectorStatus` (connected? which account? which scopes?), never a token.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.modules.database.secrets_store import (
    SecretDecryptError,
    delete_secret,
    get_secret,
    upsert_secret,
)

# Refresh an access token this many seconds before it actually expires, so a call
# that starts just under the wire doesn't land just over it.
REFRESH_WINDOW_S = 60


def _key(connector_id: str) -> str:
    return f"connector:{connector_id}"


@dataclass
class Credential:
    """What the node holds for a connected account.

    `expires_at` is an absolute epoch second, not a duration — providers hand back
    `expires_in` relative to *their* clock at grant time, which is useless once stored.
    """

    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    scopes: list[str] = field(default_factory=list)
    account: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, *, window_s: float = REFRESH_WINDOW_S) -> bool:
        """True if the access token is gone or about to be. A credential with no
        `expires_at` never expires (GitHub OAuth App user tokens)."""
        if self.expires_at is None:
            return False
        return time.time() >= self.expires_at - window_s


def save(connector_id: str, cred: Credential) -> None:
    upsert_secret(_key(connector_id), json.dumps(asdict(cred)))


def load(connector_id: str) -> Credential | None:
    """The stored credential, or None if never connected.

    Propagates `SecretDecryptError` — a credential that exists but won't decrypt is
    not the same as an absent one, and callers must be able to say so.
    """
    raw = get_secret(_key(connector_id))
    if raw is None:
        return None
    data = json.loads(raw)
    return Credential(
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token"),
        expires_at=data.get("expires_at"),
        scopes=list(data.get("scopes") or []),
        account=dict(data.get("account") or {}),
    )


def clear(connector_id: str) -> bool:
    return delete_secret(_key(connector_id))


def is_connected(connector_id: str) -> bool:
    """Whether a usable credential exists. An unreadable record counts as connected —
    it *is* there — so the caller reports a broken connection rather than offering a
    fresh one; see `load_or_error`."""
    try:
        return load(connector_id) is not None
    except SecretDecryptError:
        return True


def load_or_error(connector_id: str) -> tuple[Credential | None, str | None]:
    """`(credential, error)`. Exactly one is non-None when a record exists."""
    try:
        return load(connector_id), None
    except SecretDecryptError as exc:
        return None, str(exc)
    except ValueError:
        return None, "stored credential is corrupted — disconnect and reconnect"
