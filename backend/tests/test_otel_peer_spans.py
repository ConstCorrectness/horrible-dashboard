"""A peer's half of a trace: captured by the callee, returned, accepted, re-exported.

The callee and the caller share one process here (and so one tracer and one span
store), so the "ours vs theirs" collision check is pointed at an explicit set, and
assertions are made on what was re-exported — tagged with the peer — rather than on
store rows the callee's own local export may also have written.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import secrets

import pytest

from backend.modules.otel import destinations, opik, peer_spans, store, tracing
from backend.tests.otel_helpers import captured_spans


@pytest.fixture
def spans(monkeypatch):
    with captured_spans(monkeypatch) as exporter:
        yield exporter


@pytest.fixture(autouse=True)
def _no_collisions(monkeypatch):
    """By default nothing counts as already ours (shared process, see above)."""
    monkeypatch.setattr(peer_spans, "_our_span_ids", lambda trace_id: set())


def _caller_turn() -> str:
    """Open a caller's `ask_peer` tool span and return the traceparent it sends —
    the spans themselves end on exit, which is fine: only the header is needed."""
    with tracing.agent_span(
        turn_id="caller-turn", agent_id="main", agent_name="", model="m", provider="x"
    ):
        with tracing.tool_span("agent.ask_peer", call_id="c1", round_no=0):
            tp = tracing.current_traceparent()
    assert tp
    return tp


def _callee_turn(tp: str, *, label: str = "remote", extra: dict | None = None):
    """Run a callee's turn under `tp`, captured. Returns the bucket."""
    with tracing.remote_parent(tp), tracing.capture() as bucket:
        with tracing.agent_span(
            turn_id=label,
            agent_id="main",
            agent_name="",
            model="m",
            provider="x",
            source="peer",
        ) as agent:
            for key, value in (extra or {}).items():
                agent.set(key, value)
            with tracing.chat_span(
                provider_kind="x", model="m", endpoint="", messages=[], params={}
            ):
                pass
    return bucket


def _decoded(payload: dict) -> list:
    return peer_spans._decode(payload)


# --- capture & collect ------------------------------------------------------------


@pytest.mark.anyio
async def test_capture_isolates_concurrent_requests_in_one_trace(spans) -> None:
    tp = _caller_turn()

    async def one(label: str):
        with tracing.remote_parent(tp), tracing.capture() as bucket:
            with tracing.agent_span(
                turn_id=label, agent_id="main", agent_name="", model="m", provider="x"
            ):
                await asyncio.sleep(0.01)
                with tracing.chat_span(
                    provider_kind="x", model="m", endpoint="", messages=[], params={}
                ):
                    await asyncio.sleep(0.01)
        return bucket

    a, b = await asyncio.gather(one("req-a"), one("req-b"))

    def turns(bucket) -> set:
        return {s.attributes.get("horrible.turn_id") for s in bucket.spans} - {None}

    assert turns(a) == {"req-a"} and turns(b) == {"req-b"}
    assert len(a.spans) == 2 and len(b.spans) == 2


def test_collect_strips_content_and_opik_attributes(spans) -> None:
    tp = _caller_turn()
    bucket = _callee_turn(
        tp,
        extra={
            "gen_ai.input.messages": '[{"role":"user","content":"secret"}]',
            "opik.trace_id": "elsewhere",
            "horrible.kept": "yes",
        },
    )
    payload = peer_spans.collect(bucket)
    assert payload is not None and payload["enc"] == peer_spans.ENCODING
    decoded = _decoded(payload)
    agent = next(s for s in decoded if s.name.startswith("invoke_agent"))
    assert "gen_ai.input.messages" not in agent.attrs
    assert "opik.trace_id" not in agent.attrs
    assert agent.attrs["horrible.kept"] == "yes"


def test_collect_is_bounded_and_marks_truncation(spans) -> None:
    tp = _caller_turn()
    with tracing.remote_parent(tp), tracing.capture() as bucket:
        with tracing.agent_span(
            turn_id="big", agent_id="main", agent_name="", model="m", provider="x"
        ):
            for i in range(40):
                with tracing.tool_span(f"t{i}", call_id=None, round_no=i) as tool:
                    # Random, so gzip cannot shrink it under the budget.
                    tool.set("horrible.blob", secrets.token_hex(1000))
    payload = peer_spans.collect(bucket, limit=8 * 1024)
    assert payload is not None
    assert len(payload["body"]) <= 8 * 1024
    assert payload["truncated"] > 0
    decoded = _decoded(payload)
    # The entry span always survives: it is what the caller hangs the rest on.
    assert any(s.name.startswith("invoke_agent") for s in decoded)
    assert len(decoded) + payload["truncated"] == 41


# --- accept & ingest --------------------------------------------------------------


def test_ingest_keeps_only_our_trace_rooted_at_our_span(spans, monkeypatch) -> None:
    tp = _caller_turn()
    _, trace_hex, parent_hex, _ = tp.split("-")
    bucket = _callee_turn(tp)
    good = _decoded(peer_spans.collect(bucket))
    assert len(good) == 2

    foreign = good[0].model_copy(update={"trace_id": "f" * 32, "span_id": "1" * 16})
    orphan = good[0].model_copy(
        update={"span_id": "2" * 16, "parent_span_id": "3" * 16}
    )
    stale = good[1].model_copy(
        update={
            "span_id": "4" * 16,
            "start_ns": 1,
            "end_ns": 2,
            "parent_span_id": parent_hex,
        }
    )
    kept = peer_spans.accept(
        [*good, foreign, orphan, stale], trace_id=trace_hex, parent_span_id=parent_hex
    )
    assert sorted(s.span_id for s in kept) == sorted(s.span_id for s in good)

    # A returned span may not take the id of one of ours.
    agent = next(s for s in good if s.name.startswith("invoke_agent"))
    monkeypatch.setattr(peer_spans, "_our_span_ids", lambda t: {agent.span_id})
    kept = peer_spans.accept(good, trace_id=trace_hex, parent_span_id=parent_hex)
    # Its child goes too: it no longer hangs off anything we sent.
    assert kept == []


def test_ingest_survives_garbage_and_gzip_bombs(spans) -> None:
    tp = _caller_turn()
    bomb = base64.b64encode(gzip.compress(b"\0" * (peer_spans.MAX_INFLATED + 1024)))
    for payload in (
        None,
        "nope",
        {"enc": "other", "body": "x"},
        {"enc": peer_spans.ENCODING, "body": "!!!not base64!!!"},
        {"enc": peer_spans.ENCODING, "body": base64.b64encode(b"junk").decode()},
        {"enc": peer_spans.ENCODING, "body": bomb.decode()},
    ):
        assert peer_spans.ingest(payload, sent_traceparent=tp, peer="p") == 0
    assert peer_spans.ingest({"enc": "x"}, sent_traceparent=None, peer="p") == 0


def test_ingest_stores_stamps_and_reexports(spans) -> None:
    tp = _caller_turn()
    trace_hex = tp.split("-")[1]
    payload = peer_spans.collect(_callee_turn(tp))
    spans.clear()

    assert peer_spans.ingest(payload, sent_traceparent=tp, peer="peernode12345678") == 2

    stored = [s for s in store.get_trace(trace_hex) if s.origin == "peer"]
    assert len(stored) == 2
    assert all(
        s.resource["horrible.peer.node_id"] == "peernode12345678" for s in stored
    )
    reexported = spans.get_finished_spans()
    assert len(reexported) == 2
    assert all(
        s.resource.attributes["horrible.peer.node_id"] == "peernode12345678"
        for s in reexported
    )
    # Hung off our tool span, as one trace.
    parent_hex = tp.split("-")[2]
    agent = next(s for s in reexported if s.name.startswith("invoke_agent"))
    assert format(agent.parent.span_id, "016x") == parent_hex
    assert format(agent.context.trace_id, "032x") == trace_hex


def test_reexport_skips_destinations_the_peer_already_sent_to(
    spans, monkeypatch
) -> None:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    opik_exporter = InMemorySpanExporter()
    slot = tracing.export_slot("opik")
    slot.inner = SimpleSpanProcessor(opik_exporter)
    monkeypatch.setitem(
        destinations._status,
        "opik",
        destinations.ExportStatus(
            name="opik", detail={"fingerprint": "abcdabcdabcdabcd"}
        ),
    )
    try:
        tp = _caller_turn()
        payload = peer_spans.collect(_callee_turn(tp), exported_to=["abcdabcdabcdabcd"])
        spans.clear()
        opik_exporter.clear()
        assert peer_spans.ingest(payload, sent_traceparent=tp, peer="p") == 2
        assert len(spans.get_finished_spans()) == 2  # the generic slot still gets them
        assert opik_exporter.get_finished_spans() == ()  # Opik already has them
    finally:
        slot.inner = None


def test_a_capture_in_progress_collects_ingested_spans(spans) -> None:
    """A asks B, B asks C: C's spans reach B, and travel on to A in B's answer."""
    tp = _caller_turn()
    payload = peer_spans.collect(_callee_turn(tp))
    with tracing.capture() as outer:
        peer_spans.ingest(payload, sent_traceparent=tp, peer="c", reexport=False)
    assert len(outer.spans) == 2


# --- the callee's own Opik --------------------------------------------------------


def _remote_entry(attrs: dict):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.trace import SpanContext, TraceFlags

    flags = TraceFlags(1)
    return ReadableSpan(
        name="invoke_agent Main",
        context=SpanContext(0xABC, 0x10, is_remote=False, trace_flags=flags),
        parent=SpanContext(0xABC, 0x20, is_remote=True, trace_flags=flags),
        resource=Resource.create({"service.instance.id": "calleenode"}),
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": "main",
            "horrible.source": "peer",
            "horrible.peer_caller": "callernode123",
            **attrs,
        },
        start_time=1,
        end_time=2,
    )


def test_remote_entry_span_is_rerooted_for_an_unshared_project(monkeypatch) -> None:
    monkeypatch.setattr(opik, "_fingerprint", "1111111111111111")
    shaped = opik.opik_span(
        _remote_entry({"horrible.peer_shared_sinks": "2222222222222222"})
    )
    # Its parent is in an Opik project this one will never see: a trace of its own.
    assert shaped.parent is None
    tags = list(shaped.attributes["opik.tags"])
    assert "source:peer" in tags and "caller:callerno" in tags
    meta = json.loads(shaped.attributes["opik.metadata"])
    assert meta["remote_traceparent"] == f"00-{0xABC:032x}-{0x20:016x}-01"
    assert "peer_shared_sinks" not in meta


def test_shared_project_keeps_the_parent(monkeypatch) -> None:
    monkeypatch.setattr(opik, "_fingerprint", "1111111111111111")
    shaped = opik.opik_span(
        _remote_entry({"horrible.peer_shared_sinks": "1111111111111111"})
    )
    assert shaped.parent is not None and shaped.parent.span_id == 0x20
