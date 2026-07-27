"""The browser's network-forensics helpers: CDP timing and traceroute parsing.

All pure. The CDP conversion is the piece most likely to be silently wrong — CDP's
`-1` sentinel for "this phase didn't happen" reads as a valid number, and treating
it as a duration produces negative bars in a waterfall with no error anywhere.
"""

from __future__ import annotations

from backend.modules.browser.cdp import (
    connection_info,
    phase_timings,
    security_details,
)
from backend.modules.browser.netprobe import parse_traceroute

# A cold connection: every phase happened.
_FULL_TIMING = {
    "requestTime": 1234.5,
    "dnsStart": 0,
    "dnsEnd": 12.5,
    "connectStart": 12.5,
    "connectEnd": 40,
    "sslStart": 20,
    "sslEnd": 40,
    "sendStart": 40,
    "sendEnd": 40.5,
    "receiveHeadersEnd": 190,
}

# A connection reused from the pool: no DNS, no TCP, no TLS.
_REUSED_TIMING = {
    "requestTime": 1300.0,
    "dnsStart": -1,
    "dnsEnd": -1,
    "connectStart": -1,
    "connectEnd": -1,
    "sslStart": -1,
    "sslEnd": -1,
    "sendStart": 0,
    "sendEnd": 1,
    "receiveHeadersEnd": 55,
}


def test_phase_timings_splits_a_cold_connection():
    assert phase_timings(_FULL_TIMING) == {
        "dns": 12.5,
        "connect": 27.5,
        "tls": 20.0,
        "send": 0.5,
        "wait": 149.5,
    }


def test_a_reused_connection_omits_phases_rather_than_zeroing_them():
    # The absence is the information: it's *why* the second request to a host is
    # faster. A zero would draw a bar claiming the lookup was instantaneous.
    timings = phase_timings(_REUSED_TIMING)
    assert "dns" not in timings
    assert "connect" not in timings
    assert "tls" not in timings
    assert timings == {"send": 1.0, "wait": 54.0}


def test_phase_timings_handles_missing_and_malformed_input():
    assert phase_timings(None) == {}
    assert phase_timings({}) == {}
    assert phase_timings({"dnsStart": "x", "dnsEnd": "y"}) == {}


def test_phase_timings_ignores_an_end_before_its_start():
    assert "dns" not in phase_timings({"dnsStart": 50, "dnsEnd": 10})


def test_security_details_keeps_the_fields_worth_showing():
    out = security_details(
        {
            "protocol": "TLS 1.3",
            "cipher": "AES_128_GCM",
            "issuer": "R3",
            "subjectName": "example.com",
            "sanList": [f"h{i}.example.com" for i in range(20)],
            "validFrom": 1700000000,
            "validTo": 1800000000,
        }
    )
    assert out is not None
    assert out["protocol"] == "TLS 1.3"
    assert out["subject"] == "example.com"
    # Capped for display, but the true count survives — a certificate covering
    # hundreds of names is a CDN's shared cert, which is worth being able to see.
    assert len(out["sans"]) == 12
    assert out["san_count"] == 20
    assert out["valid_to"] == 1800000000


def test_security_details_of_a_plain_http_response():
    assert security_details(None) is None
    assert security_details({}) is None


def test_connection_info_maps_onto_the_recorder_kwargs():
    info = connection_info(
        {
            "url": "https://example.com/",
            "remoteIPAddress": "93.184.216.34",
            "remotePort": 443,
            "protocol": "h2",
            "timing": _FULL_TIMING,
            "securityDetails": {"protocol": "TLS 1.3"},
        }
    )
    assert info["remote_ip"] == "93.184.216.34"
    assert info["remote_port"] == 443
    assert info["http_protocol"] == "h2"
    assert info["timing"]["dns"] == 12.5
    assert info["tls"]["protocol"] == "TLS 1.3"
    assert info["from_cache"] is None


def test_connection_info_flags_a_cache_hit():
    info = connection_info({"url": "x", "fromDiskCache": True})
    assert info["from_cache"] is True
    # Nothing crossed the network, so there are no phases to report — which is why
    # such a row shows an empty waterfall rather than looking broken.
    assert info["timing"] is None


def test_connection_info_survives_a_bare_response():
    info = connection_info({})
    assert info["remote_ip"] is None
    assert info["remote_port"] is None


# --- traceroute parsing -----------------------------------------------------

_WINDOWS = """
Tracing route to example.com [93.184.216.34]
over a maximum of 30 hops:

  1     1 ms     1 ms     1 ms  192.168.1.1
  2     9 ms    10 ms     8 ms  10.20.30.1
  3     *        *        *     Request timed out.
  4    14 ms    13 ms    15 ms  93.184.216.34

Trace complete.
"""

_UNIX = """traceroute to example.com (93.184.216.34), 30 hops max, 60 byte packets
 1  192.168.1.1  0.512 ms  0.481 ms  0.470 ms
 2  * * *
 3  93.184.216.34  13.902 ms  13.881 ms  13.860 ms
"""

_UNIX_NAMED = """traceroute to example.com (93.184.216.34), 30 hops max
 1  router.lan (192.168.1.1)  0.5 ms  0.4 ms  0.4 ms
 2  core1.isp.net (10.20.30.1)  9.1 ms  9.0 ms  8.8 ms
"""


def test_parses_windows_tracert():
    hops = parse_traceroute(_WINDOWS)
    assert [h.ttl for h in hops] == [1, 2, 3, 4]
    assert hops[0].ip == "192.168.1.1"
    assert hops[0].rtt_ms == [1.0, 1.0, 1.0]
    assert hops[3].ip == "93.184.216.34"


def test_parses_unix_traceroute():
    hops = parse_traceroute(_UNIX)
    assert [h.ttl for h in hops] == [1, 2, 3]
    assert hops[2].rtt_ms == [13.902, 13.881, 13.86]


def test_extracts_a_hostname_when_the_tool_resolves_one():
    hops = parse_traceroute(_UNIX_NAMED)
    assert hops[0].host == "router.lan"
    assert hops[0].ip == "192.168.1.1"
    assert hops[1].host == "core1.isp.net"


def test_a_silent_router_is_still_a_hop():
    # Dropping it would misrepresent the path length: the packets did cross that
    # router, it just declined to answer ICMP.
    for output in (_WINDOWS, _UNIX):
        hops = parse_traceroute(output)
        silent = [h for h in hops if h.timeout]
        assert len(silent) == 1
        assert silent[0].rtt_ms == []


_WINDOWS_IPV6 = """
Tracing route to example.com [2606:4700:10::ac42:93f3]
over a maximum of 8 hops:

  1   103 ms     2 ms     1 ms  2607:fea8:7063:7800:3e2d:9eff:fe9f:d2ff
  2    22 ms    13 ms    12 ms  2607:f798:804:201::1
  3    14 ms    13 ms    16 ms  2606:4700:10::ac42:93f3

Trace complete.
"""


def test_parses_bare_ipv6_hops():
    # Not an edge case: a dual-stack host traces over IPv6 by default, and `tracert`
    # prints those addresses unbracketed. Missing them made every hop location-less.
    hops = parse_traceroute(_WINDOWS_IPV6)
    assert [h.ttl for h in hops] == [1, 2, 3]
    assert hops[0].ip == "2607:fea8:7063:7800:3e2d:9eff:fe9f:d2ff"
    assert hops[1].ip == "2607:f798:804:201::1"
    assert hops[2].ip == "2606:4700:10::ac42:93f3"
    assert hops[0].rtt_ms == [103.0, 2.0, 1.0]


def test_timing_columns_are_never_mistaken_for_an_address():
    hops = parse_traceroute("  1    12 ms    13 ms    14 ms  *\n")
    assert hops[0].ip == ""


def test_parse_traceroute_ignores_headers_and_junk():
    assert parse_traceroute("") == []
    assert parse_traceroute("traceroute to example.com (1.2.3.4), 30 hops max") == []
    assert parse_traceroute("not\nany\nhops\nhere") == []


# --- conditional fetch ------------------------------------------------------


def test_304_is_not_treated_as_a_redirect():
    """A Not-Modified response must survive the guard's redirect handling.

    Regression: httpx's `is_redirect` is true for any 3xx, and **304 is a 3xx** with
    no Location header. With the redirect check first, every conditional request came
    back as "redirect without a Location header" — which silently broke the crawler's
    whole incremental path, since the start URL failed and the frontier died with it.
    """
    import asyncio

    import httpx

    from backend.modules.browser.fetch import _fetch_guarded

    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.headers))
        if request.headers.get("if-none-match"):
            return httpx.Response(304)
        return httpx.Response(
            200, headers={"content-type": "text/html", "etag": "W/abc"}, text="<p>hi</p>"
        )

    transport = httpx.MockTransport(handler)

    async def run() -> tuple[int, int]:
        # Patch only the client construction; the guard logic under test is untouched.
        real_client = httpx.AsyncClient

        def fake_client(**kwargs):
            kwargs.pop("follow_redirects", None)
            return real_client(transport=transport, **kwargs)

        httpx.AsyncClient = fake_client  # type: ignore[misc]
        try:
            _u1, fresh = await _fetch_guarded(
                "https://example.com/", accept=("html",), max_bytes=10_000
            )
            _u2, cached = await _fetch_guarded(
                "https://example.com/",
                accept=("html",),
                max_bytes=10_000,
                headers={"If-None-Match": "W/abc"},
            )
            return fresh.status_code, cached.status_code
        finally:
            httpx.AsyncClient = real_client  # type: ignore[misc]

    fresh_status, cached_status = asyncio.run(run())
    assert fresh_status == 200
    assert cached_status == 304
    assert calls[1].get("if-none-match") == "W/abc"
