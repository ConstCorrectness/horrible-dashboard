"""Construct the hub's transports from settings, then start it.

Called once at app startup. Direct WS is always available; relay and LAN discovery
are added when their settings enable them (filled in by the trust/transport slice).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from backend.modules.network.hub import peer_hub
from backend.modules.network.monitor import peer_monitor
from backend.modules.network.transport.base import Transport
from backend.modules.network.transport.direct import DirectWsTransport
from backend.modules.network.transport.lan import LanDiscovery
from backend.modules.network.transport.relay import RelayTransport
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)


#: The `network.relayUrl` value that turns the relay off.
RELAY_OFF = "off"


def relay_url() -> str | None:
    """The relay broker this node registers with, or None when it is turned off.

    **On by default, hosted by the game server.** Direct connections only work where
    one side can reach the other's address: the same LAN, a VPN, a forwarded port.
    Two people at two homes have none of those, so with the relay off (which used to
    be the default) adding a friend by username failed for everyone who was not
    already on your network. The game server is the one public service every node
    already talks to, so it hosts the broker; `network.relayUrl` points elsewhere,
    and `off` disables it.
    """
    # `HORRIBLE_RELAY_URL` wins over the setting: it is how the test suite keeps
    # every booted app off the hosted relay, and how a deployment pins a broker
    # without writing to a user's settings.
    import os

    configured = (
        os.environ.get("HORRIBLE_RELAY_URL")
        or str(get_value("network.relayUrl", "") or "")
    ).strip()
    if configured.lower() == RELAY_OFF:
        return None
    if configured:
        return configured
    from backend.modules.games.client import resolve_server_url

    base = resolve_server_url().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/relay-ws"


def build_transports() -> list[Transport]:
    """The transports this node runs, per settings. Direct and relay are on by
    default; LAN discovery and WebRTC are opt-in.

    **Read once, at startup.** Toggling any of these settings does nothing until
    the backend restarts — a transport owns a bound socket or a multicast thread,
    and swapping one under live sessions is a different job from reading a flag.
    The four settings say "Takes effect on restart" for exactly this reason;
    before that they looked like live switches that silently did nothing.
    """
    transports: list[Transport] = []
    if get_value("network.enableDirect", True):
        transports.append(DirectWsTransport())
    relay = relay_url()
    if relay:
        if holder := _claim_relay():
            # Same data dir, so same node identity: a second registration would
            # make the broker close the first (it keeps one socket per node id),
            # and the two would knock each other off every few seconds. Measured
            # 2026-11-14: 38 re-registrations in 6 minutes with two backends up.
            logger.info(
                "relay: %s already holds this node's relay connection; "
                "not registering a second one",
                holder,
            )
        else:
            transports.append(RelayTransport(relay))
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


#: The bus claim that makes one process per data directory the relay's client.
RELAY_CLAIM = "network.relay"


def _claim_relay() -> str | None:
    """Take this node's relay connection. None on success, else who holds it.

    Several backends can share one data directory — a dev backend, the desktop
    shell's, a standalone module — and every one of them is the same node.
    """
    from backend.kernel import bus

    try:
        return bus.claim(RELAY_CLAIM)
    except OSError:
        # An unwritable data dir must not cost the node its relay.
        return None


def _release_relay() -> None:
    from backend.kernel import bus

    try:
        bus.release(RELAY_CLAIM)
    except OSError:
        pass


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
    # Trajectories: a friend listing and pulling runs from datasets this node has
    # marked shared, and watching a run live inside a share session. After share,
    # because live watching is authorized by that session's grant ladder.
    from backend.modules.trajectories import fabric as trajectories_fabric

    trajectories_fabric.register(peer_hub)
    # Running a friend's suite on this node's own agent, and offering ours to theirs.
    # Registered unconditionally: with `evals.acceptRemoteSuites` off an offer is
    # refused with that reason, never met with silence.
    from backend.modules.evals import fabric as evals_fabric

    evals_fabric.register(peer_hub)
    # Announce where this node can be reached, so a friend code resolves to an
    # address off the LAN. Best-effort: a node with no directory is still fully
    # usable, it just has to be given an address once.
    from backend.modules.social import directory as social_directory

    if await social_directory.publish():
        logger.info("published presence to the Atlas directory")
    # And keep it current: `lookup` ignores a record older than its TTL, so a node
    # published only at startup vanished from the directory fifteen minutes later.
    social_directory.start_refresh()
    # Friends reconnect on their own, over the relay when they are elsewhere.
    from backend.modules.social import roster as social_roster

    social_roster.start_reconnect()
    # Bind the signed-in account to this person, so `@username` finds us. Also done
    # on every sign-in; here for sessions that predate that.
    from backend.modules.games import server_auth

    server_auth.schedule_enrollment()
    peer_hub.set_transports(build_transports())
    await peer_hub.start()
    # Heartbeat the peers for live link health (RTT, throughput).
    await peer_monitor.start()
    logger.info("peer fabric started: node %s", peer_hub.identity().node_id)
    # Connect to the lobby for discovery + rooms, if configured, and to the agent
    # commons (profiles + matchmaking), if enabled. Neither is awaited: both dial
    # a server somewhere else and the lobby gathers STUN candidates first (a 2 s
    # timeout when the STUN server does not answer), and uvicorn does not bind
    # its port until startup returns. A slow or unreachable lobby must not be
    # able to hold the local app closed. Nothing local needs either connection,
    # and each reports its own state when it lands.
    from backend.modules.network.lobby import lobby_client

    _connect_in_background("lobby", lobby_client.start())
    from backend.modules.network.commons import commons_client

    _connect_in_background("commons", commons_client.start())


#: The connects started above, held so they are not garbage-collected mid-flight
#: and so shutdown can cancel one that is still dialing.
_connecting: set[asyncio.Task[None]] = set()


def _connect_in_background(name: str, connect: Coroutine[Any, Any, None]) -> None:
    async def run() -> None:
        try:
            await connect
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed dial must not be an unretrieved task error
            logger.warning("%s connect failed at startup", name, exc_info=True)

    task = asyncio.create_task(run(), name=f"network-connect-{name}")
    _connecting.add(task)
    task.add_done_callback(_connecting.discard)


async def stop_network() -> None:
    from backend.modules.network.commons import commons_client
    from backend.modules.network.lobby import lobby_client
    from backend.modules.social import directory as social_directory

    # Before the disconnects below: a connect still in flight would finish after
    # them and leave a socket open that nothing owns.
    pending = list(_connecting)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # Stop refreshing, but do not unpublish: see `directory.unpublish` for why a
    # record is left to go stale rather than deleted on every restart.
    await social_directory.stop_refresh()
    from backend.modules.social import roster as social_roster

    await social_roster.stop_reconnect()

    await commons_client.disconnect()
    await lobby_client.disconnect()
    await peer_monitor.stop()
    await peer_hub.stop()
    # After the relay transport has closed its socket, so the next holder's
    # registration cannot race this one's.
    _release_relay()
