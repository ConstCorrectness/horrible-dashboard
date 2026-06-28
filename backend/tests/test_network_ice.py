"""ICE-lite candidate gathering tests: the STUN XOR-MAPPED-ADDRESS parser (canned
response, no network) and host-candidate gathering from the advertised address."""

import socket
import struct

from backend.modules.network import ice


def _stun_success(txn: bytes, ip: str, port: int) -> bytes:
    """Build a STUN binding-success response carrying an XOR-MAPPED-ADDRESS."""
    cookie = ice._STUN_MAGIC_COOKIE
    xport = port ^ (cookie >> 16)
    xaddr = struct.unpack(">I", socket.inet_aton(ip))[0] ^ cookie
    attr_value = struct.pack(">BBH", 0, 0x01, xport) + struct.pack(">I", xaddr)
    attr = (
        struct.pack(">HH", ice._ATTR_XOR_MAPPED_ADDRESS, len(attr_value)) + attr_value
    )
    header = struct.pack(">HHI", ice._STUN_BINDING_SUCCESS, len(attr), cookie) + txn
    return header + attr


def test_parse_xor_mapped_ip_roundtrip():
    txn = b"0123456789ab"
    data = _stun_success(txn, "203.0.113.7", 54321)
    assert ice._parse_xor_mapped_ip(data, txn) == "203.0.113.7"


def test_parse_xor_mapped_ip_rejects_wrong_txn():
    data = _stun_success(b"0123456789ab", "203.0.113.7", 54321)
    assert ice._parse_xor_mapped_ip(data, b"DIFFERENTTXN") is None


def test_parse_xor_mapped_ip_rejects_garbage():
    assert ice._parse_xor_mapped_ip(b"not a stun message", b"x" * 12) is None


def test_host_candidates_includes_advertised(monkeypatch, tmp_path):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend.modules.settings.routes import set_value

    set_value("network.advertisedAddress", "ws://10.0.0.5:8000/peer-ws")
    candidates = ice.host_candidates()
    assert candidates[0] == "ws://10.0.0.5:8000/peer-ws"
    # All candidates keep the peer-ws port + path.
    assert all(c.endswith(":8000/peer-ws") for c in candidates)


def test_with_host_rewrites_only_host():
    assert (
        ice._with_host("ws://localhost:8000/peer-ws", "203.0.113.7")
        == "ws://203.0.113.7:8000/peer-ws"
    )
