"""Shared MongoDB Atlas access.

Lives at the backend root rather than inside a module because more than one module
needs it — the social layer's directory and, later, HorribleAssault's accounts and
match records. A module importing another module's internals is exactly what the
registry conventions forbid, so the cluster handle belongs to neither of them.

Credentials come from the environment (`ATLAS_DB_USER` / `ATLAS_DB_PASS`), never
from settings: `GET /api/settings` returns the whole bag to the browser, so a
password held there would be handed to any page that asked.

Atlas is treated as **optional infrastructure**. Every caller must work with the
cluster absent or unreachable — the friends roster is local and authoritative, and
the directory only ever answers "what address is this person at right now". Losing
Atlas must degrade discovery, never break the app.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal
from urllib.parse import quote_plus, urlsplit

logger = logging.getLogger(__name__)

# Cached client. Built lazily so importing this module never touches the network.
_client: Any = None
_client_key: str | None = None


def database_name() -> str:
    return os.environ.get("ATLAS_DB_NAME", "horrible")


def cluster_uri() -> str | None:
    """The `mongodb+srv://` URI for the cluster, or None if not configured.

    A full `ATLAS_DB_URI` wins when present. Otherwise the URI is assembled from
    the user, password, and cluster host.

    Both credentials are percent-encoded: Atlas passwords routinely contain `@`,
    `:`, `/` and `#`, every one of which is structural in a URI. Skipping this
    produces an "invalid URI" or, worse, a silent connection to the wrong host.
    """
    explicit = os.environ.get("ATLAS_DB_URI", "").strip()
    if explicit:
        return explicit
    user = os.environ.get("ATLAS_DB_USER", "").strip()
    password = os.environ.get("ATLAS_DB_PASS", "").strip()
    host = os.environ.get("ATLAS_CLUSTER_HOST", "").strip()
    if not (user and password and host):
        return None
    return (
        f"mongodb+srv://{quote_plus(user)}:{quote_plus(password)}@{host}"
        "/?retryWrites=true&w=majority"
    )


def is_configured() -> bool:
    """Whether enough is set to attempt a connection.

    Note `ATLAS_CLUSTER_HOST` is required even though user and password alone look
    sufficient: an Atlas SRV hostname carries a project-specific subdomain
    (`horrible-cluster.xxxxx.mongodb.net`) that cannot be derived from the cluster
    name, so there is nothing sensible to guess.
    """
    return cluster_uri() is not None


def client() -> Any:
    """The process-wide async client, or None when Atlas isn't configured."""
    global _client, _client_key
    uri = cluster_uri()
    if uri is None:
        return None
    if _client is not None and _client_key == uri:
        return _client
    from pymongo import AsyncMongoClient

    _client = AsyncMongoClient(
        uri,
        # Fail fast rather than hanging a request for the default 30s: every
        # caller has a working local fallback, so waiting buys nothing.
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        appname="horrible-dashboard",
    )
    _client_key = uri
    return _client


def collection(name: str) -> Any:
    """One collection in the app database, or None when Atlas isn't configured."""
    handle = client()
    return None if handle is None else handle[database_name()][name]


async def ping() -> tuple[bool, str]:
    """Check the cluster is reachable. Returns (ok, detail) and never raises."""
    handle = client()
    if handle is None:
        return False, (
            "Atlas is not configured — set ATLAS_CLUSTER_HOST (and "
            "ATLAS_DB_USER / ATLAS_DB_PASS) in .env"
        )
    try:
        await handle.admin.command("ping")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"connected to {database_name()}"


# ---------------------------------------------------------------------------
# Admin access (the database console's `atlas` connection)
# ---------------------------------------------------------------------------

AdminAccess = Literal["off", "ro", "rw"]

# Values of ATLAS_ADMIN that open the cluster to the database console, and at what
# level. Anything else — including unset — is "off".
_ADMIN_RO = {"1", "true", "yes", "on", "ro", "read", "readonly", "read-only"}
_ADMIN_RW = {"rw", "write", "readwrite", "read-write", "admin"}


def admin_access() -> AdminAccess:
    """Whether this node may run **arbitrary** queries against the shared cluster.

    Separate from `is_configured()` on purpose, and default-off. Every node that
    joins the social fabric has `ATLAS_DB_USER` set so it can publish its presence
    record, so "the credentials are present" is emphatically *not* the same question
    as "this operator administers the cluster" — gating on the former would hand
    every user's console (and agent) a free hand over shared infrastructure. Signed
    directory records mean a stranger can't forge presence, but nothing stops them
    reading the whole collection or dropping it, so the console needs its own gate.

    Env-only, like the credentials themselves: `GET /api/settings` returns the whole
    settings bag to the browser, so a setting here would be a switch any page could
    read (and a value the frontend could be tricked into flipping).

    `ATLAS_ADMIN=1` grants read-only access; `ATLAS_ADMIN=rw` also allows writes.
    The split is not tidiness — the console's `database.execute` is an agent tool, so
    "rw" is the difference between an agent that can inspect the cluster and one that
    can drop a collection every other node depends on.

    Note this is a gate against *ambient* credential use, not a boundary against the
    human at the keyboard: anyone who knows the cluster password can add an ordinary
    `mongodb` connection by hand. It stops a node from escalating the credential it
    holds for one narrow purpose into a cluster-wide console.
    """
    raw = os.environ.get("ATLAS_ADMIN", "").strip().lower()
    if not raw or admin_uri() is None:
        return "off"
    if raw in _ADMIN_RW:
        return "rw"
    return "ro" if raw in _ADMIN_RO else "off"


def admin_uri() -> str | None:
    """The URI the console's `atlas` connection dials.

    `ATLAS_ADMIN_URI` wins when set, so an operator can point the console at a
    higher-privileged user than the app itself runs as (the app only needs read/write
    on one collection; listing databases needs more). Otherwise the app's own URI is
    reused.
    """
    explicit = os.environ.get("ATLAS_ADMIN_URI", "").strip()
    return explicit or cluster_uri()


def cluster_label() -> str:
    """The cluster host, safe to show in the UI — never the URI, which holds the
    password. Falls back to a generic label if the URI can't be parsed."""
    uri = admin_uri()
    if not uri:
        return "not configured"
    try:
        host = urlsplit(uri).hostname
    except ValueError:
        host = None
    return host or "mongodb cluster"


async def close() -> None:
    global _client, _client_key
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
    _client = None
    _client_key = None
