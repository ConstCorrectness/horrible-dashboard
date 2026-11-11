"""The headless browser engine's egress proxy: resolve once, check, connect to that.

`BrowserSession._route` refuses a request whose host resolves to a non-public
address, but it only *checks*: Chromium then resolves the name again to connect. A
hostile DNS server answers "public" to the check and "127.0.0.1" to the connection
(DNS rebinding), and the check's verdict is cached per host besides. Routing also
misses WebSockets. So the engine's Chromium is launched behind this proxy, which
does the lookup itself, refuses any non-public answer, and connects to the address
it checked: there is no second lookup to rebind.

There is no local unlock here (unlike the desktop proxy, apps/desktop/src/
browserProxy.ts): this engine may run on a hosted server, where "local" is the
server's own network.

One proxy per process, on its own thread and event loop, shared by every session.
Plain `http://` arrives as an absolute-URI request and is forwarded with
`Connection: close`; HTTPS and WebSockets arrive as CONNECT tunnels.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import threading
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from backend.modules.browser.fetch import UnsafeUrlError, is_public_address

logger = logging.getLogger(__name__)

_HEAD_TIMEOUT_S = 30.0
_MAX_HEAD = 64 * 1024
_HOP = {
    "connection",
    "keep-alive",
    "proxy-authorization",
    "proxy-authenticate",
    "proxy-connection",
    "te",
    "trailer",
    "upgrade",
}

Resolver = Callable[[str], Awaitable[list[str]]]


async def _system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, None, type=socket.SOCK_STREAM
    )
    return [str(info[4][0]) for info in infos]


async def resolve_checked(host: str, resolve: Resolver = _system_resolve) -> str:
    """The address to connect to for `host`; `UnsafeUrlError` if any is non-public."""
    host = host.strip().lower().strip("[]").rstrip(".")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addresses = [host]
    except ValueError:
        try:
            addresses = await resolve(host)
        except OSError as exc:
            raise UnsafeUrlError(f"cannot resolve host: {host}") from exc
    if not addresses:
        raise UnsafeUrlError(f"cannot resolve host: {host}")
    for address in addresses:
        if not is_public_address(address):
            raise UnsafeUrlError(f"{host} resolves to a non-public address: {address}")
    return addresses[0]


def split_authority(authority: str) -> tuple[str, int] | None:
    """CONNECT's `host:port` (`[v6]:port` for IPv6), or None."""
    if authority.startswith("["):
        host, sep, rest = authority[1:].partition("]:")
    else:
        host, sep, rest = authority.rpartition(":")
    if not sep or not host or not rest.isdigit():
        return None
    port = int(rest)
    return (host, port) if 0 < port < 65536 else None


async def _answer(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    text = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(
        status, "Error"
    )
    body = f"{reason}\n".encode()
    writer.write(
        f"HTTP/1.1 {status} {text}\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    try:
        await writer.drain()
    finally:
        writer.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _splice(
    client: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
) -> None:
    await asyncio.gather(_pipe(client[0], upstream[1]), _pipe(upstream[0], client[1]))


def _forward_head(method: str, target: str, version: str, header_lines: list[str]):
    """The origin-form request head for an absolute-URI proxy request."""
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        return None
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    out = [f"{method} {path} {version}"]
    for line in header_lines:
        name = line.split(":", 1)[0].strip().lower()
        if name in _HOP or name == "host":
            continue
        out.append(line)
    out.append(f"Host: {parts.netloc.rsplit('@', 1)[-1]}")
    out.append("Connection: close")
    return (
        parts.hostname,
        parts.port or 80,
        ("\r\n".join(out) + "\r\n\r\n").encode("latin-1"),
    )


def make_handler(resolve: Resolver = _system_resolve):
    """The per-connection handler; `resolve` is injectable for tests."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT_S
            )
        except (
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")[:-2]
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            await _answer(writer, 400, "bad request line")
            return
        if method.upper() == "CONNECT":
            authority = split_authority(target)
            if authority is None:
                await _answer(writer, 400, "bad CONNECT target")
                return
            host, port = authority
            prelude = b"HTTP/1.1 200 Connection Established\r\n\r\n"
        else:
            forwarded = _forward_head(method, target, version, lines[1:])
            if forwarded is None:
                await _answer(writer, 400, "proxy requests need an absolute http URL")
                return
            host, port, prelude = forwarded
        try:
            address = await resolve_checked(host, resolve)
        except UnsafeUrlError as exc:
            await _answer(writer, 403, f"blocked by egress policy: {exc}")
            return
        try:
            upstream = await asyncio.open_connection(address, port)
        except OSError:
            await _answer(writer, 502, "upstream unreachable")
            return
        if method.upper() == "CONNECT":
            writer.write(prelude)
            await writer.drain()
        else:
            upstream[1].write(prelude)
        await _splice((reader, writer), upstream)

    return handle


class _Proxy:
    def __init__(self) -> None:
        self.port: int | None = None
        self._lock = threading.Lock()

    def ensure(self) -> int:
        """Start the proxy thread once; returns its loopback port."""
        with self._lock:
            if self.port is not None:
                return self.port
            ready = threading.Event()
            failure: list[BaseException] = []

            def run() -> None:
                loop = asyncio.new_event_loop()
                try:
                    server = loop.run_until_complete(
                        asyncio.start_server(
                            make_handler(), "127.0.0.1", 0, limit=_MAX_HEAD
                        )
                    )
                    self.port = server.sockets[0].getsockname()[1]
                except BaseException as exc:  # noqa: BLE001
                    failure.append(exc)
                    ready.set()
                    return
                ready.set()
                loop.run_forever()

            threading.Thread(
                target=run, name="browser-egress-proxy", daemon=True
            ).start()
            ready.wait(10)
            if self.port is None:
                raise RuntimeError(
                    f"browser egress proxy failed to start: {failure or 'timeout'}"
                )
            logger.info("browser egress proxy on 127.0.0.1:%s", self.port)
            return self.port


_proxy = _Proxy()


def egress_proxy_port() -> int:
    """The shared proxy's port, starting it on first use."""
    return _proxy.ensure()
