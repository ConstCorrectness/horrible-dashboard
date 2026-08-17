"""Running one voice-agent turn: retrieval, generation, and the room actions.

Split from :mod:`voice` so the policy there stays pure and testable without a model,
a network, or a connected Clubhouse account. This module is the impure half.

The turn is deliberately **one generation with no tool round**. What the model would
have decided with tools, the server decides here first — deterministically — and
hands over as context:

- **the room** is always in the prompt (see ``render_room_brief``);
- **retrieval** runs when the config allows it and the utterance looks like a
  question, or when ``/agent search`` asked for it outright;
- **moderation** happens only through an explicit ``/agent`` command, never because
  a model emitted a call. ``invite_speaker`` acts on a real person, and a small model
  asked to choose one will eventually choose wrong.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from backend.modules.clubhouse.voice import (
    Command,
    RoomMember,
    RoomSnapshot,
    Source,
    Turn,
    VoiceConfig,
    VoiceSession,
    build_messages,
    clean_reply,
    parse_command,
)

logger = logging.getLogger(__name__)

# Wall-clock ceiling on one turn's generation. A reply that lands after the room has
# moved on is worse than no reply: people have started a new subject and the agent
# answers the old one. Better to drop it and say so.
GENERATION_TIMEOUT_S = 25.0

# Retrieval is on the critical path of a live conversation, so it gets a tighter
# budget than the generation it feeds.
RETRIEVAL_TIMEOUT_S = 8.0

_QUESTION_HINTS = (
    "who is",
    "who's",
    "what is",
    "what's",
    "when did",
    "when was",
    "where is",
    "how many",
    "how much",
    "look up",
    "search for",
    "google",
    "latest",
    "news about",
)


def wants_retrieval(text: str, config: VoiceConfig) -> bool:
    """Whether this turn should reach for outside information.

    Heuristic on purpose. The alternative — asking the model whether it needs to
    search — is a second generation, which is exactly the round-trip this design
    exists to avoid.
    """
    if config.retrieval == "off":
        return False
    stripped = text.strip()
    if stripped.startswith("/agent search") or stripped.startswith("/agent lookup"):
        return True
    if config.retrieval != "auto":
        return False
    lowered = stripped.lower()
    return any(hint in lowered for hint in _QUESTION_HINTS)


async def _web_snippets(query: str, limit: int = 4) -> list[str]:
    """Top web hits as prose lines. Never raises — a dead search provider must cost
    the turn its citations, not its reply."""
    try:
        from backend.modules.search.pipeline import quick_search

        answer = await asyncio.wait_for(
            quick_search(query, limit=limit), timeout=RETRIEVAL_TIMEOUT_S
        )
    except Exception:  # noqa: BLE001
        logger.debug("voice retrieval: web search failed", exc_info=True)
        return []
    lines = []
    for hit in answer.hits[:limit]:
        snippet = " ".join((hit.snippet or "")[:300].split())
        if snippet:
            lines.append(f"- {hit.title} ({hit.host}): {snippet}")
    return lines


async def _library_snippets(query: str, library: str, limit: int = 3) -> list[str]:
    """Top library chunks as prose lines. Same swallow-and-degrade contract."""
    try:
        from backend.modules.library.models import LibrarySearchRequest
        from backend.modules.library.routes import search as library_search

        res = await asyncio.wait_for(
            library_search(
                LibrarySearchRequest(library=library, text=query, limit=limit)
            ),
            timeout=RETRIEVAL_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        logger.debug("voice retrieval: library search failed", exc_info=True)
        return []
    lines = []
    for group in res.groups[:limit]:
        chunk = group.chunks[0].text if group.chunks else ""
        text = " ".join(chunk[:300].split())
        if text:
            lines.append(f"- from your library, '{group.title}': {text}")
    return lines


async def gather_context(query: str, config: VoiceConfig) -> str | None:
    """Web + library snippets for one turn, or None when nothing came back.

    Both run concurrently: they are independent, and a live room pays for the slower
    one either way.
    """
    web, lib = await asyncio.gather(
        _web_snippets(query),
        _library_snippets(query, config.library),
    )
    lines = web + lib
    if not lines:
        return None
    return (
        "You looked these up just now. Use them to answer, and say where something "
        "came from if it matters. If they do not answer the question, say you could "
        "not find it — do not fill the gap from memory:\n" + "\n".join(lines)
    )


def _strip_command(text: str) -> str:
    """`/agent search foo` → `foo`; anything else unchanged."""
    return re.sub(r"^\s*/agent\s+\w+\s*", "", text).strip()


def _is_on(arg: str) -> bool:
    """`on`/`off` for a moderation toggle. Anything unrecognized reads as **off**,
    because these commands are also spoken through imperfect transcription and the
    safe default for "enable chat for the whole room" is not to."""
    return arg.strip().lower() in {"on", "true", "1", "enable", "enabled", "yes"}


def _resolve_member(room: RoomSnapshot, needle: str) -> RoomMember | None:
    """Find a person in the room by name, forgivingly.

    Matched against the room roster rather than Clubhouse's user search on purpose:
    an invite must only ever reach someone who is actually here, and a global search
    for "dave" would happily return a stranger.
    """
    query = needle.strip().lower().lstrip("@")
    if not query:
        return None
    members = [m for m in room.members if m.name]
    for m in members:
        if m.name.lower() == query:
            return m
    partial = [m for m in members if query in m.name.lower()]
    # Ambiguity resolves to nothing: inviting the wrong person onto a stage is not
    # an error you can take back quietly.
    return partial[0] if len(partial) == 1 else None


def _explain_provider_error(exc: Exception) -> str:
    """Turn a provider failure into something a person in a room can act on.

    Providers answer with their real reason in the body ("Failed to load model X"),
    which `raise_for_status` discards in favour of the status code — so the useful
    half is dug back out here rather than reporting a bare 400.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        detail = ""
        try:
            body = exc.response.json()
            detail = (
                (body.get("error") or {}).get("message") or body.get("detail") or ""
            )
        except Exception:  # noqa: BLE001
            detail = exc.response.text[:200]
        return f"The model rejected the request: {detail or exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"Couldn't reach the model server: {exc}"
    return f"The agent couldn't answer: {exc}"


async def generate_reply(messages: list[dict[str, str]], config: VoiceConfig) -> str:
    """One completion through the configured provider.

    Uses ``providers.chat`` (not ``generate``) because this turn carries a real
    message history, and ``generate`` is the single-prompt shape. The provider seam
    flattens the system tier for templates that only accept one system message.
    """
    from backend.modules.agent import providers as P
    from backend.modules.agent.routes import _load_config
    from backend.modules.telemetry.instrument import instrumented_client

    config_obj = _load_config()
    if config_obj is None:
        raise RuntimeError("Agent not configured — finish onboarding")
    info = P.provider_for(config_obj.provider)
    endpoint = config_obj.endpoint or info.default_endpoint
    model = config.model or config_obj.model

    async with instrumented_client(timeout=GENERATION_TIMEOUT_S) as client:
        result = await asyncio.wait_for(
            P.chat(client, info, endpoint, model, messages, []),
            timeout=GENERATION_TIMEOUT_S,
        )
    return result.content or ""


async def run_turn(
    session: VoiceSession,
    room: RoomSnapshot,
    utterance: str,
    *,
    speaker: str = "",
    source: Source = "voice",
) -> dict[str, Any]:
    """Produce the agent's reply for one utterance.

    Returns the reply plus *why* — the pane renders the reason, because an agent that
    stays quiet for a good reason and one that is broken look identical otherwise.
    """
    config = session.config
    session.room = room

    command = parse_command(utterance)
    query = _strip_command(utterance) if command else utterance

    retrieval: str | None = None
    if wants_retrieval(utterance, config) and query:
        retrieval = await gather_context(query, config)

    # A search command with nothing found is worth saying out loud: the alternative
    # is the model inventing an answer to a question the user explicitly asked it to
    # look up, which is the exact failure `/agent search` used to have by design.
    if command and command.name in {"search", "lookup"} and retrieval is None:
        return {
            "spoke": False,
            "reason": "search returned nothing",
            "reply": "",
            "notice": f"Couldn't find anything for “{query}”.",
        }

    prompt_text = (
        query if command and command.name in {"search", "lookup"} else utterance
    )
    messages = build_messages(
        config,
        room,
        list(session.history),
        prompt_text,
        speaker=speaker,
        source=source,
        retrieval=retrieval,
    )

    session.remember(Turn(role="room", text=utterance, speaker=speaker, source=source))
    try:
        raw = await generate_reply(messages, config)
    except asyncio.TimeoutError:
        return {
            "spoke": False,
            "reason": "generation timed out",
            "reply": "",
            "notice": "The model took too long — the room has moved on.",
        }
    except Exception as exc:  # noqa: BLE001
        # A provider failure is reported, never raised. A 500 out of this route is
        # indistinguishable in the pane from an agent that chose not to answer, and
        # "the model isn't loaded" is precisely the thing the user needs told —
        # it is the most common cause of a room agent that has gone quiet.
        logger.warning("voice generation failed: %s", exc)
        return {
            "spoke": False,
            "reason": "model error",
            "reply": "",
            "notice": _explain_provider_error(exc),
        }
    reply = clean_reply(raw)
    if not reply:
        return {"spoke": False, "reason": "model returned nothing", "reply": ""}

    session.remember(Turn(role="agent", text=reply))
    session.spoken.append(reply)
    session.last_reply_ts = time.time()
    return {
        "spoke": True,
        "reason": "replied",
        "reply": reply,
        "retrieved": retrieval is not None,
    }


async def run_command(
    command: Command, room: RoomSnapshot, session: VoiceSession
) -> dict[str, Any] | None:
    """Execute a room-changing ``/agent`` command, or None if it isn't one.

    Every branch checks moderator status against the **room snapshot**, not against
    what the caller claims: the pane sends the snapshot, but the check living here
    means one place decides, and it is the same place for chat and voice.
    """
    from backend.modules.clubhouse import models, routes as ch

    if not command.moderator_only:
        return None
    if not room.am_i_moderator():
        return {"handled": True, "notice": "Only moderators can do that."}

    channel = room.channel
    try:
        if command.name == "topic":
            if not command.arg:
                return {"handled": True, "notice": "Give me a topic to set."}
            await ch.update_channel_topic(
                channel, models.UpdateTopicRequest(topic=command.arg)
            )
            return {"handled": True, "notice": f"Room topic set to: {command.arg}"}
        if command.name == "chat":
            enable = _is_on(command.arg)
            await ch.update_chat_settings(
                channel, models.ChatSettingsRequest(enable_chat=enable)
            )
            return {
                "handled": True,
                "notice": f"Room chat {'enabled' if enable else 'disabled'}.",
            }
        if command.name == "handraise":
            enable = _is_on(command.arg)
            await ch.change_handraise_settings(
                channel,
                models.HandraiseSettingsRequest(
                    is_enabled=enable, handraise_permission=1
                ),
            )
            return {
                "handled": True,
                "notice": f"Hand raising {'enabled' if enable else 'disabled'}.",
            }
        if command.name == "invite":
            target = _resolve_member(room, command.arg)
            if target is None:
                return {
                    "handled": True,
                    "notice": f"No one here matches “{command.arg}”.",
                }
            await ch.invite_speaker(
                channel, models.InviteUserRequest(user_id=target.user_id)
            )
            return {
                "handled": True,
                "notice": f"Invited {target.name or target.user_id} to speak.",
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice command %s failed: %s", command.name, exc)
        return {"handled": True, "notice": f"That didn't work: {exc}"}
    return None
