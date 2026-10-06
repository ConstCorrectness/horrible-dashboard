"""DockerSpawner against a fake Docker client: what a user's container is created
with, and how an existing one is reused, restarted or replaced."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.hub.config import HubConfig
from backend.hub.spawner.docker import DockerSpawner, user_key
from backend.hub.store import HubStore, User


class NotFound(Exception):
    pass


class FakeContainer:
    def __init__(self, name: str, kwargs: dict[str, Any]) -> None:
        self.name = name
        self.kwargs = kwargs
        self.status = "running"
        self.removed = False
        self.attrs = {
            "NetworkSettings": {"Ports": {"8000/tcp": [{"HostPort": "49153"}]}}
        }

    def reload(self) -> None: ...

    def start(self) -> None:
        self.status = "running"

    def stop(self, timeout: int = 10) -> None:
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self) -> None:
        self.by_name: dict[str, FakeContainer] = {}
        self.runs: list[tuple[str, dict[str, Any]]] = []

    def get(self, name: str) -> FakeContainer:
        c = self.by_name.get(name)
        if c is None or c.removed:
            raise NotFound(name)
        return c

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        self.runs.append((image, kwargs))
        c = FakeContainer(kwargs["name"], kwargs)
        self.by_name[kwargs["name"]] = c
        return c


class FakeNetwork:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected: list[str] = []

    def connect(self, container: str) -> None:
        if container in self.connected:
            raise RuntimeError("endpoint with name hub already exists in network")
        self.connected.append(container)


class FakeNetworks:
    def __init__(self) -> None:
        self.by_name: dict[str, FakeNetwork] = {}

    def get(self, name: str) -> FakeNetwork:
        if name not in self.by_name:
            raise NotFound(name)
        return self.by_name[name]

    def create(self, name: str, **kwargs: Any) -> FakeNetwork:
        self.by_name[name] = FakeNetwork(name)
        return self.by_name[name]


class FakeDocker:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.networks = FakeNetworks()


ALICE = User("github:42", "alice", "Alice", "jwt")


@pytest.fixture
def setup(tmp_path):
    def _make(**overrides: Any):
        config = HubConfig(
            db_path=tmp_path / "hub.db",
            instance_image="hd-node:test",
            games_server_url="wss://games.example",
            **overrides,
        )
        store = HubStore(config.db_path)
        store.upsert_user(
            {"id": ALICE.id, "handle": "alice", "display_name": "Alice"}, "jwt"
        )
        docker = FakeDocker()
        spawner = DockerSpawner(config, store, client=docker, not_found=NotFound)
        return spawner, docker, store

    return _make


def test_user_key_is_docker_safe_and_distinct() -> None:
    assert user_key("github:42").startswith("github-42-")
    assert user_key("GitHub:42") != user_key("github:42")
    assert all(c.isalnum() or c == "-" for c in user_key("a/b c:d"))


def test_creates_an_isolated_limited_container(setup) -> None:
    spawner, docker, store = setup(
        docker_reach="network", self_container="hub", runtime="runsc"
    )
    instance = asyncio.run(spawner.ensure(ALICE))
    key = user_key(ALICE.id)
    [(image, kwargs)] = docker.containers.runs
    assert image == "hd-node:test"
    assert kwargs["name"] == f"hd-user-{key}"
    assert kwargs["volumes"] == {f"hd-data-{key}": {"bind": "/data", "mode": "rw"}}
    # Its own network, which the hub joined; no port published to the host.
    assert kwargs["network"] == f"hd-net-{key}"
    assert docker.networks.by_name[f"hd-net-{key}"].connected == ["hub"]
    assert "ports" not in kwargs
    assert kwargs["mem_limit"] == "2g"
    assert kwargs["nano_cpus"] == 1_000_000_000
    assert kwargs["pids_limit"] == 512
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["runtime"] == "runsc"
    env = kwargs["environment"]
    assert env["GAMES_SERVER_URL"] == "wss://games.example"
    assert (
        env["HORRIBLE_INSTANCE_TOKEN"]
        == instance.token
        == store.instance_token(ALICE.id)
    )
    assert instance.base_url == f"http://hd-user-{key}:8000"


def test_published_reach_binds_loopback_only(setup) -> None:
    spawner, docker, _ = setup(docker_reach="published")
    instance = asyncio.run(spawner.ensure(ALICE))
    [(_, kwargs)] = docker.containers.runs
    assert kwargs["ports"] == {"8000/tcp": ("127.0.0.1", None)}
    assert instance.base_url == "http://127.0.0.1:49153"


def test_reuses_a_running_container_and_restarts_a_stopped_one(setup) -> None:
    spawner, docker, _ = setup(docker_reach="network", self_container="hub")
    first = asyncio.run(spawner.ensure(ALICE))
    asyncio.run(spawner.stop(ALICE.id))
    container = docker.containers.by_name[f"hd-user-{user_key(ALICE.id)}"]
    assert container.status == "exited"
    second = asyncio.run(spawner.ensure(ALICE))
    assert container.status == "running"
    assert second == first
    assert len(docker.containers.runs) == 1


def test_container_without_a_recorded_token_is_replaced(setup, tmp_path) -> None:
    spawner, docker, store = setup(docker_reach="published")
    asyncio.run(spawner.ensure(ALICE))
    old = docker.containers.by_name[f"hd-user-{user_key(ALICE.id)}"]
    old_token = store.instance_token(ALICE.id)
    # The hub's database was reset, but the container survived.
    store._db.execute("DELETE FROM instances")
    store._db.commit()
    instance = asyncio.run(spawner.ensure(ALICE))
    assert old.removed
    assert len(docker.containers.runs) == 2
    assert instance.token != old_token
