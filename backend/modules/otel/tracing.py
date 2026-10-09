"""The node's own tracer: GenAI-semconv spans for the built-in agents.

Three span shapes, named per the OTel GenAI semantic conventions so any backend
(Phoenix, Langfuse, Jaeger, Tempo, Honeycomb) renders them as an agent trace:

- `invoke_agent <agent>` around `run_agent_loop` — one per turn, and one per
  delegate sub-turn, parented under the `execute_tool agent.delegate` span that
  spawned it (context propagates through the await, so this needs no plumbing);
- `chat <model>` around every provider call, at the `providers.chat`/`chat_stream`
  chokepoint — so research, games policies, the judge and the voice agent are
  traced too, without touching those callers;
- `execute_tool <name>` around each tool call, including denied, gated and
  simulated ones (a refusal is exactly the kind of thing a trace is read for).

## A private provider, never the global one

`trace.set_tracer_provider` is process-global and first-writer-wins. chromadb and
litellm both carry OTel instrumentation; claiming the global slot would route
*their* spans into our store (or have ours ignored if they got there first). The
tracer here is taken from our own `TracerProvider` instance. The OTel *context*
is shared, which is fine: it only decides parenting.

## Trace id = f(turn_id)

A root turn's trace id comes from `ids.trace_id_for_turn` through a custom
`IdGenerator`, so a span that returns from a user's MCP server carrying our
`traceparent` can be attributed to the turn with no lookup table.

## Content is opt-in, per destination

By default a span carries model, tokens, cost, duration, tool name and outcome —
never the prompt, completion, or tool arguments. `otel.captureContent` adds them,
passed through `trajectories.outbound.redact` (credential-shaped strings masked)
first. That default matches the GenAI conventions' own, and matters because the
exporter can ship spans to a third party.

An export destination can ask for content on its own (the Opik connector's
toggle). Content is then *recorded* whenever anyone wants it (`content_wanted`)
and *removed* on the way to everyone who did not ask: each destination strips
`CONTENT_ATTRS` unless it asked, and the local store strips them unless the global
setting is on — so turning content on for Opik changes nothing anywhere else.

## Export destinations

`export_slot(name)` is one swappable processor per destination. `otel` is the
generic OTLP connector's and exists from the start; others (`opik`) are added to
the provider the first time they are asked for. `TracerProvider` can add a
processor at any time but never remove one, so a slot is emptied, not dropped.

Nothing here may raise into an agent turn. Every helper yields a handle whose
methods swallow their own errors, and a disabled tracer yields a no-op handle.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from contextvars import ContextVar
from typing import Any, Iterator
from urllib.parse import urlparse

from backend.modules.otel import ids

logger = logging.getLogger("otel")

SCOPE = "horrible-dashboard.agent"
_CONTENT_MAX = 16 * 1024

_forced_trace_id: ContextVar[int | None] = ContextVar("otel_forced_trace", default=None)
_lock = threading.Lock()
_provider: Any = None
_export_slot: Any = None
_slot_cls: Any = None
#: Destinations beyond the default `otel` slot, by name.
_slots: dict[str, Any] = {}
#: Which destinations asked for content, registered when each one is configured —
#: an in-memory read, because `_content` runs on every span.
_dest_content: dict[str, bool] = {}

#: The attributes that carry prompts, completions and tool payloads. Everything
#: `_content` produces lands in one of these, so removing them is the whole of
#: "this destination gets no content".
CONTENT_ATTRS = frozenset(
    {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
    }
)


# --- settings ---------------------------------------------------------------


def _setting(key: str, default: bool) -> bool:
    try:
        from backend.modules.settings import get_value

        return bool(get_value(key, default))
    except Exception:  # noqa: BLE001
        return default


def enabled() -> bool:
    return _setting("otel.enabled", True)


def capture_content() -> bool:
    return _setting("otel.captureContent", False)


def set_destination_content(name: str, wanted: bool) -> None:
    """Register whether export destination `name` wants content. Called by the
    destination's own `configure()`; `False` (or detaching) withdraws it."""
    if wanted:
        _dest_content[name] = True
    else:
        _dest_content.pop(name, None)


def content_wanted() -> bool:
    """Whether content is recorded at all: the global setting, or any destination
    that asked for it. Recording is not shipping — see `strip_content`."""
    return capture_content() or any(_dest_content.values())


def strip_content(attrs: Any) -> dict[str, Any]:
    """`attrs` without the content attributes."""
    return {k: v for k, v in (attrs or {}).items() if k not in CONTENT_ATTRS}


# --- provider ---------------------------------------------------------------


def _span_to_model(rs: Any, *, keep_content: bool = True) -> Any:
    from backend.modules.otel.models import Span, SpanEvent

    ctx = rs.get_span_context()
    raw = rs.attributes or {}
    if not keep_content:
        raw = strip_content(raw)
    parent = rs.parent.span_id if rs.parent is not None else 0
    return Span(
        trace_id=format(ctx.trace_id, "032x"),
        span_id=format(ctx.span_id, "016x"),
        parent_span_id=format(parent, "016x") if parent else "",
        name=rs.name,
        # The SDK's SpanKind starts INTERNAL at 0; OTLP's starts it at 1.
        kind=int(rs.kind.value) + 1,
        start_ns=int(rs.start_time or 0),
        end_ns=int(rs.end_time or 0),
        status_code=int(rs.status.status_code.value),
        status_message=rs.status.description or "",
        attrs={k: (list(v) if isinstance(v, tuple) else v) for k, v in raw.items()},
        events=[
            SpanEvent(
                name=e.name,
                time_ns=int(e.timestamp or 0),
                attrs={
                    k: (list(v) if isinstance(v, tuple) else v)
                    for k, v in (e.attributes or {}).items()
                },
            )
            for e in rs.events
        ],
        resource=dict(rs.resource.attributes) if rs.resource else {},
        scope=rs.instrumentation_scope.name if rs.instrumentation_scope else "",
        origin="local",
    )


def _make_provider() -> Any:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )
    from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

    class TurnIdGenerator(RandomIdGenerator):
        def generate_trace_id(self) -> int:
            forced = _forced_trace_id.get()
            return forced if forced else super().generate_trace_id()

    class LocalStoreExporter(SpanExporter):
        """Writes finished spans to `otel_spans`, batched by the SDK's own
        `BatchSpanProcessor` thread — sqlite stays off the turn's hot path.

        Content is kept only under the global setting: a destination that asked
        for content does not thereby put it in this node's database."""

        def export(self, spans: Any) -> SpanExportResult:
            try:
                from backend.modules.otel import store

                keep = capture_content()
                store.insert_spans(
                    [_span_to_model(s, keep_content=keep) for s in spans],
                    origin="local",
                )
            except Exception:  # noqa: BLE001
                logger.debug("otel: local store export failed", exc_info=True)
                return SpanExportResult.FAILURE
            return SpanExportResult.SUCCESS

    class SwitchableProcessor(SpanProcessor):
        """A slot the external exporter can be swapped into at runtime —
        `TracerProvider` can add processors but never remove one."""

        def __init__(self) -> None:
            self.inner: Any = None

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            inner = self.inner
            if inner is not None:
                inner.on_start(span, parent_context=parent_context)

        def on_end(self, span: Any) -> None:
            inner = self.inner
            if inner is not None:
                inner.on_end(span)

        def shutdown(self) -> None:
            inner, self.inner = self.inner, None
            if inner is not None:
                inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            inner = self.inner
            return inner.force_flush(timeout_millis) if inner is not None else True

    attrs: dict[str, Any] = {"service.name": "horrible-dashboard"}
    try:
        from backend.version import app_version

        attrs["service.version"] = app_version()
    except Exception:  # noqa: BLE001
        pass
    # Which node made a span, in the semconv's own slot. It survives an OTLP round
    # trip, so a span a peer returns still says where it ran.
    try:
        from backend.modules.network.identity import load_identity

        attrs["service.instance.id"] = load_identity().node_id
    except Exception:  # noqa: BLE001
        pass
    provider = TracerProvider(
        resource=Resource.create(attrs), id_generator=TurnIdGenerator()
    )
    provider.add_span_processor(
        BatchSpanProcessor(LocalStoreExporter(), schedule_delay_millis=1000)
    )

    class CaptureProcessor(SpanProcessor):
        """Hands each finished span to the `capture()` bucket that was active when
        it *started* — a peer request's spans, returned to the peer that asked.

        Keyed by span id at start, because a span ends in whatever context its
        `with` block exits in, and two requests from one caller can run at once in
        the same trace (a research wave asking one peer two questions)."""

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            bucket = _capture.get()
            if bucket is not None:
                with _capture_lock:
                    _capturing[span.context.span_id] = bucket

        def on_end(self, span: Any) -> None:
            with _capture_lock:
                bucket = _capturing.pop(span.context.span_id, None)
            if bucket is not None:
                bucket.add(span)

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    provider.add_span_processor(CaptureProcessor())
    slot = SwitchableProcessor()
    provider.add_span_processor(slot)
    return provider, slot, SwitchableProcessor


def _get() -> tuple[Any, Any]:
    global _provider, _export_slot, _slot_cls
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider, _export_slot, _slot_cls = _make_provider()
    return _provider, _export_slot


def tracer() -> Any:
    provider, _ = _get()
    return provider.get_tracer(SCOPE)


def export_slot(name: str = "otel") -> Any:
    """The swappable processor for export destination `name`, made on first use."""
    provider, default = _get()
    if name == "otel":
        return default
    with _lock:
        slot = _slots.get(name)
        if slot is None:
            slot = _slot_cls()
            provider.add_span_processor(slot)
            _slots[name] = slot
    return slot


def export_slots() -> dict[str, Any]:
    """Every destination slot that exists, by name."""
    _, default = _get()
    with _lock:
        return {"otel": default, **_slots}


def resource() -> Any:
    """The resource every span this node makes carries."""
    return _get()[0].resource


def force_flush(timeout_ms: int = 5000) -> None:
    if _provider is not None:
        try:
            _provider.force_flush(timeout_ms)
        except Exception:  # noqa: BLE001
            logger.debug("otel: flush failed", exc_info=True)


def shutdown() -> None:
    global _provider, _export_slot
    with _lock:
        provider, _provider, _export_slot = _provider, None, None
        _slots.clear()
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("otel: shutdown failed", exc_info=True)


# --- capture ------------------------------------------------------------------

#: The most spans one capture keeps. A peer request that ran longer than this has
#: its earliest spans returned and the rest dropped — and says so (`truncated`).
CAPTURE_LIMIT = 512


class CaptureBucket:
    """The finished spans of one piece of work, in end order."""

    def __init__(self, limit: int = CAPTURE_LIMIT) -> None:
        self.spans: list[Any] = []
        self.dropped = 0
        self._limit = limit
        self._lock = threading.Lock()

    def add(self, span: Any) -> None:
        with self._lock:
            if len(self.spans) < self._limit:
                self.spans.append(span)
            else:
                self.dropped += 1

    def extend(self, spans: Any) -> None:
        for span in spans:
            self.add(span)


_capture: ContextVar[CaptureBucket | None] = ContextVar("otel_capture", default=None)
_capture_lock = threading.Lock()
_capturing: dict[int, CaptureBucket] = {}


@contextlib.contextmanager
def capture() -> Iterator[CaptureBucket]:
    """Collect every span started inside — including in tasks spawned inside,
    which inherit the context — into one bucket. Used by the callee of a peer
    agent request to send that request's spans back to the caller."""
    bucket = CaptureBucket()
    token = _capture.set(bucket)
    try:
        yield bucket
    finally:
        _capture.reset(token)
        # A span still open when the work returned (a leaked task) will never
        # reach this bucket; drop its registration rather than hold it forever.
        with _capture_lock:
            for span_id in [k for k, v in _capturing.items() if v is bucket]:
                del _capturing[span_id]


def active_capture() -> CaptureBucket | None:
    """The bucket of the capture this code runs inside, if any."""
    return _capture.get()


def current_traceparent() -> str | None:
    """W3C `traceparent` for the active span, for propagation (MCP, peers)."""
    if not enabled():
        return None
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return None
        return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
    except Exception:  # noqa: BLE001
        return None


def _is_private_host(host: str | None) -> bool:
    """Whether a target is somewhere trace context is welcome: this machine, or a
    private network. A hostname that is not a literal address is treated as public —
    resolving it here would put a DNS lookup on every outbound request."""
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain"):
        return True
    import ipaddress

    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None) or addr
    return bool(mapped.is_loopback or mapped.is_private or mapped.is_link_local)


def inject_traceparent(request: Any) -> None:
    """Stamp an outbound httpx request with W3C trace context, when asked.

    Off by default, and **never sent off the machine or off the LAN**: a hosted
    provider has no use for our trace id, and a header is a thing you have sent
    whether or not the far side reads it. Called from `telemetry/instrument.py`'s
    request hook, which is the one seam every instrumented client already passes.
    """
    try:
        if not enabled() or not _setting("otel.propagateHttp", False):
            return
        if "traceparent" in request.headers:
            return
        if not _is_private_host(request.url.host):
            return
        header = current_traceparent()
        if header:
            request.headers["traceparent"] = header
    except Exception:  # noqa: BLE001 — never fail a request over a debug header
        logger.debug("otel: traceparent injection failed", exc_info=True)


@contextlib.contextmanager
def remote_parent(traceparent: str | None) -> Iterator[None]:
    """Make a received `traceparent` the parent of spans started inside."""
    if not traceparent or not enabled():
        yield
        return
    try:
        from opentelemetry import context
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        ctx = TraceContextTextMapPropagator().extract({"traceparent": traceparent})
        token = context.attach(ctx)
    except Exception:  # noqa: BLE001
        yield
        return
    try:
        yield
    finally:
        context.detach(token)


# --- content ----------------------------------------------------------------


def _content(value: Any) -> str | None:
    """Redacted, bounded JSON for a content attribute — or None when nobody wants
    content, which is the default."""
    if not content_wanted():
        return None
    try:
        from backend.modules.trajectories.outbound import redact

        text = json.dumps(redact(value), default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return None
    return text if len(text) <= _CONTENT_MAX else text[:_CONTENT_MAX] + "…"


def _set(span: Any, key: str, value: Any) -> None:
    if value is None or span is None:
        return
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001
        pass


# --- handles ----------------------------------------------------------------


class _Handle:
    def __init__(self, span: Any) -> None:
        self.span = span

    def set(self, key: str, value: Any) -> None:
        _set(self.span, key, value)

    def fail(self, exc: BaseException | str) -> None:
        if self.span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            if isinstance(exc, BaseException):
                self.span.record_exception(exc)
                message = f"{type(exc).__name__}: {exc}"
            else:
                message = exc
            self.span.set_status(Status(StatusCode.ERROR, message[:500]))
        except Exception:  # noqa: BLE001
            pass

    def ok(self) -> None:
        if self.span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            self.span.set_status(Status(StatusCode.OK))
        except Exception:  # noqa: BLE001
            pass


class ToolHandle(_Handle):
    def finish(self, result: Any) -> None:
        """Status from the result's shape: `{"error": …}` is how every dispatch
        path in the orchestrator reports a failed, denied or gated call."""
        if self.span is None:
            return
        error = result.get("error") if isinstance(result, dict) else None
        if error:
            text = str(error)
            _set(
                self.span, "horrible.tool.gated", "denied by permission policy" in text
            )
            self.fail(text)
        else:
            self.ok()
        _set(self.span, "gen_ai.tool.call.result", _content(result))


class ChatHandle(_Handle):
    def finish(self, result: Any, *, provider_kind: str, model: str) -> None:
        if self.span is None:
            return
        usage = getattr(result, "usage", None)
        # Only what the provider reported: an absent attribute is "unmeasured",
        # which a 0 would silently turn into "free".
        _set(self.span, "gen_ai.usage.input_tokens", getattr(usage, "tokens_in", None))
        _set(
            self.span, "gen_ai.usage.output_tokens", getattr(usage, "tokens_out", None)
        )
        _set(
            self.span,
            "horrible.usage.cached_input_tokens",
            getattr(usage, "cached_in", None),
        )
        try:
            from backend.modules.agent import cost

            _set(
                self.span,
                "horrible.cost_usd",
                cost.resolve(usage, model=model, provider_kind=provider_kind),
            )
        except Exception:  # noqa: BLE001
            pass
        calls = getattr(result, "tool_calls", None) or []
        _set(self.span, "horrible.tool_calls", len(calls))
        _set(
            self.span,
            "gen_ai.response.finish_reasons",
            ["tool_calls"] if calls else ["stop"],
        )
        _set(
            self.span,
            "gen_ai.output.messages",
            _content([getattr(result, "assistant_message", None)]),
        )
        self.ok()


class AgentHandle(_Handle):
    def io(self, *, prompt: Any = None, answer: Any = None) -> None:
        """The turn's own input and output: the user's last message and the final
        answer. Content, so recorded only when someone wants it — and it is what an
        agent-trace UI (Opik) shows as the trace's input and output."""
        if self.span is None:
            return
        if prompt is not None:
            _set(
                self.span,
                "gen_ai.input.messages",
                _content([{"role": "user", "content": prompt}]),
            )
        if answer is not None:
            _set(
                self.span,
                "gen_ai.output.messages",
                _content([{"role": "assistant", "content": answer}]),
            )


#: Labels for the spans a piece of work starts on its own (see `labels`).
_labels: ContextVar[dict[str, str]] = ContextVar("otel_labels", default={})


@contextlib.contextmanager
def labels(**values: Any) -> Iterator[None]:
    """Stamp `horrible.<key>` on every **entry** span started inside: an
    `invoke_agent` or `chat` span with no parent, or whose parent arrived from
    another node. Spans nested under one of ours inherit nothing — the entry span
    already says where the work came from, and repeating it on every child is
    only noise in each one.

    Used for where work came from (`source`), and by whom it was asked
    (`requested_by`, `peer_caller`) — facts the agent loop itself cannot know."""
    merged = {
        **_labels.get(),
        **{k: str(v) for k, v in values.items() if v is not None},
    }
    token = _labels.set(merged)
    try:
        yield
    finally:
        _labels.reset(token)


def _is_entry() -> bool:
    """Whether a span started now would begin this node's part of a trace."""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        return not ctx.is_valid or bool(ctx.is_remote)
    except Exception:  # noqa: BLE001
        return True


def _entry_labels() -> dict[str, str]:
    return {f"horrible.{k}": v for k, v in _labels.get().items()} if _is_entry() else {}


_NOOP_AGENT = AgentHandle(None)


@contextlib.contextmanager
def agent_span(
    *,
    turn_id: str,
    agent_id: str,
    agent_name: str,
    model: str,
    provider: str,
    parent_turn_id: str | None = None,
    source: str | None = None,
    conversation_id: str | None = None,
) -> Iterator[AgentHandle]:
    """One agent turn. `source` says what started it (chat, delegate, fork, peer,
    games, flow, evals…); `conversation_id` is the chat session it belongs to,
    which agent-trace UIs group as a thread."""
    if not enabled():
        yield _NOOP_AGENT
        return
    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind

        from backend.modules.otel import materialize

        parent_valid = trace.get_current_span().get_span_context().is_valid
        forced = (
            None if parent_valid else _forced_trace_id.set(ids.trace_id_int(turn_id))
        )
        extra = _entry_labels()
        if source:
            extra["horrible.source"] = source
        if conversation_id:
            extra["gen_ai.conversation.id"] = conversation_id
        try:
            cm = tracer().start_as_current_span(
                f"invoke_agent {agent_name or agent_id}",
                kind=SpanKind.INTERNAL,
                attributes={
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.id": agent_id,
                    "gen_ai.agent.name": agent_name or agent_id,
                    "gen_ai.request.model": model,
                    "gen_ai.provider.name": provider,
                    "horrible.turn_id": turn_id,
                    **(
                        {"horrible.parent_turn_id": parent_turn_id}
                        if parent_turn_id
                        else {}
                    ),
                    **extra,
                },
                record_exception=False,
                set_status_on_exception=False,
            )
            span = cm.__enter__()
        finally:
            if forced is not None:
                _forced_trace_id.reset(forced)
        materialize.register_local(format(span.get_span_context().trace_id, "032x"))
    except Exception:  # noqa: BLE001
        logger.debug("otel: agent span failed to start", exc_info=True)
        yield _NOOP_AGENT
        return
    handle = AgentHandle(span)
    try:
        yield handle
    except BaseException as exc:
        # CancelledError is how a user's Stop arrives; it is an outcome, not a bug.
        handle.fail(exc)
        raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


@contextlib.contextmanager
def tool_span(
    name: str, *, call_id: str | None, round_no: int, args: Any = None
) -> Iterator[ToolHandle]:
    if not enabled():
        yield ToolHandle(None)
        return
    try:
        cm = tracer().start_as_current_span(
            f"execute_tool {name}",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.type": "function",
                "horrible.round": round_no,
                **({"gen_ai.tool.call.id": call_id} if call_id else {}),
            },
            record_exception=False,
            set_status_on_exception=False,
        )
        span = cm.__enter__()
        _set(span, "gen_ai.tool.call.arguments", _content(args))
    except Exception:  # noqa: BLE001
        yield ToolHandle(None)
        return
    handle = ToolHandle(span)
    try:
        yield handle
    except BaseException as exc:
        handle.fail(exc)
        raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


@contextlib.contextmanager
def chat_span(
    *,
    provider_kind: str,
    model: str,
    endpoint: str,
    messages: Any,
    params: dict[str, Any],
) -> Iterator[ChatHandle]:
    if not enabled():
        yield ChatHandle(None)
        return
    try:
        from opentelemetry.trace import SpanKind

        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider_kind,
            "gen_ai.request.model": model,
        }
        for key, attr in (
            ("temperature", "gen_ai.request.temperature"),
            ("max_tokens", "gen_ai.request.max_tokens"),
            ("top_p", "gen_ai.request.top_p"),
        ):
            if params.get(key) is not None:
                attributes[attr] = params[key]
        host = urlparse(endpoint or "").hostname
        if host:
            attributes["server.address"] = host
        if params.get("tool_choice"):
            attributes["horrible.tool_choice"] = str(params["tool_choice"])
        # A model call with no agent around it (research, the judge) is its own
        # entry point, and the labels are how it says what it was for.
        attributes.update(_entry_labels())
        cm = tracer().start_as_current_span(
            f"chat {model}",
            kind=SpanKind.CLIENT,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        )
        span = cm.__enter__()
        _set(span, "gen_ai.input.messages", _content(messages))
    except Exception:  # noqa: BLE001
        yield ChatHandle(None)
        return
    handle = ChatHandle(span)
    try:
        yield handle
    except BaseException as exc:
        handle.fail(exc)
        raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def traced_chat(fn: Any) -> Any:
    """Decorator for `providers.chat`/`chat_stream`: one `chat` span per call.

    A decorator rather than edits at the call sites because the call sites are
    the point — nine modules call the provider directly, and each would be a place
    to forget."""
    import functools

    @functools.wraps(fn)
    async def wrapper(
        client: Any,
        info: Any,
        endpoint: str,
        model: str,
        messages: Any,
        tools: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        kind = str(getattr(info, "kind", ""))
        with chat_span(
            provider_kind=kind,
            model=model,
            endpoint=endpoint,
            messages=messages,
            params=kwargs,
        ) as span:
            result = await fn(
                client, info, endpoint, model, messages, tools, *args, **kwargs
            )
            span.finish(result, provider_kind=kind, model=model)
            return result

    return wrapper
