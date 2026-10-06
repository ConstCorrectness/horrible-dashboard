"""Per-user instance runners. See `base.InstanceSpawner`."""

from __future__ import annotations

import logging

from backend.hub.config import HubConfig
from backend.hub.spawner.base import Instance, InstanceSpawner
from backend.hub.store import HubStore

__all__ = ["Instance", "InstanceSpawner", "make_spawner"]

logger = logging.getLogger(__name__)


def make_spawner(config: HubConfig, store: HubStore) -> InstanceSpawner:
    if config.spawner == "static":
        from backend.hub.spawner.static import StaticSpawner

        logger.warning(
            "HUB_SPAWNER=static: every user shares the backend at %s — NO isolation. "
            "Development only.",
            config.static_upstream,
        )
        return StaticSpawner(config.static_upstream, config.static_token)
    if config.spawner == "docker":
        from backend.hub.spawner.docker import DockerSpawner

        return DockerSpawner(config, store)
    if config.spawner == "fly":
        from backend.hub.spawner.fly import FlySpawner

        return FlySpawner(config, store)
    raise ValueError(f"unknown HUB_SPAWNER {config.spawner!r}")
