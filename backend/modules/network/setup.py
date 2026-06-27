"""Construct the hub's transports from settings, then start it.

Called once at app startup. Direct WS is always available; relay and LAN discovery
are added when their settings enable them (filled in by the trust/transport slice).
"""

from __future__ import annotations

import logging

from backend.modules.network.hub import peer_hub
from backend.modules.network.transport.base import Transport
from backend.modules.network.transport.direct import DirectWsTransport
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


def build_transports() -> list[Transport]:
    transports: list[Transport] = []
    if get_value("network.enableDirect", True):
        transports.append(DirectWsTransport())
    return transports


async def start_network() -> None:
    peer_hub.set_transports(build_transports())
    await peer_hub.start()
    logger.info("peer fabric started: node %s", peer_hub.identity().node_id)


async def stop_network() -> None:
    await peer_hub.stop()
