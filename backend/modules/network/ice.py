"""ICE-lite candidate gathering for the peer fabric.

Our peer links are WebSocket (TCP), so this is **ICE-lite**, not full WebRTC ICE:
we gather **host** candidates (LAN/advertised `/peer-ws` URLs) and a **server-
reflexive** candidate (the node's public IP discovered via a STUN binding request,
paired with the advertised peer-ws port). The dialer tries candidates in priority
order (host → srflx) and falls back to the relay if none connect.

This genuinely helps when a peer is reachable on the LAN or its peer-ws port is
forwarded/permissively NATed. True hole-punching through symmetric NAT over TCP is
out of scope — that needs WebRTC datachannels (a future transport); the relay is the
guaranteed fallback. See docs/architecture/network-protocol.mdx.
"""

from __future__ import annotations

import logging
import secrets
import socket
import struct
from urllib.parse import urlparse, urlunparse

from backend.modules.network import trust
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

_STUN_MAGIC_COOKIE = 0x2112A442
_STUN_BINDING_REQUEST = 0x0001
_STUN_BINDING_SUCCESS = 0x0101
_ATTR_XOR_MAPPED_ADDRESS = 0x0020


def _peerws_port(advertised: str) -> int:
    return urlparse(advertised).port or 8000


def _with_host(advertised: str, host: str) -> str:
    """The advertised peer-ws URL rewritten to use `host` (keeps port + path)."""
    parsed = urlparse(advertised)
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc))


def host_candidates() -> list[str]:
    """Reachable `/peer-ws` URLs for this node: the configured advertised address
    plus one per non-loopback local IPv4 (LAN). Deduped, advertised first."""
    advertised = trust.advertised_address()
    out: list[str] = [advertised]
    for ip in _local_ipv4s():
        url = _with_host(advertised, ip)
        if url not in out:
            out.append(url)
    return out


def _local_ipv4s() -> list[str]:
    ips: list[str] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, family=socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Best-effort: the address used to reach a public IP (no packets sent).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127.") and ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    return ips


def stun_reflexive_ip(stun_server: str, timeout: float = 2.0) -> str | None:
    """Discover this node's public IP via a STUN binding request (UDP). Returns the
    reflexive IP, or None on failure. Synchronous — call via asyncio.to_thread."""
    try:
        host, _, port_s = stun_server.partition(":")
        port = int(port_s or 3478)
        txn = secrets.token_bytes(12)
        request = (
            struct.pack(">HHI", _STUN_BINDING_REQUEST, 0, _STUN_MAGIC_COOKIE) + txn
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(request, (host, port))
            data, _ = s.recvfrom(2048)
        return _parse_xor_mapped_ip(data, txn)
    except Exception as exc:
        logger.info("STUN lookup via %s failed: %s", stun_server, exc)
        return None


def _parse_xor_mapped_ip(data: bytes, txn: bytes) -> str | None:
    """Pull the IPv4 from a STUN success response's XOR-MAPPED-ADDRESS attribute."""
    if len(data) < 20:
        return None
    msg_type, msg_len, cookie = struct.unpack(">HHI", data[:8])
    if msg_type != _STUN_BINDING_SUCCESS or cookie != _STUN_MAGIC_COOKIE:
        return None
    if data[8:20] != txn:
        return None
    offset = 20
    end = 20 + msg_len
    while offset + 4 <= end:
        attr_type, attr_len = struct.unpack(">HH", data[offset : offset + 4])
        value = data[offset + 4 : offset + 4 + attr_len]
        if (
            attr_type == _ATTR_XOR_MAPPED_ADDRESS
            and len(value) >= 8
            and value[1] == 0x01
        ):
            # value: [0]=reserved [1]=family(0x01 IPv4) [2:4]=xport [4:8]=xaddr
            xaddr = struct.unpack(">I", value[4:8])[0]
            ip_int = xaddr ^ _STUN_MAGIC_COOKIE
            return socket.inet_ntoa(struct.pack(">I", ip_int))
        offset += 4 + attr_len + ((4 - attr_len % 4) % 4)  # 32-bit aligned
    return None


async def gather_candidates() -> list[str]:
    """The prioritized `/peer-ws` candidate URLs to advertise: host (LAN) first,
    then the STUN server-reflexive candidate when ICE is enabled and a public IP is
    found. Always includes at least the advertised address."""
    candidates = host_candidates()
    if get_value("network.iceEnabled", False):
        import asyncio

        stun_server = str(get_value("network.stunServer", "stun.l.google.com:19302"))
        public_ip = await asyncio.to_thread(stun_reflexive_ip, stun_server)
        if public_ip:
            srflx = _with_host(trust.advertised_address(), public_ip)
            if srflx not in candidates:
                candidates.append(srflx)  # lowest priority (after host)
    return candidates
