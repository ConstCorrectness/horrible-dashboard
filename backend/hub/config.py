"""Hub configuration, read once from the environment at startup.

Every knob is an environment variable so the same image runs on a VPS (compose
file), Fly, or a developer's machine without a config file to mount. See
docs/architecture/hosted-hub.mdx for the full table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _csv(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _games_http_base() -> str:
    url = os.environ.get("GAMES_SERVER_URL") or "wss://horrible-games.fly.dev"
    return url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def _default_web_dist() -> Path:
    return Path(__file__).resolve().parents[2] / "apps" / "web" / "dist"


@dataclass(frozen=True)
class HubConfig:
    #: The game server whose accounts are the hub's accounts (HTTP base).
    games_http_base: str = field(default_factory=_games_http_base)
    #: The same server as instances should address it (`GAMES_SERVER_URL` passed
    #: through), so the JWT the hub hands an instance is valid where it plays.
    games_server_url: str = field(
        default_factory=lambda: os.environ.get("GAMES_SERVER_URL", "")
    )
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("HUB_DB_PATH", "hub-data/hub.db"))
    )
    #: Built frontend served at `/`. Absent → the hub serves only `/api`, `/hub`, `/ws`
    #: (the frontend is hosted elsewhere, e.g. Vercel).
    web_dist: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HUB_WEB_DIST") or _default_web_dist()
        )
    )
    #: `docker` (one container per user), `fly` (one Fly Machine per user) or
    #: `static` (dev only: every user shares one already-running backend — no
    #: isolation).
    spawner: str = field(
        default_factory=lambda: os.environ.get("HUB_SPAWNER", "docker")
    )
    static_upstream: str = field(
        default_factory=lambda: os.environ.get(
            "HUB_STATIC_UPSTREAM", "http://127.0.0.1:8000"
        )
    )
    static_token: str = field(
        default_factory=lambda: os.environ.get("HUB_STATIC_TOKEN", "")
    )
    instance_image: str = field(
        default_factory=lambda: os.environ.get(
            "HUB_INSTANCE_IMAGE", "horrible-dashboard-node:latest"
        )
    )
    #: `network` — the hub is itself a container and joins each instance's private
    #: network (the production shape); `published` — each instance publishes its port
    #: on the host's loopback (a hub running natively, e.g. on Docker Desktop).
    docker_reach: str = field(
        default_factory=lambda: os.environ.get(
            "HUB_DOCKER_REACH",
            "network" if os.environ.get("HUB_SELF_CONTAINER") else "published",
        )
    )
    #: The hub's own container id/name, for `network` reach.
    self_container: str = field(
        default_factory=lambda: os.environ.get("HUB_SELF_CONTAINER", "")
    )
    #: Fly spawner: the app instances run in (created with the same private network
    #: as the hub), the region, the Machines API token, and each user's volume size.
    fly_app: str = field(default_factory=lambda: os.environ.get("HUB_FLY_APP", ""))
    fly_region: str = field(
        default_factory=lambda: os.environ.get("HUB_FLY_REGION", "iad")
    )
    fly_token: str = field(default_factory=lambda: os.environ.get("HUB_FLY_TOKEN", ""))
    fly_volume_gb: int = field(
        default_factory=lambda: int(os.environ.get("HUB_FLY_VOLUME_GB", "1"))
    )
    fly_api: str = field(
        default_factory=lambda: os.environ.get(
            "HUB_FLY_API", "https://api.machines.dev/v1"
        )
    )
    #: OCI runtime for instances, e.g. `runsc` (gVisor). Empty → the daemon default.
    runtime: str = field(default_factory=lambda: os.environ.get("HUB_RUNTIME", ""))
    mem_limit: str = field(default_factory=lambda: os.environ.get("HUB_MEM", "2g"))
    cpus: float = field(
        default_factory=lambda: float(os.environ.get("HUB_CPUS", "1.0"))
    )
    pids_limit: int = field(
        default_factory=lambda: int(os.environ.get("HUB_PIDS", "512"))
    )
    idle_minutes: float = field(
        default_factory=lambda: float(os.environ.get("HUB_IDLE_MINUTES", "30"))
    )
    #: Browser origins allowed to open `/ws` (and, cross-origin, to call with a
    #: ticket). Empty → same-origin only.
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv("HUB_ALLOWED_ORIGINS")
    )
    #: Invite-only mode: account handles or ids allowed in. Empty → any account.
    allowed_accounts: tuple[str, ...] = field(
        default_factory=lambda: _csv("HUB_ALLOWED_ACCOUNTS")
    )
    #: `Secure` on the session cookie. Only turn off for plain-http localhost dev.
    cookie_secure: bool = field(
        default_factory=lambda: _flag("HUB_COOKIE_SECURE", True)
    )
    session_days: float = field(
        default_factory=lambda: float(os.environ.get("HUB_SESSION_DAYS", "30"))
    )
