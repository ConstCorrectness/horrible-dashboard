"""Pluggable peer transports: direct WS, relay, LAN discovery, and loopback."""

from backend.modules.network.transport.base import PeerLink, Transport

__all__ = ["PeerLink", "Transport"]
