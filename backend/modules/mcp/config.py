"""Server-config custody for the MCP module.

Configs persist as a list in `.data/mcp-servers.json`, mirroring the database module's
`connections.json`. A config is *not* a credential: it holds a command line or a URL,
which the UI is free to display. Anything secret — a bearer token, an API key an HTTP
server authenticates with — goes to the Fernet-encrypted secrets store under
`mcp:<id>` and is **never** returned by a route, exactly as connectors do it.

That split matters here more than usual: `GET /api/settings` hands the whole settings
bag to the browser, so an MCP auth token kept as a setting would be readable by any page
the app renders. It is also why `mcp-servers.json` deliberately has no `token` field —
there is nowhere in this file a secret is allowed to live.

**The `mcp-` id prefix.** A server's tool group is `mcp-<id>` (see `bridge.py`), so the
namespace can never collide with a built-in group or a connector's — an MCP server called
`github` becomes `mcp-github` and stays distinct from the GitHub connector's `github`
tools. Ids are validated to a conservative charset because they end up inside provider
tool names, where most providers only accept `[A-Za-z0-9_-]`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

# The prefix every MCP-contributed tool group carries. Keeping it in one place means
# the bridge, the routes, and the tests can't drift on it.
GROUP_PREFIX = "mcp-"

# Server ids become part of a provider tool name, which most providers restrict to
# letters, digits, underscore and hyphen. Enforced at write time so a bad id fails
# when it's added rather than mid-turn.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,38}$")

Transport = Literal["stdio", "http", "sse"]

# Keys a stored config may carry. Anything else submitted is dropped rather than
# persisted, so a client can't smuggle a `token` field into the plaintext file.
_ALLOWED_KEYS = {
    "id",
    "name",
    "transport",
    "command",
    "args",
    "env",
    "cwd",
    "url",
    "headers",
    "enabled",
    # The *names* of environment variables whose values live in the encrypted store.
    # Names are not secret — an entry from the MCP registry publishes them — and
    # keeping them here is what lets the UI show which credentials a server still
    # needs without ever holding one.
    "secretEnv",
    # Where this server came from: `manual` (a command the user typed), `registry`
    # (third-party code installed from the MCP registry) or `authored` (a project
    # scaffolded here). Persisted rather than derived because the distinction that
    # matters — whose code is this — is only knowable at the moment it is added.
    "origin",
    # For an authored server, the `author.py` project that owns its files.
    "project",
}

# The provenance values a config may carry. Anything else falls back to `manual`, the
# conservative reading: an unrecognized origin is not evidence the code is ours.
ORIGINS = ("manual", "registry", "authored")


def _store_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "mcp-servers.json"


def secret_key(server_id: str) -> str:
    """Where this server's bearer token lives in the encrypted store."""
    return f"mcp:{server_id}"


def group_name(server_id: str) -> str:
    """The agent tool group a server's tools are disclosed under."""
    return f"{GROUP_PREFIX}{server_id}"


def validate_id(server_id: str) -> str | None:
    """None if `server_id` is usable, else why it isn't."""
    if not _ID_RE.match(server_id or ""):
        return (
            "Server id must be 1-39 characters of lowercase letters, digits, "
            "underscore or hyphen, and start with a letter or digit."
        )
    return None


def validate(config: dict[str, Any]) -> str | None:
    """None if `config` is a usable server definition, else the reason it isn't."""
    if err := validate_id(str(config.get("id", ""))):
        return err
    transport = config.get("transport")
    if transport not in ("stdio", "http", "sse"):
        return "Transport must be one of: stdio, http, sse."
    if transport == "stdio":
        if not str(config.get("command", "")).strip():
            return "A stdio server needs a command."
        if not isinstance(config.get("args", []), list):
            return "args must be a list of strings."
    else:
        url = str(config.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return "An http/sse server needs an http(s) URL."
    return None


def _read() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def _write(rows: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _clean(config: dict[str, Any]) -> dict[str, Any]:
    """A config reduced to storable keys, with defaults filled in."""
    out = {k: v for k, v in config.items() if k in _ALLOWED_KEYS}
    out.setdefault("name", out.get("id", ""))
    out.setdefault("enabled", True)
    if out.get("origin") not in ORIGINS:
        out["origin"] = "manual"
    if out.get("transport") == "stdio":
        out.setdefault("args", [])
        out.setdefault("env", {})
    else:
        out.setdefault("headers", {})
    return out


def list_servers() -> list[dict[str, Any]]:
    """Every configured server, in insertion order."""
    return _read()


def get_server(server_id: str) -> dict[str, Any] | None:
    return next((c for c in _read() if c.get("id") == server_id), None)


def save_server(config: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace a server config (by id). Returns the stored form."""
    cleaned = _clean(config)
    rows = [c for c in _read() if c.get("id") != cleaned["id"]]
    rows.append(cleaned)
    _write(rows)
    return cleaned


def delete_server(server_id: str) -> bool:
    """Forget a server and its stored token. True if it existed."""
    rows = _read()
    remaining = [c for c in rows if c.get("id") != server_id]
    if len(remaining) == len(rows):
        return False
    _write(remaining)
    # Drop the credentials too — leaving them behind means a later server that reuses
    # the id silently inherits someone else's token.
    gone = next((c for c in rows if c.get("id") == server_id), {})
    try:
        from backend.modules.database.secrets_store import delete_secret

        delete_secret(secret_key(server_id))
        for name in gone.get("secretEnv") or []:
            delete_secret(env_secret_key(server_id, str(name)))
    except Exception:  # noqa: BLE001 - a missing/locked store must not block deletion
        pass
    return True


def env_secret_key(server_id: str, name: str) -> str:
    """Where one secret environment variable's value lives."""
    return f"mcp:{server_id}:env:{name}"


def set_env_secret(server_id: str, name: str, value: str) -> None:
    from backend.modules.database.secrets_store import upsert_secret

    upsert_secret(env_secret_key(server_id, name), value)


def env_secrets(server_id: str) -> dict[str, str]:
    """Resolved secret environment for a server, read at connect time only.

    A discovered server routinely wants an API key in its environment, and `env` lives
    in plaintext `mcp-servers.json` — so a key put there would sit on disk in the clear,
    which is exactly what this module's docstring forbids. The names stay in the config
    and the values come from here, the same split the bearer token already uses.
    """
    from backend.modules.database.secrets_store import get_secret_or_none

    conf = get_server(server_id) or {}
    out: dict[str, str] = {}
    for name in conf.get("secretEnv") or []:
        try:
            if value := get_secret_or_none(env_secret_key(server_id, str(name))):
                out[str(name)] = value
        except Exception:  # noqa: BLE001 — a locked store means one missing variable
            continue
    return out


def missing_env_secrets(server_id: str) -> list[str]:
    """Declared secret variables with no stored value — the "still needs a key" list."""
    conf = get_server(server_id) or {}
    have = env_secrets(server_id)
    return [str(n) for n in conf.get("secretEnv") or [] if str(n) not in have]


def auth_token(server_id: str) -> str | None:
    """The stored bearer token for an http/sse server, if one was saved."""
    from backend.modules.database.secrets_store import get_secret_or_none

    return get_secret_or_none(secret_key(server_id)) or None


def set_auth_token(server_id: str, token: str) -> None:
    from backend.modules.database.secrets_store import upsert_secret

    upsert_secret(secret_key(server_id), token)


def has_auth_token(server_id: str) -> bool:
    """Whether a token exists — never the token itself. Safe to send to the browser."""
    try:
        return bool(auth_token(server_id))
    except Exception:  # noqa: BLE001 - an unreadable store still means "something is set"
        return True
