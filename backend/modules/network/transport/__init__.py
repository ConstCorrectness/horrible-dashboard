"""Pluggable peer transports: direct WS, relay, LAN discovery, WebRTC datachannel
(optional `webrtc` extra), and loopback. The WebRTC transport is imported lazily by
`setup.build_transports` so this package loads without aiortc installed."""

from backend.modules.network.transport.base import PeerLink, Transport

__all__ = ["PeerLink", "Transport"]
