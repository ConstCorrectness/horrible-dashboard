"""Construct the hub's transports from settings, then start it.

Called once at app startup. Direct WS is always available; relay and LAN discovery
are added when their settings enable them (filled in by the trust/transport slice).
"""

from __future__ import annotations

import logging

from backend.modules.network.hub import peer_hub
from backend.modules.network.transport.base import Transport
from backend.modules.network.transport.direct import DirectWsTransport
from backend.modules.network.transport.lan import LanDiscovery
from backend.modules.network.transport.relay import RelayTransport
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


def build_transports() -> list[Transport]:
    """The transports this node runs, per settings. Direct is on by default; relay
    and LAN discovery are opt-in (a relay needs a broker URL; LAN needs multicast)."""
    transports: list[Transport] = []
    if get_value("network.enableDirect", True):
        transports.append(DirectWsTransport())
    relay_url = str(get_value("network.relayUrl", "") or "").strip()
    if relay_url:
        transports.append(RelayTransport(relay_url))
    if get_value("network.enableLanDiscovery", False):
        transports.append(LanDiscovery())
    return transports


async def start_network() -> None:
    # Handle inbound agent-to-agent requests (a peer's agent asking ours) and
    # shared-pane ops forwarded by peers.
    from backend.modules.network import agent_bridge, collab, protocol

    peer_hub.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    peer_hub.register_handler(protocol.COLLAB_OP, collab.handle_peer_collab_op)
    peer_hub.set_transports(build_transports())
    await peer_hub.start()
    logger.info("peer fabric started: node %s", peer_hub.identity().node_id)
    # Connect to the lobby for discovery + rooms, if configured.
    from backend.modules.network.lobby import lobby_client

    await lobby_client.start()


async def stop_network() -> None:
    from backend.modules.network.lobby import lobby_client

    await lobby_client.disconnect()
    await peer_hub.stop()
