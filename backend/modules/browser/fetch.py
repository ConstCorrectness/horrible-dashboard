"""SSRF-safe server-side fetch for the browser module.

Two sinks route through here, both fetching arbitrary user-supplied URLs on the
server: reader mode (`safe_fetch_html`, returns extracted text) and CLIP image
embedding (`safe_fetch_bytes`, returns raw pixels). Before every request (including
each redirect hop) we:

1. require an `http`/`https` scheme;
2. resolve the host and reject if **any** resolved address is loopback / private /
   link-local / multicast / reserved (blocks `localhost`, `127.0.0.1`,
   `169.254.169.254`, RFC1918, etc.);
3. follow redirects **manually**, re-validating each `Location` (a 302 to an
   internal host can't slip past a one-time check);
4. require an expected content type and cap the response size *during* transfer.

Both sinks share `_fetch_guarded` deliberately — a second copy of the redirect loop
would be a second guard to keep in sync, and drift is how these fail.

Residual risk: a TOCTOU DNS-rebind (the name resolving to a public IP at check time
and an internal IP at connect time) is *mitigated* but not fully eliminated — full
protection needs pinning the validated IP into the connection, which is a larger
change. This is still strictly better than the unguarded `library` ingest fetch,
which should later route through here too (see docs/modules/browser.mdx).

Extraction reuses `library.extract.extract_article` (pure, no network).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from backend.modules.library.extract import Article, extract_article

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 4
_MAX_BYTES = 4_000_000
_TIMEOUT = 15.0
_UA = "Mozilla/5.0 (compatible; horrible-dashboard/0.1) browser-reader"

# Byte fetches (CLIP image embedding). Kept narrow: only formats Pillow decodes
# safely, and a cap well under the HTML one — an image big enough to exceed this is
# not one we want to hand to a decoder anyway.
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp")
_MAX_IMAGE_BYTES = 12_000_000


class UnsafeUrlError(ValueError):
    """A URL failed the SSRF policy (bad scheme, unresolvable, or non-public IP)."""


def _check_host_public(host: str) -> None:
    """Resolve `host` and raise `UnsafeUrlError` if any address is non-public."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve host: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeUrlError(f"host resolves to a non-public address: {ip}")


def _validate(url: str) -> None:
    """Scheme + resolved-IP policy check for one URL (sync; run off the loop)."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme: {parts.scheme or '(none)'}")
    if not parts.hostname:
        raise UnsafeUrlError("missing host")
    _check_host_public(parts.hostname)


async def _fetch_guarded(
    url: str,
    *,
    accept: tuple[str, ...],
    max_bytes: int,
    user_agent: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, httpx.Response]:
    """Walk redirects under the SSRF policy and return `(final_url, response)`.

    The single place the policy is enforced. Both `safe_fetch_html` and
    `safe_fetch_bytes` go through here on purpose: two copies of a redirect loop are
    two guards that drift, and the whole value of this module is that there's exactly
    one. `accept` is a content-type substring allowlist; the body is streamed so
    `max_bytes` aborts a hostile response *during* transfer rather than after it.

    `user_agent` overrides the reader-mode default. The focused crawler passes its own
    honest, contactable agent string: a crawler that identifies as a browser has no
    business claiming to honour robots.txt. `headers` carries conditional-request
    headers (`If-None-Match`, `If-Modified-Since`) so a re-crawl can be answered with
    a **304**, which is returned as a normal response — content-type and size checks
    are skipped for it, because a 304 has no body to check.
    """
    current = url
    base_headers = {"User-Agent": user_agent or _UA, **(headers or {})}
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=_TIMEOUT, headers=base_headers
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            await asyncio.to_thread(_validate, current)
            req = client.build_request("GET", current)
            resp = await client.send(req, stream=True)
            try:
                # **Before** the redirect check, not after: 304 lives in the 3xx
                # range, so `is_redirect` is true for it, and a Not-Modified response
                # carries no Location header. Checking redirects first therefore
                # rejected every conditional request as a malformed redirect — which
                # silently broke the crawler's entire incremental path.
                if resp.status_code == 304:
                    return current, resp
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("redirect without a Location header")
                    current = urljoin(current, location)
                    continue
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if not any(t in ctype for t in accept):
                    raise UnsafeUrlError(f"unexpected content-type: {ctype!r}")
                # Trust Content-Length only to reject early — a lying header can't
                # get past the streaming cap below.
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise UnsafeUrlError(
                        f"response too large ({declared} bytes > {max_bytes})"
                    )
                await _read_capped(resp, max_bytes)
                return current, resp
            finally:
                await resp.aclose()
    raise UnsafeUrlError("too many redirects")


async def _read_capped(resp: httpx.Response, max_bytes: int) -> None:
    """Buffer a streamed response, aborting as soon as it exceeds `max_bytes`.

    Sets `resp._content` so `.text` / `.content` work downstream. The previous
    `len(resp.content) > _MAX_BYTES` check was a *complaint*, not a cap: the whole
    body was already in memory by the time it ran, so a hostile server could hand us
    a gigabyte and the check would fire far too late to matter.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in resp.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise UnsafeUrlError(f"response too large (exceeded {max_bytes} bytes)")
        chunks.append(chunk)
    resp._content = b"".join(chunks)  # noqa: SLF001 — httpx has no public setter


async def safe_fetch_html(
    url: str,
    *,
    user_agent: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Fetch `url` under the SSRF policy, following redirects manually and
    re-validating each hop. Returns `(final_url, html)`. Raises `UnsafeUrlError`
    for a policy violation, or `httpx.HTTPError` for a transport/HTTP failure."""
    final_url, resp = await _fetch_guarded(
        url,
        accept=("html", "xml", "text"),
        max_bytes=_MAX_BYTES,
        user_agent=user_agent,
        headers=headers,
    )
    return final_url, resp.text


async def safe_fetch_bytes(
    url: str,
    *,
    accept: tuple[str, ...] = _IMAGE_TYPES,
    max_bytes: int = _MAX_IMAGE_BYTES,
) -> tuple[str, bytes]:
    """Fetch `url`'s raw bytes under the same SSRF policy. Returns `(final_url, data)`.

    Added for CLIP image embedding (see modules/library/clip.py), which needs the
    actual pixels. Note this **reverses** a deliberate property of media ingest: it
    previously fetched nothing server-side and so added no URL sink at all. Every
    `asset.src` captured from an arbitrary page now becomes a backend request, which
    is exactly why it routes through the shared guard rather than a bare httpx call.
    """
    final_url, resp = await _fetch_guarded(url, accept=accept, max_bytes=max_bytes)
    return final_url, resp.content


async def fetch_readable(url: str) -> Article:
    """Reader mode: SSRF-safe fetch + main-content extraction."""
    final_url, html = await safe_fetch_html(url)
    return extract_article(html, final_url)
