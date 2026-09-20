"""Running one voice-agent turn: retrieval, generation, and the room actions.

Split from :mod:`voice` so the policy there stays pure and testable without a model,
a network, or a connected Clubhouse account. This module is the impure half.

A turn is **one generation unless the model reaches for a tool**. The server still
decides, deterministically, everything a turn gets for free and hands it over as
context:

- **the room** is always in the prompt (see ``render_room_brief``);
- **retrieval** is pre-fetched when the config allows it and the utterance looks
  like a question, or when ``/agent search`` asked for it outright;
- **moderation** happens only through an explicit ``/agent`` command, never because
  a model emitted a call. ``invite_speaker`` acts on a real person, and a small model
  asked to choose one will eventually choose wrong.

On top of that the model is offered a deliberately small set of tools
(``voice_tools``): play music into the room, control it, look something up. A turn
that calls none costs exactly what it did before; one that calls a tool pays a second
generation to turn the result into speech. Music is *resolved* here (find or download
a file) and *played* by the pane, the only place the room's published track exists.
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
    Command,
    RoomMember,
    RoomSnapshot,
    Source,
    Turn,
    VoiceConfig,
    VoiceSession,
    build_messages,
    clean_reply,
    detect_intent,
    is_meta_reply,
    is_tool_call_text,
    parse_command,
)

logger = logging.getLogger(__name__)

# Wall-clock ceiling on one turn's generation. Increased to 45.0s to accommodate
# model weight loading and local GGUF llama.cpp inference.
GENERATION_TIMEOUT_S = 45.0

# Retrieval is on the critical path of a live conversation, so it gets a tighter
# budget than the generation it feeds.
RETRIEVAL_TIMEOUT_S = 10.0

#: Pause before the single retry of a generation that failed for a reason the model
#: server owns. Long enough for a weight swap to finish, short enough that the room
#: has not moved on: the whole turn still fits inside `GENERATION_TIMEOUT_S` twice.
RETRY_BACKOFF_S = 0.75

#: Cosine similarity a library chunk must reach before it is quoted into a turn.
#:
#: A vector search always returns its nearest neighbours, and nearest is not the same
#: as relevant: with two documents indexed, "coffee" retrieved the Python standard
#: library at 0.484 and the agent dutifully answered a question about coffee with
#: ``xml.sax.saxutils``, because the turn prompt tells it these were looked up *for
#: this question*. Measured on this embedder, an unrelated query scores 0.46-0.51 and
#: a genuinely relevant one 0.69-0.83, so the floor sits in the gap. Below it the
#: snippet is dropped and the agent answers from the room instead -- and if nothing
#: clears the floor, ``gather_context`` returns None and the model is told nothing,
#: which is the honest input for "your library has nothing on this".
#:
#: Safe because the score really is a cosine similarity here: the library's
#: `search_documents` pins `.metric("cosine")`. It would be meaningless over a
#: squared-L2 table (see the vector-store notes on `1 - distance`).
LIBRARY_RELEVANCE_FLOOR = 0.6

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
        # `continue`, not `break`: the groups come back ordered by rank fusion, so
        # `top_score` is not monotonic down the list and a low one does not mean the
        # rest are lower.
        if group.top_score < LIBRARY_RELEVANCE_FLOOR:
            continue
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


# --- tools -------------------------------------------------------------------------

#: Tool calls honoured per turn. Enough for "play X and look up Y"; small enough that
#: a confused model cannot start a dozen downloads.
MAX_TOOL_CALLS = 3

_MUSIC_ACTIONS = ("stop", "pause", "resume", "volume", "louder", "quieter")

#: How far one "louder" / "quieter" moves the room music (volume is 0-1).
VOLUME_STEP = 0.2


def _fn(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def voice_tools(config: VoiceConfig) -> list[dict[str, Any]]:
    """The tools a room agent is offered.

    Deliberately not the orchestrator's catalog: a room is strangers talking to your
    agent, so it gets music and lookup and nothing that acts on a person or a file.
    """
    tools = [
        _fn(
            "play_music",
            "Play a song into the room for everyone to hear. Use it when someone asks "
            "you to play or put on music. Pass the song and/or artist as they said "
            "it; this finds and fetches it by itself. Replaces whatever is playing.",
            {
                "query": {
                    "type": "string",
                    "description": "Song and/or artist, e.g. 'Africa by Toto'.",
                }
            },
            ["query"],
        ),
        _fn(
            "music_control",
            "Control the music you are already playing in the room: stop, pause, "
            "resume, or volume (value from 0 to 1).",
            {
                "action": {"type": "string", "enum": list(_MUSIC_ACTIONS)},
                "value": {
                    "type": "number",
                    "description": "Volume from 0 to 1, only for action=volume.",
                },
            },
            ["action"],
        ),
    ]
    if config.retrieval != "off":
        tools.append(
            _fn(
                "look_up",
                "Search the web and your libraries for something you do not know: "
                "news, dates, scores, facts about a person or thing. Use it instead "
                "of guessing.",
                {"query": {"type": "string", "description": "What to search for."}},
                ["query"],
            )
        )
    return tools


def _play_action(song_id: str, title: str, *, ready: bool) -> dict[str, Any]:
    return {"type": "music.play", "songId": song_id, "title": title, "ready": ready}


async def play_music(query: str) -> tuple[str, dict[str, Any] | None]:
    """Find-or-fetch a song for the room: (what to tell the model, pane action).

    Reuses the karaoke catalog and downloader rather than keeping a second one, but
    searches **without** the karaoke bias -- someone asking a room agent for a song
    wants the record, not the backing track. The server only makes sure a file will
    exist; the pane plays it, because the room's published track lives there.
    """
    from backend.modules.karaoke import downloader, store

    query = query.strip()
    if not query:
        return "play_music needs a song or artist.", None
    ready = [s for s in store.list_songs(query, limit=5) if s["status"] == "ready"]
    if ready:
        song = ready[0]
        return (
            f"Now playing '{song['title']}' from your library.",
            _play_action(song["id"], song["title"], ready=True),
        )
    if not downloader.available():
        return f"Can't fetch music: {downloader.INSTALL_HINT}", None
    try:
        results, note = await asyncio.wait_for(
            downloader.search(query, limit=1, karaoke_bias=False),
            timeout=RETRIEVAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return "The music search timed out.", None
    if not results:
        return f"Found nothing to play for '{query}'. {note}".strip(), None
    hit = results[0]
    existing = store.find_by_video_id(hit.video_id)
    if existing is not None:
        return (
            f"Now playing '{existing['title']}'.",
            _play_action(existing["id"], existing["title"], ready=True),
        )
    song = store.create_song(
        title=hit.title, video_id=hit.video_id, url=hit.url, status="queued"
    )
    # Audio only: the room hears the song, nobody watches it, and a single audio
    # stream is what YouTube still serves without an ffmpeg merge.
    downloader.start_download(song["id"], hit.url, audio_only=True)
    return (
        f"Found '{hit.title}'. It is downloading and will start in a moment.",
        _play_action(song["id"], hit.title, ready=False),
    )


def music_control(action: Any, value: Any = None) -> tuple[str, dict[str, Any] | None]:
    """A transport verb for the room music: (what to tell the model, pane action)."""
    verb = str(action or "").strip().lower()
    if verb not in _MUSIC_ACTIONS:
        return f"Unknown music action {verb!r}.", None
    if verb == "volume":
        try:
            level = min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return "volume needs a value from 0 to 1.", None
        return f"Music volume set to {round(level * 100)}%.", {
            "type": "music.volume",
            "value": level,
        }
    if verb in ("louder", "quieter"):
        step = VOLUME_STEP if verb == "louder" else -VOLUME_STEP
        said_step = "Turned the music up." if step > 0 else "Turned the music down."
        return said_step, {"type": "music.volume", "step": step}
    said = {"stop": "Music stopped.", "pause": "Music paused.", "resume": "Resumed."}
    return said[verb], {"type": f"music.{verb}"}


async def run_voice_tool(
    call: Any, config: VoiceConfig, room: RoomSnapshot | None = None
) -> tuple[str, dict[str, Any] | None]:
    """Run one tool call. Never raises: a failure is reported to the model, which
    then says so, rather than costing the turn its reply."""
    if getattr(call, "arg_error", None):
        return f"{call.name} got unreadable arguments: {call.arg_error}", None
    args = call.arguments or {}
    try:
        if call.name == "play_music":
            return await play_music(str(args.get("query") or ""))
        if call.name == "music_control":
            if room is not None and not room.music:
                # Said, not sent: "I stopped the music" when none was playing is the
                # confident-and-wrong reply this whole path exists to prevent.
                return "Nothing is playing right now.", None
            return music_control(args.get("action"), args.get("value"))
        if call.name == "look_up" and config.retrieval != "off":
            query = str(args.get("query") or "").strip()
            if not query:
                return "look_up needs a query.", None
            found = await gather_context(query, config)
            return (
                found or f"Nothing found for '{query}'. Say you couldn't find it.",
                None,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice tool %s failed: %s", call.name, exc)
        return f"{call.name} failed: {exc}", None
    return f"There is no tool called {call.name}.", None


def _tool_report(lines: list[str]) -> str:
    return (
        "You just used your tools. The room did not see this; only your reply will "
        "reach them:\n" + "\n".join(lines) + "\n\nNow reply to the room. Say briefly "
        "what you did or found, and claim nothing these results do not say."
    )


def _args_preview(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())[:160]


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
        if not _is_transient(exc):
            raise
        # One retry, and only for a failure the server owns. A local provider that
        # JIT-loads models drops a request while it swaps weights -- a 500 with an
        # HTML body, or a refused connection -- and the same turn sent again a moment
        # later answers normally. Without this the room hears nothing at all and the
        # pane blames the model, which is the shape of "it just never works".
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

    Returns the reply plus *why* — the pane renders the reason, because an agent that
    stays quiet for a good reason and one that is broken look identical otherwise.
    """
    config = session.config
    session.room = room

    # Auto-learn and track speaker profile / notes in persistent people memory
    from backend.modules.clubhouse.people_memory import people_memory_store

    # Never on a nudge: there is no speaker to learn about, so this used to attribute
    # the operator's own instruction to whichever name the pane had attached to it
    # ("Room Atmosphere") and extract "facts" from it.
    if speaker and speaker != "Someone" and source != "nudge":
        # By id when the pane knows it: a chat label carries an @handle that no
        # roster name matches.
        target = room.member(speaker_id) or _resolve_member(room, speaker)
        if target and target.user_id:
            people_memory_store.learn_user(
                target.user_id,
                target.name,
                bio=target.bio,
                room_topic=room.topic or room.channel,
            )
        people_memory_store.auto_extract_facts(speaker, utterance)

    command = parse_command(utterance)
    query = _strip_command(utterance) if command else utterance

    # Handle immediate synchronous memory & room commands
    if command:
        cmd_result = await run_command(command, room, session)
        if cmd_result:
            return {
                "spoke": False,
                "reason": f"command:{command.name}",
                "reply": "",
                "notice": cmd_result.get("notice", ""),
                "actions": cmd_result.get("actions", []),
            }

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

    # A nudge is not part of the conversation -- it was never said in the room. Kept
    # in the history it becomes a phantom line the agent will later summarise back
    # ("you asked me to say something"), and the twelve-turn window is small enough
    # that a few of them crowd out real speech.
    if source != "nudge":
        session.remember(
            Turn(role="room", text=utterance, speaker=speaker, source=source)
        )
    from types import SimpleNamespace

    actions: list[dict[str, Any]] = []

    async def run_calls(calls: list[Any]) -> list[dict[str, str]]:
        """Run tool calls and return the turn with their results folded into it.

        The results ride the same turn message rather than `tool` role messages:
        that keeps the roles alternating for strict templates, and the generation
        that follows offers no tools, so it has to speak.
        """
        lines = []
        for call in calls:
            said, action = await run_voice_tool(call, config, room)
            if action is not None:
                actions.append(action)
            lines.append(f"- {call.name}({_args_preview(call.arguments)}): {said}")
        return messages[:-1] + [
            {
                "role": "user",
                "content": messages[-1]["content"] + "\n\n" + _tool_report(lines),
            }
        ]

    # An unambiguous request is routed without asking the model (see
    # `detect_intent`), which also makes it one generation instead of two. A lookup
    # the server already pre-fetched is not fetched twice.
    intent = detect_intent(prompt_text, music_playing=bool(room.music))
    if (
        intent
        and intent[0] == "look_up"
        and (config.retrieval == "off" or retrieval is not None)
    ):
        intent = None
    try:
        if intent is not None:
            name, arguments = intent
            call = SimpleNamespace(name=name, arguments=arguments, arg_error=None)
            result = await generate_reply(await run_calls([call]), config)
        else:
            result = await generate_reply(messages, config, tools=voice_tools(config))
            calls = result.tool_calls[:MAX_TOOL_CALLS]
            if calls:
                # The only path that costs a second generation, and only because
                # the model asked for a tool.
                result = await generate_reply(await run_calls(calls), config)
            elif is_tool_call_text(result.content or ""):
                # The model meant to call a tool and wrote the call out as speech
                # instead. Offering it none forces words: it cannot reach for the
                # syntax it just fumbled. Costs a second generation on the turns a
                # small model gets wrong, and nothing on the turns it gets right.
                logger.info(
                    "voice: model emitted a tool call as text; regenerating without tools"
                )
                result = await generate_reply(messages, config)
        raw = result.content or ""
    except asyncio.TimeoutError:
        return {
            "spoke": False,
            "reason": "generation timed out",
            "reply": "",
            "notice": "The model took too long — the room has moved on.",
            # A song a tool already started still plays; only the words were lost.
            "actions": actions,
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
            "actions": actions,
        }
    reply = clean_reply(raw)
    if reply and is_tool_call_text(reply):
        # It fumbled the syntax twice. Silence is the right answer -- a synthesized
        # voice reading `lookup query="coffee"` into a live room is worse than a
        # turn the agent sat out -- and the reason says which of the two happened.
        return {
            "spoke": False,
            "reason": "dropped a reply that was a tool call, not speech",
            "reply": "",
            "actions": actions,
        }
    if reply and is_meta_reply(reply):
        # Neither spoken nor remembered: said aloud it is nonsense to the room, and
        # remembered it becomes an example the model copies on every later turn.
        return {
            "spoke": False,
            "reason": "dropped a reply that only acknowledged the prompt",
            "reply": "",
            "actions": actions,
        }
    if not reply:
        return {
            "spoke": False,
            "reason": "model returned nothing",
            "reply": "",
            "actions": actions,
        }

    session.remember(Turn(role="agent", text=reply))
    session.spoken.append(reply)
    session.last_reply_ts = time.time()
    filler_text: str | None = None
    if config.thinking_filler and retrieval is not None:
        filler_text = "Hmm, let me check that..."

    return {
        "spoke": True,
        "reason": "replied",
        "reply": reply,
        "retrieved": retrieval is not None,
        "filler": filler_text,
        "actions": actions,
    }


async def run_command(
    command: Command, room: RoomSnapshot, session: VoiceSession
) -> dict[str, Any] | None:
    """Execute a room or memory ``/agent`` command, or None if it isn't one."""
    from backend.modules.clubhouse import models, routes as ch
    from backend.modules.clubhouse.people_memory import people_memory_store

    cmd = command.name.lower()

    # --- Music: the same paths as the tools, for when a small model fumbles a call ---
    if cmd == "play":
        if not command.arg:
            return {"handled": True, "notice": "Usage: /agent play <song or artist>"}
        said, action = await play_music(command.arg)
        return {"handled": True, "notice": said, "actions": [action] if action else []}
    if cmd in {"stop", "pause", "resume", "volume"}:
        said, action = music_control(cmd, command.arg or None)
        return {"handled": True, "notice": said, "actions": [action] if action else []}

    # --- People Knowledge & Memory Commands (available to everyone) ---
    if cmd in {"whois", "profile"}:
        if not command.arg:
            return {
                "handled": True,
                "notice": "Usage: /agent whois <name or @username>",
            }
        person = people_memory_store.find_by_name_or_username(command.arg)
        if person is None:
            # Check current room members
            mem = _resolve_member(room, command.arg)
            if mem:
                person = people_memory_store.learn_user(
                    mem.user_id, mem.name, bio=mem.bio, room_topic=room.topic
                )
        if person is None:
            return {
                "handled": True,
                "notice": f"I don't have profile memory for “{command.arg}” yet.",
            }
        parts = [f"👤 {person.name}"]
        if person.username:
            parts[0] += f" (@{person.username})"
        if person.bio:
            parts.append(f"Bio: {person.bio}")
        if person.notes:
            parts.append("Learned notes: " + "; ".join(person.notes))
        if person.tags:
            parts.append("Tags: " + ", ".join(person.tags))
        if person.rooms_seen:
            parts.append(f"Seen in {len(person.rooms_seen)} room(s)")
        return {"handled": True, "notice": " | ".join(parts)}

    if cmd == "remember":
        # Format: /agent remember <name> <fact> or /agent remember @user <fact>
        parts = command.arg.split(maxsplit=1)
        if len(parts) < 2:
            return {
                "handled": True,
                "notice": "Usage: /agent remember <name> <fact to remember>",
            }
        target_name, fact = parts[0], parts[1]
        person = people_memory_store.find_by_name_or_username(target_name)
        if person is None:
            mem = _resolve_member(room, target_name)
            if mem and mem.user_id:
                person = people_memory_store.learn_user(
                    mem.user_id, mem.name, bio=mem.bio, room_topic=room.topic
                )
        if person:
            people_memory_store.add_note(person.user_id, fact)
            return {
                "handled": True,
                "notice": f"🧠 Remembered about {person.name}: “{fact}”",
            }
        return {
            "handled": True,
            "notice": f"Couldn't identify “{target_name}” in this room or memory.",
        }

    if cmd == "forget":
        if not command.arg:
            return {"handled": True, "notice": "Usage: /agent forget <name>"}
        if people_memory_store.forget_person(command.arg):
            return {
                "handled": True,
                "notice": f"🧹 Wiped memories for “{command.arg}”.",
            }
        return {
            "handled": True,
            "notice": f"No stored memory found for “{command.arg}”.",
        }

    if cmd == "notes":
        if not command.arg:
            return {"handled": True, "notice": "Usage: /agent notes <name>"}
        person = people_memory_store.find_by_name_or_username(command.arg)
        if not person or not person.notes:
            return {"handled": True, "notice": f"No notes stored for “{command.arg}”."}
        return {
            "handled": True,
            "notice": f"Notes for {person.name}: " + "; ".join(person.notes),
        }

    if cmd in {"people", "roster"}:
        known = [p for m in room.members if (p := people_memory_store.get(m.user_id))]
        if not known:
            return {
                "handled": True,
                "notice": f"There are {len(room.members)} people in this room, none in persistent memory yet.",
            }
        summary = ", ".join(f"{p.name} ({len(p.notes)} notes)" for p in known[:10])
        return {
            "handled": True,
            "notice": f"Recognized {len(known)} people in room: {summary}",
        }

    if cmd == "tag":
        parts = command.arg.split(maxsplit=1)
        if len(parts) < 2:
            return {"handled": True, "notice": "Usage: /agent tag <name> <tag>"}
        target_name, tag = parts[0], parts[1]
        person = people_memory_store.find_by_name_or_username(target_name)
        if person:
            people_memory_store.add_tag(person.user_id, tag)
            return {"handled": True, "notice": f"Tagged {person.name} with #{tag}"}
        return {"handled": True, "notice": f"Couldn't find “{target_name}”."}

    # --- Moderator-only room control commands ---
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
                    is_enabled=enable,
                    handraise_permission=models.HandraisePermission.everyone,
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
