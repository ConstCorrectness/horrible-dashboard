"""Results → a supervised fine-tuning dataset.

The half of the flywheel that turns "this model fails these cases" into something
you can train on. Everything downstream already exists: a `training` project, the
recipe form's `SFTConfig`/`LoraConfig`, `convert.py` → GGUF into llama.cpp's
managed dir, serve it, re-run the identical suite, compare.

## What gets exported is the IDEAL trajectory, not the model's

The obvious design — record what the model did and train on it — is wrong in both
directions. For a case the model **failed**, its trajectory is the mistake you are
trying to remove. For a case it **passed**, its trajectory and the ideal one are
the same thing anyway. So an example is built from the *case*: the prompt it was
given, the tools it was shown, and the call `expect` says was correct.

That has a consequence worth stating: **a case is a claim about correct behaviour,
and exporting turns that claim into training data.** A wrong expectation does not
merely mis-score a model here — it teaches one. This module is why the "read the
tool declaration before writing what you expect of it" rule matters beyond the
scoreboard.

## The two traps, as code

**One system message.** The runner assembles several (the agent prompt, the skills
catalog, the group guides) because that is the production shape and the provider
seam flattens them. A chat template does not: a strict Jinja template raises on a
second system message ("must be at the beginning"), so `_flatten_system` merges
them into one before anything is written.

**Do not pre-render the chat template.** Examples are written as `messages` +
`tools`, never as a rendered string. The tokenizer's own `apply_chat_template` is
what the serving path uses, and rendering here would bake in *this* model's
template and silently mistrain anything else — the exact failure `--jinja` exists
to avoid at serving time. `meta.rendered_by` records which model the data was
drawn against so the mismatch is at least visible.

## `no_call` cases need an answer from somewhere

A case that expects no tool call is expecting *prose*, and the case does not
contain the prose. So repairing one requires a `reference_run_id` — a stronger
model's run of the same suite, whose answer is borrowed. Without one those cases
are **reported as skipped**, never invented: a fabricated answer is training data
that teaches a model to say something nobody checked.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.modules.database.app_db import get_data_dir
from backend.modules.evals import store
from backend.modules.evals.models import EvalCase, ExportResponse

logger = logging.getLogger(__name__)


def exports_dir() -> Path:
    path = get_data_dir() / "evals" / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _flatten_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge every system message into one leading message.

    Strict chat templates reject a second system message outright — the failure is
    a 500 from the engine, not a warning — so the split the runner keeps for the
    recorder has to be undone before this becomes training data.
    """
    systems = [
        str(m.get("content") or "") for m in messages if m.get("role") == "system"
    ]
    rest = [m for m in messages if m.get("role") != "system"]
    if not systems:
        return rest
    return [{"role": "system", "content": "\n\n".join(s for s in systems if s)}, *rest]


def _assistant_tool_message(case: EvalCase) -> dict[str, Any]:
    """The assistant turn that makes the expected calls."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in case.expect.calls
        ],
    }


def _tool_results(case: EvalCase) -> list[dict[str, Any]]:
    """The tool results the case's fixtures would have produced.

    Included so the trajectory is complete: a tool-calling example that stops at
    the call teaches the model to call and never to use what came back, which is
    the failure that shows up as a model looping on the same tool.
    """
    return [
        {
            "role": "tool",
            "name": c.name,
            "content": json.dumps(case.fixtures.get(c.name, {"ok": True})),
        }
        for c in case.expect.calls
    ]


def _example(
    case: EvalCase,
    tools: list[dict[str, Any]],
    system: str,
    final_answer: str,
) -> dict[str, Any]:
    """One training example: messages + the tools the model was shown."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(case.history)
    messages.append({"role": "user", "content": case.prompt})

    if case.expect.grade == "no_call":
        messages.append({"role": "assistant", "content": final_answer})
    else:
        messages.append(_assistant_tool_message(case))
        messages.extend(_tool_results(case))
        if final_answer:
            messages.append({"role": "assistant", "content": final_answer})

    return {
        "messages": _flatten_system(messages),
        # Carried alongside rather than rendered in: `apply_chat_template(messages,
        # tools=...)` at training time is what matches the serving path.
        "tools": tools,
        "meta": {"case_id": case.id, "tags": case.tags},
    }


def _tools_for(
    case: EvalCase, agent_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The tool schemas this case showed the model, recomputed the same way the
    runner computes them — so an example trains on the catalog the case was
    actually graded against."""
    from backend.modules.agent.roster import get_agent
    from backend.modules.evals.runner_agent import EvalConnection, _tools_for_case

    conn = EvalConnection(agent_tools, {})
    tools, _groups = _tools_for_case(conn, case, get_agent("main"))
    return tools


def build(
    run_id: str,
    *,
    mode: str = "both",
    reference_run_id: str = "",
    agent_tools: list[dict[str, Any]] | None = None,
    out: str = "",
) -> ExportResponse:
    """Turn one run's results into an SFT dataset on disk."""
    run = store.get_run(run_id)
    if run is None:
        raise ValueError(f"no run {run_id!r}")
    suite = store.get_suite(run.suite_id)
    if suite is None:
        raise ValueError(f"run {run_id!r} points at a suite that no longer exists")

    cases = {c.id: c for c in store.load_cases(suite)}
    results = store.list_results(run_id)

    # Answers a `no_call` case can borrow, keyed by case. From the reference run
    # when there is one, and from THIS run's own passes otherwise — a case this
    # model already answers correctly needs no stronger model to supply the text.
    answers: dict[str, str] = {
        r.case_id: r.answer for r in results if r.passed and r.answer
    }
    if reference_run_id:
        for r in store.list_results(reference_run_id):
            if r.passed and r.answer:
                answers[r.case_id] = r.answer

    system = _system_prompt()
    tools_cache: dict[str, list[dict[str, Any]]] = {}
    examples: list[dict[str, Any]] = []
    skipped: list[str] = []
    correct = repaired = 0

    for result in results:
        case = cases.get(result.case_id)
        if case is None:
            skipped.append(f"{result.case_id}: no longer in the suite")
            continue
        if case.type == "hf_benchmark":
            # A benchmark scores a dataset; there is no single trajectory to
            # imitate, and inventing one would be inventing the dataset.
            skipped.append(f"{case.id}: benchmark cases have no trajectory to export")
            continue

        wanted = (result.passed and mode in ("correct", "both")) or (
            not result.passed and mode in ("repair", "both")
        )
        if not wanted:
            continue

        if result.error:
            skipped.append(f"{case.id}: the case errored, so there is nothing to learn")
            continue

        answer = answers.get(case.id, "")
        if case.expect.grade == "no_call" and not answer:
            skipped.append(
                f"{case.id}: expects an answer, and no run has produced a correct one "
                "— give reference_run_id a stronger model's run"
            )
            continue
        if case.expect.grade != "no_call" and not case.expect.calls:
            skipped.append(f"{case.id}: no expected calls to learn from")
            continue

        key = f"{case.expose.mode}:{','.join(sorted(case.expose.preload))}"
        if key not in tools_cache:
            tools_cache[key] = _tools_for(case, agent_tools or [])

        examples.append(_example(case, tools_cache[key], system, answer))
        if result.passed:
            correct += 1
        else:
            repaired += 1

    path = _resolve_out(out, run)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in examples)
        + ("\n" if examples else ""),
        encoding="utf-8",
    )

    logger.info(
        "evals: exported %d example(s) from run %s (%d correct, %d repaired, %d skipped)",
        len(examples),
        run_id,
        correct,
        repaired,
        len(skipped),
    )
    return ExportResponse(
        path=str(path),
        examples=len(examples),
        correct=correct,
        repaired=repaired,
        skipped=skipped,
    )


def _resolve_out(out: str, run: Any) -> Path:
    """Where the dataset lands.

    A bare name or a relative path stays inside the exports directory; an absolute
    path is honoured. `Path.joinpath` would happily follow `../..`, so a relative
    path is resolved and then checked to be inside the directory it claimed to be
    in — an export name reaches this from an HTTP body.
    """
    if not out:
        return exports_dir() / f"{run.suite_id}-{run.label or run.model}-{run.id}.jsonl"
    candidate = Path(out)
    if candidate.is_absolute():
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    root = exports_dir().resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{out!r} would write outside the exports directory")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _system_prompt() -> str:
    """The system prompt examples are written against.

    The orchestrator's own, so a fine-tune is trained on the prompt it will be
    served behind. Falls back to a plain instruction if the roster cannot be read,
    rather than exporting examples with no system message at all.
    """
    try:
        from backend.modules.agent.roster import get_agent

        spec = get_agent("main")
        if spec and spec.system_prompt:
            return str(spec.system_prompt)
    except Exception:  # noqa: BLE001
        logger.debug(
            "evals: could not read the orchestrator system prompt", exc_info=True
        )
    return "You are a helpful assistant with access to tools."


def preview(run_id: str, limit: int = 3, **kwargs: Any) -> list[dict[str, Any]]:
    """The first few examples, without writing anything.

    Worth having its own path: the thing you want to check before training on a
    dataset is what one row looks like, and reading that out of a file you just
    wrote is a worse loop than not writing it.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        response = build(run_id, out=str(Path(tmp) / "preview.jsonl"), **kwargs)
        lines = Path(response.path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[:limit]]
