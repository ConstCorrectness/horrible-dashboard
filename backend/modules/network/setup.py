"""Construct the hub's transports from settings, then start it.

Called once at app startup. Direct WS is always available; relay and LAN discovery
are added when their settings enable them (filled in by the trust/transport slice).
"""

from __future__ import annotations

import logging

from backend.modules.network.hub import peer_hub
from backend.modules.network.monitor import peer_monitor
from backend.modules.network.transport.base import Transport
from backend.modules.network.transport.direct import DirectWsTransport
from backend.modules.network.transport.lan import LanDiscovery
from backend.modules.network.transport.relay import RelayTransport
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


def build_transports() -> list[Transport]:
    """The transports this node runs, per settings. Direct is on by default; relay
    and LAN discovery are opt-in (a relay needs a broker URL; LAN needs multicast).

    **Read once, at startup.** Toggling any of these settings does nothing until
    the backend restarts — a transport owns a bound socket or a multicast thread,
    and swapping one under live sessions is a different job from reading a flag.
    The four settings say "Takes effect on restart" for exactly this reason;
    before that they looked like live switches that silently did nothing.
    """
    transports: list[Transport] = []
    if get_value("network.enableDirect", True):
        transports.append(DirectWsTransport())
    relay_url = str(get_value("network.relayUrl", "") or "").strip()
    if relay_url:
        transports.append(RelayTransport(relay_url))
    if get_value("network.enableLanDiscovery", False):
        transports.append(LanDiscovery())
    if get_value("network.enableWebRtc", False):
        from backend.modules.network.transport import webrtc

        if webrtc.AIORTC_AVAILABLE:
            transports.append(webrtc.WebRtcTransport())
        else:
            logger.warning(
                "network.enableWebRtc is on but aiortc is not installed; "
                "run `uv sync --extra webrtc` to enable the WebRTC transport"
            )
    return transports


async def start_network() -> None:
    # Handle inbound agent-to-agent requests (a peer's agent asking ours),
    # shared-pane ops, and peer chat forwarded by peers.
    from backend.modules.network import agent_bridge, chat, collab, protocol

    peer_hub.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    peer_hub.register_handler(
        protocol.AGENT_CANCEL, agent_bridge.handle_remote_agent_cancel
    )
    from backend.modules.network import remote_view

    peer_hub.register_handler(protocol.VIEW_REQUEST, remote_view.handle_view_request)
    peer_hub.register_handler(protocol.COLLAB_OP, collab.handle_peer_collab_op)
    peer_hub.register_handler(protocol.PEER_CHAT, chat.handle_peer_chat)
    from backend.modules.network import remote_control

    peer_hub.register_handler(
        protocol.REMOTE_COMMAND, remote_control.handle_remote_command
    )

    from backend.modules.network.mobile_tools import register_mobile_tools

    register_mobile_tools()
    # Training fabric: advertise/receive "GPU offered / help wanted" ads.
    from backend.modules.training import fabric as training_fabric

    training_fabric.register(peer_hub)
    # Social layer: person identity, friend requests, and roster presence.
    from backend.modules.social import register_social

    register_social(peer_hub)
    # Notification rules and standing watches. After social, because it subscribes
    # to the roster's presence events — the "came online" signal a watch fires on.
    from backend.modules.notifications import register_notifications
    from backend.modules.notifications.agent_tools import register_notification_tools

    register_notifications()
    register_notification_tools()
    # HorribleAssault: match invites, and playing in a friend's match on their
    # node. Registered after social because an invite is addressed to a person.
    from backend.modules.hassault import fabric as hassault_fabric

    hassault_fabric.register(peer_hub)
    # Announce where this node can be reached, so a friend code resolves to an
    # address off the LAN. Best-effort: a node with no directory is still fully
    # usable, it just has to be given an address once.
    from backend.modules.social import directory as social_directory

    if await social_directory.publish():
        logger.info("published presence to the Atlas directory")
    peer_hub.set_transports(build_transports())
    await peer_hub.start()
    # Heartbeat the peers for live link health (RTT, throughput).
    await peer_monitor.start()
    logger.info("peer fabric started: node %s", peer_hub.identity().node_id)
    # Connect to the lobby for discovery + rooms, if configured.
    from backend.modules.network.lobby import lobby_client

    await lobby_client.start()
    # Connect to the agent commons (profiles + matchmaking), if enabled.
    from backend.modules.network.commons import commons_client

    await commons_client.start()


async def stop_network() -> None:
    from backend.modules.network.commons import commons_client
    from backend.modules.network.lobby import lobby_client

    await commons_client.disconnect()
    await lobby_client.disconnect()
    await peer_monitor.stop()
    await peer_hub.stop()
