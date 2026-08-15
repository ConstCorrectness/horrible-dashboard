"""Active network probes: how a name resolves, and how the packets get there.

The browser's network view answers "what did this page request". This module
answers the questions underneath it — the ones a URL bar hides completely:

- **Where does a name actually come from?** `resolve_chain` walks the DNS
  delegation from a root server down, showing every referral: the root pointing at
  the TLD's nameservers, the TLD pointing at the domain's, and the authoritative
  server finally answering. Each rung records who answered, in how long, and whether
  the referral was DNSSEC-signed. Your OS resolver does this once and then hides it
  behind a cache forever.
- **What path do the packets take?** `traceroute` shows the hops between here and
  the server, which is where latency actually comes from.

**Privileges.** The DNS walk is plain UDP/53 and needs none — which is why it's the
centrepiece rather than the afterthought. Traceroute is the opposite: it needs raw
ICMP sockets, which need administrator rights on Windows and root on Unix, so this
shells out to the *system* `tracert`/`traceroute` binary and parses its output
instead. Less elegant, but it works as a normal user, which matters more.

**Safety.** Every target goes through the browser module's `_check_host_public`
first. Probing internal hosts is a reconnaissance primitive, and the fact that these
are user-triggered doesn't change that a page could suggest a target.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from backend import paths

logger = logging.getLogger(__name__)

DNS_TIMEOUT_S = 4.0
DNS_TOTAL_TIMEOUT_S = 25.0
TRACEROUTE_TIMEOUT_S = 60.0
MAX_HOPS = 30

# Resolvers used when UDP/53 is blocked outbound (corporate networks, some ISPs).
# DoH answers the *final* question but cannot show the delegation chain — it is a
# recursive resolver, so the walk collapses to one hop and says so.
_DOH_ENDPOINTS = (
    ("Cloudflare", "https://cloudflare-dns.com/dns-query"),
    ("Google", "https://dns.google/resolve"),
)


@dataclass
class DnsHop:
    """One rung of the delegation ladder."""

    level: str  # "root" | "tld" | "authoritative" | "answer"
    zone: str  # the zone this server is authoritative for
    server: str  # IP we asked
    server_name: str  # its hostname, when known
    query: str  # the name we asked about
    # Nameservers this server delegated to, if it referred us onward, and the zone
    # they are authoritative for (which is how the next rung labels itself).
    referral: list[str] = field(default_factory=list)
    referral_zone: str = ""
    # Glue records — the addresses shipped with a referral, without which resolution
    # would be circular for in-zone nameservers.
    glue: dict[str, list[str]] = field(default_factory=dict)
    answers: list[str] = field(default_factory=list)
    # An alias answer. Kept apart from `answers` because a CNAME is not an address:
    # resolution continues at a different name, often in a different zone entirely,
    # which is exactly the thing a flat "here's your answer" would hide.
    cname: str = ""
    # Whether the parent published a DS record, i.e. whether this link of the chain
    # of trust is signed.
    signed: bool = False
    rtt_ms: float | None = None
    error: str | None = None


@dataclass
class DnsChain:
    name: str
    record_type: str
    hops: list[DnsHop]
    addresses: list[str]
    dnssec: bool
    elapsed_ms: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "record_type": self.record_type,
            "hops": [asdict(h) for h in self.hops],
            "addresses": self.addresses,
            "dnssec": self.dnssec,
            "elapsed_ms": self.elapsed_ms,
            "notes": self.notes,
        }


def _level_for(zone: str) -> str:
    if zone == ".":
        return "root"
    return "tld" if zone.count(".") <= 1 else "authoritative"


async def resolve_chain(name: str, record_type: str = "A") -> DnsChain:
    """Walk the delegation from the root down to the authoritative answer.

    Iterative resolution done by hand: ask a root server, follow the referral it
    gives, ask that server, and so on. This is exactly what a recursive resolver does
    once and then hides — seeing it is the whole point.

    Never raises. A network that blocks UDP/53 degrades to a DoH lookup with a note
    saying the chain couldn't be walked, because a resolver that answers over HTTPS
    is recursive and has no delegation to show.
    """
    started = time.perf_counter()
    name = (name or "").strip().rstrip(".")
    if not name:
        return DnsChain(name, record_type, [], [], False, 0, ["no name given"])

    try:
        import dns.asyncresolver  # noqa: F401 — presence check
        import dns.message
        import dns.name
        import dns.rdatatype
        import dns.resolver
    except ImportError:
        return DnsChain(
            name, record_type, [], [], False, 0, ["dnspython is not installed"]
        )

    hops: list[DnsHop] = []
    notes: list[str] = []
    signed_so_far = True

    try:
        target = dns.name.from_text(name)
        rdtype = dns.rdatatype.from_text(record_type)
    except Exception as exc:  # noqa: BLE001
        return DnsChain(name, record_type, [], [], False, 0, [f"bad name: {exc}"])

    # Seed from the system's configured root hints via a normal resolver — asking
    # for the NS set of "." is itself a legitimate first question.
    servers = await _root_servers()
    if not servers:
        notes.append("couldn't reach a root server over UDP/53")
        addresses = await _doh_fallback(name, record_type, notes)
        return DnsChain(
            name,
            record_type,
            hops,
            addresses,
            False,
            int((time.perf_counter() - started) * 1000),
            notes,
        )

    zone = "."
    deadline = time.monotonic() + DNS_TOTAL_TIMEOUT_S

    for _depth in range(12):  # a delegation deeper than this is pathological
        if time.monotonic() > deadline:
            notes.append("delegation walk timed out")
            break

        hop = await _ask_one(servers, target, rdtype, zone)
        hop.signed = signed_so_far and hop.signed
        signed_so_far = hop.signed
        hops.append(hop)

        if hop.error and not hop.referral and not hop.answers and not hop.cname:
            notes.append(hop.error)
            break
        if hop.answers:
            hop.level = "answer"
            break
        if hop.cname:
            # An alias, not an address. Restart the walk at the target name: it
            # usually lives in a different zone (a CDN's), so the delegation from
            # here is genuinely a different chain — which is the interesting part.
            hop.level = "answer"
            notes.append(f"{hop.query} is an alias for {hop.cname}")
            if len(hops) < 8:
                target = dns.name.from_text(hop.cname)
                servers = await _root_servers()
                zone = "."
                continue
            break
        if not hop.referral:
            notes.append(f"{hop.server} gave neither an answer nor a referral")
            break

        # Follow the referral. Prefer glue: without it we'd have to resolve the
        # nameserver's own name, which for an in-zone nameserver is circular.
        next_servers = [ip for ips in hop.glue.values() for ip in ips]
        if not next_servers:
            next_servers = await _resolve_ns_names(hop.referral)
            if next_servers:
                notes.append(
                    f"no glue for {hop.zone or 'the referral'} — resolved the "
                    "nameserver names separately"
                )
        if not next_servers:
            notes.append("referral had no usable nameserver addresses")
            break
        servers = next_servers[:4]
        zone = hop.referral_zone or zone

    addresses = [a for hop in hops for a in hop.answers]
    if not addresses and not notes:
        notes.append("no address returned")

    return DnsChain(
        name=name,
        record_type=record_type,
        hops=hops,
        addresses=addresses,
        dnssec=signed_so_far and bool(addresses),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        notes=notes,
    )


async def _root_servers() -> list[str]:
    """Addresses of a few root servers, from the system resolver."""
    import dns.asyncresolver

    try:
        answer = await dns.asyncresolver.resolve(".", "NS", lifetime=DNS_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return []
    names = [str(r.target).rstrip(".") for r in answer][:3]
    return await _resolve_ns_names(names)


async def _resolve_ns_names(names: list[str]) -> list[str]:
    import dns.asyncresolver

    out: list[str] = []
    for ns in names[:3]:
        try:
            answer = await dns.asyncresolver.resolve(ns, "A", lifetime=DNS_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            continue
        out.extend(str(r.address) for r in answer)
        if out:
            break
    return out


async def _ask_one(servers: list[str], target: Any, rdtype: Any, zone: str) -> DnsHop:
    """Ask one level of the hierarchy, trying each server until one answers."""
    import dns.asyncquery
    import dns.message
    import dns.rdatatype

    hop = DnsHop(
        level=_level_for(zone), zone=zone, server="", server_name="", query=str(target)
    )
    query = dns.message.make_query(target, rdtype, want_dnssec=True)
    for server in servers:
        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                dns.asyncquery.udp(query, server, timeout=DNS_TIMEOUT_S),
                timeout=DNS_TIMEOUT_S + 1,
            )
        except Exception as exc:  # noqa: BLE001 — try the next server
            hop.error = f"{server}: {exc}"
            continue

        hop.server = server
        hop.rtt_ms = round((time.perf_counter() - started) * 1000, 1)
        hop.error = None

        for rrset in response.answer:
            if rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                hop.answers.extend(str(r) for r in rrset)
            elif rrset.rdtype == dns.rdatatype.CNAME:
                hop.cname = str(rrset[0].target).rstrip(".")

        for rrset in response.authority:
            if rrset.rdtype == dns.rdatatype.NS:
                hop.referral.extend(str(r.target).rstrip(".") for r in rrset)
                hop.referral_zone = str(rrset.name).rstrip(".") or "."
            elif rrset.rdtype == dns.rdatatype.DS:
                # A DS record in the parent is the link in the chain of trust: it
                # says "the child zone's key is signed, and here's its digest".
                hop.signed = True

        for rrset in response.additional:
            if rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
                owner = str(rrset.name).rstrip(".")
                hop.glue.setdefault(owner, []).extend(str(r) for r in rrset)

        hop.server_name = hop.glue and next(iter(hop.glue), "") or ""
        break

    return hop


async def _doh_fallback(name: str, record_type: str, notes: list[str]) -> list[str]:
    """Answer the question over DNS-over-HTTPS when UDP/53 is unavailable.

    This gets an address but *cannot* show a delegation chain: a DoH endpoint is a
    recursive resolver that did the walk for us and reports only the result.
    """
    import httpx

    for label, endpoint in _DOH_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=DNS_TIMEOUT_S) as client:
                res = await client.get(
                    endpoint,
                    params={"name": name, "type": record_type},
                    headers={"Accept": "application/dns-json"},
                )
                data = res.json()
        except Exception:  # noqa: BLE001
            continue
        answers = [
            str(a.get("data")) for a in (data.get("Answer") or []) if a.get("data")
        ]
        if answers:
            notes.append(
                f"answered over DNS-over-HTTPS ({label}) — a recursive resolver, so "
                "the delegation chain can't be shown"
            )
            return answers
    notes.append("DNS-over-HTTPS fallback also failed")
    return []


# --- traceroute -------------------------------------------------------------


@dataclass
class TraceHop:
    ttl: int
    host: str = ""  # reverse-resolved name, when the tool gives one
    ip: str = ""
    rtt_ms: list[float] = field(default_factory=list)
    timeout: bool = False


# `tracert` (Windows) and `traceroute` (Unix) print different shapes, but every hop
# line starts with the TTL and carries some mix of times and addresses. Rather than
# two grammars, pull the pieces out and assemble — far less brittle than matching a
# whole line, which is what breaks across locales and versions.
_HOP_LINE_RE = re.compile(r"^\s*(\d{1,2})\s+(.*)$")
_MS_RE = re.compile(r"(?:<\s*)?(\d+(?:[.,]\d+)?)\s*ms", re.IGNORECASE)
_HOSTNAME_RE = re.compile(
    r"\b([a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9-]+)+)\b"
)


def _extract_ip(rest: str) -> str:
    """The address in a traceroute hop line, IPv4 or IPv6.

    Scans whitespace-delimited tokens and asks `ipaddress` rather than matching one
    large regex. Bare IPv6 addresses are the reason: they appear unbracketed in
    `tracert` output (a dual-stack host traces over v6 by default, so this is the
    *common* case, not an edge one), and a v6 pattern loose enough to catch them is
    also loose enough to match fragments of the timing columns.
    """
    for token in rest.replace("[", " ").replace("]", " ").split():
        candidate = token.strip(",()")
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return candidate
    return ""


def parse_traceroute(output: str) -> list[TraceHop]:
    """Parse `tracert`/`traceroute` output into hops. Pure — testable per platform.

    A hop that only produced `*` is marked `timeout` rather than dropped: a router
    that doesn't answer ICMP is still a hop the packets went through, and silently
    omitting it would misrepresent the path length.
    """
    hops: list[TraceHop] = []
    for line in output.splitlines():
        match = _HOP_LINE_RE.match(line)
        if not match:
            continue
        try:
            ttl = int(match.group(1))
        except ValueError:
            continue
        if not 1 <= ttl <= MAX_HOPS:
            continue

        rest = match.group(2)
        hop = TraceHop(ttl=ttl)
        hop.rtt_ms = [
            float(m.group(1).replace(",", ".")) for m in _MS_RE.finditer(rest)
        ]

        hop.ip = _extract_ip(rest)

        # Take a hostname only if it isn't just the IP written out.
        for name_match in _HOSTNAME_RE.finditer(rest):
            candidate = name_match.group(1)
            if candidate != hop.ip and not candidate.replace(".", "").isdigit():
                hop.host = candidate
                break

        hop.timeout = not hop.rtt_ms and not hop.ip
        if hop.ip or hop.rtt_ms or "*" in rest:
            hops.append(hop)
    return hops


def _traceroute_command(host: str) -> list[str] | None:
    """The platform's traceroute invocation, or None if the tool isn't installed."""
    if platform.system() == "Windows":
        exe = shutil.which("tracert")
        # `-d` skips reverse DNS, which otherwise dominates the runtime.
        return [exe, "-d", "-h", str(MAX_HOPS), "-w", "1000", host] if exe else None
    exe = shutil.which("traceroute")
    if not exe:
        return None
    return [exe, "-n", "-m", str(MAX_HOPS), "-w", "1", host]


async def traceroute(host: str) -> dict[str, Any]:
    """Hop-by-hop path to `host`, via the system traceroute binary.

    Shelling out rather than crafting packets is deliberate: raw ICMP sockets need
    administrator rights on Windows and root on Unix, and a feature that only works
    for elevated users is a feature most people never see.
    """
    started = time.perf_counter()
    command = _traceroute_command(host)
    if command is None:
        tool = "tracert" if platform.system() == "Windows" else "traceroute"
        return {
            "host": host,
            "hops": [],
            "error": (
                f"{tool} is not installed or not on PATH. This probe uses the system "
                "tool because raw ICMP sockets need administrator rights."
            ),
        }

    # Blocking `subprocess.run` on a thread, not `asyncio.create_subprocess_exec`.
    # On Windows, `uvicorn --reload` installs a SelectorEventLoop, which cannot spawn
    # subprocesses at all — asyncio raises `NotImplementedError` with an empty
    # message, so the failure reads as "traceroute failed: " and explains nothing.
    # The LSP and PTY managers hit this first and solved it the same way.
    try:
        completed = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=TRACEROUTE_TIMEOUT_S,
                check=False,
            ),
            timeout=TRACEROUTE_TIMEOUT_S + 5,
        )
    except (asyncio.TimeoutError, subprocess.TimeoutExpired):
        return {"host": host, "hops": [], "error": "traceroute timed out"}
    except Exception as exc:  # noqa: BLE001
        # `str(exc)` alone is not enough: several of the exceptions this can raise
        # (NotImplementedError, PermissionError) carry an empty message, which
        # renders as "traceroute failed: " and explains nothing. An exception object
        # is always truthy, so `exc or ...` doesn't fall back either.
        logger.warning("traceroute to %s failed", host, exc_info=True)
        detail = str(exc) or type(exc).__name__
        return {"host": host, "hops": [], "error": f"traceroute failed: {detail}"}

    output = (completed.stdout or b"").decode("utf-8", "replace")
    hops = parse_traceroute(output)
    return {
        "host": host,
        "hops": [asdict(h) for h in hops],
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "error": None if hops else "no hops parsed from the traceroute output",
    }


# --- geolocation ------------------------------------------------------------
#
# DB-IP Lite rather than MaxMind GeoLite2: it is CC-BY 4.0 and freely
# redistributable, where GeoLite2 requires an account and a license key just to
# download. Attribution is required and is rendered in the UI.

_GEO_READER: Any = None
_GEO_TRIED = False


def geoip_db_path() -> Any:

    return paths.data_dir() / "geoip" / "dbip-city.mmdb"


def _reader() -> Any:
    """The GeoIP reader, or None. Lazy and cached — opening the database is slow and
    the extra is optional."""
    global _GEO_READER, _GEO_TRIED
    if _GEO_TRIED:
        return _GEO_READER
    _GEO_TRIED = True
    path = geoip_db_path()
    if not path.is_file():
        return None
    try:
        import geoip2.database

        _GEO_READER = geoip2.database.Reader(str(path))
    except Exception as exc:  # noqa: BLE001
        logger.info("GeoIP database unavailable: %s", exc)
    return _GEO_READER


def locate(ip: str) -> dict[str, Any] | None:
    """City-level location for a public IP, or None.

    Private addresses return None rather than a guess — the first hops of any
    traceroute are the user's own router, and pretending to know where a 192.168.x.x
    address is would be inventing data.
    """
    try:
        if ipaddress.ip_address(ip).is_private:
            return None
    except ValueError:
        return None

    reader = _reader()
    if reader is None:
        return None
    try:
        record = reader.city(ip)
    except Exception:  # noqa: BLE001 — an unlisted IP is the common case
        return None
    if record.location.latitude is None:
        return None
    return {
        "ip": ip,
        "lat": record.location.latitude,
        "lon": record.location.longitude,
        "city": record.city.name,
        "country": record.country.iso_code,
    }


def geo_status() -> dict[str, Any]:
    return {
        "available": _reader() is not None,
        "path": str(geoip_db_path()),
        "attribution": "IP geolocation by DB-IP (CC BY 4.0)",
        "hint": (
            "Install the optional extra (uv sync --extra geoip) and download the "
            "DB-IP Lite City database (free, CC-BY) to this path to plot routes on "
            "a map."
        ),
    }
