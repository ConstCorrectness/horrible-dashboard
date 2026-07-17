"""Shared OAuth machinery for connectors: the device flow, the loopback
authorization-code + PKCE flow, and access-token refresh.

Two properties this file exists to hold:

* **The token never reaches the browser.** Device codes and authorize URLs do; the
  access token is exchanged inside this process and written straight to the encrypted
  store. The frontend only ever polls for `{connected}`.
* **The redirect URI is a constant**, derived from the backend's own bound port rather
  than the incoming request. `request.url_for()` is Host-header dependent, so behind
  the dev Vite proxy it resolves to :5173 while the provider demands an exact
  registered match — that mismatch is what made the previous Google integration
  unusable in dev.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets as pysecrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from backend.modules.connectors import store
from backend.modules.connectors.store import Credential

logger = logging.getLogger(__name__)

# How long an interactive flow may sit unfinished before we forget it.
FLOW_TTL_S = 900.0

# Exchanges an authorization code for a credential. Returns a `Credential`, or a dict
# with an `error` key.
CodeExchange = Callable[[str, str], Awaitable["Credential | dict[str, Any]"]]

# Refreshes an expiring credential in place. Same return contract as CodeExchange.
Refresher = Callable[[Credential], Awaitable["Credential | dict[str, Any]"]]


def backend_origin() -> str:
    """The origin a provider should redirect back to.

    Always loopback on the backend's own port — never the request's Host — so one
    registered redirect URI works in dev (behind the Vite proxy), in prod, and under
    Tauri alike.
    """
    port = os.environ.get("HORRIBLE_DEV_BACKEND_PORT") or os.environ.get(
        "HORRIBLE_BACKEND_PORT", "8000"
    )
    return f"http://127.0.0.1:{port}"


def redirect_uri(connector_id: str) -> str:
    return f"{backend_origin()}/api/connectors/{connector_id}/callback"


# --- PKCE -------------------------------------------------------------------


def new_pkce_pair() -> tuple[str, str]:
    """`(verifier, challenge)` for PKCE S256."""
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# --- in-flight flows --------------------------------------------------------
#
# Deliberately in-process: an interrupted sign-in should die with the process rather
# than linger on disk. One interactive flow per connector at a time.


@dataclass
class _Flow:
    """An interactive connect flow waiting on the user."""

    expires_at: float
    # redirect flows
    state: str = ""
    code_verifier: str = ""
    exchange: CodeExchange | None = None
    # device flows
    device_code: str = ""
    poll: Callable[[str], Awaitable[Any]] | None = None
    interval: float = 5.0
    # terminal result, set once the flow completes
    done: dict[str, Any] = field(default_factory=dict)

    def expired(self) -> bool:
        return time.time() > self.expires_at


_flows: dict[str, _Flow] = {}

# Serializes refreshes per connector so a burst of parallel tool calls can't each
# spend the refresh token.
_refresh_locks: dict[str, asyncio.Lock] = {}


def _lock_for(connector_id: str) -> asyncio.Lock:
    lock = _refresh_locks.get(connector_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[connector_id] = lock
    return lock


def reset_flows() -> None:
    """Drop all in-flight flows (used between tests)."""
    _flows.clear()
    _refresh_locks.clear()


def _account_of(cred: Credential) -> dict[str, Any]:
    return cred.account or {}


# --- redirect (authorization-code) flow -------------------------------------


def begin_redirect(
    connector_id: str,
    *,
    authorize_url: Callable[[str, str], str],
    exchange: CodeExchange,
) -> dict[str, Any]:
    """Start a redirect flow.

    `authorize_url(state, challenge)` builds the provider's consent URL; `exchange`
    turns the returned code into a credential. Returns the step the browser needs.
    """
    state = pysecrets.token_urlsafe(32)
    verifier, challenge = new_pkce_pair()
    _flows[connector_id] = _Flow(
        expires_at=time.time() + FLOW_TTL_S,
        state=state,
        code_verifier=verifier,
        exchange=exchange,
    )
    return {
        "step": "redirect",
        "authorize_url": authorize_url(state, challenge),
        "expires_in": FLOW_TTL_S,
    }


async def finish_redirect(
    connector_id: str, *, code: str, state: str
) -> dict[str, Any]:
    """Handle the provider's callback: verify `state`, exchange the code, persist.

    `state` is checked with a constant-time compare against the value minted at
    `begin_redirect` and consumed once — an unknown or replayed state is rejected
    rather than exchanged.
    """
    flow = _flows.get(connector_id)
    if flow is None:
        return {"error": "no sign-in in progress"}
    if flow.expired():
        _flows.pop(connector_id, None)
        return {"error": "sign-in timed out — start again"}
    if not code:
        return {"error": "the provider returned no authorization code"}
    if not state or not pysecrets.compare_digest(state, flow.state):
        # Do not consume the flow: a forged callback must not cancel the real one.
        return {"error": "state mismatch — sign-in rejected"}
    if flow.exchange is None:
        return {"error": "connector cannot complete a redirect sign-in"}

    result = await flow.exchange(code, flow.code_verifier)
    if isinstance(result, dict):
        flow.done = {"error": result.get("error") or "sign-in failed"}
        return flow.done

    store.save(connector_id, result)
    flow.done = {"connected": True, "account": _account_of(result)}
    return flow.done


# --- device flow ------------------------------------------------------------


def begin_device(
    connector_id: str,
    *,
    user_code: str,
    verification_uri: str,
    device_code: str,
    poll: Callable[[str], Awaitable[Any]],
    interval: float = 5.0,
    expires_in: float = FLOW_TTL_S,
) -> dict[str, Any]:
    """Register a started device flow and return the step the user acts on."""
    _flows[connector_id] = _Flow(
        expires_at=time.time() + expires_in,
        device_code=device_code,
        poll=poll,
        interval=interval,
    )
    return {
        "step": "device",
        "user_code": user_code,
        "verification_uri": verification_uri,
        "interval": interval,
        "expires_in": expires_in,
    }


async def poll_flow(connector_id: str) -> dict[str, Any]:
    """Poll whichever flow is in progress for this connector.

    Redirect flows complete in the callback, so polling one just reports the result.
    Device flows are driven from here.
    """
    flow = _flows.get(connector_id)
    if flow is None:
        # Already finished and cleaned up, or never started — report the ground truth.
        if store.is_connected(connector_id):
            return {"connected": True}
        return {"error": "no sign-in in progress"}
    if flow.done:
        _flows.pop(connector_id, None)
        return flow.done
    if flow.expired():
        _flows.pop(connector_id, None)
        return {"error": "sign-in timed out — start again"}
    if flow.poll is None:
        # A redirect flow still waiting on the callback.
        return {"pending": True}

    result = await flow.poll(flow.device_code)
    if isinstance(result, dict):
        if result.get("pending"):
            return {"pending": True}
        _flows.pop(connector_id, None)
        return {"error": result.get("error") or "sign-in failed"}

    store.save(connector_id, result)
    _flows.pop(connector_id, None)
    return {"connected": True, "account": _account_of(result)}


def cancel_flow(connector_id: str) -> None:
    _flows.pop(connector_id, None)


# --- refresh ----------------------------------------------------------------


async def ensure_fresh(
    connector_id: str, refresh: Refresher | None
) -> Credential | None:
    """The connector's credential, refreshed if it's within the expiry window.

    Every tool calls this before touching a provider API. Serialized per connector, and
    the expiry is re-checked *inside* the lock so a queue of parallel callers refreshes
    once and then reuses the result rather than each spending the refresh token.
    """
    cred, error = store.load_or_error(connector_id)
    if error or cred is None:
        return None
    if not cred.is_expired() or refresh is None or not cred.refresh_token:
        return cred

    async with _lock_for(connector_id):
        cred, error = store.load_or_error(connector_id)
        if error or cred is None:
            return None
        if not cred.is_expired():
            return cred

        result = await refresh(cred)
        if isinstance(result, dict):
            logger.warning(
                "connector %s token refresh failed: %s",
                connector_id,
                result.get("error"),
            )
            return None
        # Providers may or may not rotate the refresh token; keep the old one if the
        # response omitted it, or the next refresh has nothing to present.
        if not result.refresh_token:
            result.refresh_token = cred.refresh_token
        if not result.account:
            result.account = cred.account
        if not result.scopes:
            result.scopes = cred.scopes
        store.save(connector_id, result)
        return result
