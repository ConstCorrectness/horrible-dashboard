"""What this node advertises about its local inference capacity.

This is the half of capability advertisement that makes "find a peer with a GPU"
answerable at all. Without it a node says only `agent`, which tells a friend that
it can hold a conversation and nothing about whether it could hold a 70B one.

Three rules the attrs follow, each inherited from a module that learned it:

- **Never report "no accelerator" when the truth is "could not ask."**
  `hardware.probe` reports three states, and flattening them here would put the
  exact fiction that module exists to prevent onto the wire, where a peer would
  then act on it.
- **A model list is a filesystem walk**, so it is cached rather than recomputed on
  every presence broadcast.
- **Advertise what is already loaded, prominently.** The lease policy grants
  against the hot model by default (evicting a friend's chat model because you
  asked is not reachable by default), so `serving` is the field that decides
  whether a request will succeed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.modules.network.models import PeerCapability

logger = logging.getLogger(__name__)

CAPABILITY = "inference"

#: Model catalogues are a filesystem walk across several roots (ours, Ollama's,
#: LM Studio's). Presence can fire every few seconds, so the answer is cached.
_CACHE_TTL_S = 60.0
_cache: tuple[float, dict[str, Any]] | None = None

#: Strong references to in-flight announce tasks; asyncio holds only weak ones.
_announce_tasks: set[asyncio.Task[None]] = set()


def _accelerator_attrs() -> dict[str, Any]:
    """Describe the machine's accelerator, preserving the "could not ask" state."""
    from backend.modules.hardware import probe as hardware

    profile = hardware.get_profile()
    primary = profile.primary
    attrs: dict[str, Any] = {
        "ramMb": profile.ram_mb,
        # False means "we looked and could not tell", never "there is none".
        # A peer must be able to distinguish those before deciding to lean on
        # this machine.
        "certain": profile.certain,
    }
    if primary is not None:
        attrs.update(
            {
                "accelerator": primary.kind,
                "acceleratorName": primary.name,
                "vramMb": primary.vram_mb,
                # Unified memory is not VRAM: calling a 16 GB Mac a 16 GB card
                # would have a peer size a model against memory the OS is also
                # using for everything else.
                "unified": primary.unified,
                "vramExact": primary.exact,
            }
        )
    return attrs


def _model_attrs() -> dict[str, Any]:
    """Which GGUFs this node could serve, and which one it is serving now."""
    from backend.modules.llamacpp.catalog import list_models
    from backend.modules.llamacpp.server import llama_manager

    attrs: dict[str, Any] = {}
    try:
        models = list_models()
    except Exception:
        logger.exception("inference capability: model catalogue failed")
        models = []

    # Names only. The full catalogue carries absolute paths, and a path leaks this
    # machine's directory layout and username to every peer for no benefit -- a
    # borrower picks a model by name.
    attrs["models"] = sorted({m.name for m in models})[:64]
    attrs["modelCount"] = len(models)

    if llama_manager.running():
        # The field the lease policy actually turns on: granting against the
        # already-loaded model needs no eviction, so this is what tells a peer
        # their request will be cheap rather than refused.
        attrs["serving"] = llama_manager.alias
    return attrs


def _build() -> dict[str, Any]:
    attrs = _accelerator_attrs()
    attrs.update(_model_attrs())
    return attrs


def capability() -> PeerCapability | None:
    """The live `inference` capability, or None when this node has nothing to say.

    Returning None rather than an empty capability matters: "I advertise
    inference with no models" would show up in a peer's UI as an offer, and every
    request against it would fail.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        attrs = _cache[1]
    else:
        try:
            attrs = _build()
        except Exception:
            logger.exception("inference capability probe failed")
            return None
        _cache = (now, attrs)

    if not attrs.get("modelCount") and not attrs.get("serving"):
        return None
    return PeerCapability(id=CAPABILITY, attrs=dict(attrs))


def invalidate() -> None:
    """Drop the cache so the next advertisement re-probes."""
    global _cache
    _cache = None


def changed() -> None:
    """Note that this node's inference capacity changed, and tell its peers.

    Called when a model is loaded or stopped. The whole point of `serving` is that
    it is current -- a 60-second stale window on it would have peers requesting a
    model that had already gone away, and getting a refusal that looks like a bug.

    Best-effort by construction: it must never be able to fail a model load. The
    announce is fire-and-forget (and itself debounced in the hub), and outside a
    running loop -- a sync CLI path, a test -- there is simply nobody to tell.
    """
    invalidate()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from backend.modules.network.hub import peer_hub

    task = loop.create_task(peer_hub.announce_presence())
    _announce_tasks.add(task)
    task.add_done_callback(_announce_tasks.discard)


def register() -> None:
    from backend.modules.network import capabilities

    capabilities.register(CAPABILITY, capability)
