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

    # `inline` although a remote turn takes minutes: the handler spawns the turn
    # itself and returns immediately, because it must keep a cancellable handle in
    # `_active_remote_turns` for `agent_cancel` to find. A generic `detach` would
    # hand the task to the hub, where nothing can cancel one turn by request id.
    peer_hub.register_handler(
        protocol.AGENT_REQUEST, agent_bridge.handle_remote_agent_request
    )
    peer_hub.register_handler(
        protocol.AGENT_CANCEL, agent_bridge.handle_remote_agent_cancel
    )
    # Collab ops are the case `serial` exists for: applying them is slower than a
    # dict write, and applying two backwards corrupts the document.
    peer_hub.register_handler(
        protocol.COLLAB_OP, collab.handle_peer_collab_op, mode="serial"
    )
    peer_hub.register_handler(protocol.PEER_CHAT, chat.handle_peer_chat)
    from backend.modules.network import remote_control

    # Detached: a remote command opens panes and plays media, awaiting the
    # browser. Inline, a phone could stall every other window on the link.
    peer_hub.register_handler(
        protocol.REMOTE_COMMAND, remote_control.handle_remote_command, mode="detach"
    )

    from backend.modules.network.mobile_tools import register_mobile_tools

    register_mobile_tools()
    # The fabric's own agent surface: survey, measure, find peers, and the three
    # lease verbs. All loadable (`group="network"`) — the always-on peer verbs
    # stay `list_peers` and `agent.ask_peer`, which live in the orchestrator.
    from backend.modules.network.agent_tools import register_network_tools

    register_network_tools()
    # The bench's echo handler, so a peer can measure the link to *us*. Trivial by
    # design: an echo that did real work would measure the work.
    from backend.modules.network import bench

    bench.register(peer_hub)
    # Advertise this node's inference capacity (accelerator, VRAM, models, and
    # which one is loaded) so "find a peer with a GPU" is answerable without
    # asking every friend in turn.
    from backend.modules.llamacpp.capability import register as register_inference

    register_inference()
    # Compute lending: the lease protocol and the byte tunnel it authorizes.
    # Registered unconditionally — the handlers exist, but every one of them is
    # gated on `network.allowComputeLending`, which is default-off, so a node that
    # has not opted in refuses with a reason instead of going silent.
    from backend.modules.network import lease as lease_module

    lease_module.register(peer_hub)
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
    # Share sessions: invites, joins, grants, and the SDP/ICE pass-through for
    # the browser-to-browser media link. After social, because an invite is
    # addressed to a person rather than to a machine.
    from backend.modules.share import register_share

    register_share(peer_hub)
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
