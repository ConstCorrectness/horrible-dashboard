"""The context inspector's record: every provider call, as the provider received it.

`recorder.capture_round` snapshots the context as the *harness* assembled it, one
labelled block per message the orchestrator built. That is the right record for
"where did these tokens come from" and the wrong one for "what did the model see":

- `providers.normalize_system_messages` joins the whole system tier into **one**
  message and turns a mid-conversation system nudge into a **`user`** message, after
  the round snapshot is taken. The blocks say five system messages; the model got one.
- Blocks are clipped for transport, assistant `tool_calls` are folded into the text,
  and `tool_call_id` is dropped.
- The forced-tool retry is a second provider call inside one round. `capture_round`
  runs once per round, so that call was never recorded anywhere.

So this hooks the provider call itself (`providers.chat` / `chat_stream`): `begin`
after normalization, `note_payload` from each dialect once its body is final, and
`finish` with the response. The turn and round come from the telemetry contextvar
(`telemetry/turn.py`), so no provider signature had to learn what a turn is.
Outside an agent turn nothing is recorded.

Provenance is not lost by recording the literal request: `normalize_system_messages_
mapped` reports which input message each stretch of each sent message came from,
and `recorder.kinds_for` names those inputs, so the joined system message carries
segments — "characters 4012–9876 were the skill catalog".

## Observation must not break the thing it observes

Every entry point swallows and logs, and the provider-side wrappers swallow again.

## Stored verbatim

Bodies are not redacted: credentials travel in headers, which are never recorded
here, and `note_payload` drops the auth kwargs litellm takes in-band. What *is* in
the body — tool results, editor buffers — is the thing being inspected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from backend.modules.interpretability.models import (
    RequestDetail,
    RequestMessage,
    RequestSegment,
    RequestSummary,
)

logger = logging.getLogger(__name__)

#: Request-body keys that carry credentials rather than content. litellm takes the
#: key as a kwarg, so it would otherwise land in `params`.
_SECRET_PARAMS = frozenset(
    {"api_key", "apikey", "authorization", "headers", "extra_headers", "api_base"}
)

#: Prune the durable store once per this many finished turns. `agent_turns` was
#: never pruned before; its cap is now enforced from here, where the turn ends.
_PRUNE_EVERY = 200


@dataclass
class _Pending:
    id: str
    turn_id: str
    round: int
    attempt: int
    ts: float
    started: float
    row: dict[str, Any]
    sent: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    token: Token[Any] | None = None
    #: The body as the dialect will send it, when that differs from `sent` (a strict
    #: provider's sanitized tool names).
    wire_messages: list[dict[str, Any]] | None = None
    wire_tools: list[dict[str, Any]] | None = None
    params: dict[str, Any] = field(default_factory=dict)
    est_tokens: int = 0


_current: ContextVar[_Pending | None] = ContextVar("ctx_request", default=None)
#: Calls made so far per (turn, round). Two calls in one round is the forced-tool
#: retry; more is a rate-limit retry of one of them.
_attempts: dict[tuple[str, int], int] = {}
_finished_turns = 0


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def body_hash(value: Any) -> str:
    return hashlib.sha1(_canonical(value).encode("utf-8", "replace")).hexdigest()[:20]


def _bodies_for(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]], counter: Any
) -> tuple[list[str], str, list[tuple[str, str, str, int, int]], bool, int]:
    """Hash every message and the tool list; tokenize only bodies not stored yet.

    Returns `(message hashes, tools hash, new body rows, exact, estimated tokens)`.
    The estimate is summed per *occurrence*: two identical tool results in one
    request are both in the context.
    """
    from backend.modules.interpretability import recorder, store
    from backend.modules.interpretability.tokenizer import estimate

    message_hashes = [body_hash(m) for m in messages]
    tools_hash = body_hash(tools) if tools else ""
    tokens_by_hash = store.body_tokens(sorted({*message_hashes, tools_hash} - {""}))
    rows: list[tuple[str, str, str, int, int]] = []
    for h, msg in zip(message_hashes, messages):
        if h in tokens_by_hash:
            continue
        text = recorder.message_text(msg)
        tokens = counter.count(text) if counter is not None else estimate(text)
        tokens_by_hash[h] = tokens
        rows.append((h, "message", _canonical(msg), len(text), tokens))
    if tools_hash and tools_hash not in tokens_by_hash:
        tokens = (
            counter.count_json(tools)
            if counter is not None
            else estimate(_canonical(tools))
        )
        tokens_by_hash[tools_hash] = tokens
        rows.append(
            (tools_hash, "tools", _canonical(tools), len(_canonical(tools)), tokens)
        )
    exact = bool(counter is not None and getattr(counter, "exact", False))
    est = sum(tokens_by_hash.get(h, 0) for h in [*message_hashes, tools_hash] if h)
    return message_hashes, tools_hash, rows, exact, est


def _spans(
    original: list[dict[str, Any]],
    sent: list[dict[str, Any]],
    spans: list[list[tuple[int, int, int]]],
    turn_id: str,
) -> list[dict[str, Any]]:
    """Per sent message: its provenance segments and, when the wire changed its
    role, the role the orchestrator gave it."""
    from backend.modules.interpretability import recorder

    kinds, names = recorder.kinds_for(original, turn_id)
    out: list[dict[str, Any]] = []
    for msg, segs in zip(sent, spans):
        segments = []
        from_role = None
        for src, start, end in segs:
            kind = kinds[src] if src < len(kinds) else "unknown"
            segments.append(
                {
                    "src": src,
                    "start": start,
                    "end": end,
                    "kind": kind,
                    "label": recorder.label_for(kind),
                    "toolName": names[src] if src < len(names) else None,
                }
            )
            orig_role = original[src].get("role") if src < len(original) else None
            if orig_role and orig_role != msg.get("role"):
                from_role = str(orig_role)
        out.append({"segments": segments, "fromRole": from_role})
    return out


async def begin(
    *,
    original: list[dict[str, Any]],
    sent: list[dict[str, Any]],
    spans: list[list[tuple[int, int, int]]],
    tools: list[dict[str, Any]],
    dialect: str,
    provider: str,
    model: str,
    params: dict[str, Any],
) -> _Pending | None:
    """Record a provider call as it goes out. None outside an agent turn."""
    try:
        from backend.modules.telemetry import turn as telemetry_turn

        mark = telemetry_turn.current()
        if mark is None:
            return None
        turn_id, round_no = mark
        from backend.modules.interpretability import recorder, store

        attempt = _attempts.get((turn_id, round_no), 0)
        _attempts[(turn_id, round_no)] = attempt + 1

        counter = recorder.turn_counter(turn_id)
        message_hashes, tools_hash, bodies, exact, est = _bodies_for(
            sent, tools, counter
        )
        now = time.time()
        pending = _Pending(
            id=uuid.uuid4().hex[:16],
            turn_id=turn_id,
            round=round_no,
            attempt=attempt,
            ts=now,
            started=time.monotonic(),
            row={},
            sent=sent,
            tools=tools,
            params=dict(params),
            est_tokens=est,
        )
        pending.row = {
            "id": pending.id,
            "turn_id": turn_id,
            "round": round_no,
            "attempt": attempt,
            "ts": now,
            "dialect": dialect,
            "provider": provider,
            "model": model,
            "status": "pending",
            "exact": int(exact),
            "params": _canonical(pending.params),
            "tools_hash": tools_hash,
            "message_hashes": json.dumps(message_hashes),
            "spans": json.dumps(_spans(original, sent, spans, turn_id)),
            "response": None,
            "usage": None,
            "duration_ms": None,
            "error": None,
        }
        store.save_request(pending.row, bodies)
        pending.token = _current.set(pending)
        await _push(pending)
        return pending
    except Exception:
        logger.exception("interpretability: request capture failed")
        return None


def note_payload(payload: dict[str, Any]) -> None:
    """The body a dialect is about to send. Called again when a dialect retries
    with a different body, so the last call is the body the server accepted."""
    pending = _current.get()
    if pending is None:
        return
    try:
        pending.params = {
            k: v
            for k, v in payload.items()
            if k not in ("messages", "tools") and k.lower() not in _SECRET_PARAMS
        }
        messages = payload.get("messages")
        if isinstance(messages, list) and messages is not pending.sent:
            pending.wire_messages = messages
        tools = payload.get("tools")
        if isinstance(tools, list) and tools is not pending.tools:
            pending.wire_tools = tools
    except Exception:
        logger.exception("interpretability: note_payload failed")


async def finish(
    pending: _Pending,
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """Record what came back (or that nothing did) and seal the request."""
    try:
        from backend.modules.interpretability import recorder, store

        if pending.token is not None:
            try:
                _current.reset(pending.token)
            except ValueError:
                # Reset from a different context (should not happen; harmless).
                _current.set(None)
        row = pending.row
        bodies: list[tuple[str, str, str, int, int]] = []
        if pending.wire_messages is not None or pending.wire_tools is not None:
            # The wire differed from the normalized list (sanitized tool names).
            # Re-address it: the literal request is what went out.
            messages = (
                pending.wire_messages
                if pending.wire_messages is not None
                else pending.sent
            )
            tools = (
                pending.wire_tools if pending.wire_tools is not None else pending.tools
            )
            hashes, tools_hash, bodies, _exact, pending.est_tokens = _bodies_for(
                messages, tools, recorder.turn_counter(pending.turn_id)
            )
            row["message_hashes"] = json.dumps(hashes)
            row["tools_hash"] = tools_hash
        row["params"] = _canonical(pending.params)
        row["duration_ms"] = int((time.monotonic() - pending.started) * 1000)
        if error is not None:
            row["status"] = "error"
            row["error"] = f"{type(error).__name__}: {error}"[:2000]
        else:
            row["status"] = "ok"
            if result is not None:
                row["response"] = _canonical(getattr(result, "assistant_message", None))
                usage = getattr(result, "usage", None)
                if usage is not None:
                    row["usage"] = _canonical(
                        {
                            "tokensIn": usage.tokens_in,
                            "tokensOut": usage.tokens_out,
                            "cachedIn": usage.cached_in,
                            "costUsd": usage.cost_usd,
                        }
                    )
        store.save_request(row, bodies)
        # A cancelled call is still recorded, but the socket is not written to
        # while the task unwinds.
        import asyncio

        if not isinstance(error, asyncio.CancelledError):
            await _push(pending)
    except Exception:
        logger.exception("interpretability: request finish failed")


def forget_turn(turn_id: str) -> None:
    """Drop per-turn counters when a turn ends, and prune periodically."""
    global _finished_turns
    for key in [k for k in _attempts if k[0] == turn_id]:
        _attempts.pop(key, None)
    _finished_turns += 1
    if _finished_turns % _PRUNE_EVERY == 0:
        from backend.modules.interpretability import store

        store.prune()


# ── reading back ─────────────────────────────────────────────────────────────


def _usage(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row["usage"]) if row.get("usage") else {}
    except ValueError:
        return {}


def _response(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        value = json.loads(row["response"]) if row.get("response") else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _call_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    function = call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(call.get("name") or "")


def _preview(response: dict[str, Any] | None) -> str:
    content = (response or {}).get("content")
    return content.strip()[:240] if isinstance(content, str) else ""


def summary(row: dict[str, Any], est_tokens: int | None = None) -> RequestSummary:
    usage = _usage(row)
    response = _response(row)
    calls = (response or {}).get("tool_calls") or []
    return RequestSummary(
        id=row["id"],
        turnId=row["turn_id"],
        round=row["round"],
        attempt=row["attempt"],
        ts=row["ts"],
        dialect=row["dialect"],
        provider=row["provider"],
        model=row["model"],
        status=row["status"],
        messageCount=len(json.loads(row["message_hashes"] or "[]")),
        estTokens=est_tokens if est_tokens is not None else row.get("est_tokens", 0),
        exact=bool(row["exact"]),
        promptTokens=usage.get("tokensIn"),
        completionTokens=usage.get("tokensOut"),
        toolCalls=len(calls),
        toolNames=[_call_name(c) for c in calls],
        responsePreview=_preview(response),
        durationMs=row["duration_ms"],
        error=row["error"],
    )


def detail(row: dict[str, Any], bodies: dict[str, Any]) -> RequestDetail:
    hashes: list[str] = json.loads(row["message_hashes"] or "[]")
    spans: list[dict[str, Any]] = json.loads(row["spans"] or "[]")
    messages: list[RequestMessage] = []
    for i, h in enumerate(hashes):
        b = bodies.get(h)
        body = json.loads(b["body"]) if b is not None else {}
        span = spans[i] if i < len(spans) else {}
        messages.append(
            RequestMessage(
                index=i,
                role=str(body.get("role") or ""),
                hash=h,
                body=body,
                tokens=b["tokens"] if b is not None else 0,
                chars=b["chars"] if b is not None else 0,
                segments=[RequestSegment(**s) for s in span.get("segments", [])],
                fromRole=span.get("fromRole"),
            )
        )
    tools_body = bodies.get(row["tools_hash"]) if row["tools_hash"] else None
    tools = json.loads(tools_body["body"]) if tools_body is not None else []
    tools_tokens = tools_body["tokens"] if tools_body is not None else 0
    base = summary(row, sum(m.tokens for m in messages) + tools_tokens)
    return RequestDetail(
        **base.model_dump(),
        messages=messages,
        tools=tools,
        toolsHash=row["tools_hash"],
        toolsTokens=tools_tokens,
        params=json.loads(row["params"] or "{}"),
        response=_response(row),
    )


def _context_length(turn_id: str) -> int | None:
    from backend.modules.interpretability import recorder, store

    turn = recorder.get_turn(turn_id) or store.get_turn(turn_id)
    return turn.modelContextLength if turn is not None else None


def list_for_turn(turn_id: str) -> list[RequestSummary]:
    """A turn's provider calls in order — recorded, or rebuilt when it predates
    literal capture."""
    from backend.modules.interpretability import store

    rows = store.list_requests(turn_id)
    if rows:
        return [summary(r) for r in rows]
    return [
        RequestSummary(**d.model_dump(include=set(RequestSummary.model_fields)))
        for d in reconstruct(turn_id)
    ]


def get(request_id: str) -> RequestDetail | None:
    from backend.modules.interpretability import store

    if request_id.startswith(_RECON_PREFIX):
        turn_id, _, round_text = request_id[len(_RECON_PREFIX) :].rpartition(":")
        for d in reconstruct(turn_id):
            if str(d.round) == round_text:
                return d
        return None
    found = store.get_request(request_id)
    if found is None:
        return None
    row, bodies = found
    out = detail(row, bodies)
    out.contextLength = _context_length(row["turn_id"])
    return out


# ── reconstruction, for turns recorded before literal capture ───────────────

#: Reconstructed requests are addressed `r:<turn id>:<round>`; recorded ones by a
#: random hex id, which never starts with `r:`.
_RECON_PREFIX = "r:"


def reconstruct(turn_id: str) -> list[RequestDetail]:
    """Approximate a turn's requests from its stored `RoundSnapshot`s.

    The snapshot clipped long blocks, folded tool calls into text and recorded
    no tool schemas, so this is a best effort and says exactly where it falls
    short: `agentpedia.rebuild` undoes the fold and pairs tool results to calls,
    and the provider's own `normalize_system_messages_mapped` then flattens the
    system tier the way the wire would have. A forced-tool retry was never
    snapshotted and cannot be recovered.
    """
    from backend.modules.agent.providers import normalize_system_messages_mapped
    from backend.modules.agentpedia.rebuild import messages_from
    from backend.modules.interpretability import recorder, store

    turn = recorder.get_turn(turn_id) or store.get_turn(turn_id)
    if turn is None or not turn.rounds:
        return []
    originals = [messages_from(r) for r in turn.rounds]
    out: list[RequestDetail] = []
    for idx, rnd in enumerate(turn.rounds):
        original, report = originals[idx]
        sent, spans = normalize_system_messages_mapped(original)
        messages: list[RequestMessage] = []
        for i, (msg, segs) in enumerate(zip(sent, spans)):
            segments: list[RequestSegment] = []
            from_role = None
            tokens = 0
            for src, start, end in segs:
                block = rnd.blocks[src] if src < len(rnd.blocks) else None
                if block is None:
                    continue
                tokens += block.tokens
                segments.append(
                    RequestSegment(
                        src=src,
                        start=start,
                        end=end,
                        kind=block.kind,
                        label=block.label,
                        toolName=block.toolName,
                        clipped=block.clipped,
                    )
                )
                if block.role and block.role != msg.get("role"):
                    from_role = block.role
            content = msg.get("content")
            messages.append(
                RequestMessage(
                    index=i,
                    role=str(msg.get("role") or ""),
                    hash=body_hash(msg),
                    body=msg,
                    tokens=tokens,
                    chars=len(content) if isinstance(content, str) else 0,
                    segments=segments,
                    fromRole=from_role,
                )
            )
        # This round's answer is the first message the next round appended.
        response = None
        if idx + 1 < len(originals):
            following = originals[idx + 1][0]
            if len(following) > len(original):
                candidate = following[len(original)]
                if candidate.get("role") == "assistant":
                    response = candidate
        notes = [
            "Rebuilt from the clipped snapshot: this turn predates literal capture."
        ]
        if report.clipped:
            notes.append(f"Clipped in the snapshot: {', '.join(report.clipped)}.")
        if report.unlinked_tool_results:
            notes.append(
                f"{report.unlinked_tool_results} tool result(s) had no call to pair with."
            )
        notes.append(
            "Tool schemas were not recorded, only names; tool_call ids are minted, "
            "and reasoning_content is absent."
        )
        out.append(
            RequestDetail(
                id=f"{_RECON_PREFIX}{turn_id}:{rnd.round}",
                turnId=turn_id,
                round=rnd.round,
                attempt=0,
                ts=turn.startedAt,
                provider=turn.provider,
                model=turn.model,
                status="ok",
                messageCount=len(messages),
                estTokens=rnd.totalTokens,
                exact=turn.exact,
                toolCalls=len((response or {}).get("tool_calls") or []),
                toolNames=[
                    _call_name(c) for c in (response or {}).get("tool_calls") or []
                ],
                responsePreview=_preview(response),
                reconstructed=True,
                messages=messages,
                tools=[
                    {"name": t.name, "group": t.group, "tokens": t.tokens}
                    for t in rnd.tools
                ],
                toolsTokens=rnd.toolTokens,
                params={
                    k: v
                    for k, v in {
                        "temperature": turn.temperature,
                        "top_p": turn.topP,
                        "max_tokens": turn.maxTokens,
                        "num_ctx": turn.requestedNumCtx,
                    }.items()
                    if v is not None
                },
                response=response,
                contextLength=turn.modelContextLength,
                notes=notes,
            )
        )
    return out


async def _push(pending: _Pending) -> None:
    """Tell the pane a request started or finished. Summary only — the bodies are
    fetched on demand, so a 60k-token request is not shoved down the socket twice."""
    from backend.modules.interpretability import recorder

    conn = recorder.turn_conn(pending.turn_id)
    if conn is None:
        return
    try:
        await conn.send_json(
            {
                "channel": "interpretability",
                "event": "request",
                "data": summary(pending.row, pending.est_tokens).model_dump(),
            }
        )
    except Exception:
        pass
