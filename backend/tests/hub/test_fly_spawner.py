"""FlySpawner against a fake Fly Machines API: what a user's machine and volume are
created with, and how an existing machine is reused, started, upgraded or replaced."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.hub.config import HubConfig
from backend.hub.spawner.docker import user_key
from backend.hub.spawner.fly import FlySpawner, auth_header, memory_mb, volume_name
from backend.hub.store import HubStore, User

ALICE = User("local:abc123", "alice", "Alice", "jwt")


class FakeFly:
    """Just enough of api.machines.dev for the spawner."""

    def __init__(self) -> None:
        self.machines: dict[str, dict[str, Any]] = {}
        self.volumes: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.auth: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("authorization", ""))
        body = json.loads(request.content) if request.content else None
        path = request.url.path.split("/apps/hd-instances", 1)[1]
        self.calls.append((request.method, path, body))
        parts = path.strip("/").split("/")
        if parts[0] == "volumes":
            if request.method == "POST":
                vid = f"vol_{len(self.volumes) + 1}"
                self.volumes[vid] = {"id": vid, **body}
                return httpx.Response(200, json=self.volumes[vid])
            vol = self.volumes.get(parts[1])
            return httpx.Response(200, json=vol) if vol else httpx.Response(404)
        if parts[0] == "machines":
            if len(parts) == 1:
                mid = f"m{len(self.machines) + 1}"
                self.machines[mid] = {"id": mid, "state": "started", **body}
                return httpx.Response(200, json=self.machines[mid])
            machine = self.machines.get(parts[1])
            if machine is None:
                return httpx.Response(404)
            if request.method == "GET":
                return httpx.Response(200, json=machine)
            if request.method == "DELETE":
                machine["state"] = "destroyed"
                return httpx.Response(200, json={"ok": True})
            if len(parts) == 3 and parts[2] in ("start", "stop"):
                machine["state"] = "started" if parts[2] == "start" else "stopped"
                return httpx.Response(200, json={"ok": True})
            if len(parts) == 2 and request.method == "POST":
                machine["config"] = body["config"]
                return httpx.Response(200, json=machine)
        return httpx.Response(400, json={"error": f"unhandled {request.method} {path}"})


@pytest.fixture
def setup(tmp_path):
    def _make(**overrides: Any):
        settings: dict[str, Any] = {
            "db_path": tmp_path / "hub.db",
            "instance_image": "registry.fly.io/hd-instances:v1",
            "games_server_url": "wss://games.example",
            "fly_app": "hd-instances",
            "fly_token": "FlyV1 fm2_secret",
            "fly_region": "iad",
            **overrides,
        }
        config = HubConfig(**settings)
        store = HubStore(config.db_path)
        store.upsert_user({"id": ALICE.id, "handle": "alice"}, "jwt")
        fake = FakeFly()
        client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        return FlySpawner(config, store, client=client), fake, store, config

    return _make


def test_helpers() -> None:
    assert memory_mb("2g") == 2048
    assert memory_mb("512m") == 512
    assert memory_mb("1024") == 1024
    assert auth_header("FlyV1 fm2_x") == "FlyV1 fm2_x"
    assert auth_header("abc") == "Bearer abc"
    name = volume_name("local:abc123")
    assert len(name) <= 30 and all(c.isalnum() or c == "_" for c in name)
    assert name == name.lower()


def test_requires_app_and_token(tmp_path) -> None:
    store = HubStore(tmp_path / "h.db")
    with pytest.raises(ValueError):
        FlySpawner(
            HubConfig(db_path=tmp_path / "h.db", fly_app="", fly_token=""), store
        )


def test_creates_a_private_machine_with_its_own_volume(setup) -> None:
    spawner, fake, store, _ = setup(mem_limit="1g")
    instance = asyncio.run(spawner.ensure(ALICE))
    [vol] = fake.volumes.values()
    assert vol["name"] == volume_name(ALICE.id)
    assert vol["region"] == "iad" and vol["encrypted"] is True and vol["size_gb"] == 1
    [machine] = fake.machines.values()
    config = machine["config"]
    assert machine["name"] == f"hd-user-{user_key(ALICE.id)}"
    assert config["image"] == "registry.fly.io/hd-instances:v1"
    assert config["mounts"] == [{"volume": vol["id"], "path": "/data"}]
    # Reachable only over the private network: no services, no public port.
    assert "services" not in config
    assert config["guest"] == {"cpu_kind": "shared", "cpus": 1, "memory_mb": 1024}
    assert config["env"]["GAMES_SERVER_URL"] == "wss://games.example"
    assert config["env"]["HORRIBLE_INSTANCE_TOKEN"] == instance.token
    # Fly's private network is IPv6-only; a `::` socket from asyncio is v6-only.
    assert config["env"]["HORRIBLE_BIND_HOST"] == "::"
    assert instance.token == store.instance_token(ALICE.id)
    assert instance.base_url == f"http://{machine['id']}.vm.hd-instances.internal:8000"
    assert fake.auth == {"FlyV1 fm2_secret"}


def test_reuses_and_restarts_a_stopped_machine(setup) -> None:
    spawner, fake, _, _ = setup()
    first = asyncio.run(spawner.ensure(ALICE))
    asyncio.run(spawner.stop(ALICE.id))
    [machine] = fake.machines.values()
    assert machine["state"] == "stopped"
    second = asyncio.run(spawner.ensure(ALICE))
    assert second == first
    assert machine["state"] == "started"
    assert len(fake.machines) == 1 and len(fake.volumes) == 1


def test_a_new_image_is_rolled_onto_the_existing_machine(setup, tmp_path) -> None:
    spawner, fake, store, config = setup()
    asyncio.run(spawner.ensure(ALICE))
    upgraded = FlySpawner(
        HubConfig(
            **{**config.__dict__, "instance_image": "registry.fly.io/hd-instances:v2"}
        ),
        store,
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )
    asyncio.run(upgraded.ensure(ALICE))
    [machine] = fake.machines.values()
    assert machine["config"]["image"] == "registry.fly.io/hd-instances:v2"
    assert len(fake.machines) == 1


def test_lost_token_replaces_the_machine_but_keeps_the_volume(setup) -> None:
    spawner, fake, store, _ = setup()
    old = asyncio.run(spawner.ensure(ALICE))
    store._db.execute("DELETE FROM instances")
    store._db.commit()
    new = asyncio.run(spawner.ensure(ALICE))
    assert new.token != old.token
    assert [m["state"] for m in fake.machines.values()] == ["destroyed", "started"]
    assert len(fake.volumes) == 1
    [_, current] = fake.machines.values()
    assert current["config"]["mounts"][0]["volume"] == "vol_1"


def test_api_errors_surface(setup) -> None:
    spawner, fake, _, _ = setup()

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "no capacity"})

    spawner._http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    with pytest.raises(RuntimeError, match="no capacity"):
        asyncio.run(spawner.ensure(ALICE))


def test_a_launching_machine_is_not_started(setup) -> None:
    """Found on the first live sign-in: Fly reports a launch in progress as
    `created`, and answers `start` there with 412 — so it must be left alone."""
    spawner, fake, _, _ = setup()
    asyncio.run(spawner.ensure(ALICE))
    [machine] = fake.machines.values()
    machine["state"] = "created"
    asyncio.run(spawner.ensure(ALICE))
    assert not any(path.endswith("/start") for _, path, _ in fake.calls)
