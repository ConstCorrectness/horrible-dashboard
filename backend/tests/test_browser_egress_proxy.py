"""The headless engine's egress proxy (backend/modules/browser/egress_proxy.py).

It must refuse a name that resolves to a non-public address on both ways in (plain
HTTP and CONNECT), and otherwise connect to the address it checked, with the Host
header taken from the request URI. The address table is shared with the desktop
proxy (apps/desktop/test/browserProxy.test.ts has the same cases).
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.browser import egress_proxy
from backend.modules.browser.fetch import UnsafeUrlError, is_public_address


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1%12",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:c0a8:101::1",
        "localhost",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not is_public_address(address)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "100.128.0.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]
)
def test_public_addresses_pass(address: str) -> None:
    assert is_public_address(address)


def test_split_authority() -> None:
    assert egress_proxy.split_authority("example.com:443") == ("example.com", 443)
    assert egress_proxy.split_authority("[::1]:5173") == ("::1", 5173)
    assert egress_proxy.split_authority("example.com") is None
    assert egress_proxy.split_authority("a:0") is None


def _fixed(table: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        return table.get(host, [])

    return resolve


def test_resolve_checked_refuses_any_private_answer() -> None:
    resolve = _fixed(
        {
            "rebind.example": ["93.184.216.34", "127.0.0.1"],
            "ok.example": ["93.184.216.34"],
        }
    )

    async def go() -> None:
        with pytest.raises(UnsafeUrlError):
            await egress_proxy.resolve_checked("rebind.example", resolve)
        assert (
            await egress_proxy.resolve_checked("ok.example", resolve) == "93.184.216.34"
        )
        with pytest.raises(UnsafeUrlError):
            await egress_proxy.resolve_checked("[::1]", resolve)
        with pytest.raises(UnsafeUrlError):
            await egress_proxy.resolve_checked("nowhere.example", resolve)

    asyncio.run(go())


async def _origin() -> tuple[asyncio.AbstractServer, int]:
    """A loopback origin that answers with the Host header it got."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        host = next(
            (
                ln.split(":", 1)[1].strip()
                for ln in head.split("\r\n")
                if ln.lower().startswith("host:")
            ),
            "",
        )
        body = f"host={host}".encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _ask(proxy_port: int, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(raw)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    return data


def test_proxy_refuses_a_name_that_resolves_to_loopback() -> None:
    async def go() -> None:
        origin, port = await _origin()
        proxy = await asyncio.start_server(
            egress_proxy.make_handler(_fixed({"rebind.example": ["127.0.0.1"]})),
            "127.0.0.1",
            0,
        )
        pport = proxy.sockets[0].getsockname()[1]
        try:
            plain = await _ask(
                pport,
                f"GET http://rebind.example:{port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
            )
            assert plain.startswith(b"HTTP/1.1 403")
            tunnel = await _ask(
                pport, f"CONNECT rebind.example:{port} HTTP/1.1\r\n\r\n".encode()
            )
            assert tunnel.startswith(b"HTTP/1.1 403")
        finally:
            proxy.close()
            origin.close()

    asyncio.run(go())


def test_proxy_connects_to_the_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    # Treat loopback as public for this test so the origin is reachable.
    monkeypatch.setattr(egress_proxy, "is_public_address", lambda _a: True)

    async def go() -> None:
        origin, port = await _origin()
        proxy = await asyncio.start_server(
            egress_proxy.make_handler(_fixed({"site.example": ["127.0.0.1"]})),
            "127.0.0.1",
            0,
        )
        pport = proxy.sockets[0].getsockname()[1]
        try:
            plain = await _ask(
                pport,
                f"GET http://site.example:{port}/x HTTP/1.1\r\nHost: wrong\r\nProxy-Connection: keep-alive\r\n\r\n".encode(),
            )
            assert plain.startswith(b"HTTP/1.1 200")
            assert plain.endswith(f"host=site.example:{port}".encode())
            tunnel = await _ask(
                pport, f"CONNECT site.example:{port} HTTP/1.1\r\n\r\n".encode()
            )
            assert tunnel.startswith(b"HTTP/1.1 200 Connection Established")
        finally:
            proxy.close()
            origin.close()

    asyncio.run(go())
