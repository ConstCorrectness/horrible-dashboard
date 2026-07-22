"""The deep-research pipeline: plan → subagents → synthesis → citations → export.

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


async def run_subagent_step(
    run: dict[str, Any],
    spec: dict[str, Any],
    sub: ModelChoice,
    *,
    is_cancelled,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
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
                report=report, sources=sources_block or "(none)"
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
        {"report": final_report, "sources": sources, "stripped_markers": bad},
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
