"""SSRF-safe server-side fetch for the browser module's reader mode.

Reader mode fetches an arbitrary user-supplied URL on the server and returns its
extracted text, so it is a classic SSRF sink. Before every request (including each
redirect hop) we:

1. require an `http`/`https` scheme;
2. resolve the host and reject if **any** resolved address is loopback / private /
   link-local / multicast / reserved (blocks `localhost`, `127.0.0.1`,
   `169.254.169.254`, RFC1918, etc.);
3. follow redirects **manually**, re-validating each `Location` (a 302 to an
   internal host can't slip past a one-time check);
4. cap the response size and require an HTML-ish content type.

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


async def safe_fetch_html(url: str) -> tuple[str, str]:
    """Fetch `url` under the SSRF policy, following redirects manually and
    re-validating each hop. Returns `(final_url, html)`. Raises `UnsafeUrlError`
    for a policy violation, or `httpx.HTTPError` for a transport/HTTP failure."""
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=_TIMEOUT, headers={"User-Agent": _UA}
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            await asyncio.to_thread(_validate, current)
            resp = await client.get(current)
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise UnsafeUrlError("redirect without a Location header")
                current = urljoin(current, location)
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if not any(t in ctype for t in ("html", "xml", "text")):
                raise UnsafeUrlError(f"not a readable page (content-type: {ctype!r})")
            if len(resp.content) > _MAX_BYTES:
                raise UnsafeUrlError("response too large")
            return current, resp.text
    raise UnsafeUrlError("too many redirects")


async def fetch_readable(url: str) -> Article:
    """Reader mode: SSRF-safe fetch + main-content extraction."""
    final_url, html = await safe_fetch_html(url)
    return extract_article(html, final_url)
