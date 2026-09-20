"""Who may push spans at this node.

Every other external-push receiver here (`/api/trajectories/ingest`, localtrack)
is unauthenticated, and `pnpm dev:desktop` binds `0.0.0.0` by default — so on a
LAN-bound node those accept writes from anyone on the network. This one does not
copy that: a trace carries prompts and tool output, and a collector anyone can
write to is also one anyone can fill.

The rule: **loopback needs nothing, everything else needs the ingest token.** An
agent in a notebook kernel on this machine should work with zero configuration;
one on another box sets `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>`.

## "Loopback" through the Vite proxy

Under `pnpm dev` the browser layout reaches the backend through Vite, so *every*
request looks like it came from 127.0.0.1 — including one a LAN client sent to
Vite's 0.0.0.0:5173 under `pnpm dev:lan`. The proxy is configured with `xfwd`, and
`X-Forwarded-For` is trusted **only when the direct peer is itself loopback** (the
proxy), reading its **last** entry: that is the address the proxy saw, appended by
the proxy. Earlier entries are whatever the client claimed. A non-loopback peer is
judged by its own address, whatever header it sends.

The token is readable only by a loopback caller (`GET /api/otel/ingest`), for the
same reason: handing it to whoever asks would make it decoration.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets

from fastapi import HTTPException, Request

from backend.modules.database import secrets_store

TOKEN_SECRET = "otel:ingest_token"


def is_loopback_addr(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    # Node's proxy reports an IPv4 client on a dual-stack socket as
    # `::ffff:127.0.0.1`, which `is_loopback` calls *not* loopback.
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).is_loopback


def client_is_loopback(request: Request) -> bool:
    peer = request.client.host if request.client else None
    if not is_loopback_addr(peer):
        return False
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        return is_loopback_addr(last)
    return True


def get_token(*, create: bool = True) -> str | None:
    token = secrets_store.get_secret_or_none(TOKEN_SECRET)
    if token or not create:
        return token
    token = secrets.token_urlsafe(32)
    secrets_store.upsert_secret(TOKEN_SECRET, token)
    return token


def rotate_token() -> str:
    token = secrets.token_urlsafe(32)
    secrets_store.upsert_secret(TOKEN_SECRET, token)
    return token


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def require_ingest(request: Request) -> None:
    """Route dependency for the OTLP endpoint."""
    if client_is_loopback(request):
        return
    supplied = _bearer(request)
    expected = get_token(create=False)
    if supplied and expected and hmac.compare_digest(supplied, expected):
        return
    raise HTTPException(
        status_code=401,
        detail="OTLP ingest from a non-loopback address needs the ingest token",
    )


def require_loopback(request: Request) -> None:
    """Route dependency for anything that reveals or changes the token."""
    if not client_is_loopback(request):
        raise HTTPException(status_code=403, detail="only available from this machine")
