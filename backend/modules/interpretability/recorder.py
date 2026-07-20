"""Capture what the model is actually given, one round at a time.

The agent loop assembles a fresh context every round (progressive disclosure
recomputes the tool list as the model calls `load_tools`), sends it, and moves on —
nothing has ever persisted or surfaced it. This records each round as a
`RoundSnapshot`, keeps a small ring of recent turns for the pane to page back
through, and pushes it live on the `interpretability` `/ws` channel.

Design constraints, both non-negotiable:

* **Capture must never break a turn.** It runs inside `run_agent_loop`, so every
  entry point swallows its own exceptions. A failed capture costs you a snapshot;
  it must not cost the user their answer.
* **Capture must not mutate the context.** It only ever reads `messages`/`tools`.
  Nothing here may append, reorder, or edit — the pane's entire premise is that it
  shows the real prompt, so observing it cannot change it.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from backend.modules.interpretability.models import (
    ContextBlock,
    RoundSnapshot,
    ToolEntry,
    TurnSnapshot,
)
from backend.modules.interpretability.tokenizer import Counter

logger = logging.getLogger(__name__)

# How many recent turns stay resident. Snapshots hold clipped previews, not full
# buffers, so this is bounded by roughly MAX_TURNS × rounds × MAX_BLOCK_CHARS.
MAX_TURNS = 25

# Per-block preview cap for transport. Token counts always reflect the FULL text —
# see ContextBlock.clipped.
MAX_BLOCK_CHARS = 4000

# The marker `_active_editor_message` wraps the focused buffer in. Content-sniffing
# is how we tell it apart from the guides message: both are `role: system`, and the
# orchestrator can't tag them without the tag reaching the provider.
_BUFFER_MARKER = "<<<BUFFER"

_turns: deque[TurnSnapshot] = deque(maxlen=MAX_TURNS)
# Per-turn capture state: where the assembled prompt ended and the loop's own
# appends began. Pinned at round 0 so classification stays stable as the list grows.
_prompt_end: dict[str, int] = {}


def _clip(text: str) -> tuple[str, bool, int]:
    full = len(text)
    if full <= MAX_BLOCK_CHARS:
        return text, False, full
    return text[:MAX_BLOCK_CHARS], True, full


def _as_text(content: Any) -> str:
    """Message content as the string the provider will see. Usually already a
    string; tool results and multimodal parts arrive as structures."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    import json

    try:
        return json.dumps(content, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(content)


def _classify_prompt(messages: list[dict[str, Any]]) -> list[str]:
    """Label the assembled prompt (round 0) by kind.

    Order is the contract from `run_agent_turn`:
        system → guides? → history… → editor? → user
    Roles alone can't separate system/guides/editor, so this leans on position for
    the first (always the spec's system prompt) and a content marker for the editor
    buffer. If the assembly order in orchestrator.py changes, this must change with
    it — the pane mislabelling blocks is worse than not showing them.
    """
    kinds: list[str] = []
    last_user = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
    )
    for i, msg in enumerate(messages):
        role = str(msg.get("role") or "")
        if role == "system":
            if i == 0:
                kinds.append("system")
            elif _BUFFER_MARKER in _as_text(msg.get("content")):
                kinds.append("editor")
            else:
                kinds.append("guides")
        elif role == "user":
            # The final user message is this turn's prompt; earlier ones are prior
            # conversation replayed as history.
            kinds.append("user" if i == last_user else "history")
        elif role == "assistant":
            kinds.append("history")
        elif role == "tool":
            kinds.append("tool_result")
        else:
            kinds.append(role or "unknown")
    return kinds


_LABELS = {
    "system": "System prompt",
    "guides": "Tool guides",
    "history": "Conversation history",
    "editor": "Focused editor buffer",
    "user": "User prompt",
    "assistant": "Assistant (this turn)",
    "tool_result": "Tool result",
    "nudge": "Force-tool nudge",
}


def _blocks(
    messages: list[dict[str, Any]], turn_id: str, counter: Counter
) -> list[ContextBlock]:
    """Turn the raw provider message list into labelled, counted blocks."""
    boundary = _prompt_end.get(turn_id)
    if boundary is None:
        boundary = len(messages)
        _prompt_end[turn_id] = boundary
    kinds = _classify_prompt(messages[:boundary])
    # Everything past the boundary is the loop's own work this turn.
    for msg in messages[boundary:]:
        role = str(msg.get("role") or "")
        if role == "assistant":
            kinds.append("assistant")
        elif role == "tool":
            kinds.append("tool_result")
        elif role == "system":
            kinds.append("nudge")
        else:
            kinds.append(role or "unknown")

    blocks: list[ContextBlock] = []
    for msg, kind in zip(messages, kinds):
        text = _as_text(msg.get("content"))
        # An assistant message carrying tool calls has little or no content — its
        # real context cost is the serialized calls, so count those too.
        calls = msg.get("tool_calls")
        if calls:
            text = (text + "\n" if text else "") + _as_text(calls)
        preview, clipped, full = _clip(text)
        blocks.append(
            ContextBlock(
                kind=kind,
                role=str(msg.get("role") or ""),
                label=_LABELS.get(kind, kind.replace("_", " ").title()),
                content=preview,
                tokens=counter.count(text),
                clipped=clipped,
                fullChars=full,
            )
        )
    return blocks


def _tool_entries(tools: list[dict[str, Any]], counter: Counter) -> list[ToolEntry]:
    """Per-tool schema cost. Tool JSON is usually the largest single share of an
    agent's context and the least visible, which is the main thing this pane fixes."""
    from backend.modules.agent.orchestrator import _group_of

    entries: list[ToolEntry] = []
    for tool in tools:
        name = str((tool.get("function") or {}).get("name") or "")
        try:
            group = _group_of(name) if name else ""
        except Exception:
            group = ""
        entries.append(
            ToolEntry(name=name, group=group, tokens=counter.count_json(tool))
        )
    return entries


async def capture_round(
    conn: Any,
    *,
    turn_id: str,
    agent_id: str,
    model: str,
    provider: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    round_no: int,
    tools_selected: int,
    tool_budget: int,
    active_groups: set[str] | None,
    context_size: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    tokenizer_repo: str = "",
    parent_turn_id: str | None = None,
    agent_name: str = "",
    tool_groups: list[str] | None = None,
    permission_mode: str | None = None,
) -> None:
    """Record one round and push it to the pane. Never raises."""
    try:
        counter = await Counter.create(model, tokenizer_repo)
        blocks = _blocks(messages, turn_id, counter)
        entries = _tool_entries(tools, counter)
        message_tokens = sum(b.tokens for b in blocks)
        tool_tokens = sum(t.tokens for t in entries)
        snapshot = RoundSnapshot(
            round=round_no,
            blocks=blocks,
            tools=entries,
            messageTokens=message_tokens,
            toolTokens=tool_tokens,
            totalTokens=message_tokens + tool_tokens,
            toolsSelected=tools_selected,
            toolBudget=tool_budget,
            toolsTruncated=tools_selected > tool_budget,
            activeGroups=sorted(active_groups or ()),
        )
        turn = _upsert_turn(
            turn_id,
            agent_id=agent_id,
            model=model,
            provider=provider,
            counter=counter,
            context_size=context_size,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            parent_turn_id=parent_turn_id,
            agent_name=agent_name,
            tool_groups=tool_groups,
            permission_mode=permission_mode,
        )
        turn.rounds.append(snapshot)
        await _push(conn, "round", {"turnId": turn_id, "round": snapshot.model_dump()})
    except Exception:
        logger.exception("interpretability: round capture failed (turn %s)", turn_id)


async def capture_peer_ask(
    conn: Any,
    *,
    parent_turn_id: str,
    peer_id: str,
    prompt: str,
) -> None:
    """Record that a turn reached out to another user's node via `agent.ask_peer`.

    Deliberately opaque: no rounds, no token counts. The peer's agent assembles its
    own context on its own machine, and we have no visibility into it — nor should
    we. Recording the handoff anyway is what keeps the tree honest: a delegation
    that leaves this node shows up as a leaf that says so, instead of a silent gap
    the reader has to guess about. Never raises.
    """
    try:
        preview, _clipped, _full = _clip(prompt or "")
        turn = TurnSnapshot(
            turnId=f"{parent_turn_id}:peer:{peer_id}:{time.time():.0f}",
            agentId=f"peer:{peer_id}",
            agentName=f"Peer {peer_id}",
            parentTurnId=parent_turn_id,
            startedAt=time.time(),
            kind="peer",
            peerId=peer_id,
            sentPrompt=preview,
        )
        if len(_turns) == _turns.maxlen:
            _prompt_end.pop(_turns[0].turnId, None)
        _turns.append(turn)
        await _push(conn, "peer", {"turn": turn.model_dump()})
    except Exception:
        logger.exception(
            "interpretability: peer capture failed (parent %s)", parent_turn_id
        )


def _upsert_turn(turn_id: str, **fields: Any) -> TurnSnapshot:
    for turn in _turns:
        if turn.turnId == turn_id:
            return turn
    counter: Counter = fields["counter"]
    turn = TurnSnapshot(
        turnId=turn_id,
        agentId=fields["agent_id"],
        agentName=fields.get("agent_name") or "",
        parentTurnId=fields.get("parent_turn_id"),
        toolGroups=fields.get("tool_groups"),
        permissionMode=fields.get("permission_mode"),
        model=fields["model"],
        provider=fields["provider"],
        startedAt=time.time(),
        exact=counter.exact,
        tokenizerRepo=counter.repo,
        tokenizerSource=counter.source,
        requestedNumCtx=fields.get("context_size"),
        temperature=fields.get("temperature"),
        topP=fields.get("top_p"),
        maxTokens=fields.get("max_tokens"),
    )
    if len(_turns) == _turns.maxlen:
        _prompt_end.pop(_turns[0].turnId, None)
    _turns.append(turn)
    return turn


def finish_turn(turn_id: str, model_context_length: int | None = None) -> None:
    """Drop per-turn capture state; optionally stamp the model's true window."""
    _prompt_end.pop(turn_id, None)
    if model_context_length is None:
        return
    for turn in _turns:
        if turn.turnId == turn_id:
            turn.modelContextLength = model_context_length
            return


async def _push(conn: Any, event: str, data: dict[str, Any]) -> None:
    if conn is None:
        return
    try:
        await conn.send_json(
            {"channel": "interpretability", "event": event, "data": data}
        )
    except Exception:
        # A closed socket is normal (user shut the tab mid-turn); the ring still has it.
        pass


def recent_turns(limit: int = MAX_TURNS) -> list[TurnSnapshot]:
    """Most recent first — the pane opens on the newest turn."""
    return list(reversed(list(_turns)))[:limit]


def get_turn(turn_id: str) -> TurnSnapshot | None:
    return next((t for t in _turns if t.turnId == turn_id), None)


def clear() -> None:
    _turns.clear()
    _prompt_end.clear()
