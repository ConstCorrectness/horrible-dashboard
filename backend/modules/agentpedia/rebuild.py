"""Rebuild one round's message list out of the snapshot that recorded it.

`agent_turns` was written to be *read by a human* — labelled blocks, previews
clipped at 4 KB, an assistant's tool calls folded into its text so the pane could
count them. A fork needs the opposite: the list of role/content dicts the provider
was actually handed. This file turns the first back into the second, and is honest
about the places where that is lossy.

Three things are recovered rather than stored:

* **An assistant's tool calls.** `recorder._blocks` appends `json.dumps(tool_calls)`
  to the message text so the call's context cost is counted. That is reversible —
  the tail of the block parses as a JSON array of call objects — and it must be
  reversed, because a model handed its own previous calls as *prose* would be
  reading a transcript of a conversation it did not have.
* **`tool_call_id`.** Not recorded at all. The OpenAI dialect keys a tool result to
  the call it answers, so the ids are re-derived by pairing each tool block with the
  calls of the assistant block above it, in order. A tool result with no call to
  pair against is reported, not invented.
* **The system prompt.** Blocks are clipped at 4000 characters, and an agent's
  system prompt is routinely longer than that. Where the live spec's prompt starts
  with the recorded preview, the full text is substituted and the substitution is
  reported — that is a *verified* restoration rather than a guess, and it is the
  difference between a fork that ran the real prompt and one that ran the first
  4000 characters of it.

Everything left clipped is named in the report and the pane says so. A fork that
quietly ran a truncated prompt and produced a different answer would be exactly the
wrong kind of finding: it would look like the edit that caused it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from backend.modules.agentpedia.models import ForkEdit, RebuildReport
from backend.modules.interpretability.models import RoundSnapshot

logger = logging.getLogger(__name__)


def _split_tool_calls(text: str) -> tuple[str, list[dict[str, Any]] | None]:
    """Undo the recorder's `content + newline + json(tool_calls)` fold.

    Conservative on purpose: the tail has to parse as a JSON *array of objects*
    that look like tool calls. An assistant message that genuinely ends with a JSON
    array of anything else keeps its text.
    """
    head, sep, tail = text.rpartition("\n")
    candidate = tail if sep else text
    stripped = candidate.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        return text, None
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        return text, None
    if not isinstance(parsed, list) or not parsed:
        return text, None
    if not all(
        isinstance(call, dict) and ("function" in call or "name" in call)
        for call in parsed
    ):
        return text, None
    return (head if sep else ""), parsed


def _ensure_ids(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every recovered call an id.

    Ollama does not send one — that dialect keys tool results by `tool_name`
    instead — so a turn recorded there comes back with calls carrying no id at all.
    The fork may well run against an OpenAI-dialect provider, where a tool message
    without a `tool_call_id` is rejected outright, so one is minted here and used
    consistently for the message that answers it.
    """
    out: list[dict[str, Any]] = []
    for call in calls:
        copy = dict(call)
        if not copy.get("id"):
            copy["id"] = f"rebuilt_{uuid.uuid4().hex[:12]}"
        out.append(copy)
    return out


def _call_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(call.get("name") or "")


def messages_from(
    snapshot: RoundSnapshot,
    *,
    system_prompt: str = "",
) -> tuple[list[dict[str, Any]], RebuildReport]:
    """The round's message list, plus what could not be reproduced exactly."""
    report = RebuildReport()
    messages: list[dict[str, Any]] = []
    #: The calls made by the most recent assistant message, oldest first — the
    #: queue each following tool block draws from.
    awaiting: list[tuple[str, str]] = []

    for block in snapshot.blocks:
        role = block.role or "user"
        content = block.content or ""

        if block.clipped:
            if block.kind == "system" and content and system_prompt.startswith(content):
                content = system_prompt
                report.restored.append(block.label)
            else:
                report.clipped.append(block.label)

        if role == "assistant":
            text, calls = _split_tool_calls(content)
            message: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                calls = _ensure_ids(calls)
                message["tool_calls"] = calls
                report.tool_calls_recovered += len(calls)
                awaiting = [(str(c["id"]), _call_name(c)) for c in calls]
            messages.append(message)
            continue

        if role == "tool":
            message = {"role": "tool", "content": content}
            if awaiting:
                call_id, name = awaiting.pop(0)
                # Both keys, always. Which one the provider reads depends on the
                # dialect the *fork* runs against, which need not be the dialect the
                # original turn ran against — changing the provider is one of the
                # edits. An extra key is ignored; a missing one is a 400.
                message["tool_call_id"] = call_id
                message["tool_name"] = name
            else:
                report.unlinked_tool_results += 1
            messages.append(message)
            continue

        messages.append({"role": role, "content": content})

    report.messages = len(messages)
    report.exact = not report.clipped and not report.unlinked_tool_results
    return messages, report


def apply_edits(
    messages: list[dict[str, Any]],
    snapshot: RoundSnapshot,
    edits: list[ForkEdit],
    report: RebuildReport,
) -> list[dict[str, Any]]:
    """Apply the message-shaped edits, recording every one that matched nothing.

    A rejected edit is the failure mode this reports loudest. "I dropped the tool
    and it still answered the same way" is a finding; "I dropped a tool whose name
    I misspelled and it still answered the same way" is the same sentence with the
    meaning removed, and nothing on the screen would tell the two apart.

    The provider-shaped edits (`set_model`, `set_provider`, `set_temperature`) are
    not message edits and are resolved in `fork.py`; the tool-shaped ones
    (`drop_tool`, `drop_group`) become the loop's `deny_tools`.
    """
    out = list(messages)
    # `truncate_history` is the one edit that *removes* messages, so it runs last:
    # `edit_message` addresses a message by index, and an index resolved against a
    # list that has already had three messages taken out of it would edit whatever
    # slid into that slot. Same list, same numbers, whatever order they were typed.
    for edit in sorted(edits, key=lambda e: e.op == "truncate_history"):
        if edit.op == "set_system":
            index = next(
                (i for i, m in enumerate(out) if m.get("role") == "system"), None
            )
            if index is None:
                report.rejected.append("set_system: this round has no system message")
                continue
            out[index] = {"role": "system", "content": edit.content or ""}
            report.applied.append("set_system")

        elif edit.op == "edit_message":
            index = edit.index if edit.index is not None else -1
            if not 0 <= index < len(out):
                report.rejected.append(
                    f"edit_message: no message at index {edit.index}"
                )
                continue
            out[index] = {**out[index], "content": edit.content or ""}
            report.applied.append(f"edit_message[{index}]")

        elif edit.op == "truncate_history":
            keep = max(0, edit.keep or 0)
            positions = [
                i for i, block in enumerate(snapshot.blocks) if block.kind == "history"
            ]
            if not positions:
                report.rejected.append("truncate_history: this round has no history")
                continue
            drop = set(positions[: max(0, len(positions) - keep)])
            if not drop:
                report.rejected.append(
                    f"truncate_history: this round has {len(positions)} history"
                    f" messages, so keeping {keep} drops none"
                )
                continue
            out = [m for i, m in enumerate(out) if i not in drop]
            report.applied.append(f"truncate_history(keep={keep}, dropped={len(drop)})")

    return out
