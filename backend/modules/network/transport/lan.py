"""LAN discovery via mDNS (zeroconf).

Advertises this node as `_horrible-peer._tcp` carrying its node_id and dial address,
and browses for the same service. A discovered peer is dialed through the **direct**
transport (so the actual link is a normal `/peer-ws` connection) — discovery only
finds peers, it doesn't carry traffic. Auto-connect happens only under the open-lan
trust mode; otherwise discovered peers are surfaced for a manual connect.

Best-effort: if multicast isn't available (many containers/CI), `start` logs and
returns without sinking the other transports.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from backend.modules.network import identity, trust
from backend.modules.network.transport.base import PeerLink, Transport

if TYPE_CHECKING:
    from backend.modules.network.hub import PeerHub

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_horrible-peer._tcp.local."


class LanDiscovery(Transport):
    """Not a traffic-carrying transport — it advertises/browses and asks the hub to
    dial discovered peers via direct. Registered in the transport list for its
    start/stop lifecycle only; `dial` is unused."""

    name = "lan"

    def __init__(self) -> None:
        self._azc: Any = None
        self._browser: Any = None
        self._info: Any = None
        self._hub: PeerHub | None = None

    async def start(self, hub: PeerHub) -> None:
        self._hub = hub
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
        except Exception:  # pragma: no cover - dependency missing
            logger.info("zeroconf unavailable; LAN discovery disabled")
            return
        try:
            self._azc = AsyncZeroconf()
            self._info = self._build_info(ServiceInfo, hub)
            await self._azc.async_register_service(self._info)
            self._browser = AsyncServiceBrowser(
                self._azc.zeroconf,
                SERVICE_TYPE,
                handlers=[self._on_change],
            )
            logger.info("LAN discovery advertising %s", hub.signer.node_id)
        except Exception:
            logger.exception("LAN discovery failed to start")

    def _build_info(self, ServiceInfo: Any, hub: PeerHub) -> Any:
        import socket

        address = trust.advertised_address()
        parsed = urlparse(address)
        port = parsed.port or 8000
        me = hub.signer
        props = {
            b"node_id": me.node_id.encode(),
            b"address": address.encode(),
            b"name": identity.node_name().encode(),
        }
        host_ip = socket.gethostbyname(socket.gethostname())
        return ServiceInfo(
            SERVICE_TYPE,
            f"{me.node_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(host_ip)],
            port=port,
            properties=props,
        )

    def _on_change(
        self, zeroconf: Any, service_type: str, name: str, state_change: Any
    ) -> None:
        from zeroconf import ServiceStateChange

        if state_change is not ServiceStateChange.Added:
            return
        asyncio.ensure_future(self._maybe_connect(zeroconf, service_type, name))

    async def _maybe_connect(self, zeroconf: Any, service_type: str, name: str) -> None:
        if self._hub is None or self._azc is None:
            return
        try:
            info = await self._azc.async_get_service_info(service_type, name)
        except Exception:
            return
        if info is None:
            return
        props = info.properties or {}
        node_id = (props.get(b"node_id") or b"").decode() or None
        address = (props.get(b"address") or b"").decode() or None
        if not node_id or node_id == self._hub.signer.node_id:
            return  # ignore self
        if node_id in self._hub.peers:
            return  # already connected
        if trust.trust_mode() != trust.TRUST_OPEN_LAN or not address:
            logger.info("discovered LAN peer %s (manual connect required)", node_id)
            return
        try:
            await self._hub.connect(address, "direct")
            logger.info("auto-connected to LAN peer %s", node_id)
        except Exception as exc:
            logger.info("LAN auto-connect to %s failed: %s", node_id, exc)

    async def dial(self, address: str) -> PeerLink:
        raise NotImplementedError(
            "LAN discovery connects peers via the direct transport"
        )

    async def stop(self) -> None:
        if self._azc is not None:
            with contextlib.suppress(Exception):
                if self._info is not None:
                    await self._azc.async_unregister_service(self._info)
                if self._browser is not None:
                    await self._browser.async_cancel()
                await self._azc.async_close()
