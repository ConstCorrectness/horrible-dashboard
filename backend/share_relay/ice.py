"""ICE configuration for the relay's own peer connections.

Without this the relay gathers **host candidates only** — and on a hosting
platform a host candidate is a private address, so a browser on the public
internet has nothing dialable and the connection simply never completes. It is a
silent failure: WHIP returns a perfectly good SDP answer, the viewer page shows
"connecting", and nothing in any log says why. Locally it works flawlessly,
because on one machine a host candidate is exactly right.

Env-configured, like everything else about this service — the relay has no
settings store, and its config belongs to whoever deploys it rather than to any
node that uses it.

The semantics deliberately match `buildIceConfig` in the frontend and
`_ice_servers` in `backend/modules/network/transport/webrtc.py`: **STUN is a bare
`host:port`** that gets the scheme added, **TURN is a full URL** passed through.
Three readers of the same idea that disagreed about whether a scheme is included
would fail as "ICE just does not connect", with nothing to say why.
"""

from __future__ import annotations

import os
from typing import Any

#: Same default as the node side, so the two ends agree out of the box.
DEFAULT_STUN = "stun.l.google.com:19302"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def stun_server() -> str:
    """The STUN server as `host:port`. Empty disables STUN entirely."""
    return _env("SHARE_RELAY_STUN", DEFAULT_STUN)


def turn_config() -> tuple[str, str, str]:
    """`(url, username, credential)`. Any empty part means no usable TURN."""
    return (
        _env("SHARE_RELAY_TURN_URL"),
        _env("SHARE_RELAY_TURN_USER"),
        _env("SHARE_RELAY_TURN_PASS"),
    )


def turn_is_incomplete() -> bool:
    """A TURN URL configured without credentials.

    Worth surfacing rather than silently dropping: TURN is the thing that makes a
    symmetric NAT work, so "I set up TURN and it still fails" is exactly the
    situation where a missing password has to be visible.
    """
    url, user, password = turn_config()
    return bool(url) and not (user and password)


def describe() -> dict[str, Any]:
    """What `/health` reports. Names and flags only -- **never the credential**."""
    url, user, password = turn_config()
    return {
        "stun": stun_server(),
        "turn": bool(url and user and password),
        "turn_incomplete": turn_is_incomplete(),
    }


def ice_servers() -> list[Any]:
    """The relay's own `RTCIceServer` list, for its publisher and viewer PCs.

    aiortc is imported lazily here for the same reason `fanout` does it: the
    config logic above is tested without a media stack.
    """
    from aiortc import RTCIceServer

    servers: list[Any] = []
    stun = stun_server()
    if stun:
        servers.append(RTCIceServer(urls=[f"stun:{stun}"]))

    url, user, password = turn_config()
    if url and user and password:
        servers.append(RTCIceServer(urls=[url], username=user, credential=password))
    return servers


def viewer_ice() -> list[dict[str, Any]]:
    """The ICE config handed to the **viewer page**, as plain JSON.

    STUN always; TURN only when `SHARE_RELAY_TURN_FOR_VIEWERS=1`. A public viewer
    is a stranger, and a TURN credential in a page anyone can open makes the
    relay operator's TURN bandwidth free for the whole internet. But a viewer
    behind a symmetric NAT cannot connect without one, so the trade is the
    operator's to make rather than ours to decide — it is simply off by default.
    """
    servers: list[dict[str, Any]] = []
    stun = stun_server()
    if stun:
        servers.append({"urls": [f"stun:{stun}"]})

    if _env("SHARE_RELAY_TURN_FOR_VIEWERS") == "1":
        url, user, password = turn_config()
        if url and user and password:
            servers.append({"urls": [url], "username": user, "credential": password})
    return servers
