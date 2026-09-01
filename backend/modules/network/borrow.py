"""Borrowing a peer's installed extras.

The idea that needs no GPU. A laptop without `voice` (torch, 1–2 GB), without the
`llamacpp` tracer (cmake and a C++ compiler), without `browser-engine` (150 MB of
Chromium) should be able to use the desktop in the next room that has all three.

This is the *routing* half; `backend/extras.py` is the probe and
`network/lease.py` is the consent. What lives here is one rule and its
advertisement.

**The rule** (`borrow_or_hint`): probe locally → if available, run locally → if a
peer offers it and lending is arranged, run there → otherwise return the install
hint this feature already returned, now naming the peer that could have done it.

Two things it will not do:

- **Never silently remote something the user believes is local.** Every result
  says which node produced it, the same way `get_embeddings` returns a `method`
  so callers can refuse to persist a fallback. A borrowed transcript that looks
  local is how a user comes to believe their laptop has Whisper.
- **Never treat "could not ask" as "absent".** An extra that is installed but
  failing to load is a broken local install, and quietly answering it by shipping
  audio to a friend's machine hides a problem the user needs to fix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend import extras
from backend.modules.network.models import PeerCapability

logger = logging.getLogger(__name__)

CAPABILITY = "extras"

#: Extras a peer may borrow, mapped to the lease `service` that carries them.
#: Not every extra is here: `webrtc` and `geoip` are properties of *this* node's
#: own networking and mean nothing borrowed, and `games-native` renders frames for
#: a local pane.
BORROWABLE: dict[str, str] = {
    "voice": "voice",
    "clip": "clip",
    "llamacpp": "trace",
    "browser-engine": "browser",
}


@dataclass(frozen=True)
class Route:
    """Where a piece of work should run."""

    where: str  # "local" | "peer" | "unavailable"
    node_id: str | None = None
    reason: str = ""
    install: str = ""

    @property
    def local(self) -> bool:
        return self.where == "local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "where": self.where,
            "nodeId": self.node_id,
            "reason": self.reason,
            "install": self.install,
        }


def capability() -> PeerCapability | None:
    """Advertise which extras this node has, so `network.find_peers` can answer
    "who can transcribe this?".

    Only the ones actually installed are listed. Advertising an absent extra would
    make a peer's UI offer something every request against it would refuse, and
    advertising an *uncertain* one is worse: it would send a friend's work to a
    machine whose own install is broken.
    """
    installed = sorted(name for name in BORROWABLE if extras.probe(name).available)
    if not installed:
        return None
    return PeerCapability(id=CAPABILITY, attrs={"installed": installed})


def peers_with(extra: str) -> list[str]:
    """Connected, trusted node ids advertising `extra`."""
    from backend.modules.network.hub import peer_hub

    out: list[str] = []
    for info in peer_hub.list_peers():
        if not info.trusted or info.status != "connected":
            continue
        for cap in info.caps:
            if cap.id != CAPABILITY:
                continue
            listed = cap.attrs.get("installed")
            if isinstance(listed, list) and extra in listed:
                out.append(info.node_id)
    return out


def route(extra: str, *, allow_peer: bool = True) -> Route:
    """Decide where work needing `extra` should run.

    The order is deliberate and the middle step is the interesting one: a peer is
    consulted only after the local probe has answered *certainly* that the extra
    is absent.
    """
    verdict = extras.probe(extra)
    if verdict.available:
        return Route(where="local")

    if not verdict.certain:
        # Installed but broken, or unprobeable. Borrowing here would paper over a
        # local problem the user can actually fix, and would do it silently.
        return Route(
            where="unavailable",
            reason=verdict.reason,
            install=verdict.install,
        )

    if allow_peer and extra in BORROWABLE:
        candidates = peers_with(extra)
        if candidates:
            return Route(where="peer", node_id=candidates[0], reason=verdict.reason)

    hint = verdict.reason
    if allow_peer and extra in BORROWABLE:
        hint = f"{hint}, and no connected friend is offering it either"
    return Route(where="unavailable", reason=hint, install=verdict.install)


async def acquire(extra: str) -> tuple[str | None, Route]:
    """Route, and if the answer is a peer, take out a lease and return its endpoint.

    Returns `(endpoint, route)`. `endpoint` is None for local and unavailable —
    the caller runs its own code path in the first case and reports `route.reason`
    in the second.
    """
    decision = route(extra)
    if decision.where != "peer" or decision.node_id is None:
        return None, decision

    from backend.modules.network.hub import peer_hub
    from backend.modules.network.lease import leases

    service = BORROWABLE[extra]
    existing = leases.active_borrow(service)
    if existing is not None and existing.node_id == decision.node_id:
        return existing.endpoint, decision

    try:
        borrowed = await leases.request(peer_hub, decision.node_id, service)
    except Exception as exc:  # noqa: BLE001 - a refusal is an answer, not a crash
        # Falling back to "unavailable" rather than raising: the caller's job is
        # to do the work or explain why it cannot, and "your friend said no" is an
        # explanation, not an error.
        return None, Route(
            where="unavailable",
            reason=f"{decision.node_id} declined to lend {extra}: {exc}",
            install=extras.probe(extra).install,
        )
    return borrowed.endpoint, decision


def register() -> None:
    from backend.modules.network import capabilities

    capabilities.register(CAPABILITY, capability)
