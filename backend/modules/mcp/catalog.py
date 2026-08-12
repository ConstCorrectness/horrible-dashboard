"""Finding servers: the official MCP registry, plus a curated overlay we ship.

The same shape the search module uses for engines — a live third-party source merged
with something of our own — and for the same reason: the registry is comprehensive and
unopinionated (thousands of entries, many of them one-person experiments published
once), while the overlay is short, checked, and describes *why you would want this*.
Neither alone is a good browse experience.

## The registry's rows are versions, not servers

`GET /v0/servers` returns one row per published **version**, so an unfiltered browse is
mostly the same handful of servers repeating. `version=latest` is passed on every call;
without it the first page of results is routinely three copies of one server.

## Turning an entry into something runnable

A registry entry is a *description* of how to obtain a server, not a command line. It
carries `packages` (an npm/pypi/oci identifier plus argument and environment
declarations) and `remotes` (a URL). `install_options` converts both into candidate
configs this node could actually launch — which is where the sharp edges are:

- **`runtimeHint` is frequently absent**, so the runtime is inferred from
  `registryType` (npm → `npx`, pypi → `uvx`, oci → `docker`). An entry we can't map to
  a runtime is returned with `command: ""` and a reason, rather than a command line
  that would fail confusingly at spawn time.
- **`npx` without `-y` prompts**, and its stdin *is* the protocol pipe — so the prompt
  never gets an answer and the server hangs until the 90s connect timeout. `-y` is
  added when the entry didn't already declare it.
- **Secret environment variables never enter the config file.** An entry that declares
  `isSecret` yields a `secret_env` name list; the values go to the encrypted store, the
  same split `config.py` enforces for bearer tokens.

## Entries are untrusted text

A name, title and description come from whoever published the entry. They are shown to
a person, and — if the server is installed — its *own* instructions reach the model
under the fencing `bridge.guide_for` already applies. Nothing here is fed to a model,
and the fields are length-capped so a hostile entry can't wreck the pane.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_TIMEOUT_S = 15.0
_CACHE_TTL_S = 300.0

_CURATED_FILE = Path(__file__).parent / "curated.json"

# Field caps. Registry text is third-party and displayed; nothing here should be able
# to push a pane around or fill memory.
_MAX_NAME = 200
_MAX_TEXT = 600

# `registryType` → the runner that fetches and executes that kind of package.
_RUNTIME_FOR = {"npm": "npx", "pypi": "uvx", "oci": "docker", "nuget": "dnx"}

# Registry transport names → our own. `streamable-http` is the current spelling of what
# this codebase calls `http`.
_TRANSPORT_FOR = {
    "streamable-http": "http",
    "streamable_http": "http",
    "http": "http",
    "sse": "sse",
    "stdio": "stdio",
}

_ID_SAFE = re.compile(r"[^a-z0-9_-]+")


@dataclass
class EnvVar:
    """One environment variable an entry says its server needs."""

    name: str
    description: str = ""
    required: bool = False
    secret: bool = False
    default: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "secret": self.secret,
            "default": self.default,
        }


@dataclass
class InstallOption:
    """One concrete way to run an entry, as a candidate server config."""

    kind: str  # "package" or "remote"
    label: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    env: list[EnvVar] = field(default_factory=list)
    # Why this option can't be used as-is, when it can't. Empty when it can.
    unsupported: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "transport": self.transport,
            "command": self.command,
            "args": self.args,
            "url": self.url,
            "env": [e.public() for e in self.env],
            "unsupported": self.unsupported,
        }


@dataclass
class CatalogEntry:
    """One discoverable server, from either source."""

    name: str
    title: str
    description: str
    version: str = ""
    repository: str = ""
    source: str = "registry"  # or "curated"
    note: str = ""  # curated-only: why this one is worth having
    installs: list[InstallOption] = field(default_factory=list)

    @property
    def suggested_id(self) -> str:
        return suggest_id(self.name)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "repository": self.repository,
            "source": self.source,
            "note": self.note,
            "suggestedId": self.suggested_id,
            "installs": [i.public() for i in self.installs],
        }


def suggest_id(name: str) -> str:
    """A server id proposed from a registry name like `io.github.owner/thing`.

    The last path segment, lowercased and reduced to the charset `config.validate_id`
    allows. Reverse-DNS prefixes are dropped on purpose: `io_github_owner_thing` is a
    tool-name prefix the model reads on every turn the group is loaded, and the owner
    is not the useful half.
    """
    tail = (name or "").rsplit("/", 1)[-1].lower()
    cleaned = _ID_SAFE.sub("-", tail).strip("-")[:39]
    return cleaned or "server"


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def parse_entry(raw: dict[str, Any]) -> CatalogEntry | None:
    """One registry row → a `CatalogEntry`. Pure; None if it isn't usable."""
    server = raw.get("server") if isinstance(raw.get("server"), dict) else raw
    if not isinstance(server, dict):
        return None
    name = _text(server.get("name"), _MAX_NAME)
    if not name:
        return None
    repository = ""
    if isinstance(server.get("repository"), dict):
        repository = _text(server["repository"].get("url"), _MAX_NAME)
    return CatalogEntry(
        name=name,
        title=_text(server.get("title")) or name,
        description=_text(server.get("description")),
        version=_text(server.get("version"), 40),
        repository=repository,
        source="registry",
        installs=install_options(server),
    )


def install_options(server: dict[str, Any]) -> list[InstallOption]:
    """Every way this node could run the described server. Pure.

    Remotes come first: a hosted server needs nothing installed and executes none of
    the publisher's code on this machine, so it is the option to offer by default when
    an entry has both.
    """
    out: list[InstallOption] = []
    for remote in server.get("remotes") or []:
        if option := _remote_option(remote):
            out.append(option)
    for package in server.get("packages") or []:
        if option := _package_option(package):
            out.append(option)
    return out


def _remote_option(remote: Any) -> InstallOption | None:
    if not isinstance(remote, dict):
        return None
    url = _text(remote.get("url"), _MAX_NAME)
    if not url.startswith(("http://", "https://")):
        return None
    transport = _TRANSPORT_FOR.get(str(remote.get("type") or "").lower(), "http")
    return InstallOption(
        kind="remote",
        label=f"Hosted ({transport})",
        transport="http" if transport == "stdio" else transport,
        url=url,
    )


def _package_option(package: Any) -> InstallOption | None:
    if not isinstance(package, dict):
        return None
    registry_type = str(package.get("registryType") or "").lower()
    identifier = _text(package.get("identifier"), _MAX_NAME)
    if not identifier:
        return None
    version = _text(package.get("version"), 40)
    runtime = _text(package.get("runtimeHint"), 40) or _RUNTIME_FOR.get(
        registry_type, ""
    )
    env = [
        e for e in (_env_var(v) for v in package.get("environmentVariables") or []) if e
    ]
    label = f"{registry_type or 'package'}: {identifier}"

    if not runtime:
        return InstallOption(
            kind="package",
            label=label,
            env=env,
            unsupported=f"no runtime known for registry type {registry_type or '?'}",
        )

    runtime_args = _arguments(package.get("runtimeArguments"))
    package_args = _arguments(package.get("packageArguments"))

    if registry_type == "oci":
        spec = f"{identifier}:{version}" if version else identifier
        args = ["run", "-i", "--rm", *runtime_args, spec, *package_args]
    else:
        spec = f"{identifier}@{version}" if version else identifier
        # `npx` prompts before installing a package it doesn't have, and its stdin is
        # the protocol pipe — the prompt is never answered and the connect times out
        # 90 seconds later with nothing to show. `-y` is the difference between "this
        # server works" and "this server hangs".
        if runtime in ("npx", "bunx") and not ({"-y", "--yes"} & set(runtime_args)):
            runtime_args = ["-y", *runtime_args]
        args = [*runtime_args, spec, *package_args]

    transport = _TRANSPORT_FOR.get(
        str((package.get("transport") or {}).get("type") or "stdio").lower(), "stdio"
    )
    option = InstallOption(
        kind="package",
        label=label,
        transport=transport,
        command=runtime,
        args=args,
        env=env,
    )
    if transport != "stdio":
        # A package that serves http describes a server you must start yourself and
        # then point at; we have no URL for it, so it can't be installed from here.
        option.unsupported = f"package declares {transport} transport but no URL"
    return option


def _arguments(raw: Any) -> list[str]:
    """Registry argument declarations → an argv fragment.

    Only arguments with a concrete value are emitted. A declaration that merely says
    "this server takes a `--root` you must supply" has no value to pass, and inventing
    one would produce a command line that fails in a way the user can't read.
    """
    out: list[str] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        value = (
            item.get("value") if item.get("value") is not None else item.get("default")
        )
        if value is None or str(value).strip() == "":
            continue
        if str(item.get("type") or "").lower() == "named":
            if name := _text(item.get("name"), 80):
                out.extend([name, str(value)])
        else:
            out.append(str(value))
    return out


def _env_var(raw: Any) -> EnvVar | None:
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), 80)
    if not name:
        return None
    return EnvVar(
        name=name,
        description=_text(raw.get("description")),
        required=bool(raw.get("isRequired")),
        secret=bool(raw.get("isSecret")),
        default=_text(raw.get("default"), 200),
    )


# --- the curated overlay ------------------------------------------------------


def curated_entries() -> list[CatalogEntry]:
    """The shipped shortlist. Never raises — a bad file costs the overlay, not the
    registry results."""
    try:
        rows = json.loads(_CURATED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("couldn't read the curated MCP catalog at %s", _CURATED_FILE)
        return []
    out: list[CatalogEntry] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        entry = CatalogEntry(
            name=_text(row.get("name"), _MAX_NAME),
            title=_text(row.get("title")) or _text(row.get("name"), _MAX_NAME),
            description=_text(row.get("description")),
            repository=_text(row.get("repository"), _MAX_NAME),
            source="curated",
            note=_text(row.get("note")),
            installs=install_options(row),
        )
        if entry.name:
            out.append(entry)
    return out


def matches(entry: CatalogEntry, query: str) -> bool:
    """Substring match over the fields a person would search. Pure."""
    needle = (query or "").strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        [entry.name, entry.title, entry.description, entry.note]
    ).lower()
    return needle in haystack


def merge(
    curated: list[CatalogEntry], registry: list[CatalogEntry]
) -> list[CatalogEntry]:
    """Curated first, then registry entries the overlay doesn't already cover.

    Deduped by name, and curated wins: the overlay exists precisely to say something
    better about an entry than its own description does, so letting the registry row
    displace it would delete the reason the overlay exists.
    """
    seen = {e.name.lower() for e in curated}
    return [*curated, *(e for e in registry if e.name.lower() not in seen)]


# --- the live half ------------------------------------------------------------

_cache: dict[str, tuple[float, list[CatalogEntry]]] = {}


async def search_registry(query: str, *, limit: int = 30) -> list[CatalogEntry]:
    """Query the official registry. Returns [] on any failure — the overlay still works.

    Plain `httpx` to a hardcoded host: a vendor API chosen by this codebase, the same
    egress leg as the search module's providers. Nothing attacker-supplied decides the
    URL.
    """
    key = f"{query}:{limit}"
    now = time.monotonic()
    if (hit := _cache.get(key)) and now - hit[0] < _CACHE_TTL_S:
        return hit[1]

    import httpx

    # `version=latest` collapses the registry's per-version rows; without it a browse
    # is mostly duplicates.
    params: dict[str, Any] = {"limit": max(1, min(limit, 100)), "version": "latest"}
    if query.strip():
        params["search"] = query.strip()

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(REGISTRY_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — a registry outage is not a pane error
        logger.warning("mcp: registry search failed: %s", exc)
        return []

    rows = payload.get("servers") if isinstance(payload, dict) else None
    entries = [e for e in (parse_entry(r) for r in rows or []) if e is not None]
    _cache[key] = (now, entries)
    return entries


async def discover(query: str, *, limit: int = 30) -> list[CatalogEntry]:
    """The browse list: the curated overlay filtered by `query`, then the registry."""
    curated = [e for e in curated_entries() if matches(e, query)]
    return merge(curated, await search_registry(query, limit=limit))
