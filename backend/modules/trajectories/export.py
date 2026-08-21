"""Recorded runs → a supervised fine-tuning dataset.

The training half of the loop. Downstream everything already exists: a `training`
project, the recipe form's `SFTConfig`/`LoraConfig`, `convert.py` → GGUF into
llama.cpp's managed dir, serve it, re-run the suite, compare.

## This exports the model's ACTUAL trajectory — the opposite of `evals/export.py`

That distinction is the whole reason both exist, so it is worth stating plainly.
The evals exporter builds an example from the **case**: the prompt, the tools, and
the call `expect` says was correct. It exports the *ideal* trajectory, because for
a failed case the model's own trajectory is the mistake you are removing.

Here there is no `expect`. A trajectory is a record of what happened, and the
claim that it was *good* comes from a label somebody attached afterwards. So:

**Only graded successes are exported, and `outcome` is not optional.** Exporting
ungraded runs would train on whatever the agent happened to do, which is how you
distil a model's own failure modes back into it. `min_score` and `label_source`
narrow it further — `source="human"` is the setting to reach for when the training
run actually matters, because an `agent-critic` label is a model grading a model.

## The two traps, inherited verbatim

**One system message.** A run's harness prompt plus any system turns in the run
must be merged: a strict Jinja template *raises* on a second system message
("must be at the beginning") — a 500 from the engine, not a warning.

**Do not pre-render the chat template.** Examples are `messages` + `tools`, never
a rendered string. The tokenizer's own `apply_chat_template` is what the serving
path uses; rendering here bakes in one model's template and silently mistrains
anything else. `meta.drawn_from` records the model the data came off.

## Redaction happens here

This is one of the three boundaries where trajectory data leaves the node, so
every payload goes through `store.redact()` on the way out. The store keeps tool
arguments raw on purpose; a training file full of API keys is a different matter
entirely.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.modules.database.app_db import get_data_dir
from backend.modules.trajectories import store
from backend.modules.trajectories.models import TrajectoryDetail

logger = logging.getLogger("trajectories")


def exports_dir() -> Path:
    path = get_data_dir() / "trajectories" / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _flatten_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge every system message into one leading message.

    Lifted from `evals/export.py` deliberately rather than imported: that module's
    copy is shaped by an `EvalCase`, and a shared helper across two exporters with
    different inputs is a coupling neither wants. The *rule* is what matters, and
    it is the same rule — a strict chat template rejects a second system message
    outright.
    """
    systems = [
        str(m.get("content") or "") for m in messages if m.get("role") == "system"
    ]
    rest = [m for m in messages if m.get("role") != "system"]
    if not systems:
        return rest
    return [{"role": "system", "content": "\n\n".join(s for s in systems if s)}, *rest]


def run_to_messages(run: TrajectoryDetail) -> tuple[list[dict[str, Any]], list[str]]:
    """Rebuild one run as an OpenAI-shaped `messages` list.

    Returns `(messages, problems)`. `problems` is non-empty when the run could not
    be turned into a faithful example — it is **reported, never patched over**. A
    silently repaired trajectory is training data nobody checked.

    ## Assistant prose and the tool call it precedes are ONE turn

    A trajectory stores the model's narration ("Let me run the tests") and the
    call it then made as two steps, because a step is one decision and the
    narration is not a decision. A chat transcript does not work that way: in both
    the Anthropic and OpenAI shapes an assistant turn carries its text *and* its
    `tool_calls` together.

    Emitting them as two consecutive assistant messages produces a conversation
    that never alternates — which a strict chat template either rejects outright
    or, worse, renders into something that teaches a model to emit a bare text
    turn and then a bare tool turn. So pending assistant prose is held and folded
    into the next action's message, and only flushed as its own turn when the next
    thing is not an action.
    """
    problems: list[str] = []
    messages: list[dict[str, Any]] = []
    pending_text: str = ""

    def flush_text() -> None:
        nonlocal pending_text
        if pending_text:
            messages.append({"role": "assistant", "content": pending_text})
            pending_text = ""

    if run.harness_detail and run.harness_detail.system_prompt:
        messages.append({"role": "system", "content": run.harness_detail.system_prompt})
    elif run.goal:
        problems.append("no harness system prompt recorded")

    call_index = 0
    for step in run.step_list:
        if step.kind == "message" and step.role in ("user", "system"):
            flush_text()
            if step.content:
                messages.append({"role": step.role, "content": step.content})
        elif step.kind == "action":
            call_id = f"call_{call_index}"
            call_index += 1
            messages.append(
                {
                    "role": "assistant",
                    # The narration that led to this call, on the same turn.
                    "content": pending_text,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": step.name or "",
                                # A string, matching the OpenAI wire shape the
                                # serving path emits — not a nested object.
                                "arguments": json.dumps(store.redact(step.args or {})),
                            },
                        }
                    ],
                }
            )
            pending_text = ""
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(store.redact(step.result))
                    if step.result is not None
                    else "",
                }
            )
        elif step.kind == "message" and step.role == "assistant" and step.content:
            flush_text()
            pending_text = step.content
    flush_text()

    if not any(m["role"] == "user" for m in messages):
        problems.append("no user turn recorded")
    if messages and messages[-1].get("role") != "assistant":
        # Training on a trajectory that stops mid-tool-call teaches the model to
        # stop mid-tool-call.
        problems.append("run does not end on an assistant turn")
    # Cheap invariant, loudly reported: two assistant turns in a row is the shape
    # a strict chat template rejects, and the whole reason `pending_text` exists.
    roles = [m["role"] for m in messages]
    if any(a == "assistant" and b == "assistant" for a, b in zip(roles, roles[1:])):
        problems.append("consecutive assistant turns")
    return _flatten_system(messages), problems


def _tools_of(run: TrajectoryDetail) -> list[dict[str, Any]]:
    """The tool catalog the run was shown, from its harness."""
    if not run.harness_detail:
        return []
    schemas = run.harness_detail.tool_schemas or {}
    return [
        schemas[name] for name in sorted(schemas) if isinstance(schemas[name], dict)
    ]


def export_dataset(
    *,
    name: str,
    dataset_id: str | None = None,
    harness: str | None = None,
    label_source: str | None = None,
    min_score: float | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Write an SFT JSONL file. Returns a report including what it skipped.

    Only `outcome='success'` runs are considered — see the module docstring.
    """
    runs, _ = store.list_runs(
        dataset_id=dataset_id, harness=harness, outcome="success", limit=limit
    )
    path = exports_dir() / f"{store._safe_id(name) or 'export'}.jsonl"
    written = 0
    skipped: list[str] = []

    with path.open("w", encoding="utf-8") as handle:
        for summary in runs:
            run = store.get_run(summary.id)
            if run is None:
                continue
            if label_source or min_score is not None:
                labels = [lbl for lbl in run.labels if lbl.key == "outcome"]
                if label_source and not any(
                    lbl.source == label_source for lbl in labels
                ):
                    skipped.append(f"{run.id}: no '{label_source}' outcome label")
                    continue
                if min_score is not None and not any(
                    (lbl.score or 0.0) >= min_score for lbl in labels
                ):
                    skipped.append(f"{run.id}: no outcome label scoring >= {min_score}")
                    continue
            messages, problems = run_to_messages(run)
            if problems:
                skipped.append(f"{run.id}: {'; '.join(problems)}")
                continue
            handle.write(
                json.dumps(
                    {
                        "messages": messages,
                        "tools": _tools_of(run),
                        "meta": {
                            "run_id": run.id,
                            "dataset_id": run.dataset_id,
                            "harness": run.harness,
                            # Which model produced this data. A template mismatch
                            # at training time is at least visible from here.
                            "drawn_from": run.model,
                            "outcome": run.outcome,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            written += 1

    return {
        "path": str(path),
        "examples": written,
        "candidates": len(runs),
        "skipped": skipped[:50],
        "skippedCount": len(skipped),
        "note": (
            "Only graded successes are exported. Ungraded runs are never included —"
            " training on what the agent happened to do distils its failure modes."
        ),
    }
