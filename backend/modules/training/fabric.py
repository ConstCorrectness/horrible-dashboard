"""Peer-fabric training ads: advertise/seek training compute across the fabric.

v1 is **advertise + manual handoff**: a node broadcasts an ad (`offering` GPU /
`seeking` help, plus hardware specs) to every peer over the existing PeerHub;
matches surface in the UI and the actual handoff is a normal collab/agent chat.
No remote execution engine here.

The ad handler is registered on the hub at network startup; received ads land in
an in-process store and fan out to every browser on the shared `training` channel
(via the same subscriber pipe presence uses). When a new peer connects
(`peer_update`), we re-send our current ad so late joiners see it.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from backend.modules.settings.routes import get_value
from backend.modules.training import specs
from backend.modules.training.models import TrainingAdModel
from backend.modules.training.stream import broadcast

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub, PeerSession
    from backend.modules.network.models import PeerEnvelope

logger = logging.getLogger(__name__)

TRAINING_AD = "training_ad"

# Ads received from peers, keyed by their node id (latest wins; "none" removes).
_ads: dict[str, TrainingAdModel] = {}


def known_ads() -> list[TrainingAdModel]:
    return list(_ads.values())


def _my_status() -> str:
    """The advertise mode from settings: off | offering | seeking."""
    value = str(get_value("training.fabric.advertise", "off") or "off")
    return value if value in ("offering", "seeking") else "off"


def _my_ad(hub: PeerHub) -> TrainingAdModel:
    status = _my_status()
    return TrainingAdModel(
        node_id=hub.identity().node_id,
        node_name=hub.identity().node_name or "",
        status=status if status != "off" else "none",
        specs=specs.snapshot() if status != "off" else {},
        note=str(get_value("training.fabric.note", "") or ""),
        ts=time.time(),
    )


async def _on_ad(hub: PeerHub, session: PeerSession, env: PeerEnvelope) -> None:
    """Inbound training ad from a peer: validate, store (or drop), fan to browsers."""
    try:
        ad = TrainingAdModel.model_validate(env.data or {})
    except Exception as exc:  # noqa: BLE001 — never trust a peer's payload
        logger.info(
            "ignoring malformed training ad from %s: %s", session.info.node_id, exc
        )
        return
    # A node can only speak for itself — pin the ad to the sender's node id.
    ad.node_id = session.info.node_id
    if ad.status == "none":
        _ads.pop(ad.node_id, None)
    else:
        _ads[ad.node_id] = ad
    await broadcast("training_ad", ad.model_dump())


async def broadcast_ad(hub: PeerHub) -> None:
    """Send this node's current ad to every connected peer (called on advertise
    change and when a new peer joins)."""
    ad = _my_ad(hub)
    for peer in hub.list_peers():
        try:
            await hub.send_to(peer.node_id, TRAINING_AD, ad.model_dump())
        except Exception as exc:  # noqa: BLE001 — one dead peer can't stop the rest
            logger.debug("training ad send to %s failed: %s", peer.node_id, exc)


def register(hub: PeerHub) -> None:
    """Wire the ad handler and re-broadcast on new peers. Call at network startup."""
    hub.register_handler(TRAINING_AD, _on_ad)

    def _on_peer_update(event: str, data: dict[str, Any]) -> None:
        # A peer (re)connected — re-send our ad so they see it. Scheduled since
        # the hub emits synchronously and send_to is async.
        if event == "peer_update" and _my_status() != "off":
            import asyncio

            asyncio.ensure_future(broadcast_ad(hub))

    hub.subscribe(_on_peer_update)
