"""Two hubs that can reach each other only through a relay broker.

A real broker (uvicorn, in-process, on a free local port) and the real
`RelayTransport` on each side: this is the path two dashboards at two houses take,
with no direct route between them.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest
import uvicorn

from backend.modules.network.relay_broker import app as broker_app
from backend.modules.network.transport.relay import RelayTransport
from backend.tests.test_network_foundation import _make_hub

FRIEND_REQUEST = "social_friend_request"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Broker:
    def __init__(self) -> None:
        self.port = _free_port()
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/relay-ws"

    async def start(self) -> None:
        config = uvicorn.Config(
            broker_app, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(100):
            if self.server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("broker did not start")

    async def stop(self) -> None:
        assert self.server is not None and self.task is not None
        self.server.should_exit = True
        await self.task


def _relay_hub(monkeypatch, tmp_path, name: str, url: str):
    hub, node_id = _make_hub(monkeypatch, tmp_path, name, trust_mode="manual")
    transport = RelayTransport(url)
    transport.BACKOFF_S = (0.05, 0.2)
    hub.set_transports([transport])
    return hub, node_id, transport


async def _wait(event: asyncio.Event, timeout: float = 5.0) -> None:
    await asyncio.wait_for(event.wait(), timeout)


def test_two_nodes_meet_through_the_relay_as_strangers(monkeypatch, tmp_path):
    broker = Broker()

    async def go():
        await broker.start()
        hub_a, id_a, relay_a = _relay_hub(monkeypatch, tmp_path, "a", broker.url)
        hub_b, id_b, relay_b = _relay_hub(monkeypatch, tmp_path, "b", broker.url)
        seen: list[str] = []

        async def on_request(_hub, session, env):
            seen.append(env.src)

        hub_b.register_handler(FRIEND_REQUEST, on_request)
        for hub in (hub_a, hub_b):
            hub.allow_from_strangers(FRIEND_REQUEST)
        try:
            await hub_a.start()
            await hub_b.start()
            await _wait(relay_a.registered)
            await _wait(relay_b.registered)
            monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path / "b"))

            info = await hub_a.connect(f"relay:{id_b}", "relay")
            await hub_a.send_to(id_b, FRIEND_REQUEST, {})
            for _ in range(50):
                if seen:
                    break
                await asyncio.sleep(0.05)
            return info, seen, id_a in hub_b.strangers
        finally:
            await hub_a.stop()
            await hub_b.stop()
            await broker.stop()

    info, seen, b_holds_a = asyncio.run(go())
    assert info.transport == "relay"
    assert info.trusted is False
    assert b_holds_a
    assert seen, "the friend request never crossed the relay"


def test_dialing_a_node_that_is_not_on_the_relay_fails_fast(monkeypatch, tmp_path):
    broker = Broker()

    async def go():
        await broker.start()
        hub_a, _id_a, relay_a = _relay_hub(monkeypatch, tmp_path, "a", broker.url)
        try:
            await hub_a.start()
            await _wait(relay_a.registered)
            started = time.monotonic()
            # The reason reaches the add-friend screen, so it has to say something.
            with pytest.raises(Exception, match="not online"):
                await hub_a.connect("relay:nobodyhome0000", "relay")
            return time.monotonic() - started
        finally:
            await hub_a.stop()
            await broker.stop()

    assert asyncio.run(go()) < 5.0  # well under HANDSHAKE_TIMEOUT_S


def test_the_transport_reregisters_after_the_broker_restarts(monkeypatch, tmp_path):
    """The hosted broker will restart; a node must come back without a restart of
    its own, or every friend elsewhere loses it until someone notices."""
    broker = Broker()

    async def go():
        await broker.start()
        hub_a, _id_a, relay_a = _relay_hub(monkeypatch, tmp_path, "a", broker.url)
        try:
            await hub_a.start()
            await _wait(relay_a.registered)
            await broker.stop()
            for _ in range(100):
                if not relay_a.registered.is_set():
                    break
                await asyncio.sleep(0.05)
            dropped = not relay_a.registered.is_set()
            restarted = Broker()
            restarted.port = broker.port
            await restarted.start()
            try:
                await _wait(relay_a.registered, timeout=10)
                return dropped, relay_a.registered.is_set()
            finally:
                await restarted.stop()
        finally:
            await hub_a.stop()

    dropped, back = asyncio.run(go())
    assert dropped and back


def test_a_broker_that_is_down_at_boot_does_not_stop_the_hub(monkeypatch, tmp_path):
    async def go():
        hub_a, _id_a, relay_a = _relay_hub(
            monkeypatch, tmp_path, "a", f"ws://127.0.0.1:{_free_port()}/relay-ws"
        )
        started = time.monotonic()
        await hub_a.start()
        elapsed = time.monotonic() - started
        await hub_a.stop()
        return elapsed, relay_a.registered.is_set()

    elapsed, registered = asyncio.run(go())
    assert elapsed < 1.0
    assert registered is False
