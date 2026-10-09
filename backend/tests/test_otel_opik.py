"""Opik as an export destination: configuration, the probe, and span shaping.

The shaping tests build spans by hand and assert on what Opik's ingest would read;
the end-to-end test drives the real tracer through a real HTTP collector standing
in for Opik, so the URL, the headers and the bytes are the ones Opik would get.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.modules.otel import decode, destinations, export, opik, tracing
from backend.tests.otel_helpers import collector

_ENV = (
    "OPIK_URL_OVERRIDE",
    "OPIK_API_KEY",
    "OPIK_WORKSPACE",
    "OPIK_PROJECT_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    yield
    destinations.detach("opik")
    destinations.detach("otel")


def _span(
    name: str,
    attrs: dict,
    *,
    parent: int | None = None,
    remote: bool = False,
    node: str = "abcdefghijklmnop",
):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import SpanContext, TraceFlags

    ctx = SpanContext(0xABC, 0x1, is_remote=False, trace_flags=TraceFlags(1))
    parent_ctx = (
        SpanContext(0xABC, parent, is_remote=remote, trace_flags=TraceFlags(1))
        if parent
        else None
    )
    return ReadableSpan(
        name=name,
        context=ctx,
        parent=parent_ctx,
        resource=Resource.create({"service.instance.id": node}),
        attributes=attrs,
        start_time=1,
        end_time=2,
    )


def _meta(span) -> dict:
    return json.loads(span.attributes["opik.metadata"])


# --- configuration -----------------------------------------------------------------


def test_base_url_normalization() -> None:
    n = opik.normalize_base
    assert n("http://localhost:5173/") == "http://localhost:5173"
    assert n("http://localhost:5173/api/v1/private/otel") == "http://localhost:5173"
    assert n("http://localhost:5173/api") == "http://localhost:5173"
    assert n("https://www.comet.com") == "https://www.comet.com/opik"
    assert (
        n("https://www.comet.com/opik/api/v1/private/otel")
        == "https://www.comet.com/opik"
    )
    # Not every path containing "api" is the API prefix.
    assert (
        n("https://opik.example.com/apis-team") == "https://opik.example.com/apis-team"
    )
    cfg = opik.OpikConfig(base="https://www.comet.com/opik")
    assert cfg.traces_url == "https://www.comet.com/opik/api/v1/private/otel/v1/traces"


def test_env_overrides_stored_config(monkeypatch) -> None:
    assert opik.config() is None
    opik._put(opik._URL, "http://localhost:5173")
    opik._put(opik._PROJECT, "stored-project")
    cfg = opik.config()
    assert cfg is not None
    assert (cfg.base, cfg.project, cfg.workspace) == (
        "http://localhost:5173",
        "stored-project",
        "default",
    )
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "https://www.comet.com/opik/api")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "env-project")
    monkeypatch.setenv("OPIK_API_KEY", "k")
    cfg = opik.config()
    assert (cfg.base, cfg.project, cfg.api_key) == (
        "https://www.comet.com/opik",
        "env-project",
        "k",
    )


def test_an_api_key_alone_means_comet_cloud(monkeypatch) -> None:
    monkeypatch.setenv("OPIK_API_KEY", "k")
    cfg = opik.config()
    assert cfg is not None and cfg.base == opik.CLOUD_URL


def test_headers_cloud_and_self_hosted() -> None:
    cloud = opik.OpikConfig(
        base=opik.CLOUD_URL, api_key="key", workspace="team", project="p"
    )
    assert cloud.headers() == {
        "projectName": "p",
        "Comet-Workspace": "team",
        "Authorization": "key",
    }
    local = opik.OpikConfig(base="http://localhost:5173", project="p")
    assert local.headers() == {"projectName": "p"}


def test_include_content_defaults_on_only_for_loopback() -> None:
    assert opik.default_include_content("http://localhost:5173")
    assert opik.default_include_content("http://[::1]:5173")
    assert opik.default_include_content("http://127.0.0.1:5173")
    assert not opik.default_include_content(opik.CLOUD_URL)
    assert not opik.default_include_content("http://192.168.1.20:5173")


# --- shaping -----------------------------------------------------------------------


def test_flavor_packs_unmapped_attributes_into_metadata_json() -> None:
    shaped = opik.opik_span(
        _span(
            "execute_tool read",
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "read",
                "horrible.round": 2,
                "custom.thing": "x",
            },
            parent=7,
        )
    )
    attrs = dict(shaped.attributes)
    # Opik files unmapped attributes under INPUT; none may survive as-is.
    assert not any(k.startswith("horrible.") or k == "custom.thing" for k in attrs)
    assert attrs["gen_ai.tool.name"] == "read"
    assert _meta(shaped) == {"round": 2, "custom.thing": "x"}
    # Not an entry span: no trace-level tags.
    assert "opik.tags" not in attrs


def test_flavor_moves_model_and_root_io_off_invoke_agent() -> None:
    shaped = opik.opik_span(
        _span(
            "invoke_agent Main",
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.id": "main",
                "gen_ai.request.model": "kimi-k3",
                "gen_ai.input.messages": json.dumps(
                    [{"role": "user", "content": "hi"}]
                ),
                "gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "content": "yo"}]
                ),
                "horrible.turn_id": "t1",
            },
        )
    )
    attrs = dict(shaped.attributes)
    # Each of these would type the agent span as an LLM call.
    for key in (
        "gen_ai.request.model",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
    ):
        assert key not in attrs
    assert json.loads(attrs["input"]) == {
        "messages": [{"role": "user", "content": "hi"}]
    }
    assert json.loads(attrs["output"])["messages"][0]["content"] == "yo"
    meta = _meta(shaped)
    assert meta["model"] == "kimi-k3" and meta["turn_id"] == "t1"


def test_flavor_mirrors_cost_on_chat_spans_only() -> None:
    chat = opik.opik_span(
        _span(
            "chat m",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "m",
                "gen_ai.request.max_tokens": 512,
                "gen_ai.usage.input_tokens": 10,
                "horrible.cost_usd": 0.25,
                "horrible.usage.cached_input_tokens": 4,
            },
            parent=3,
        )
    )
    attrs = dict(chat.attributes)
    assert attrs["gen_ai.usage.cost"] == 0.25
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 4
    assert attrs["gen_ai.request.model"] == "m"
    # A request parameter, not input.
    assert "gen_ai.request.max_tokens" not in attrs
    assert _meta(chat)["gen_ai.request.max_tokens"] == 512

    agent = opik.opik_span(
        _span(
            "invoke_agent a",
            {"gen_ai.operation.name": "invoke_agent", "horrible.cost_usd": 0.25},
        )
    )
    assert "gen_ai.usage.cost" not in agent.attributes


def test_flavor_tags_and_threads_root_spans() -> None:
    root = opik.opik_span(
        _span(
            "invoke_agent Main",
            {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.id": "coder",
                "gen_ai.conversation.id": "session-9",
                "horrible.source": "delegate",
            },
        )
    )
    assert list(root.attributes["opik.tags"]) == [
        "agent:coder",
        "source:delegate",
        "node:abcdefgh",
    ]
    assert root.attributes["thread_id"] == "session-9"
    assert _meta(root)["node_id"] == "abcdefghijklmnop"

    # A model call with no agent around it is a bare LLM call.
    bare = opik.opik_span(_span("chat m", {"gen_ai.operation.name": "chat"}))
    assert "source:llm" in bare.attributes["opik.tags"]

    # A remote parent makes a span this node's entry point too.
    remote = opik.opik_span(
        _span(
            "invoke_agent x",
            {"gen_ai.operation.name": "invoke_agent"},
            parent=5,
            remote=True,
        )
    )
    assert "opik.tags" in remote.attributes


def test_flavor_drops_inbound_opik_attributes() -> None:
    shaped = opik.opik_span(
        _span(
            "execute_tool t",
            {
                "gen_ai.operation.name": "execute_tool",
                "opik.trace_id": "someone-elses-trace",
                "opik.parent_span_id": "x",
                "thread_id": "hijack",
                "input": "planted",
            },
            parent=2,
        )
    )
    attrs = dict(shaped.attributes)
    assert "opik.trace_id" not in attrs and "opik.parent_span_id" not in attrs
    assert "thread_id" not in attrs and "input" not in attrs


# --- probe -------------------------------------------------------------------------


def _base(server) -> str:
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}"


def test_probe_accepts_a_healthy_opik() -> None:
    server, received = collector(
        get_routes={
            "/api/v1/private/projects": (200, {"content": [], "total": 0}),
            "/api/is-alive/ver": (200, {"version": "2.2.87"}),
        }
    )
    try:
        cfg = opik.OpikConfig(base=_base(server), workspace="default", project="p")
        result = opik.probe(cfg)
        assert result.ok and result.version == "2.2.87"
        projects = next(
            h for p, h, _ in received if p == "GET /api/v1/private/projects"
        )
        assert projects["comet-workspace"] == "default"
    finally:
        server.shutdown()
        server.server_close()


def test_probe_rejects_something_that_is_not_opik() -> None:
    # What our own Vite dev server answers on 127.0.0.1:5173: a JSON 404.
    server, _ = collector(get_routes={})
    try:
        result = opik.probe(opik.OpikConfig(base=_base(server)))
        assert not result.ok
        assert "not as Opik" in result.message
    finally:
        server.shutdown()
        server.server_close()


def test_probe_names_the_vite_port_clash() -> None:
    assert "[::1]:5173" in opik._vite_hint("http://127.0.0.1:5173")
    assert "[::1]:5173" in opik._vite_hint("http://localhost:5173")
    assert opik._vite_hint("https://www.comet.com/opik") == ""
    # Already the way around it: no advice to do what was just done.
    assert opik._vite_hint("http://[::1]:5173") == ""


def test_probe_says_when_a_local_opik_is_not_running() -> None:
    import socket

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = opik.probe(opik.OpikConfig(base=f"http://127.0.0.1:{port}"))
    assert not result.ok and "Is Opik running?" in result.message


def test_probe_reports_a_rejected_key() -> None:
    server, _ = collector(
        get_routes={"/api/v1/private/projects": (401, {"message": "no"})}
    )
    try:
        result = opik.probe(opik.OpikConfig(base=_base(server), api_key="bad"))
        assert not result.ok and "refused the credentials" in result.message
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.anyio
async def test_connector_validates_before_storing() -> None:
    connector = opik.build()
    step = await connector.begin({})
    fields = {f["name"]: f for f in step["fields"]}
    assert fields["url"]["value"] == opik.CLOUD_URL
    assert fields["include_content"]["kind"] == "toggle"
    assert fields["include_content"]["value"] == "false"  # Cloud: off by default
    assert fields["api_key"]["secret"] and fields["api_key"]["value"] == ""

    result = await connector.submit({"url": opik.CLOUD_URL, "workspace": ""})
    assert "error" in result and "API key" in result["error"]
    assert opik.config() is None

    server, _ = collector(get_routes={})
    try:
        result = await connector.submit({"url": _base(server)})
        assert "error" in result
        assert opik.config() is None, "a URL that is not Opik must not be stored"
    finally:
        server.shutdown()
        server.server_close()


# --- end to end --------------------------------------------------------------------


def _opik_server(post_status: int = 200):
    return collector(
        post_status=post_status,
        get_routes={"/api/v1/private/projects": (200, {"content": []})},
    )


def _decoded(received) -> list:
    spans = []
    for path, headers, body in received:
        if path.startswith("GET "):
            continue
        spans += decode.decode(
            decode.inflate(body, headers.get("content-encoding")),
            "application/x-protobuf",
        )
    return spans


def _run_turn() -> None:
    from backend.modules.agent.providers import Usage

    class Result:
        usage = Usage(tokens_in=12, tokens_out=3)
        tool_calls: list = []
        assistant_message = {"role": "assistant", "content": "done"}

    with tracing.agent_span(
        turn_id="opik-e2e",
        agent_id="main",
        agent_name="Main",
        model="m",
        provider="ollama",
    ):
        with tracing.chat_span(
            provider_kind="ollama",
            model="m",
            endpoint="http://localhost:11434",
            messages=[{"role": "user", "content": "secret plan"}],
            params={},
        ) as chat:
            chat.finish(Result(), provider_kind="ollama", model="m")
    tracing.force_flush()


@pytest.mark.anyio
async def test_opik_spans_reach_a_fake_opik(monkeypatch) -> None:
    from backend.modules.agent import cost

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(cost, "resolve", lambda *a, **k: 0.5)
    server, received = _opik_server()
    try:
        result = await opik.build().submit(
            {
                "url": _base(server),
                "workspace": "default",
                "project": "agents",
                "api_key": "opik-key",
                "include_content": "false",
            }
        )
        assert result.get("connected"), result
        await asyncio.to_thread(_run_turn)

        posts = [(p, h) for p, h, _ in received if not p.startswith("GET ")]
        assert posts, "nothing was exported"
        path, headers = posts[-1]
        assert path == "/api/v1/private/otel/v1/traces"
        assert headers["authorization"] == "opik-key"
        assert headers["comet-workspace"] == "default"
        assert headers["projectname"] == "agents"

        by_op = {s.attrs.get("gen_ai.operation.name"): s for s in _decoded(received)}
        root, chat = by_op["invoke_agent"], by_op["chat"]
        assert {"agent:main", "source:chat"} <= set(root.attrs["opik.tags"])
        assert not any(
            k.startswith("horrible.") for s in by_op.values() for k in s.attrs
        )
        assert json.loads(root.attrs["opik.metadata"])["turn_id"] == "opik-e2e"
        assert chat.attrs["gen_ai.usage.cost"] == 0.5
        assert chat.attrs["gen_ai.usage.input_tokens"] == 12
        # Content was not asked for.
        assert "gen_ai.input.messages" not in chat.attrs
        st = destinations.status("opik")
        assert st is not None and st.spans_ok >= 2 and not st.failing
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.anyio
async def test_destinations_filter_content_independently(monkeypatch) -> None:
    """Opik asks for content; the generic collector and the local store did not."""
    from backend.modules.otel import store

    monkeypatch.setattr(tracing, "enabled", lambda: True)
    monkeypatch.setattr(tracing, "capture_content", lambda: False)
    opik_server, opik_received = _opik_server()
    otel_server, otel_received = collector()
    try:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _base(otel_server))
        assert await asyncio.to_thread(export.configure)
        result = await opik.build().submit(
            {"url": _base(opik_server), "include_content": "true"}
        )
        assert result.get("connected"), result
        assert tracing.content_wanted()

        await asyncio.to_thread(_run_turn)

        opik_chat = next(
            s
            for s in _decoded(opik_received)
            if s.attrs.get("gen_ai.operation.name") == "chat"
        )
        assert "secret plan" in opik_chat.attrs["gen_ai.input.messages"]
        otel_chat = next(
            s
            for s in _decoded(otel_received)
            if s.attrs.get("gen_ai.operation.name") == "chat"
        )
        assert not any(k in tracing.CONTENT_ATTRS for k in otel_chat.attrs)
        local = store.get_trace(otel_chat.trace_id)
        assert local, "the local store never received the turn"
        assert not any(k in tracing.CONTENT_ATTRS for s in local for k in s.attrs)
    finally:
        tracing.set_destination_content("opik", False)
        for server in (opik_server, otel_server):
            server.shutdown()
            server.server_close()


@pytest.mark.anyio
async def test_export_status_records_a_rejected_export(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "enabled", lambda: True)
    server, _ = _opik_server(post_status=401)
    try:
        assert (await opik.build().submit({"url": _base(server)})).get("connected")
        await asyncio.to_thread(_run_turn)
        st = destinations.status("opik")
        assert st is not None and st.failing
        assert st.last_error.startswith("HTTP 401")
        assert opik.build().status().error.startswith("Last export failed")
        listed = destinations.statuses()
        assert listed[0]["name"] == "opik" and listed[0]["failing"] is True
        assert "opik-key" not in json.dumps(listed)
    finally:
        server.shutdown()
        server.server_close()
