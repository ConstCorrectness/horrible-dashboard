"""One Fly Machine per user — the hosted backend for open signup.

Per user, in the instances app (``HUB_FLY_APP``):

- a volume ``hd_<hash>`` (encrypted, ``HUB_FLY_VOLUME_GB``) mounted at ``/data``,
- a machine ``hd-user-<key>`` from ``HUB_INSTANCE_IMAGE``, with **no services** — it
  has no public address and is reached only over Fly's private network, at
  ``<machine id>.vm.<app>.internal:8000``.

A Machine is a Firecracker microVM, which is the isolation a stranger's shell
needs and a container is not. The instances app should be created on its own
private network shared only with the hub (``fly apps create --network``), so
instances cannot reach the organisation's other apps. Instances on that network
can reach each other, so the per-instance token (`backend/hosted.py`) is what keeps
one user out of another's backend.

Fly addresses machines and volumes by generated ids, so where each user's are is
kept in the hub's ``placements`` table. The machine is stopped when idle and
started on the next request; a changed ``HUB_INSTANCE_IMAGE`` is rolled onto a
user's machine the next time it is ensured.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from typing import Any

import httpx

from backend.hub.config import HubConfig
from backend.hub.spawner.base import Instance
from backend.hub.spawner.docker import user_key
from backend.hub.store import HubStore, User

logger = logging.getLogger(__name__)

INSTANCE_PORT = 8000


class FlyApiError(RuntimeError):
    pass


def memory_mb(spec: str) -> int:
    """``2g`` → 2048, ``512m`` → 512, ``1024`` → 1024."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([gGmM]?)[bB]?\s*", spec)
    if not m:
        raise ValueError(f"bad memory size {spec!r}")
    value, unit = float(m.group(1)), m.group(2).lower()
    return int(value * 1024) if unit == "g" else int(value)


def volume_name(user_id: str) -> str:
    """Fly volume names allow ``[a-z0-9_]`` up to 30 characters."""
    return "hd_" + hashlib.sha256(user_id.encode()).hexdigest()[:24]


def auth_header(token: str) -> str:
    # `fly tokens create` prints a macaroon that already carries its scheme.
    token = token.strip()
    return token if token.startswith("FlyV1 ") else f"Bearer {token}"


class FlySpawner:
    def __init__(
        self,
        config: HubConfig,
        store: HubStore,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not config.fly_app or not config.fly_token:
            raise ValueError("HUB_SPAWNER=fly needs HUB_FLY_APP and HUB_FLY_TOKEN")
        self._config = config
        self._store = store
        self._http = client or httpx.AsyncClient(timeout=60.0)
        self._base = f"{config.fly_api.rstrip('/')}/apps/{config.fly_app}"
        self._headers = {"Authorization": auth_header(config.fly_token)}
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- Machines API ------------------------------------------------------------

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """One API call. ``None`` for a 404 (gone), raises on any other error."""
        res = await self._http.request(
            method, f"{self._base}{path}", json=body, headers=self._headers
        )
        if res.status_code == 404:
            return None
        if res.status_code >= 400:
            raise FlyApiError(f"{method} {path}: {res.status_code} {res.text[:300]}")
        return res.json() if res.content else {}

    def _machine_config(self, volume_id: str, token: str, key: str) -> dict[str, Any]:
        # `::` — Fly's private network is IPv6-only (see deploy/dashboard-node).
        env = {"HORRIBLE_INSTANCE_TOKEN": token, "HORRIBLE_BIND_HOST": "::"}
        if self._config.games_server_url:
            env["GAMES_SERVER_URL"] = self._config.games_server_url
        return {
            "image": self._config.instance_image,
            "env": env,
            "guest": {
                "cpu_kind": "shared",
                "cpus": max(1, int(self._config.cpus)),
                "memory_mb": memory_mb(self._config.mem_limit),
            },
            "mounts": [{"volume": volume_id, "path": "/data"}],
            "restart": {"policy": "no"},
            "auto_destroy": False,
            "metadata": {"hd_user": key},
        }

    async def _ensure_volume(self, user: User, placement: dict[str, Any]) -> str:
        volume_id = placement.get("volume_id")
        if volume_id and await self._call("GET", f"/volumes/{volume_id}") is not None:
            return str(volume_id)
        logger.info("creating volume for %s", user.id)
        created = await self._call(
            "POST",
            "/volumes",
            {
                "name": volume_name(user.id),
                "region": self._config.fly_region,
                "size_gb": self._config.fly_volume_gb,
                "encrypted": True,
            },
        )
        assert created is not None
        return str(created["id"])

    async def _create_machine(self, user: User, volume_id: str, token: str) -> str:
        key = user_key(user.id)
        logger.info("creating machine for %s", user.id)
        created = await self._call(
            "POST",
            "/machines",
            {
                "name": f"hd-user-{key}",
                "region": self._config.fly_region,
                "config": self._machine_config(volume_id, token, key),
            },
        )
        assert created is not None
        return str(created["id"])

    # ---- InstanceSpawner -----------------------------------------------------------

    async def ensure(self, user: User) -> Instance:
        lock = self._locks.setdefault(user.id, asyncio.Lock())
        async with lock:
            return await self._ensure(user)

    async def _ensure(self, user: User) -> Instance:
        placement = self._store.placement(user.id)
        token = self._store.instance_token(user.id)
        machine_id = placement.get("machine_id")
        machine = (
            await self._call("GET", f"/machines/{machine_id}") if machine_id else None
        )
        if machine is not None and machine.get("state") == "destroyed":
            machine = None

        if machine is not None and token is None:
            # The hub lost this machine's token: replace the machine, keep the volume.
            logger.info("replacing machine %s: no instance token on record", machine_id)
            await self._call("DELETE", f"/machines/{machine_id}?force=true")
            machine = None
        if token is None:
            token = secrets.token_urlsafe(32)
            self._store.set_instance_token(user.id, token)

        volume_id = await self._ensure_volume(user, placement)
        if machine is None:
            machine_id = await self._create_machine(user, volume_id, token)
        else:
            # Compared without any `@sha256:` pin: `fly machine update --image` stores
            # the tag *and* its digest, and an exact compare would read a machine
            # rolled by hand as out of date and restart it on its next wake.
            image = str((machine.get("config") or {}).get("image") or "").split("@")[0]
            if image != self._config.instance_image.split("@")[0]:
                logger.info(
                    "rolling %s onto %s", machine_id, self._config.instance_image
                )
                await self._call(
                    "POST",
                    f"/machines/{machine_id}",
                    {
                        "config": self._machine_config(
                            volume_id, token, user_key(user.id)
                        )
                    },
                )
            # Only a machine at rest is started. `created` is NOT at rest: it is a
            # launch still in progress (pulling the image, preparing the volume),
            # and Fly answers `start` there with 412. Every in-flight state is left
            # alone; the hub's health probe is what waits for it.
            elif machine.get("state") in ("stopped", "suspended"):
                await self._call("POST", f"/machines/{machine_id}/start")
        self._store.set_placement(
            user.id, {"machine_id": machine_id, "volume_id": volume_id}
        )
        return self._instance(str(machine_id), token)

    async def running(self, user: User) -> Instance | None:
        machine_id = self._store.placement(user.id).get("machine_id")
        token = self._store.instance_token(user.id)
        if not machine_id or token is None:
            return None
        machine = await self._call("GET", f"/machines/{machine_id}")
        if machine is None or machine.get("state") != "started":
            return None
        return self._instance(str(machine_id), token)

    def _instance(self, machine_id: str, token: str) -> Instance:
        return Instance(
            base_url=f"http://{machine_id}.vm.{self._config.fly_app}.internal:{INSTANCE_PORT}",
            token=token,
        )

    async def stop(self, user_id: str) -> None:
        machine_id = self._store.placement(user_id).get("machine_id")
        if machine_id:
            await self._call("POST", f"/machines/{machine_id}/stop")

    async def close(self) -> None:
        await self._http.aclose()
