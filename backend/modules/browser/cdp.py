"""Connection forensics from Chromium's DevTools protocol.

Playwright's `Response` object tells you the status and the headers. Chromium
*measured* far more than that for every request — how long the DNS lookup took,
which IP actually answered, whether the connection was reused, what TLS version and
certificate were negotiated, whether it spoke HTTP/3 — and the browser module was
throwing all of it away.

That data is the difference between a list of URLs and an explanation of how the web
works. `Network.responseReceived` carries it; this module is the pure translation
from CDP's wire format into the shape `IoEvent` stores, kept separate from
`session.py` so it can be tested without a browser.

**CDP's timing format is a trap.** `requestTime` is an absolute timestamp in
*seconds*; every other field is a millisecond offset relative to it, and `-1` means
"this phase did not happen" — a reused connection has no DNS or TLS phase, and
treating its `-1` as a duration produces negative bars in a waterfall.
"""

from __future__ import annotations

from typing import Any

# CDP phase boundaries, as (start_field, end_field) pairs. `receiveHeadersEnd` has no
# paired start: it's measured from when the request finished being sent.
_PHASES: tuple[tuple[str, str, str], ...] = (
    ("dns", "dnsStart", "dnsEnd"),
    ("connect", "connectStart", "connectEnd"),
    ("tls", "sslStart", "sslEnd"),
    ("send", "sendStart", "sendEnd"),
)


def _num(timing: dict[str, Any], key: str) -> float | None:
    """One CDP timing field, or None when it didn't happen.

    Negative values are CDP's "not applicable" marker, not a measurement.
    """
    value = timing.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number < 0 else number


def phase_timings(timing: dict[str, Any] | None) -> dict[str, float]:
    """CDP `ResourceTiming` → per-phase durations in milliseconds.

    Phases that didn't happen are **omitted**, not zeroed: a connection reused from
    the pool genuinely has no DNS phase, and a zero would draw a bar claiming the
    lookup was instant rather than absent.

    `wait` is time-to-first-byte after the request was sent — the server's own
    thinking time, and usually the most interesting number on the row.
    """
    if not timing:
        return {}

    out: dict[str, float] = {}
    for name, start_key, end_key in _PHASES:
        start = _num(timing, start_key)
        end = _num(timing, end_key)
        if start is None or end is None or end < start:
            continue
        duration = round(end - start, 2)
        # A sub-microsecond phase is measurement noise from a reused connection.
        if duration > 0:
            out[name] = duration

    send_end = _num(timing, "sendEnd")
    headers_end = _num(timing, "receiveHeadersEnd")
    if send_end is not None and headers_end is not None and headers_end >= send_end:
        out["wait"] = round(headers_end - send_end, 2)

    return out


def security_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """CDP `SecurityDetails` → the fields worth showing.

    Deliberately partial. The full structure carries certificate chains, SCT audit
    logs and signature algorithms; what answers "who am I actually talking to and is
    it current" is the protocol, the issuer, the subject, the SANs and the validity
    window.
    """
    if not details:
        return None

    sans = [str(name) for name in (details.get("sanList") or [])][:12]
    out: dict[str, Any] = {
        "protocol": details.get("protocol"),
        "cipher": details.get("cipher"),
        "issuer": details.get("issuer"),
        "subject": details.get("subjectName"),
        "sans": sans,
        "san_count": len(details.get("sanList") or []),
    }
    for src, dst in (("validFrom", "valid_from"), ("validTo", "valid_to")):
        value = details.get(src)
        if value:
            try:
                out[dst] = int(value)  # CDP gives seconds since the epoch
            except (TypeError, ValueError):
                pass
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def connection_info(response: dict[str, Any]) -> dict[str, Any]:
    """A CDP `Network.responseReceived` payload → the fields `IoEvent` stores.

    Keyed the way `record_browser_request` takes them, so the caller can splat it.
    """
    port = response.get("remotePort")
    try:
        port = int(port) if port is not None else None
    except (TypeError, ValueError):
        port = None

    # `fromDiskCache`/`fromPrefetchCache` mean nothing crossed the network, which is
    # why such a row shows no timing phases — worth stating rather than looking like
    # a bug.
    cached = bool(
        response.get("fromDiskCache")
        or response.get("fromPrefetchCache")
        or response.get("fromServiceWorker")
    )

    return {
        "remote_ip": response.get("remoteIPAddress") or None,
        "remote_port": port,
        "http_protocol": response.get("protocol") or None,
        "tls": security_details(response.get("securityDetails")),
        "timing": phase_timings(response.get("timing")) or None,
        "from_cache": cached or None,
    }
