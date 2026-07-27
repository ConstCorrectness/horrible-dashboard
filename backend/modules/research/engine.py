"""The deep-research pipeline.

    plan → [subagents → critique]* → synthesis → verify → citations → export

The bracketed pair is one **round**. A single pass answers what it thought to ask
at the start; the critique step reads what actually came back, names the gaps, and
either declares the findings sufficient or writes new subagent specs targeting only
what's missing. That loop is where the depth comes from — the second round is asking
questions the first round *taught* it to ask.

`verify` sits between synthesis and citations rather than after, because citations
rewrites the report and appends `## References`. Verification has to inform that
rewrite; running it afterwards would need a second rewrite pass that clobbers the
first.


Built directly on the agent module's provider layer (`providers.chat` /
`chat_stream`) rather than `delegate.py` — a delegate turn is tied to a `/ws`
connection with a five-minute timeout, and a research run must keep going after
the browser tab is long gone. Cloud keys ride in automatically through the
litellm dialect's secrets lookup; the default stays whatever local model the
node is configured with.

Each stage is one **step function** with the same shape: it takes the run + its
step row, does its work, and returns `(output, transcript, tokens)`. The runner
owns persistence, retry, cancellation, and eventing — step functions stay pure
enough to test against a scripted fake provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from backend.modules.agent import providers as P
from backend.modules.research import prompts, rtools
from backend.modules.research.broadcast import publish_delta
from backend.modules.settings.routes import get_value

logger = logging.getLogger(__name__)

MAX_SUBAGENT_ROUNDS_SLACK = 2  # rounds beyond max_tool_calls before we force a stop
_CITE_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class ModelChoice:
    info: P.ProviderInfo
    endpoint: str
    model: str


def _agent_config() -> Any | None:
    from backend.modules.agent.routes import _load_config

    return _load_config()


def resolve_models(run: dict[str, Any]) -> tuple[ModelChoice, ModelChoice]:
    """(lead, subagent) model choices: run overrides → research settings →
    the node's configured agent model."""
    provider_kind = run.get("provider") or str(get_value("research.provider", "") or "")
    model = run.get("model") or str(get_value("research.model", "") or "")
    config = _agent_config()
    if not provider_kind:
        provider_kind = config.provider if config else P.DEFAULT_PROVIDER
    if not model:
        if config is None:
            raise RuntimeError(
                "no model configured — set research.model or configure the agent"
            )
        model = config.model
    info = P.provider_for(provider_kind)
    endpoint = info.default_endpoint
    if config and config.provider == provider_kind and config.endpoint:
        endpoint = config.endpoint
    lead = ModelChoice(info=info, endpoint=endpoint, model=model)

    sub_model = str(get_value("research.subagentModel", "") or "") or model
    sub = ModelChoice(info=info, endpoint=endpoint, model=sub_model)
    return lead, sub


def _estimate_tokens(messages: list[dict[str, Any]], reply: str) -> int:
    chars = sum(len(str(m.get("content") or "")) for m in messages) + len(reply)
    return max(1, chars // 4)


async def _chat(
    choice: ModelChoice,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[P.ChatResult, int]:
    async with httpx.AsyncClient(timeout=180.0) as client:
        result = await P.chat(
            client, choice.info, choice.endpoint, choice.model, messages, tools or []
        )
    return result, _estimate_tokens(messages, result.content)


# --- plan -------------------------------------------------------------------

_EFFORT_NOTES = {
    "auto": "you decide against the rubric",
    "quick": "the user asked for a quick pass — hold to the quick tier",
    "standard": "the user asked for a standard run",
    "deep": "the user asked for a deep run",
}


def parse_plan(raw: str, *, max_subagents: int) -> dict[str, Any]:
    """Strict-ish plan parsing: find the JSON object, validate shape, clamp
    counts. Raises ValueError with a message worth feeding back for repair."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found")
    try:
        plan = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("subagents"), list):
        raise ValueError("missing 'subagents' array")
    complexity = plan.get("complexity")
    if complexity not in ("quick", "standard", "deep"):
        raise ValueError("complexity must be quick|standard|deep")
    subagents = []
    for i, spec in enumerate(plan["subagents"][:max_subagents]):
        if not isinstance(spec, dict) or not str(spec.get("objective", "")).strip():
            raise ValueError(f"subagent {i} has no objective")
        calls = spec.get("max_tool_calls", 10)
        try:
            calls = max(1, min(int(calls), 25))
        except (TypeError, ValueError):
            calls = 10
        subagents.append(
            {
                "name": str(spec.get("name") or f"subagent-{i + 1}")[:40],
                "objective": str(spec["objective"]),
                "output_format": str(spec.get("output_format") or "prose findings"),
                "tool_guidance": str(spec.get("tool_guidance") or ""),
                "boundaries": str(spec.get("boundaries") or ""),
                "max_tool_calls": calls,
            }
        )
    if not subagents:
        raise ValueError("plan has zero subagents")
    return {"complexity": complexity, "subagents": subagents}


async def run_plan_step(
    run: dict[str, Any], lead: ModelChoice
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    max_subagents = int(get_value("research.maxSubagents", 4) or 4)
    prompt = prompts.PLAN_PROMPT.format(
        query=run["query"],
        effort=run["effort"],
        effort_note=_EFFORT_NOTES.get(run["effort"], ""),
        rubric=prompts.EFFORT_RUBRIC.format(max_subagents=max_subagents),
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    result, tokens = await _chat(lead, messages)
    messages.append(result.assistant_message)
    try:
        plan = parse_plan(result.content, max_subagents=max_subagents)
    except ValueError as exc:
        # One repair round: tell the model what was wrong, ask for JSON only.
        messages.append(
            {
                "role": "user",
                "content": prompts.PLAN_REPAIR_PROMPT.format(error=str(exc)),
            }
        )
        result, more = await _chat(lead, messages)
        tokens += more
        messages.append(result.assistant_message)
        plan = parse_plan(result.content, max_subagents=max_subagents)
    return plan, messages, tokens


# --- subagent ---------------------------------------------------------------


def summarize_tool_result(name: str, outcome: dict[str, Any]) -> str:
    """A one-line rendering of a tool result, for the live trace. Pure — testable.

    Deliberately says *how much* and *what of*, not the content: the trace exists to
    answer "is this subagent making progress or spinning", and a 400-char excerpt of
    the first result answers neither.
    """
    if not isinstance(outcome, dict):
        return str(outcome)[:200]
    if error := outcome.get("error"):
        return f"error: {str(error)[:180]}"
    if isinstance(outcome.get("results"), list):
        results = outcome["results"]
        if not results:
            return "no results"
        first = results[0] if isinstance(results[0], dict) else {}
        lead = str(first.get("title") or first.get("url") or "")[:120]
        return (
            f"{len(results)} result(s); top: {lead}"
            if lead
            else f"{len(results)} result(s)"
        )
    if isinstance(outcome.get("entries"), list):
        return f"{len(outcome['entries'])} paper(s)"
    if outcome.get("text") is not None:
        return f"{len(str(outcome['text']))} chars from {str(outcome.get('title') or '')[:80]}"
    if outcome.get("source_id"):
        return f"saved: {str(outcome.get('title') or outcome['source_id'])[:120]}"
    return ", ".join(sorted(outcome))[:180] or "ok"


async def run_subagent_step(
    run: dict[str, Any],
    spec: dict[str, Any],
    sub: ModelChoice,
    *,
    is_cancelled,
    on_tool=None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """One subagent's tool loop.

    `on_tool` is injected the same way `is_cancelled` is, so the engine stays
    runner-agnostic: it is called with a small dict after every tool call, and the
    runner decides whether that means a `/ws` event, a DB row, or nothing. Without it
    a subagent's transcript only lands when the step *finishes*, which is minutes of
    a blank console on a run where you most want to see what's happening.
    """
    definitions, handlers = rtools.make_tools(run["library"])
    max_calls = int(spec.get("max_tool_calls", 10))
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompts.SUBAGENT_PROMPT.format(
                objective=spec["objective"],
                output_format=spec["output_format"],
                tool_guidance=spec["tool_guidance"],
                boundaries=spec["boundaries"],
                max_tool_calls=max_calls,
            ),
        }
    ]
    tokens = 0
    calls_used = 0
    max_rounds = max_calls + MAX_SUBAGENT_ROUNDS_SLACK
    final_text = ""
    for round_no in range(max_rounds):
        if is_cancelled():
            raise asyncio.CancelledError("run cancelled")
        # Past the call budget the model gets no tools — it must summarize.
        tools = definitions if calls_used < max_calls else []
        result, spent = await _chat(sub, messages, tools)
        tokens += spent
        messages.append(result.assistant_message)
        if not result.tool_calls:
            final_text = result.content
            break
        for call in result.tool_calls:
            started = time.monotonic()
            handler = handlers.get(call.name)
            if handler is None:
                outcome: dict[str, Any] = {"error": f"unknown tool {call.name}"}
            else:
                try:
                    outcome = await handler(call.arguments)
                except Exception as exc:  # noqa: BLE001 — tool bugs must not kill the run
                    logger.exception("research tool %s failed", call.name)
                    outcome = {"error": f"{call.name} crashed: {exc}"}
            calls_used += 1
            if on_tool is not None:
                try:
                    on_tool(
                        {
                            "seq": calls_used,
                            "name": call.name,
                            "args": call.arguments,
                            "ok": not outcome.get("error"),
                            "ms": int((time.monotonic() - started) * 1000),
                            "summary": summarize_tool_result(call.name, outcome),
                        }
                    )
                except Exception:  # noqa: BLE001 — observation must never break the run
                    logger.exception("on_tool callback failed")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(outcome)[:12_000],
                }
            )
    else:
        final_text = "(subagent hit its round limit before reporting)"
    if not final_text:
        final_text = "(no findings reported)"

    findings, sources = _split_findings(final_text)
    return (
        {
            "name": spec["name"],
            "findings": findings,
            "sources": sources,
            "tool_calls_used": calls_used,
        },
        messages,
        tokens,
    )


def _split_findings(text: str) -> tuple[str, list[dict[str, str]]]:
    """Split a subagent report at its `SOURCES:` line into (findings, sources)."""
    head, sep, tail = text.partition("SOURCES:")
    sources: list[dict[str, str]] = []
    if sep:
        for line in tail.splitlines():
            line = line.strip().lstrip("-•").strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            entry = {
                "title": parts[0] if parts else line,
                "url": parts[1] if len(parts) > 1 else "",
                "note": parts[2] if len(parts) > 2 else "",
            }
            if entry["title"] or entry["url"]:
                sources.append(entry)
    return head.strip(), sources


# --- critique (closes a round) ----------------------------------------------


def parse_followups(raw: str, *, max_subagents: int) -> dict[str, Any]:
    """Parse a critique reply. Same strict-JSON + clamp discipline as `parse_plan`.

    Reuses `parse_plan`'s per-spec validation by construction: the critique prompt
    asks for the same subagent shape, so one validator covers both and a change to
    the spec format can't drift between them.
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    sufficient = bool(data.get("sufficient"))
    gaps = [str(g)[:400] for g in (data.get("gaps") or []) if str(g).strip()][:10]

    specs: list[dict[str, Any]] = []
    raw_specs = data.get("subagents") or []
    if not isinstance(raw_specs, list):
        raise ValueError("'subagents' must be an array")
    for i, spec in enumerate(raw_specs[:max_subagents]):
        if not isinstance(spec, dict) or not str(spec.get("objective", "")).strip():
            continue  # a malformed follow-up is dropped, not fatal — we already
            # have findings, and refusing to continue over one bad spec would
            # throw away a whole round of work
        calls = spec.get("max_tool_calls", 8)
        try:
            calls = max(1, min(int(calls), 25))
        except (TypeError, ValueError):
            calls = 8
        specs.append(
            {
                "name": str(spec.get("name") or f"followup-{i + 1}")[:40],
                "objective": str(spec["objective"]),
                "output_format": str(spec.get("output_format") or "prose findings"),
                "tool_guidance": str(spec.get("tool_guidance") or ""),
                "boundaries": str(spec.get("boundaries") or ""),
                "max_tool_calls": calls,
            }
        )

    # "Sufficient" and "here are more tasks" contradict each other; the tasks win,
    # because a model that wrote them found something it wanted answered.
    if specs:
        sufficient = False
    return {"sufficient": sufficient, "gaps": gaps, "subagents": specs}


async def run_critique_step(
    run: dict[str, Any],
    plan: dict[str, Any],
    subagent_outputs: list[dict[str, Any]],
    lead: ModelChoice,
    *,
    round_no: int,
    followups: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Review a round's findings and decide whether another round is warranted."""
    max_subagents = int(get_value("research.maxSubagents", 4) or 4)
    _numbered, findings_block = number_sources(subagent_outputs)
    plan_block = "\n".join(
        f"- {s['name']}: {s['objective']}" for s in plan.get("subagents", [])
    )
    followup_block = ""
    if followups:
        # Anything the user asked for mid-run that no subagent picked up becomes an
        # explicit gap, so it shapes the next round instead of being lost.
        followup_block = "\nThe user has also asked, mid-run:\n" + "\n".join(
            f"- {t}" for t in followups
        )

    prompt = prompts.CRITIQUE_PROMPT.format(
        query=run["query"],
        plan=plan_block or "(no plan recorded)",
        findings=findings_block or "(no findings)",
        followups=followup_block,
        max_subagents=max_subagents,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    result, tokens = await _chat(lead, messages)
    messages.append(result.assistant_message)
    try:
        critique = parse_followups(result.content, max_subagents=max_subagents)
    except ValueError as exc:
        messages.append(
            {
                "role": "user",
                "content": prompts.CRITIQUE_REPAIR_PROMPT.format(error=str(exc)),
            }
        )
        result, more = await _chat(lead, messages)
        tokens += more
        messages.append(result.assistant_message)
        critique = parse_followups(result.content, max_subagents=max_subagents)

    critique["round"] = round_no
    return critique, messages, tokens


# --- synthesis --------------------------------------------------------------


def number_sources(
    subagent_outputs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Merge subagent source lists into one globally numbered list (deduped by
    URL) and render the findings block for the synthesis prompt."""
    numbered: list[dict[str, Any]] = []
    by_url: dict[str, int] = {}
    blocks: list[str] = []
    for output in subagent_outputs:
        lines = [f"### Subagent: {output.get('name', '?')}", output.get("findings", "")]
        source_lines = []
        for source in output.get("sources", []):
            key = source.get("url") or source.get("title", "")
            if not key:
                continue
            if key in by_url:
                n = by_url[key]
            else:
                numbered.append(dict(source))
                n = len(numbered)
                by_url[key] = n
            source_lines.append(
                f"[{n}] {source.get('title', '')} — {source.get('url', '')}"
            )
        if source_lines:
            lines.append("Its sources:\n" + "\n".join(source_lines))
        blocks.append("\n".join(part for part in lines if part))
    return numbered, "\n\n".join(blocks)


async def run_synthesis_step(
    run: dict[str, Any],
    step_id: str,
    subagent_outputs: list[dict[str, Any]],
    lead: ModelChoice,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    numbered, findings_block = number_sources(subagent_outputs)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompts.SYNTHESIS_PROMPT.format(
                query=run["query"], findings=findings_block
            ),
        }
    ]

    # Stream deltas to the console, throttled to ~4 Hz.
    buffer: list[str] = []
    last_flush = 0.0

    async def on_delta(_reasoning: str, content: str) -> None:
        nonlocal last_flush
        if content:
            buffer.append(content)
        now = time.monotonic()
        if buffer and now - last_flush > 0.25:
            publish_delta(run["id"], step_id, "".join(buffer))
            buffer.clear()
            last_flush = now

    async with httpx.AsyncClient(timeout=300.0) as client:
        result = await P.chat_stream(
            client,
            lead.info,
            lead.endpoint,
            lead.model,
            messages,
            [],
            on_delta,
        )
    if buffer:
        publish_delta(run["id"], step_id, "".join(buffer))
    messages.append(result.assistant_message)
    tokens = _estimate_tokens(messages, result.content)
    return (
        {"report": result.content, "sources": numbered},
        messages,
        tokens,
    )


# --- verification ------------------------------------------------------------


def parse_claims(raw: str, *, max_claims: int) -> list[dict[str, Any]]:
    """Parse the claim-extraction reply into `[{claim, citations}]`. Pure."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError("no JSON array found")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("expected a JSON array")

    out: list[dict[str, Any]] = []
    for item in data[:max_claims]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("claim") or "").strip()
        if not text:
            continue
        citations: list[int] = []
        for n in item.get("citations") or []:
            try:
                citations.append(int(n))
            except (TypeError, ValueError):
                continue
        out.append({"claim": text[:500], "citations": citations})
    return out


def assess_independence(
    claims: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Grade each claim by how many **independent publishers** back it. Pure.

    This is the part that needs no model, and it's the part that makes "two
    independent sources" mean anything. Citation *count* is trivially gamed by a
    report that cites `openai.com/blog`, `platform.openai.com/docs` and
    `openai.com/research` for one claim — three markers, one publisher, one point of
    view. Grouping by registrable domain collapses those to one.

    Verdicts: `supported` (≥2 domains), `single-sourced` (1), `unsupported` (0
    resolvable citations).
    """
    from backend.modules.search.canonical import registrable_domain

    graded: list[dict[str, Any]] = []
    for claim in claims:
        domains: list[str] = []
        for n in claim.get("citations") or []:
            if not 1 <= n <= len(sources):
                continue  # a dangling marker supports nothing
            url = str(sources[n - 1].get("url") or "")
            domain = registrable_domain(url) if url else ""
            # A source with no URL still counts as *a* source, keyed by its title —
            # subagents do occasionally cite a paper by name alone.
            key = domain or str(sources[n - 1].get("title") or "")[:60]
            if key and key not in domains:
                domains.append(key)

        if not domains:
            verdict = "unsupported"
        elif len(domains) == 1:
            verdict = "single-sourced"
        else:
            verdict = "supported"
        graded.append(
            {
                "claim": claim["claim"],
                "citations": claim.get("citations") or [],
                "domains": domains,
                "independent_sources": len(domains),
                "verdict": verdict,
            }
        )
    return graded


def parse_contradictions(raw: str) -> list[dict[str, Any]]:
    """Parse the contradiction-check reply. Pure. Malformed → none, never an error:
    a failed contradiction check must not fail a run that has a good report."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for item in data[:10]:
        if not isinstance(item, dict) or not str(item.get("topic") or "").strip():
            continue
        positions = []
        for position in item.get("positions") or []:
            if not isinstance(position, dict):
                continue
            try:
                source = int(position.get("source"))
            except (TypeError, ValueError):
                continue
            positions.append(
                {"source": source, "claim": str(position.get("claim") or "")[:300]}
            )
        # A "disagreement" with fewer than two sides is an opinion, not a conflict.
        if len(positions) >= 2:
            out.append({"topic": str(item["topic"])[:300], "positions": positions})
    return out


def render_verification(verification: dict[str, Any]) -> str:
    """The audit block handed to the citations step. Pure — and deliberately terse,
    because it rides in a prompt alongside a full report."""
    lines: list[str] = []
    for claim in verification.get("claims", []):
        if claim["verdict"] == "supported":
            continue  # only the problems are worth prompt budget
        markers = ", ".join(f"[{n}]" for n in claim["citations"]) or "(no citation)"
        lines.append(f'- {claim["verdict"]}: "{claim["claim"]}" — {markers}')
    for conflict in verification.get("contradictions", []):
        sides = "; ".join(
            f"[{p['source']}] {p['claim']}" for p in conflict["positions"]
        )
        lines.append(f"- contradiction on {conflict['topic']}: {sides}")
    return "\n".join(lines) or "(no problems found)"


async def run_verification_step(
    run: dict[str, Any],
    synthesis_output: dict[str, Any],
    subagent_outputs: list[dict[str, Any]],
    lead: ModelChoice,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Audit the synthesized report: which claims are actually independently backed,
    and where do the sources disagree.

    Two model calls at most, and the expensive part of the work — deciding what
    counts as independent — is done deterministically in between. `research.verifyDepth`
    gates it: `off` skips entirely, `cheap` (the default) runs both calls, and
    `corroborate` is reserved for a future tool-using pass over single-sourced claims.
    """
    depth = str(get_value("research.verifyDepth", "cheap") or "cheap")
    report = synthesis_output["report"]
    sources = synthesis_output["sources"]
    if depth == "off":
        return (
            {"skipped": True, "claims": [], "contradictions": [], "summary": {}},
            [],
            0,
        )

    max_claims = max(1, int(get_value("research.maxVerifiedClaims", 12) or 12))
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompts.VERIFY_EXTRACT_PROMPT.format(
                report=report, max_claims=max_claims
            ),
        }
    ]
    result, tokens = await _chat(lead, messages)
    messages.append(result.assistant_message)
    try:
        claims = parse_claims(result.content, max_claims=max_claims)
    except ValueError as exc:
        logger.warning("claim extraction unparseable (%s); auditing nothing", exc)
        claims = []

    graded = assess_independence(claims, sources)

    # Contradictions come from ONE pass over the whole findings block, not pairwise
    # comparison of subagent outputs — that's O(n²) calls and mostly surfaces
    # differences of emphasis.
    contradictions: list[dict[str, Any]] = []
    if subagent_outputs:
        _numbered, findings_block = number_sources(subagent_outputs)
        conflict_messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": prompts.CONTRADICTIONS_PROMPT.format(
                    query=run["query"], findings=findings_block
                ),
            }
        ]
        try:
            conflict_result, more = await _chat(lead, conflict_messages)
            tokens += more
            conflict_messages.append(conflict_result.assistant_message)
            contradictions = parse_contradictions(conflict_result.content)
            messages.extend(conflict_messages)
        except Exception as exc:  # noqa: BLE001 — additive; a good report still ships
            logger.warning("contradiction check failed: %s", exc)

    verification = {
        "skipped": False,
        "claims": graded,
        "contradictions": contradictions,
        "summary": {
            "total": len(graded),
            "single_sourced": sum(
                1 for c in graded if c["verdict"] == "single-sourced"
            ),
            "unsupported": sum(1 for c in graded if c["verdict"] == "unsupported"),
            "contradicted": len(contradictions),
        },
    }
    return verification, messages, tokens


# --- citations --------------------------------------------------------------


def check_citations(report: str, source_count: int) -> list[int]:
    """Deterministic post-check: every [n] must resolve. Returns bad numbers."""
    return sorted(
        {
            n
            for n in (int(m.group(1)) for m in _CITE_RE.finditer(report))
            if n < 1 or n > source_count
        }
    )


async def run_citations_step(
    run: dict[str, Any],
    synthesis_output: dict[str, Any],
    lead: ModelChoice,
    verification_output: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    report = synthesis_output["report"]
    sources = synthesis_output["sources"]
    sources_block = "\n".join(
        f"[{i + 1}] {s.get('title', '')} — {s.get('url', '')} ({s.get('note', '')})"
        for i, s in enumerate(sources)
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": prompts.CITATIONS_PROMPT.format(
                report=report,
                sources=sources_block or "(none)",
                verification=render_verification(verification_output or {}),
            ),
        }
    ]
    result, tokens = await _chat(lead, messages)
    messages.append(result.assistant_message)
    final_report = result.content.strip() or report
    bad = check_citations(final_report, len(sources))
    if bad:
        # The checker itself misnumbered; strip the offending markers rather than
        # ship dangling citations.
        final_report = _CITE_RE.sub(
            lambda m: "" if int(m.group(1)) in bad else m.group(0), final_report
        )
    return (
        {
            "report": final_report,
            "sources": sources,
            "stripped_markers": bad,
            # Carried through so the console can show the audit next to the report
            # without re-reading the verify step.
            "verification": (verification_output or {}).get("summary") or {},
        },
        messages,
        tokens,
    )


# --- export -----------------------------------------------------------------


async def run_export_step(
    run: dict[str, Any], citations_output: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    from backend.modules.artifacts.store import store_bytes
    from backend.modules.library.models import IngestRequest
    from backend.modules.library.routes import add_source
    from backend.modules.research import obsidian
    from backend.modules.research.capture import filename_for_title

    report: str = citations_output["report"]
    title = f"Research: {run['query'][:80]}"
    artifact = store_bytes(
        report.encode("utf-8"),
        kind="report",
        mime="text/markdown",
        filename=filename_for_title(title, "md"),
        meta={"title": title, "run_id": run["id"]},
    )
    source = await add_source(
        IngestRequest(
            type="note",
            library=run["library"],
            title=title,
            text=report,
            tags=["research-report"],
        )
    )
    obsidian_result: dict[str, Any] | None = None
    try:
        obsidian_result = obsidian.export_source(None, artifact)
    except obsidian.ObsidianNotConfigured:
        pass  # export is optional by design
    except Exception as exc:  # noqa: BLE001 — a vault hiccup must not fail the run
        logger.warning("obsidian export failed for run %s: %s", run["id"], exc)
    return (
        {
            "artifact_id": artifact["id"],
            "source_id": source.id,
            "obsidian": obsidian_result,
        },
        [],
        0,
    )
