"""A node advertises the port it actually serves on.

Found live: a friend's node was started with a bare `uvicorn --port 8100` and
published `ws://10.0.0.142:8000/peer-ws` to the presence directory, because the
advertised port came from `HORRIBLE_DEV_BACKEND_PORT` — which only `pnpm dev` sets —
with a default of 8000. Every reconnect through the directory dialed a closed port.
See backend/server_port.py.
"""

from __future__ import annotations

import asyncio

import pytest

from backend import server_port
from backend.modules.network import ice, lease, trust

DEV_PORT_ENV = "HORRIBLE_DEV_BACKEND_PORT"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    server_port.reset()
    monkeypatch.delenv(DEV_PORT_ENV, raising=False)
    monkeypatch.delenv("UVICORN_PORT", raising=False)
    monkeypatch.setattr(trust, "lan_ip", lambda: "10.0.0.142")
    # Pytest's own argv carries no `--port`; start every test from that.
    monkeypatch.setattr(server_port.sys, "argv", ["pytest"])
    yield
    server_port.reset()


def _launched_with(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(server_port.sys, "argv", ["uvicorn", *argv])


# ------------------------------------------------------------------ the live bug


def test_a_bare_uvicorn_on_8100_advertises_8100_not_8000(monkeypatch):
    _launched_with(
        monkeypatch, "backend.app:app", "--host", "0.0.0.0", "--port", "8100"
    )
    assert trust.advertised_address() == "ws://10.0.0.142:8100/peer-ws"
    # Invites and the presence record are built from the ICE candidates, which must
    # carry the same port on every host candidate.
    monkeypatch.setattr(ice, "_local_ipv4s", lambda: ["10.0.0.142", "100.91.147.73"])
    assert ice.host_candidates() == [
        "ws://10.0.0.142:8100/peer-ws",
        "ws://100.91.147.73:8100/peer-ws",
    ]
    # The tunnel target for lent extras had the same assumption.
    assert lease._own_api_endpoint() == ("127.0.0.1", 8100)


def test_the_published_presence_record_carries_the_served_port(monkeypatch):
    from backend.modules.social import directory

    _launched_with(monkeypatch, "backend.app:app", "--port", "8100")
    monkeypatch.setattr(ice, "_local_ipv4s", lambda: ["10.0.0.142"])
    candidates = asyncio.run(ice.gather_candidates())
    record = directory.build_record(candidates)
    assert record is not None
    assert record["addresses"] == ["ws://10.0.0.142:8100/peer-ws"]


# ----------------------------------------------------------------- the precedence


def test_the_setting_still_wins(monkeypatch):
    _launched_with(monkeypatch, "--port", "8100")
    monkeypatch.setattr(
        trust, "get_value", lambda key, default=None: "wss://pi.example.ts.net/peer-ws"
    )
    assert trust.advertised_address() == "wss://pi.example.ts.net/peer-ws"


def test_the_dev_launcher_variable_is_a_fallback_not_an_override(monkeypatch):
    monkeypatch.setenv(DEV_PORT_ENV, "8000")
    assert server_port.port() == 8000
    # A command line that names a port is what the server was actually told.
    _launched_with(monkeypatch, "--port", "8100")
    assert server_port.port() == 8100


def test_nothing_known_is_8000(monkeypatch):
    assert server_port.port() == server_port.DEFAULT_PORT == 8000


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["--port", "8100"], {}, 8100),
        (["--port=8211"], {}, 8211),
        (["--bind", "0.0.0.0:9000"], {}, 9000),
        (["-b", "[::]:9001"], {}, 9001),
        (["--bind", "unix:/run/horrible.sock"], {}, None),
        (["--port", "8100", "--port", "8200"], {}, 8200),
        (["--port", "not-a-port"], {}, None),
        (["--port", "70000"], {}, None),
        ([], {"UVICORN_PORT": "8300"}, 8300),
        (["--port", "8100"], {"UVICORN_PORT": "8300"}, 8100),
        ([], {}, None),
    ],
)
def test_configured_port_reads_what_the_server_was_told(argv, env, expected):
    assert server_port.configured_port(["uvicorn", *argv], env) == expected


# ------------------------------------------------------- learning from a request


def test_the_socket_a_request_arrived_on_overrules_the_assumption(monkeypatch):
    """Lifespan runs before uvicorn binds, so startup can only assume. The first
    request's `scope["server"]` is the real socket, and a disagreement is reported so
    the caller republishes."""
    monkeypatch.setenv(DEV_PORT_ENV, "8000")
    assert trust.advertised_address().endswith(":8000/peer-ws")

    assert server_port.observe(("127.0.0.1", 8123)) is True
    assert trust.advertised_address() == "ws://10.0.0.142:8123/peer-ws"
    # Same port again is not news.
    assert server_port.observe(("127.0.0.1", 8123)) is False


def test_an_observation_that_agrees_is_not_a_change(monkeypatch):
    _launched_with(monkeypatch, "--port", "8100")
    assert server_port.observe(("0.0.0.0", 8100)) is False


@pytest.mark.parametrize("server", [("/run/horrible.sock", None), None, (), "8100"])
def test_a_request_with_no_usable_port_teaches_nothing(server):
    assert server_port.observe(server) is False
    assert server_port.port() == 8000


def test_the_middleware_republishes_once_when_the_port_was_wrong(monkeypatch):
    import backend.app as app_module

    republished: list[int] = []

    async def fake_republish() -> None:
        republished.append(server_port.port())

    monkeypatch.setattr(app_module, "_republish_presence", fake_republish)
    monkeypatch.setenv(DEV_PORT_ENV, "8000")

    seen: list[str] = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    middleware = app_module._ObserveServerPort(inner)

    async def go() -> None:
        await middleware({"type": "lifespan"}, None, None)
        assert republished == []  # lifespan carries no server; nothing learned
        await middleware(
            {"type": "websocket", "server": ("127.0.0.1", 8100)}, None, None
        )
        await middleware({"type": "http", "server": ("127.0.0.1", 8100)}, None, None)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert republished == [8100]
    assert seen == ["lifespan", "websocket", "http"]
