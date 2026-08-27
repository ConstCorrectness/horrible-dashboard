"""Minting and revoking the session's **public link**.

The node talks to the relay; the browser never does anything but push media at
the URL it is handed. That split is the point:

- **The relay key never reaches the browser.** It is env-only on this node
  (`SHARE_RELAY_KEY`), used once to mint, and the browser is given only the
  token-bearing ingest URL. A key in the browser would be a key in every page the
  host has open.
- **The token is held here too.** Revoking has to work when the pane is closed,
  when the tab has crashed, and when the agent asks -- so "stop the public link"
  is a node operation, not a browser one.

The relay URL is a **setting** (it is public -- it appears in every link) while
the key is **env only** (`GET /api/settings` hands the whole bag to the browser;
see the `secretKeys` blanking note in CLAUDE.md, and prefer not needing the
backstop at all).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

#: How long a node waits on the relay. Short: minting is one small POST, and a
#: host clicking "create link" must not sit on a spinner because a relay in
#: another region is unwell.
TIMEOUT_S = 8.0


class LinkError(RuntimeError):
    """A minting or revoking failure worth showing the host verbatim."""


def relay_base() -> str:
    configured = str(get_value("share.relayUrl", "") or "").strip().rstrip("/")
    return configured


def _key() -> str:
    return os.environ.get("SHARE_RELAY_KEY", "")


def _headers() -> dict[str, str]:
    key = _key()
    return {"X-Relay-Key": key} if key else {}


class LinkHandle:
    """What the node keeps about a minted link.

    `ingest_url` is handed to the browser and nothing else is. In particular the
    token is *derivable* from that URL, which is fine -- the browser is the one
    party that legitimately needs to push to it -- but the key is not in it.
    """

    def __init__(
        self, token: str, view_url: str, ingest_url: str, expires_at: float
    ) -> None:
        self.token = token
        self.view_url = view_url
        self.ingest_url = ingest_url
        self.expires_at = expires_at


async def mint(
    *, title: str, ttl_s: int | None = None, passphrase: str = ""
) -> LinkHandle:
    """Ask the relay for a link. Raises `LinkError` with something a human can act on."""
    base = relay_base()
    if not base:
        raise LinkError(
            "No relay is configured. Set `share.relayUrl` in settings to the "
            "address of a share relay before minting a public link."
        )
    payload: dict[str, object] = {"title": title}
    if ttl_s is not None:
        payload["ttl_s"] = ttl_s
    if passphrase:
        payload["passphrase"] = passphrase

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.post(f"{base}/streams", json=payload, headers=_headers())
    except httpx.HTTPError as exc:
        raise LinkError(f"Could not reach the share relay at {base}: {exc}") from exc

    if res.status_code == 401:
        raise LinkError(
            "The relay rejected this node's key. Check SHARE_RELAY_KEY matches the relay's."
        )
    if res.status_code >= 400:
        raise LinkError(f"The relay refused to mint a link ({res.status_code}).")

    body = res.json()
    return LinkHandle(
        token=body["token"],
        view_url=body["view_url"],
        ingest_url=body["ingest_url"],
        expires_at=float(body.get("expires_at") or 0.0),
    )


async def revoke(token: str) -> bool:
    """Kill a link. Best effort by design.

    A relay that cannot be reached must not stop the host from ending a session:
    the link dies at expiry regardless, and refusing to close a session because a
    remote service is down would be the worse failure. The caller drops its copy
    of the token either way -- so this returns whether the relay confirmed it,
    never whether the node is finished with it.
    """
    base = relay_base()
    if not base or not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.delete(f"{base}/streams/{token}", headers=_headers())
        return res.status_code < 400
    except httpx.HTTPError as exc:
        logger.warning("share: could not revoke link on the relay: %s", exc)
        return False


async def start_restream(token: str, destination: str) -> str:
    """Ask the relay to push this stream to an RTMP destination.

    The stream key is resolved **here**, on the node, and travels node -> relay.
    It is never sent to the browser and never returned from this function: the
    caller gets a label, and every log line goes through `streaming.redact`.

    Raises `LinkError` with something the host can act on.
    """
    from backend.modules.share import streaming

    base = relay_base()
    if not base:
        raise LinkError("No relay is configured, so there is nothing to restream from.")
    if not token:
        raise LinkError("Mint a public link first — the relay restreams that stream.")

    target = streaming.target_url(destination)
    if not target:
        known = streaming.DESTINATIONS.get(destination)
        where = f" Find it in {known.where}." if known else ""
        raise LinkError(
            f"No stream key stored for {destination}. Add one in the streaming "
            f"connector.{where}"
        )
    label = (
        streaming.DESTINATIONS[destination].label
        if destination in streaming.DESTINATIONS
        else destination
    )

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.post(
                f"{base}/restream/{token}",
                json={"target": target, "label": label},
                headers=_headers(),
            )
    except httpx.HTTPError as exc:
        raise LinkError(f"Could not reach the share relay: {exc}") from exc

    if res.status_code == 503:
        raise LinkError(
            "That relay has no ffmpeg installed, so it cannot restream to RTMP."
        )
    if res.status_code == 409:
        raise LinkError("Start sharing your screen before restreaming it.")
    if res.status_code >= 400:
        raise LinkError(f"The relay refused to start the restream ({res.status_code}).")
    logger.info("share: restreaming to %s", streaming.redact(target))
    return label


async def stop_restream(token: str) -> bool:
    """Stop an RTMP push. Best effort, for the same reason `revoke` is."""
    base = relay_base()
    if not base or not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.delete(f"{base}/restream/{token}", headers=_headers())
        return res.status_code < 400
    except httpx.HTTPError as exc:
        logger.warning("share: could not stop the restream: %s", exc)
        return False


async def restream_status(token: str) -> dict[str, Any]:
    """Whether the relay is currently pushing, for the pane."""
    base = relay_base()
    if not base or not token:
        return {"live": False}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.get(f"{base}/restream/{token}", headers=_headers())
        return res.json() if res.status_code < 400 else {"live": False}
    except httpx.HTTPError:
        return {"live": False}


#: What the relay says about a token, from this node's point of view.
#:
#: Four states, not two, and the distinction is the whole point of this call. A
#: relay we could not reach is **not** a relay that has forgotten the token: the
#: first is our own network being unwell, the second means every viewer holding
#: that URL is looking at a dead page. Collapsing them would swap one lie
#: ("relaying") for another ("relay down") and the pane would still be wrong, so
#: `unknown` exists to be rendered as "cannot tell" rather than as either answer.
#: Same three-state rule the hardware probe and the audio providers follow.
RelayState = str  # "live" | "idle" | "gone" | "unknown"


async def stream_status(token: str) -> dict[str, Any]:
    """Ask the relay whether it is still holding this stream.

    The failure this exists to catch: the relay's registry is in this-process
    memory, so an OOM kill, a redeploy or a crash takes every token with it while
    the host's browser goes on believing its WHIP publish is still good. Nothing
    on the media path reports that — WebRTC to a dead peer just stops — so the
    node has to ask.

    `live` here means the relay is holding published media for the token, which
    is a stronger claim than "the token exists": a token whose publisher dropped
    is `idle`, and telling a host "the link is fine, nothing is arriving" is a
    different piece of advice from "the link is gone, mint a new one".
    """
    base = relay_base()
    if not base or not token:
        return {"state": "unknown", "live": False, "viewers": 0, "detail": ""}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.get(f"{base}/streams/{token}", headers=_headers())
    except httpx.HTTPError as exc:
        # Could not ask. Explicitly not "gone": a flaky link here would otherwise
        # tell the host to throw away a URL that is still perfectly good.
        return {
            "state": "unknown",
            "live": False,
            "viewers": 0,
            "detail": f"Could not reach the share relay: {exc}",
        }

    if res.status_code == 404:
        # The relay answered and does not have it. Unknown, revoked and expired
        # are one answer by design (`Registry.get`), so this cannot say which.
        return {
            "state": "gone",
            "live": False,
            "viewers": 0,
            "detail": (
                "The relay no longer has this link — it expired, was revoked, or "
                "the relay restarted. Mint a new one."
            ),
        }
    if res.status_code == 401:
        return {
            "state": "unknown",
            "live": False,
            "viewers": 0,
            "detail": "The relay rejected this node's key, so it will not say.",
        }
    if res.status_code >= 400:
        return {
            "state": "unknown",
            "live": False,
            "viewers": 0,
            "detail": f"The relay answered {res.status_code}.",
        }

    body = res.json()
    live = bool(body.get("live"))
    return {
        "state": "live" if live else "idle",
        "live": live,
        "viewers": int(body.get("viewers") or 0),
        "expires_at": float(body.get("expires_at") or 0.0),
        "detail": ""
        if live
        else "The relay holds this link but is receiving no picture from it.",
    }
