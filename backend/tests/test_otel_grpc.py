"""The opt-in OTLP/gRPC receiver, driven by the real gRPC exporter.

Hand-rolling the wire would test our own idea of it; the point of speaking gRPC at
all is that an exporter nobody configured for us can reach the node.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.otel import auth, grpc_server, materialize, store
from backend.modules.trajectories import store as traj_store


@pytest.fixture(autouse=True)
def _reset():
    materialize.reset()
    yield
    materialize.reset()


def free_port() -> int:
    """A concrete free port. Deliberately not 0: to this module 0 means *off*, which
    is the whole opt-in — and never 4317, so a developer running the real receiver
    does not fail the suite."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def grpc_settings(monkeypatch):
    def configured(host: str = "127.0.0.1", port: int | None = None):
        chosen = free_port() if port is None else port
        monkeypatch.setattr(grpc_server, "configured", lambda: (host, chosen))
        return chosen

    return configured


def _export(endpoint: str, headers=None) -> str:
    """Send one agent-shaped trace with the SDK's gRPC exporter. Returns its id."""
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "grpc-agent"}))
    provider.add_span_processor(
        SimpleSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, insecure=True, headers=headers)
        )
    )
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span(
        "invoke_agent grpc-helper",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "grpc-helper",
        },
    ) as root:
        with tracer.start_as_current_span(
            "execute_tool lookup",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "lookup",
            },
        ):
            pass
        trace_id = format(root.get_span_context().trace_id, "032x")
    provider.shutdown()
    return trace_id


@pytest.mark.anyio
async def test_off_unless_a_port_is_set(grpc_settings) -> None:
    grpc_settings(port=0)  # 0 is the default, and the default is off
    assert await grpc_server.start() is None
    assert grpc_server.bound_port() is None


@pytest.mark.anyio
async def test_a_grpc_exporter_reaches_the_node(grpc_settings) -> None:
    wanted = grpc_settings()
    host = grpc_server.configured()[0]
    port = await grpc_server.start()
    # Reported from the bind, not the setting — a taken port must never be
    # advertised as an endpoint.
    assert port == wanted and grpc_server.bound_port() == port
    try:
        # On a worker thread: the exporter is synchronous, and calling it on the
        # loop would block the very server it is exporting to.
        trace_id = await asyncio.to_thread(_export, f"{host}:{port}")
        stored = store.get_trace(trace_id)
        assert len(stored) == 2
        assert {s.name for s in stored} == {
            "invoke_agent grpc-helper",
            "execute_tool lookup",
        }
        with traj_store.get_db_conn() as conn:
            row = conn.execute(
                "SELECT id, agent_name, status FROM traj_runs WHERE external_id = ?",
                (trace_id,),
            ).fetchone()
        assert row is not None
        assert (row["agent_name"], row["status"]) == ("grpc-helper", "complete")
    finally:
        await grpc_server.stop()
    assert grpc_server.bound_port() is None


def test_authorization_follows_the_http_rule() -> None:
    token = auth.get_token()
    # Loopback, in both address families and through a mapped IPv4.
    assert grpc_server.authorized("ipv4:127.0.0.1:53201", ())
    assert grpc_server.authorized("ipv6:[::1]:53201", ())
    assert grpc_server.authorized("ipv6:[::ffff:127.0.0.1]:53201", ())
    # Anything else needs the token, exactly as over HTTP.
    assert not grpc_server.authorized("ipv4:10.0.0.7:53201", ())
    assert not grpc_server.authorized(
        "ipv4:10.0.0.7:53201", (("authorization", "Bearer no"),)
    )
    assert grpc_server.authorized(
        "ipv4:10.0.0.7:53201", (("authorization", f"Bearer {token}"),)
    )
    # A token in the wrong scheme is not a token.
    assert not grpc_server.authorized(
        "ipv4:10.0.0.7:53201", (("authorization", token),)
    )
