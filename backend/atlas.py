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
from typing import Any
from urllib.parse import quote_plus

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


async def close() -> None:
    global _client, _client_key
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
    _client = None
    _client_key = None
