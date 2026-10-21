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

**STUN is not enough on Fly.** Fly routes no inbound UDP to the ephemeral ports
aiortc gathers on, so a STUN-only relay connects only when the *other* end's NAT
happens to allow the hole punch -- which is precisely "the share link is black
for some people and fine for others". TURN fixes it, and it comes from one of two
places, in this order:

1. **Cloudflare**, when `CLOUDFLARE_TURN_KEY_ID` and `CLOUDFLARE_TURN_API_TOKEN`
   are set. Credentials are minted through Cloudflare's API with a lifetime, so
   the one a stranger reads out of the viewer page expires instead of being the
   operator's bandwidth forever. That is why Cloudflare TURN goes to viewers
   unconditionally while a static one still needs `SHARE_RELAY_TURN_FOR_VIEWERS`.
2. **A static server**, `SHARE_RELAY_TURN_URL`/`USER`/`PASS`, as before.

Minting is async and everything that *reads* the config is sync, so callers
`await refresh()` first and the readers use whatever is cached. A Cloudflare
failure falls back to the static server, then to STUN, and never raises -- a
relay that cannot reach Cloudflare should still serve the people whose networks
never needed TURN.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Same default as the node side, so the two ends agree out of the box.
DEFAULT_STUN = "stun.l.google.com:19302"

CLOUDFLARE_ENDPOINT = (
    "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/"
    "generate-ice-servers"
)

#: How long a minted credential lives. A TURN allocation is refreshed with the
#: same credential every few minutes for as long as the connection lasts, so this
#: must outlast the longest share anyone will watch -- an expired credential kills
#: the relayed path mid-stream.
CLOUDFLARE_TTL_S = 24 * 3600

#: Re-mint once less than this remains, so a connection opened on a cached
#: credential still has at least this long before it expires.
CLOUDFLARE_REFRESH_BELOW_S = 12 * 3600

#: After a failed mint, how long to serve the fallback before asking again.
CLOUDFLARE_RETRY_S = 60.0

CLOUDFLARE_TIMEOUT_S = 5.0

#: The cached Cloudflare entry: `{"urls", "username", "credential"}` and when it
#: expires (monotonic). Module state because the relay is one process and the
#: credential is shared by every connection on it.
_cloudflare: dict[str, Any] | None = None
_cloudflare_expires = 0.0
_cloudflare_retry_at = 0.0


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


def cloudflare_config() -> tuple[str, str]:
    """`(key_id, api_token)`. Either empty means Cloudflare TURN is off."""
    return _env("CLOUDFLARE_TURN_KEY_ID"), _env("CLOUDFLARE_TURN_API_TOKEN")


def cloudflare_enabled() -> bool:
    key_id, token = cloudflare_config()
    return bool(key_id and token)


def _now() -> float:
    return time.monotonic()


def _cloudflare_turn() -> dict[str, Any] | None:
    """The cached Cloudflare TURN entry, if it is still usable."""
    if _cloudflare is None or _now() >= _cloudflare_expires:
        return None
    return _cloudflare


def _static_turn() -> dict[str, Any] | None:
    url, user, password = turn_config()
    if not (url and user and password):
        return None
    return {"urls": [url], "username": user, "credential": password}


def turn_source() -> str | None:
    """Where TURN is coming from right now: `cloudflare`, `static` or None."""
    if _cloudflare_turn() is not None:
        return "cloudflare"
    if _static_turn() is not None:
        return "static"
    return None


def _pick_turn(entries: list[Any]) -> dict[str, Any] | None:
    """The TURN entry out of Cloudflare's `iceServers` list (the one with a credential)."""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        urls = entry.get("urls")
        urls = [urls] if isinstance(urls, str) else list(urls or [])
        turn_urls = [u for u in urls if isinstance(u, str) and u.startswith("turn")]
        if turn_urls and entry.get("username") and entry.get("credential"):
            return {
                "urls": turn_urls,
                "username": str(entry["username"]),
                "credential": str(entry["credential"]),
            }
    return None


async def refresh(client: httpx.AsyncClient | None = None) -> None:
    """Make sure the cached Cloudflare credential is fresh. Never raises.

    A no-op when Cloudflare is not configured, when the cache has more than
    `CLOUDFLARE_REFRESH_BELOW_S` left, or inside the retry window after a
    failure -- so it is cheap to await before every connection.
    """
    global _cloudflare, _cloudflare_expires, _cloudflare_retry_at
    if not cloudflare_enabled():
        return
    now = _now()
    if (
        _cloudflare is not None
        and _cloudflare_expires - now > CLOUDFLARE_REFRESH_BELOW_S
    ):
        return
    if now < _cloudflare_retry_at:
        return

    key_id, token = cloudflare_config()
    url = CLOUDFLARE_ENDPOINT.format(key_id=key_id)
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=CLOUDFLARE_TIMEOUT_S) as owned:
                res = await owned.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"ttl": CLOUDFLARE_TTL_S},
                )
        else:
            res = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"ttl": CLOUDFLARE_TTL_S},
            )
        res.raise_for_status()
        entry = _pick_turn(res.json().get("iceServers") or [])
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        # Status and type only: the request carries the API token, and some
        # httpx errors render the request they failed on.
        logger.warning(
            "Cloudflare TURN mint failed (%s); serving %s until it is retried",
            getattr(getattr(exc, "response", None), "status_code", type(exc).__name__),
            "static TURN" if _static_turn() else "STUN only",
        )
        _cloudflare_retry_at = now + CLOUDFLARE_RETRY_S
        return
    if entry is None:
        logger.warning("Cloudflare TURN answered without a usable TURN entry")
        _cloudflare_retry_at = now + CLOUDFLARE_RETRY_S
        return
    _cloudflare = entry
    _cloudflare_expires = now + CLOUDFLARE_TTL_S
    _cloudflare_retry_at = 0.0


def reset_cache() -> None:
    """Forget the minted credential. For tests."""
    global _cloudflare, _cloudflare_expires, _cloudflare_retry_at
    _cloudflare = None
    _cloudflare_expires = 0.0
    _cloudflare_retry_at = 0.0


def describe() -> dict[str, Any]:
    """What `/health` reports. Names and flags only -- **never the credential**."""
    source = turn_source()
    return {
        "stun": stun_server(),
        "turn": source is not None,
        "turn_source": source,
        "turn_incomplete": turn_is_incomplete(),
    }


def _browser_turn(entry: dict[str, Any]) -> dict[str, Any] | None:
    """A TURN entry fit for a browser.

    Drops port-53 URLs: browsers block that port, so a candidate on it only
    times out -- and with no trickle channel (WHIP/WHEP wait for gathering) a
    timeout is a delay before every connection.
    """
    urls = [u for u in entry["urls"] if ":53?" not in u and not u.endswith(":53")]
    if not urls:
        return None
    return {**entry, "urls": urls}


def browser_ice(include_static_turn: bool = True) -> list[dict[str, Any]]:
    """STUN plus TURN as plain JSON, for a browser's `RTCPeerConnection`."""
    servers: list[dict[str, Any]] = []
    stun = stun_server()
    if stun:
        servers.append({"urls": [f"stun:{stun}"]})

    turn = _cloudflare_turn()
    if turn is None and include_static_turn:
        turn = _static_turn()
    if turn is not None:
        entry = _browser_turn(turn)
        if entry is not None:
            servers.append(entry)
    return servers


def _aiortc_turn_url(urls: list[str]) -> str | None:
    """The one TURN URL aiortc will use.

    aiortc takes only the **first** TURN URL it understands (udp, tcp, or turns
    over tcp). The relay itself has open outbound UDP, so plain UDP goes first:
    it is the cheapest path, and handing aiortc Cloudflare's list unsorted would
    let the order of somebody else's JSON decide our transport.
    """

    def rank(url: str) -> int:
        if url.startswith("turn:") and "transport=tcp" not in url:
            return 0
        if url.startswith("turn:"):
            return 1
        return 2

    usable = [
        u
        for u in urls
        if (
            u.startswith("turn:")
            or (u.startswith("turns:") and "transport=udp" not in u)
        )
        and ":53?" not in u
        and not u.endswith(":53")
    ]
    return min(usable, key=rank) if usable else None


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

    turn = _cloudflare_turn() or _static_turn()
    if turn is not None:
        url = _aiortc_turn_url(turn["urls"])
        if url is not None:
            servers.append(
                RTCIceServer(
                    urls=[url], username=turn["username"], credential=turn["credential"]
                )
            )
    return servers


def viewer_ice() -> list[dict[str, Any]]:
    """The ICE config handed to the **viewer page**, as plain JSON.

    STUN always. Cloudflare TURN whenever it is configured: its credential
    expires, so publishing it in a page anyone can open costs a bounded amount.
    A **static** TURN credential only when `SHARE_RELAY_TURN_FOR_VIEWERS=1` -- that
    one never expires, so handing it to strangers makes the operator's TURN
    bandwidth free for the whole internet, and the trade stays the operator's.
    """
    return browser_ice(include_static_turn=_env("SHARE_RELAY_TURN_FOR_VIEWERS") == "1")
