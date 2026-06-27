"""Distributed peer fabric: node identity, transports, and the `PeerHub` that lets
this backend node connect to other users' nodes. See docs/architecture/distributed.mdx
and docs/modules/network.mdx."""

from backend.modules.network.bridge import handle_network_message, subscribe_conn
from backend.modules.network.collab import collab_manager, handle_collab_message
from backend.modules.network.hub import PeerHub, peer_hub
from backend.modules.network.routes import router

__all__ = [
    "PeerHub",
    "peer_hub",
    "router",
    "handle_network_message",
    "subscribe_conn",
    "collab_manager",
    "handle_collab_message",
]
