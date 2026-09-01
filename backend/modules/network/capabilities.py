"""What this node advertises to peers, and how modules contribute to it.

`PeerHub.capabilities()` used to be a hardcoded `["agent", "collab", "hassault",
"share"]`. That answered *whether* a peer does hassault but not *whether it has a
game open right now*, so "find a friend", "find an open game" and "find a peer
with a GPU" were three separate mechanisms and the third did not exist. This
module makes them one: a registry modules contribute to, each entry carrying live
`attrs`.

**The flat `list[str]` form is frozen forever.** `capabilities()` keeps its exact
signature and output because three things depend on it and one of them is
cryptographic:

- `commons.build_profile` feeds it into `CommonsProfile.agent_capabilities`, which
  is Ed25519-signed over `canonical_profile_bytes` and re-served by a federated
  index. Changing the element type invalidates every profile already published.
- `lobby.py` puts it on the rendezvous wire, typed `string[]` in the frontend.
- The Kotlin client sends exactly this shape and nothing else.

So the rich form travels as a **separate, additive** `caps` field. A peer that
sends only `capabilities` (any Android build, any node older than this change)
has its `caps` synthesized from it, and every existing consumer -- the `'agent' in
p.capabilities` filters in the relay panel, hassault's fabric and routes,
`mobile_tools` -- keeps working untouched.

Providers are called **at advertisement time**, not at registration, so `attrs`
are current rather than whatever was true at boot. A provider returning `None`
means "not offering this right now" and the capability is omitted entirely.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from backend.modules.network.models import PeerCapability

logger = logging.getLogger(__name__)

#: Returns the live capability, or None to withdraw it for now.
Provider = Callable[[], "PeerCapability | None"]

_providers: dict[str, Provider] = {}


def register(cap_id: str, provider: Provider) -> None:
    """Register (or replace) the provider for one capability id.

    Replacing is the point, not an accident: `hassault` is registered statically
    here so a bare `PeerHub()` reports it, and the hassault module then upgrades
    it in place with a provider that counts open matches.
    """
    _providers[cap_id] = provider


def register_static(cap_id: str, *, version: int = 1) -> None:
    """Register a capability with no live detail -- the plain "I support this"."""
    cap = PeerCapability(id=cap_id, version=version)
    register(cap_id, lambda: cap)


def unregister(cap_id: str) -> None:
    _providers.pop(cap_id, None)


def _safe_attrs(cap_id: str, attrs: dict[str, object]) -> dict[str, object]:
    """Drop any attr that will not survive JSON, one key at a time.

    These bytes are signed, so a value `json.dumps` chokes on does not merely get
    dropped -- it raises inside `canonical_bytes` and fails the handshake. A peer
    becoming unreachable because a module put a `datetime` in its attrs would be a
    baffling bug, so the value is discarded loudly instead.
    """
    clean: dict[str, object] = {}
    for key, value in attrs.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            logger.warning(
                "capability %s: dropping attr %r, not JSON-serializable (%s)",
                cap_id,
                key,
                type(value).__name__,
            )
            continue
        clean[key] = value
    return clean


def snapshot() -> list[PeerCapability]:
    """Every capability this node currently offers, sorted by id.

    Sorted so the advertised order is stable across processes -- an unstable order
    would make otherwise-identical presence broadcasts look like changes.

    A provider that raises is logged and skipped. It must never sink the
    handshake: one module's bad probe should cost that module's capability, not
    the node's ability to connect to anyone.
    """
    caps: list[PeerCapability] = []
    for cap_id, provider in sorted(_providers.items()):
        try:
            cap = provider()
        except Exception:
            logger.exception("capability provider %s failed", cap_id)
            continue
        if cap is None:
            continue
        if cap.attrs:
            cap = cap.model_copy(update={"attrs": _safe_attrs(cap_id, cap.attrs)})
        caps.append(cap)
    return caps


def ids() -> list[str]:
    """The flat `list[str]` form. This is what `PeerHub.capabilities()` returns."""
    return [cap.id for cap in snapshot()]


def wire() -> list[dict[str, object]]:
    """The `caps` field's on-the-wire form."""
    return [cap.model_dump() for cap in snapshot()]


def from_wire(
    raw_caps: object, fallback_ids: list[str] | None = None
) -> list[PeerCapability]:
    """Parse a peer's `caps`, synthesizing it from `capabilities` when absent.

    The synthesis is what keeps every older node and every Android build working:
    they send `capabilities` and no `caps`, and a peer that says "hassault" without
    detail still means it does hassault.
    """
    parsed: list[PeerCapability] = []
    if isinstance(raw_caps, list):
        for item in raw_caps:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(PeerCapability.model_validate(item))
            except Exception:
                logger.debug("ignoring malformed capability %r", item)
    if parsed:
        return parsed
    return [PeerCapability(id=cap_id) for cap_id in (fallback_ids or [])]


def reset() -> None:
    """Drop every provider and reinstate the built-ins (tests)."""
    _providers.clear()
    _register_builtins()


def _register_builtins() -> None:
    """The four this node has always advertised.

    Registered at **import** time rather than in `start_network`, because a bare
    `PeerHub()` must report them: tests construct one directly and assert against
    this exact list, and so does anything that reads capabilities before the
    network has started.
    """
    for cap_id in ("agent", "collab", "hassault", "share"):
        register_static(cap_id)


_register_builtins()
