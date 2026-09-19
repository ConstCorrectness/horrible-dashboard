"""A trace → one trajectory run. Pure: spans in, `TrajectoryWrite` out.

Two attribute vocabularies are in the wild and the frameworks split across them,
so both are read:

- **OTel GenAI semantic conventions** (`gen_ai.*`) — Pydantic AI, Google ADK, raw
  `opentelemetry-sdk` users, and this node's own tracer. The operation is named by
  `gen_ai.operation.name` (`invoke_agent`, `chat`, `execute_tool`, …).
- **OpenInference** (`openinference.span.kind` = `AGENT|CHAIN|LLM|TOOL|…`) —
  LangChain/LangGraph and the OpenAI Agents SDK through Arize's instrumentors.

Older emitters use neither cleanly (ADK's `call_llm` carries a model but no
operation name; early Pydantic AI named spans `running tool`), so classification
falls back on the attributes a span *has*, not only the one that names it.

## One run per trace, keyed by the trace id

`external_id` is always the trace id. Spans arrive child-first and the root last,
so any key derived from "the root span" would change the moment the root arrived
and file a second run beside the first. A trace with two top-level agents becomes
one run whose steps nest under two `agent` observation steps — honest about what
was recorded, and stable across every partial projection of it.

## The shape the trajectories store already has

A tool call and its result are **one** `action` step, as everywhere else in
trajectories. An LLM call is an assistant `message`. Nesting uses `parent_seq`
(the nearest ancestor span that became a step); `round` is the index of the LLM
call a step sits in, which is what the local recorder means by it.

Token totals keep **None ≠ 0**: a trace whose spans report no usage has *unknown*
tokens, never zero. Only LLM-class spans are summed — OpenInference often repeats
the totals on the enclosing CHAIN/AGENT span, and summing both doubles them.
"""

from __future__ import annotations

import json
from typing import Any

from backend.modules.otel.models import STATUS_ERROR, Span
from backend.modules.trajectories.models import StepWrite, TrajectoryWrite

#: Dataset received traces land in when the sender names none.
DEFAULT_DATASET = "otel"
#: Resource attribute a sender sets to choose its dataset (injected into kernels).
DATASET_ATTR = "horrible.dataset"

_MAX_TEXT = 20_000

AGENT, CHAIN, LLM, TOOL, OTHER = "agent", "chain", "llm", "tool", "other"

_GENAI_OPS = {
    "invoke_agent": AGENT,
    "create_agent": AGENT,
    "chat": LLM,
    "text_completion": LLM,
    "generate_content": LLM,
    "execute_tool": TOOL,
}
_OI_KINDS = {"AGENT": AGENT, "CHAIN": CHAIN, "LLM": LLM, "TOOL": TOOL}


# --- classification -------------------------------------------------------


def classify(span: Span) -> str:
    a = span.attrs
    op = a.get("gen_ai.operation.name")
    if isinstance(op, str) and op in _GENAI_OPS:
        return _GENAI_OPS[op]
    oi = a.get("openinference.span.kind")
    if isinstance(oi, str) and oi.upper() in _OI_KINDS:
        return _OI_KINDS[oi.upper()]
    if isinstance(oi, str):
        return OTHER  # RETRIEVER/EMBEDDING/RERANKER/GUARDRAIL/EVALUATOR
    if "gen_ai.tool.name" in a or span.name.startswith(
        ("execute_tool", "running tool")
    ):
        return TOOL
    if span.name.startswith(("invoke_agent", "agent run")) or "gen_ai.agent.name" in a:
        return AGENT
    if any(
        k in a
        for k in (
            "gen_ai.request.model",
            "gen_ai.response.model",
            "gen_ai.usage.input_tokens",
            "llm.model_name",
        )
    ):
        return LLM
    return OTHER


# --- attribute readers ------------------------------------------------------


def _first(attrs: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def _text_of_parts(message: Any) -> str:
    """Text out of one GenAI-semconv message (`{role, parts: [...]}`) or a plain
    `{role, content}` chat message."""
    if not isinstance(message, dict):
        return str(message) if message is not None else ""
    parts = message.get("parts")
    if isinstance(parts, list):
        texts = [
            str(p.get("content") or p.get("text") or "")
            for p in parts
            if isinstance(p, dict) and p.get("type", "text") == "text"
        ]
        return "".join(texts)
    content = message.get("content")
    if isinstance(content, list):
        return "".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return str(content) if content is not None else ""


def _oi_messages(attrs: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Re-assemble OpenInference's flattened `llm.input_messages.N.message.*`."""
    found: dict[int, dict[str, Any]] = {}
    head = prefix + "."
    for key, value in attrs.items():
        if not key.startswith(head):
            continue
        rest = key[len(head) :].split(".", 2)
        if len(rest) < 3 or rest[1] != "message" or not rest[0].isdigit():
            continue
        found.setdefault(int(rest[0]), {})[rest[2]] = value
    return [found[i] for i in sorted(found)]


def output_text(span: Span) -> str | None:
    a = span.attrs
    msgs = _maybe_json(a.get("gen_ai.output.messages"))
    if isinstance(msgs, list) and msgs:
        text = "".join(_text_of_parts(m) for m in msgs)
        if text:
            return text
    completion = a.get("gen_ai.completion")
    if isinstance(completion, str) and completion:
        return completion
    oi = _oi_messages(a, "llm.output_messages")
    if oi:
        text = "".join(str(m.get("content") or "") for m in oi)
        if text:
            return text
    for event in span.events:
        if event.name in ("gen_ai.choice", "gen_ai.assistant.message"):
            msg = _maybe_json(event.attrs.get("message") or event.attrs.get("content"))
            text = _text_of_parts(msg) if isinstance(msg, dict) else str(msg or "")
            if text:
                return text
    value = a.get("output.value")
    return str(value) if value not in (None, "") else None


def input_messages(span: Span) -> Any:
    a = span.attrs
    msgs = _maybe_json(a.get("gen_ai.input.messages"))
    if isinstance(msgs, list):
        return msgs
    oi = _oi_messages(a, "llm.input_messages")
    if oi:
        return oi
    prompt = a.get("gen_ai.prompt")
    if prompt:
        return _maybe_json(prompt)
    value = a.get("input.value")
    return _maybe_json(value) if value not in (None, "") else None


def _last_user_text(messages: Any) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _text_of_parts(message)
            if text:
                return text
    return None


def tool_name(span: Span) -> str:
    name = _first(span.attrs, "gen_ai.tool.name", "tool.name", "tool_name")
    if name:
        return str(name)
    for prefix in ("execute_tool ", "running tool "):
        if span.name.startswith(prefix):
            return span.name[len(prefix) :].strip()
    return span.name


def tool_args(span: Span) -> Any:
    return _maybe_json(
        _first(
            span.attrs,
            "gen_ai.tool.call.arguments",
            "tool_arguments",
            "gcp.vertex.agent.tool_call_args",
            "tool.parameters",
            "input.value",
        )
    )


def tool_result(span: Span) -> Any:
    return _maybe_json(
        _first(
            span.attrs,
            "gen_ai.tool.call.result",
            "tool_response",
            "gcp.vertex.agent.tool_response",
            "output.value",
        )
    )


def model_of(span: Span) -> str:
    return str(
        _first(
            span.attrs,
            "gen_ai.response.model",
            "gen_ai.request.model",
            "llm.model_name",
        )
        or ""
    )


def tokens_of(span: Span) -> tuple[int | None, int | None]:
    a = span.attrs
    tin = _as_int(
        _first(
            a,
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.prompt_tokens",
            "llm.token_count.prompt",
        )
    )
    tout = _as_int(
        _first(
            a,
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.completion_tokens",
            "llm.token_count.completion",
        )
    )
    return tin, tout


def _error_of(span: Span) -> str | None:
    for event in span.events:
        if event.name == "exception":
            message = event.attrs.get("exception.message") or event.attrs.get(
                "exception.type"
            )
            if message:
                return str(message)
    if span.status_code == STATUS_ERROR:
        return span.status_message or "error"
    return None


def _duration_ms(span: Span) -> int | None:
    if span.start_ns and span.end_ns and span.end_ns >= span.start_ns:
        return (span.end_ns - span.start_ns) // 1_000_000
    return None


def _ts(span: Span) -> float | None:
    return span.start_ns / 1e9 if span.start_ns else None


# --- the projection ---------------------------------------------------------


def _add(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return value if total is None else total + value


def project(spans: list[Span], *, final: bool = False) -> TrajectoryWrite | None:
    """Spans of one trace → one run. `final` marks a trace whose root never
    arrived as finished anyway (the materializer's idle timeout)."""
    if not spans:
        return None
    trace_id = spans[0].trace_id
    by_id = {s.span_id: s for s in spans}
    # end_ns breaks start ties: sequential siblings on a coarse clock (Windows)
    # can share a start tick, and the one that finished first ran first.
    ordered = sorted(spans, key=lambda s: (s.start_ns, s.end_ns, s.span_id))
    classes = {s.span_id: classify(s) for s in spans}

    def ancestors(span: Span) -> list[Span]:
        out: list[Span] = []
        seen = {span.span_id}
        cur = by_id.get(span.parent_span_id)
        while cur is not None and cur.span_id not in seen:
            out.append(cur)
            seen.add(cur.span_id)
            cur = by_id.get(cur.parent_span_id)
        return out

    # The run's anchor: the topmost agent span, else the topmost chain, else the
    # trace root. Its fields describe the run; it is not itself a step.
    def topmost(kind: str) -> Span | None:
        for span in ordered:
            if classes[span.span_id] == kind and not any(
                classes[a.span_id] == kind for a in ancestors(span)
            ):
                return span
        return None

    # Falling back to "the earliest span whose parent we lack" would be wrong: while
    # the root is still in flight that is the first *tool call*, and hiding it as
    # the anchor drops a real step from every partial projection. Only a true
    # root that is not itself content (no parent at all, class OTHER) may anchor.
    true_root = next(
        (s for s in ordered if not s.parent_span_id and classes[s.span_id] == OTHER),
        None,
    )
    anchor_span = topmost(AGENT) or topmost(CHAIN) or true_root
    hidden: set[str] = (
        {anchor_span.span_id} | {a.span_id for a in ancestors(anchor_span)}
        if anchor_span is not None
        else set()
    )
    # Metadata still needs a span to read from when nothing anchors.
    anchor = anchor_span or ordered[0]
    # A trace is finished when its true root (no parent at all) has arrived.
    rooted = any(not s.parent_span_id for s in spans)

    steps: list[StepWrite] = []
    seq_of: dict[str, int] = {}
    round_no = -1
    tokens_in: int | None = None
    tokens_out: int | None = None
    model = ""
    provider = ""
    goal: str | None = None
    llm_calls = 0

    for span in ordered:
        if span.span_id in hidden:
            continue
        cls = classes[span.span_id]
        parent_seq = next(
            (seq_of[a.span_id] for a in ancestors(span) if a.span_id in seq_of), None
        )
        error = _error_of(span)
        common: dict[str, Any] = {
            "seq": len(steps),
            "parent_seq": parent_seq,
            "duration_ms": _duration_ms(span),
            "ts": _ts(span),
            "ok": error is None,
            "error": error,
        }
        if cls == LLM:
            llm_calls += 1
            round_no += 1
            tin, tout = tokens_of(span)
            tokens_in = _add(tokens_in, tin)
            tokens_out = _add(tokens_out, tout)
            model = model or model_of(span)
            provider = provider or str(
                _first(
                    span.attrs,
                    "gen_ai.provider.name",
                    "gen_ai.system",
                    "llm.provider",
                    "llm.system",
                )
                or ""
            )
            messages = input_messages(span)
            if goal is None:
                goal = _last_user_text(messages)
            step = StepWrite(
                kind="message",
                role="assistant",
                round=max(round_no, 0),
                name=model_of(span) or span.name,
                content=_clip(output_text(span)),
                args={"input": messages} if messages is not None else None,
                tokens=tout,
                **common,
            )
        elif cls == TOOL:
            step = StepWrite(
                kind="action",
                role="tool",
                round=max(round_no, 0),
                name=tool_name(span),
                args=tool_args(span),
                result=tool_result(span),
                **common,
            )
        else:
            label = (
                f"agent {_first(span.attrs, 'gen_ai.agent.name') or span.name}"
                if cls == AGENT
                else span.name
            )
            step = StepWrite(
                kind="observation",
                round=max(round_no, 0),
                name=label,
                args={k: v for k, v in span.attrs.items() if not k.startswith("llm.")}
                or None,
                **common,
            )
        seq_of[span.span_id] = step.seq  # type: ignore[assignment]
        steps.append(step)

    a = anchor.attrs
    resource = anchor.resource or next((s.resource for s in spans if s.resource), {})
    service = str(resource.get("service.name") or "")
    agent_name = str(
        _first(a, "gen_ai.agent.name", "agent_name", "agent.name")
        or (anchor.name if classes[anchor.span_id] in (AGENT, CHAIN) else "")
        or service
    )
    if goal is None:
        goal = _last_user_text(input_messages(anchor)) or (
            str(a["input.value"]) if a.get("input.value") else None
        )
    anchor_model = model_of(anchor)
    dataset = next(
        (str(s.resource[DATASET_ATTR]) for s in spans if s.resource.get(DATASET_ATTR)),
        DEFAULT_DATASET,
    )
    ended = rooted or final
    anchor_error = _error_of(anchor) if rooted else None
    starts = [s.start_ns for s in spans if s.start_ns]
    ends = [s.end_ns for s in spans if s.end_ns]
    return TrajectoryWrite(
        dataset_id=dataset,
        source="external",
        external_id=trace_id,
        agent_id=str(_first(a, "gen_ai.agent.id") or ""),
        agent_name=agent_name,
        model=anchor_model or model,
        provider=provider,
        goal=_clip(goal) or "",
        status=("failed" if anchor_error else "complete") if ended else "running",
        rounds=llm_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        started_at=min(starts) / 1e9 if starts else None,
        finished_at=(max(ends) / 1e9 if ends else None) if ended else None,
        error=anchor_error or "",
        meta={
            "otel": {
                "trace_id": trace_id,
                "service": service,
                "spans": len(spans),
                "scopes": sorted({s.scope for s in spans if s.scope}),
                "partial": bool(final and not rooted),
            }
        },
        step_list=steps,
    )
