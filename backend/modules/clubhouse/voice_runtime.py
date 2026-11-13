"""Running one voice-agent turn: gate already passed, build the prompt, generate.

Split from :mod:`voice` so the policy there stays pure and testable without a model,
a network, or a connected Clubhouse account. This module is the impure half.

A turn is **exactly one generation**, with no tools offered. The agent talks; it does
not search, play music, remember people or moderate the room. Everything it knows
about the room arrives in the prompt (see ``voice.render_room_brief``), because "who
is here" is the most-asked question of a room agent and the honest alternative to
having it in the prompt is a confident invention.

Every outcome is written to the session's debug log (``VoiceSession.log``): the reply
that was spoken, the reply that was thrown away and why, the failure and its cause.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from typing import Any

from backend.modules.clubhouse.voice import (
    RoomSnapshot,
    Source,
    Turn,
    TurnEvent,
    VoiceConfig,
    VoiceSession,
    build_messages,
    clean_reply,
    fold_consecutive_roles,
    has_consecutive_roles,
    is_meta_reply,
)

logger = logging.getLogger(__name__)

# Wall-clock ceiling on one turn's generation. Increased to 45.0s to accommodate
# model weight loading and local GGUF llama.cpp inference.
GENERATION_TIMEOUT_S = 45.0

#: Pause before the single retry of a generation that failed for a reason the model
#: server owns. Long enough for a weight swap to finish, short enough that the room
#: has not moved on: the whole turn still fits inside `GENERATION_TIMEOUT_S` twice.
RETRY_BACKOFF_S = 0.75


def _is_transient(exc: Exception) -> bool:
    """Whether this failure is worth sending the same request into again.

    A 5xx, a timeout or a dropped connection is the server's problem and may well not
    recur. A 4xx is ours -- a model that isn't loaded, a malformed payload -- and
    retrying it only doubles the wait before the pane can say so.
    """
    import httpx

    if isinstance(exc, asyncio.TimeoutError):
        return False  # the turn's own deadline; the room has already moved on
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


#: Words an HTTP status line is already made of. A body containing only these is the
#: status code spelled out, not a reason.
_STATUS_BOILERPLATE = frozenset(
    """
    doctype html head body title pre error errors internal server service
    bad gateway request unavailable timeout timed out not found forbidden
    unauthorized failure failed unknown occurred an the a
    """.split()
)


def _plain_text(body: str) -> str:
    """The readable part of an error body, or "" if there isn't one.

    A provider that fails inside its own web layer answers with an HTML page rather
    than JSON (LM Studio returns Express's ``<pre>Internal Server Error</pre>``), and
    reporting that verbatim put a DOCTYPE in a toast in the room pane. Tags are
    stripped; a body with nothing left but boilerplate yields "", which the caller
    renders as the bare status code.
    """
    # A JSON body that `response.json()` refused (the wrong content-type, usually)
    # still holds the reason in plain sight; reported raw it reads as source code.
    stripped = (body or "").strip()
    if stripped.startswith("{"):
        try:
            err = json.loads(stripped).get("error")
            if isinstance(err, str) and err.strip():
                return err.strip()[:200]
        except Exception:  # noqa: BLE001 — it was a guess; fall through to stripping
            pass
    text = re.sub(r"(?is)<(script|style).*?</>", " ", body or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = " ".join(text.split())[:200]
    # "Error Internal Server Error" is the page's title plus its body -- true, and it
    # says nothing the status code doesn't. A body made of nothing but the words a
    # status line is already built from is dropped, so the caller falls back to
    # reporting the code itself.
    words = re.findall(r"[a-z]+", text.lower())
    return "" if words and set(words) <= _STATUS_BOILERPLATE else text


#: What a chat template that enforces user/assistant alternation says when handed
#: ``user, user``: "Conversation roles must alternate user/assistant/user/..." from the
#: Mistral and older Gemma templates, or LM Studio's "Error rendering prompt with jinja
#: template" wrapped around it.
_ROLE_ORDER_REJECTION = re.compile(r"alternat|jinja|roles? must", re.I)

#: `(endpoint, model)` pairs that have rejected consecutive same-role messages. Once a
#: model has said so, every later turn is folded up front: a rejected request costs a
#: round trip -- and on a local server that is JIT-swapping weights, seconds -- that a
#: live room cannot spend on every utterance.
_FOLD_REQUIRED: set[tuple[str, str]] = set()


def _rejects_role_order(exc: Exception) -> bool:
    """Whether the server refused the request because of the *shape* of the history."""
    import httpx

    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    return bool(_ROLE_ORDER_REJECTION.search(f"{exc} {exc.response.text[:600]}"))


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
            err = body.get("error")
            # `error` is a string on some servers and an object on others. LM Studio
            # answers `{"error": "Model is unloaded."}` -- the most actionable message
            # this route ever gets -- and reading `.get("message")` off it threw, so
            # it fell through to the raw-body branch and was reported as JSON source.
            if isinstance(err, str):
                detail = err
            elif isinstance(err, dict):
                detail = str(err.get("message") or "")
            detail = detail or str(body.get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = _plain_text(exc.response.text)
        return f"The model rejected the request: {detail or exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"Couldn't reach the model server: {exc}"
    return f"The agent couldn't answer: {exc}"


async def generate_reply(
    messages: list[dict[str, str]],
    config: VoiceConfig,
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    """One completion through the configured provider, as a ``providers.ChatResult``.

    ``tools`` are offered when given; the caller runs any calls that come back.

    Uses ``providers.chat`` (not ``generate``) because this turn carries a real
    message history, and ``generate`` is the single-prompt shape. The provider seam
    flattens the system tier for templates that only accept one system message.
    """
    from backend.modules.agent import providers as P
    from backend.modules.agent.routes import _endpoint_for, _load_config
    from backend.modules.settings.routes import get_value
    from backend.modules.telemetry.instrument import instrumented_client

    config_obj = _load_config()
    if config_obj is None:
        raise RuntimeError("Agent not configured — finish onboarding")
    info = P.provider_for(config_obj.provider)
    endpoint = _endpoint_for(info, config_obj)
    model = (config.model or "").strip() or config_obj.model

    timeout_val = float(
        get_value("voice.generationTimeout", GENERATION_TIMEOUT_S)
        or GENERATION_TIMEOUT_S
    )

    fold_key = (endpoint, model)
    if fold_key in _FOLD_REQUIRED:
        messages = fold_consecutive_roles(messages)

    async def once() -> Any:
        async with instrumented_client(timeout=timeout_val) as client:
            return await asyncio.wait_for(
                P.chat(
                    client,
                    info,
                    endpoint,
                    model,
                    messages,
                    tools or [],
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    # A spoken reply is two or three sentences under a ~160-token
                    # cap. A thinking model left at its default spends that entire
                    # cap reasoning and answers with nothing (Gemma 4 does this by
                    # default).
                    think=False,
                ),
                timeout=timeout_val,
            )

    try:
        result = await once()
    except Exception as exc:  # noqa: BLE001
        if _rejects_role_order(exc) and has_consecutive_roles(messages):
            # This model's template cannot take one message per utterance. Say so in
            # the log, remember it, and send the same conversation folded. Checked
            # before the transient retry: a 500 from a template error is not
            # transient, and resending the identical request would only fail again.
            logger.info(
                "voice: %s rejected consecutive same-role messages; folding", model
            )
            _FOLD_REQUIRED.add(fold_key)
            messages = fold_consecutive_roles(messages)
            result = await once()
        elif not _is_transient(exc):
            raise
        else:
            # One retry, and only for a failure the server owns. A local provider
            # that JIT-loads models drops a request while it swaps weights -- a 500
            # with an HTML body, or a refused connection -- and the same turn sent
            # again a moment later answers normally. Without this the room hears
            # nothing at all and the pane blames the model, which is the shape of
            # "it just never works".
            logger.info("voice generation retrying after transient failure: %s", exc)
            await asyncio.sleep(RETRY_BACKOFF_S)
            result = await once()
    if (
        not (result.content or "").strip()
        and not result.tool_calls
        and (
            result.assistant_message.get("reasoning_content")
            or result.assistant_message.get("thinking")
        )
    ):
        # Some models reason regardless of the switch (R1 distills, for one). Said
        # out loud rather than returned as "", which the pane shows as an agent that
        # chose to stay quiet -- the failure that made this look like a prompt bug.
        raise RuntimeError(
            "the model spent its whole reply budget thinking and never answered. "
            "Pick a non-reasoning model for this room, or raise max tokens."
        )
    return result


def _model_label(config: VoiceConfig) -> str:
    """The model this room's turns run on, for the log. Never raises."""
    override = (config.model or "").strip()
    if override:
        return override
    try:
        from backend.modules.agent.routes import _load_config

        loaded = _load_config()
        return str(getattr(loaded, "model", "") or "")
    except Exception:  # noqa: BLE001 -- a label is not worth a failed turn
        return ""


def _usage_detail(result: Any) -> str:
    usage = getattr(result, "usage", None)
    tokens_in = getattr(usage, "tokens_in", None)
    tokens_out = getattr(usage, "tokens_out", None)
    if tokens_in is None and tokens_out is None:
        return ""
    return f"tokens {tokens_in if tokens_in is not None else '?'} in / " + (
        f"{tokens_out if tokens_out is not None else '?'} out"
    )


async def run_turn(
    session: VoiceSession,
    room: RoomSnapshot,
    utterance: str,
    *,
    speaker: str = "",
    source: Source = "voice",
    speaker_id: int | None = None,
) -> dict[str, Any]:
    """Produce the agent's reply for one utterance.

    Returns the reply plus *why* -- the pane renders the reason, because an agent that
    stays quiet for a good reason and one that is broken look identical otherwise.
    """
    config = session.config
    session.room = room
    model = _model_label(config)

    messages = build_messages(
        config,
        room,
        list(session.history),
        utterance,
        speaker=speaker,
        source=source,
    )

    # A nudge is not part of the conversation -- it was never said in the room. Kept
    # in the history it becomes a phantom line the agent will later summarise back
    # ("you asked me to say something"), and the twelve-turn window is small enough
    # that a few of them crowd out real speech.
    if source != "nudge":
        session.remember(
            Turn(role="room", text=utterance, speaker=speaker, source=source)
        )

    def event(kind: str, reason: str, **extra: Any) -> None:
        session.log(
            TurnEvent(
                kind=kind,  # type: ignore[arg-type]
                text=extra.pop("text", utterance),
                reason=reason,
                source=source,
                speaker=speaker,
                model=model,
                **extra,
            )
        )

    started = time.monotonic()

    def elapsed_ms() -> int:
        return round((time.monotonic() - started) * 1000)

    try:
        result = await generate_reply(messages, config)
        raw = result.content or ""
    except asyncio.TimeoutError:
        event("error", "generation timed out", ms=elapsed_ms())
        return {
            "spoke": False,
            "reason": "generation timed out",
            "reply": "",
            "notice": "The model took too long — the room has moved on.",
        }
    except Exception as exc:  # noqa: BLE001
        # A provider failure is reported, never raised. A 500 out of this route is
        # indistinguishable in the pane from an agent that chose not to answer, and
        # "the model isn't loaded" is precisely the thing the user needs told --
        # it is the most common cause of a room agent that has gone quiet.
        notice = _explain_provider_error(exc)
        event("error", "model error", ms=elapsed_ms(), detail=notice)
        logger.warning("voice generation failed: %s", exc, exc_info=True)
        return {
            "spoke": False,
            "reason": "model error",
            "reply": "",
            "notice": notice,
        }

    ms = elapsed_ms()
    detail = _usage_detail(result)
    reply = clean_reply(raw)
    if reply and is_meta_reply(reply):
        # Neither spoken nor remembered: said aloud it is nonsense to the room, and
        # remembered it becomes an example the model copies on every later turn.
        event(
            "silent",
            "dropped a reply that only acknowledged the prompt",
            text=reply,
            ms=ms,
            detail=detail,
        )
        return {
            "spoke": False,
            "reason": "dropped a reply that only acknowledged the prompt",
            "reply": "",
        }
    if not reply:
        event("silent", "model returned nothing", ms=ms, detail=detail)
        return {"spoke": False, "reason": "model returned nothing", "reply": ""}

    session.remember(Turn(role="agent", text=reply))
    session.spoken.append(reply)
    session.last_reply_ts = time.time()
    event("replied", "replied", text=reply, ms=ms, detail=detail)
    return {"spoke": True, "reason": "replied", "reply": reply}
