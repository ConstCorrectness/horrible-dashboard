"""One Docker container per user — the VPS / docker-compose backend.

Per user:

- container ``hd-user-<key>`` from ``HUB_INSTANCE_IMAGE`` (deploy/dashboard-node),
- named volume ``hd-data-<key>`` at ``/data`` (settings, secrets, files, ``$HOME``),
- its **own** bridge network ``hd-net-<key>``, so instances cannot reach each other.
  The hub joins each one (``network`` reach). A shared network with
  ``enable_icc=false`` would also cut the hub off, which is why it is per user.
- cpu / memory / pid limits, ``no-new-privileges``, every capability dropped, and
  optionally a sandboxing runtime (``HUB_RUNTIME=runsc`` for gVisor).

When the hub itself runs natively (Docker Desktop during development) it cannot
join a container network, so ``published`` reach binds the instance's port to the
host loopback on a random port instead.

The Docker SDK is synchronous; every call runs on a worker thread. The container
is *stopped*, never removed, when idle, so the next sign-in only has to start it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from typing import Any

from backend.hub.config import HubConfig
from backend.hub.spawner.base import Instance
from backend.hub.store import HubStore, User

logger = logging.getLogger(__name__)

INSTANCE_PORT = 8000
LABEL = "dev.horrible.hub"


def user_key(user_id: str) -> str:
    """A Docker-safe name fragment for an account id, stable and collision-free:
    readable prefix plus a short hash of the exact id."""
    slug = re.sub(r"[^a-z0-9]+", "-", user_id.lower()).strip("-")[:24] or "u"
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:10]
    return f"{slug}-{digest}"


class DockerSpawner:
    def __init__(
        self,
        config: HubConfig,
        store: HubStore,
        *,
        client: Any = None,
        not_found: type[BaseException] | None = None,
    ) -> None:
        if client is None or not_found is None:
            import docker  # the `hub` extra

            client = client or docker.from_env()
            not_found = not_found or docker.errors.NotFound
        self._config = config
        self._store = store
        self._client = client
        self._not_found = not_found
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- public --------------------------------------------------------------

    async def ensure(self, user: User) -> Instance:
        lock = self._locks.setdefault(user.id, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(self._ensure_sync, user)

    async def stop(self, user_id: str) -> None:
        name = f"hd-user-{user_key(user_id)}"
        container = await asyncio.to_thread(self._get, self._client.containers, name)
        if container is not None:
            await asyncio.to_thread(container.stop, timeout=10)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            await asyncio.to_thread(close)

    # ---- sync internals (worker thread) ---------------------------------------

    def _get(self, collection: Any, name: str) -> Any:
        try:
            return collection.get(name)
        except self._not_found:
            return None

    def _ensure_sync(self, user: User) -> Instance:
        key = user_key(user.id)
        name = f"hd-user-{key}"
        network = self._ensure_network(f"hd-net-{key}")
        token = self._store.instance_token(user.id)
        container = self._get(self._client.containers, name)

        if container is not None and token is None:
            # The hub lost its record of this container's token (a reset hub.db):
            # the only way to learn it again is to issue a new one.
            logger.info("recreating %s: no instance token on record", name)
            container.remove(force=True)
            container = None
        if token is None:
            token = secrets.token_urlsafe(32)
            self._store.set_instance_token(user.id, token)

        if container is None:
            container = self._create(name, key, network, token)
        else:
            container.reload()
            if container.status != "running":
                container.start()
                container.reload()

        return Instance(base_url=self._address(container, name), token=token)

    def _ensure_network(self, name: str) -> Any:
        network = self._get(self._client.networks, name)
        if network is None:
            network = self._client.networks.create(
                name, driver="bridge", labels={LABEL: "instance-net"}
            )
        if self._config.docker_reach == "network" and self._config.self_container:
            try:
                network.connect(self._config.self_container)
            except Exception as exc:  # noqa: BLE001 - "already connected" is fine
                if "already exists" not in str(exc) and "already attached" not in str(
                    exc
                ):
                    raise
        return network

    def _create(self, name: str, key: str, network: Any, token: str) -> Any:
        environment = {"HORRIBLE_INSTANCE_TOKEN": token}
        if self._config.games_server_url:
            environment["GAMES_SERVER_URL"] = self._config.games_server_url
        kwargs: dict[str, Any] = {
            "name": name,
            "detach": True,
            "environment": environment,
            "volumes": {f"hd-data-{key}": {"bind": "/data", "mode": "rw"}},
            "network": network.name,
            "mem_limit": self._config.mem_limit,
            "nano_cpus": int(self._config.cpus * 1_000_000_000),
            "pids_limit": self._config.pids_limit,
            "security_opt": ["no-new-privileges"],
            "cap_drop": ["ALL"],
            "labels": {LABEL: "instance"},
            "restart_policy": {"Name": "no"},
        }
        if self._config.runtime:
            kwargs["runtime"] = self._config.runtime
        if self._config.docker_reach == "published":
            kwargs["ports"] = {f"{INSTANCE_PORT}/tcp": ("127.0.0.1", None)}
        logger.info("creating instance %s from %s", name, self._config.instance_image)
        container = self._client.containers.run(self._config.instance_image, **kwargs)
        container.reload()
        return container

    def _address(self, container: Any, name: str) -> str:
        if self._config.docker_reach == "published":
            ports = container.attrs["NetworkSettings"]["Ports"]
            host_port = ports[f"{INSTANCE_PORT}/tcp"][0]["HostPort"]
            return f"http://127.0.0.1:{host_port}"
        return f"http://{name}:{INSTANCE_PORT}"
