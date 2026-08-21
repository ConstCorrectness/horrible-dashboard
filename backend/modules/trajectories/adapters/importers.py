"""File importers: someone else's agent log → a `TrajectoryWrite`.

Every function here is **pure** — `(parsed content, dataset_id) -> list[TrajectoryWrite]`
with no database, no clock and no network. That is what makes them testable against
committed fixtures, and importers are exactly the code that needs fixtures: the
formats are other people's, they change without telling you, and the failure mode
is a silently mangled trajectory rather than an exception.

## `meta.raw` keeps the original

A normaliser is lossy by definition and this one will be wrong about something. The
untranslated records go into `meta.raw`, so when the mapping is fixed the data can
be re-derived instead of re-collected. Re-collecting is usually impossible — the
run happened once.

## Formats

- **`claude-code`** — the JSONL transcript Claude Code writes per session. One JSON
  object per line, each with a `type` and a `message` in Anthropic's shape, where
  tool calls are `tool_use` content blocks and results arrive as `tool_result`
  blocks in a *later* user message. Re-pairing those by `tool_use_id` is the whole
  job, and it is why the importer cannot be a simple `for line in file`.
- **`openai`** — a list of chat-completions messages, where a tool call is an
  `assistant` message with `tool_calls` and the result is the next `tool` message
  carrying `tool_call_id`.
- **`messages`** — a plain `[{role, content}]` list, for anything that has already
  been reduced to a conversation. No actions, so it imports as messages only.
"""

from __future__ import annotations

import json
from typing import Any

from backend.modules.trajectories.models import HarnessWrite, StepWrite, TrajectoryWrite

#: Formats `import_any` understands.
FORMATS = ("claude-code", "openai", "messages")


class ImportFormatError(ValueError):
    """The payload is not the format it was declared to be.

    Raised loudly rather than skipped: an importer that quietly produces zero runs
    is indistinguishable from an empty file, and the user would conclude the
    feature is broken rather than that they picked the wrong format.
    """


def _text_of(content: Any) -> str:
    """Flatten Anthropic/OpenAI content, which is a string or a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def parse_jsonl(raw: str) -> list[dict[str, Any]]:
    """Parse JSONL, skipping blank lines and reporting the line that broke.

    The line number matters: these files are tens of thousands of lines and
    "expecting value" with no position is not a diagnosis.
    """
    records: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"line {number}: {exc}") from exc
        if isinstance(value, dict):
            records.append(value)
    return records


# --- claude-code ------------------------------------------------------------


def from_claude_code(raw: str, dataset_id: str) -> list[TrajectoryWrite]:
    """One Claude Code JSONL transcript → one run.

    Tool calls and their results are in *different* records — a `tool_use` block in
    an assistant message, a `tool_result` block in a later user message keyed by
    `tool_use_id`. They are re-paired here so an action carries its own
    observation, which is the schema's central convention.
    """
    records = parse_jsonl(raw)
    if not records:
        return []
    if not any(
        "message" in r or r.get("type") in ("user", "assistant") for r in records
    ):
        raise ImportFormatError(
            "no user/assistant records found — is this a Claude Code transcript?"
        )

    # Pass one: every tool result, by the id of the call it answers.
    results: dict[str, Any] = {}
    for record in records:
        message = record.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[str(block.get("tool_use_id") or "")] = block.get("content")

    steps: list[StepWrite] = []
    goal = ""
    model = ""
    system_prompt = ""

    for record in records:
        kind = record.get("type")
        message = record.get("message") or {}
        role = message.get("role") or kind
        model = str(message.get("model") or model)

        if kind == "system" or role == "system":
            system_prompt = system_prompt or _text_of(message.get("content"))
            continue

        content = message.get("content")
        blocks = content if isinstance(content, list) else []

        if role == "user":
            # A user record that is only tool results is the transport for the
            # previous call's answer, not a turn — pairing already took it.
            text = _text_of(content)
            if text:
                if not goal:
                    goal = text.strip()[:500]
                steps.append(StepWrite(kind="message", role="user", content=text))
            continue

        if role == "assistant":
            text = _text_of(content)
            if text:
                steps.append(StepWrite(kind="message", role="assistant", content=text))
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                call_id = str(block.get("id") or "")
                result = results.get(call_id)
                is_error = isinstance(result, dict) and result.get("is_error")
                steps.append(
                    StepWrite(
                        kind="action",
                        name=str(block.get("name") or ""),
                        args=block.get("input") or {},
                        result=result,
                        ok=not is_error,
                    )
                )

    return [
        TrajectoryWrite(
            dataset_id=dataset_id,
            source="imported",
            goal=goal,
            model=model,
            status="complete",
            # Deliberately ungraded: nothing in a transcript says the session went
            # well, and inventing `success` here would put unvetted runs straight
            # into the SFT export, which only takes graded successes.
            outcome=None,
            harness=HarnessWrite(
                agent_id="claude-code", model=model, system_prompt=system_prompt
            ),
            step_list=steps,
            meta={"raw": records[:200], "format": "claude-code"},
        )
    ]


# --- openai -----------------------------------------------------------------


def from_openai(raw: str, dataset_id: str) -> list[TrajectoryWrite]:
    """A chat-completions message list → one run.

    Accepts a bare list, or an object with `messages` (a request body) — both are
    what people actually have on disk.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportFormatError(str(exc)) from exc

    if isinstance(parsed, dict):
        messages = parsed.get("messages")
        model = str(parsed.get("model") or "")
        tools = parsed.get("tools") or []
    else:
        messages, model, tools = parsed, "", []
    if not isinstance(messages, list):
        raise ImportFormatError("expected a list of messages, or {messages: [...]}")

    # Results first, keyed by the call they answer.
    results = {
        str(m.get("tool_call_id") or ""): m.get("content")
        for m in messages
        if isinstance(m, dict) and m.get("role") == "tool"
    }

    steps: list[StepWrite] = []
    goal = ""
    system_prompt = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system":
            system_prompt = system_prompt or _text_of(message.get("content"))
            continue
        if role == "tool":
            continue
        if role == "user":
            text = _text_of(message.get("content"))
            if not goal and text:
                goal = text.strip()[:500]
            steps.append(StepWrite(kind="message", role="user", content=text))
            continue
        if role == "assistant":
            text = _text_of(message.get("content"))
            if text:
                steps.append(StepWrite(kind="message", role="assistant", content=text))
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    # OpenAI sends arguments as a JSON *string*. A model that
                    # emitted malformed JSON is a fact about the run worth
                    # keeping, so an unparseable payload is preserved as the
                    # string it was rather than dropped.
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass
                steps.append(
                    StepWrite(
                        kind="action",
                        name=str(function.get("name") or ""),
                        args=arguments,
                        result=results.get(str(call.get("id") or "")),
                        ok=True,
                    )
                )

    schemas = {
        str((t.get("function") or {}).get("name") or i): t
        for i, t in enumerate(tools)
        if isinstance(t, dict)
    }
    return [
        TrajectoryWrite(
            dataset_id=dataset_id,
            source="imported",
            goal=goal,
            model=model,
            status="complete",
            outcome=None,
            harness=HarnessWrite(
                model=model,
                system_prompt=system_prompt,
                tool_names=sorted(schemas),
                tool_schemas=schemas,
            ),
            step_list=steps,
            meta={"raw": messages[:200], "format": "openai"},
        )
    ]


# --- plain messages ---------------------------------------------------------


def from_messages(raw: str, dataset_id: str) -> list[TrajectoryWrite]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImportFormatError(str(exc)) from exc
    if not isinstance(parsed, list):
        raise ImportFormatError("expected a list of {role, content} objects")

    steps: list[StepWrite] = []
    goal = ""
    for message in parsed:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        text = _text_of(message.get("content"))
        if role == "user" and not goal and text:
            goal = text.strip()[:500]
        steps.append(
            StepWrite(
                kind="message",
                role=role
                if role in ("user", "assistant", "system", "tool")
                else "user",
                content=text,
            )
        )
    return [
        TrajectoryWrite(
            dataset_id=dataset_id,
            source="imported",
            goal=goal,
            status="complete",
            outcome=None,
            step_list=steps,
            meta={"raw": parsed[:200], "format": "messages"},
        )
    ]


def import_any(fmt: str, raw: str, dataset_id: str) -> list[TrajectoryWrite]:
    if fmt == "claude-code":
        return from_claude_code(raw, dataset_id)
    if fmt == "openai":
        return from_openai(raw, dataset_id)
    if fmt == "messages":
        return from_messages(raw, dataset_id)
    raise ImportFormatError(f"unknown format '{fmt}' (known: {', '.join(FORMATS)})")
