"""The Clubhouse voice agent: a server-side conversation session per room.

The voice agent used to be a one-shot ``POST /api/agent/generate`` issued from
``RoomsPanel.tsx`` with the raw utterance as the whole prompt. That shape has three
faults this module exists to fix:

- **No memory.** Every utterance was an independent completion, so the agent could
  not follow a conversation, answer "what did you just say?", or avoid repeating
  itself. A room is a *conversation*; a stateless completion cannot hold one.
- **No room knowledge.** It did not know the topic, who was on stage, who was
  moderating, or even its own name — so "who's here?" was answered by hallucination.
- **No way to look anything up.** ``/agent search`` built a prompt politely asking
  the model to "perform a simulated web search", which is not a search.

**Single generation per turn, deliberately.** The model never emits a tool call and
never gets a second round: a live room is a realtime medium, and a tool-calling loop
against a local model spends seconds per round while people are talking over the
silence. Instead the *server* decides — deterministically, before generating — what
context this turn needs (room brief, transcript, retrieval), assembles it, and runs
exactly one completion. Capability without the latency of agency.

The session is **process-global and keyed by channel** (the karaoke precedent): the
pane is a renderer, so a reload, a second pane, or a workspace switch rejoins the same
conversation rather than resetting it.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

# How many prior turns ride in the prompt. Small on purpose: this is a spoken
# conversation on a local model, and the room brief + retrieval already cost tokens.
DEFAULT_MEMORY_TURNS = 12

# Hard ceiling on one spoken reply. TTS reads every character aloud, so a model that
# ignores "be brief" holds the floor for a minute — the room's problem, not the log's.
DEFAULT_MAX_TOKENS = 160

# A reply may not begin within this many seconds of the last one unless it was
# explicitly addressed. Without it, two people talking produces two overlapping
# answers and the agent argues with itself.
DEFAULT_COOLDOWN_S = 6.0

Posture = Literal["addressed", "conversational", "always"]
Source = Literal["voice", "chat"]

DEFAULT_WAKE_WORDS = ["agent", "assistant", "bot"]

DEFAULT_PERSONA = (
    "You are a participant in a live Clubhouse audio room. You are speaking out "
    "loud to a room of people, not writing."
)

# Style rules are separated from the persona because the user edits the persona and
# must not be able to delete the things that make speech intelligible — a numbered
# list or a 400-word answer is unusable read aloud, and every model reaches for both.
SPEECH_RULES = (
    "How to speak here:\n"
    "- Two or three sentences. You are one voice among many; a monologue is rude.\n"
    "- Plain spoken prose. No markdown, no bullet points, no numbered lists, no "
    "emoji, no stage directions — every character is read aloud by a speech "
    "synthesizer.\n"
    "- Never say your own name as a prefix and never write 'Agent:' — you are "
    "already speaking as yourself.\n"
    "- Address people by name when replying to them.\n"
    "- If you do not know something and were given nothing to look it up with, say "
    "so in one sentence. Do not invent facts about the room or the people in it.\n"
    "- The transcript comes from imperfect speech recognition. If an utterance is "
    "garbled, ask for a repeat rather than guessing at it."
)


@dataclass
class RoomMember:
    """One person in the room, as the pane currently sees them.

    The pane is the authority here, not this module: it holds the live PubNub feed
    and the Agora volume indicator, so speaker/mute/hand/speaking state is *pushed*
    with each turn. Re-deriving it server-side would mean polling the Clubhouse API
    and being seconds stale about who is talking right now.
    """

    user_id: int
    name: str = ""
    is_speaker: bool = False
    is_moderator: bool = False
    is_muted: bool = False
    hand_raised: bool = False
    speaking: bool = False
    bio: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RoomMember:
        return cls(
            user_id=int(raw.get("user_id") or raw.get("userId") or 0),
            name=str(raw.get("name") or "").strip(),
            is_speaker=bool(raw.get("is_speaker") or raw.get("isSpeaker")),
            is_moderator=bool(raw.get("is_moderator") or raw.get("isModerator")),
            is_muted=bool(raw.get("is_muted") or raw.get("isMuted")),
            hand_raised=bool(raw.get("hand_raised") or raw.get("handRaised")),
            speaking=bool(raw.get("speaking")),
            bio=(raw.get("bio") or None),
        )


@dataclass
class RoomSnapshot:
    """The room as of this turn."""

    channel: str
    topic: str | None = None
    club: str | None = None
    members: list[RoomMember] = field(default_factory=list)
    my_user_id: int | None = None
    my_name: str = "the agent"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RoomSnapshot:
        return cls(
            channel=str(raw.get("channel") or ""),
            topic=(raw.get("topic") or None),
            club=(raw.get("club") or None),
            members=[RoomMember.from_dict(u) for u in (raw.get("members") or [])],
            my_user_id=raw.get("my_user_id") or raw.get("myUserId"),
            my_name=str(raw.get("my_name") or raw.get("myName") or "the agent"),
        )

    def member(self, user_id: int | None) -> RoomMember | None:
        if user_id is None:
            return None
        return next((m for m in self.members if m.user_id == user_id), None)

    def am_i_moderator(self) -> bool:
        me = self.member(self.my_user_id)
        return bool(me and me.is_moderator)

    def am_i_speaker(self) -> bool:
        me = self.member(self.my_user_id)
        return bool(me and me.is_speaker)


@dataclass
class VoiceConfig:
    """Per-room agent settings. Owned by the pane, persisted by the caller."""

    enabled: bool = False
    posture: Posture = "addressed"
    wake_words: list[str] = field(default_factory=lambda: list(DEFAULT_WAKE_WORDS))
    persona: str = DEFAULT_PERSONA
    temperature: float = 0.7
    max_tokens: int = DEFAULT_MAX_TOKENS
    memory_turns: int = DEFAULT_MEMORY_TURNS
    cooldown_s: float = DEFAULT_COOLDOWN_S
    respond_to_voice: bool = True
    respond_to_chat: bool = False
    # Whether a turn may reach the web / the user's libraries. "command" restricts it
    # to an explicit `/agent search`; "auto" also lets a question trigger a lookup.
    retrieval: Literal["off", "command", "auto"] = "command"
    library: str = "default"
    # Which model answers. Blank = whatever the agent is configured with. A live room
    # is a latency budget, not a reasoning budget: the orchestrator may be on a 27B
    # that takes 20 seconds a turn, which is unusable when people are talking.
    model: str = ""
    # Speak replies aloud (TTS into the room) vs. only posting them to room chat.
    speak: bool = True
    post_to_chat: bool = True
    robot_emoji_prefix: bool = False
    tts_voice: str = "en-US-ChristopherNeural"
    tts_rate: str = "+0%"
    tts_pitch: str = "+0Hz"
    # Conversational flow and human-like timing
    turn_eagerness: Literal["fast", "normal", "patient"] = "normal"
    endpointing_delay_ms: int = 750
    thinking_filler: bool = True
    silence_timeout_s: float = 0.0
    allow_barge_in: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VoiceConfig:
        base = cls()
        words = raw.get("wake_words") or raw.get("wakeWords")
        eagerness = raw.get("turn_eagerness") or raw.get("turnEagerness") or base.turn_eagerness
        endpointing = int(raw.get("endpointing_delay_ms", raw.get("endpointingDelayMs", base.endpointing_delay_ms)))
        if eagerness == "fast" and "endpointing_delay_ms" not in raw and "endpointingDelayMs" not in raw:
            endpointing = 400
        elif eagerness == "patient" and "endpointing_delay_ms" not in raw and "endpointingDelayMs" not in raw:
            endpointing = 1200

        return cls(
            enabled=bool(raw.get("enabled", base.enabled)),
            posture=raw.get("posture") or base.posture,
            wake_words=[str(w).strip().lower() for w in words if str(w).strip()]
            if isinstance(words, list)
            else base.wake_words,
            persona=str(raw.get("persona") or base.persona),
            temperature=float(raw.get("temperature", base.temperature)),
            max_tokens=int(
                raw.get("max_tokens", raw.get("maxTokens", base.max_tokens))
            ),
            memory_turns=int(
                raw.get("memory_turns", raw.get("memoryTurns", base.memory_turns))
            ),
            cooldown_s=float(
                raw.get("cooldown_s", raw.get("cooldownS", base.cooldown_s))
            ),
            respond_to_voice=bool(
                raw.get("respond_to_voice", raw.get("respondToVoice", True))
            ),
            respond_to_chat=bool(
                raw.get("respond_to_chat", raw.get("respondToChat", False))
            ),
            retrieval=raw.get("retrieval") or base.retrieval,
            library=str(raw.get("library") or base.library),
            model=str(raw.get("model") or base.model),
            speak=bool(raw.get("speak", base.speak)),
            post_to_chat=bool(raw.get("post_to_chat", raw.get("postToChat", True))),
            robot_emoji_prefix=bool(
                raw.get("robot_emoji_prefix", raw.get("robotEmojiPrefix", False))
            ),
            tts_voice=str(raw.get("tts_voice", raw.get("ttsVoice", base.tts_voice))),
            tts_rate=str(raw.get("tts_rate", raw.get("ttsRate", base.tts_rate))),
            tts_pitch=str(raw.get("tts_pitch", raw.get("ttsPitch", base.tts_pitch))),
            turn_eagerness=eagerness,  # type: ignore[arg-type]
            endpointing_delay_ms=endpointing,
            thinking_filler=bool(raw.get("thinking_filler", raw.get("thinkingFiller", base.thinking_filler))),
            silence_timeout_s=float(raw.get("silence_timeout_s", raw.get("silenceTimeoutS", base.silence_timeout_s))),
            allow_barge_in=bool(raw.get("allow_barge_in", raw.get("allowBargeIn", base.allow_barge_in))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "posture": self.posture,
            "wakeWords": self.wake_words,
            "persona": self.persona,
            "temperature": self.temperature,
            "maxTokens": self.max_tokens,
            "memoryTurns": self.memory_turns,
            "cooldownS": self.cooldown_s,
            "respondToVoice": self.respond_to_voice,
            "respondToChat": self.respond_to_chat,
            "retrieval": self.retrieval,
            "library": self.library,
            "model": self.model,
            "speak": self.speak,
            "postToChat": self.post_to_chat,
            "robotEmojiPrefix": self.robot_emoji_prefix,
            "ttsVoice": self.tts_voice,
            "ttsRate": self.tts_rate,
            "ttsPitch": self.tts_pitch,
            "turnEagerness": self.turn_eagerness,
            "endpointingDelayMs": self.endpointing_delay_ms,
            "thinkingFiller": self.thinking_filler,
            "silenceTimeoutS": self.silence_timeout_s,
            "allowBargeIn": self.allow_barge_in,
        }




@dataclass
class Turn:
    """One line of the conversation as the agent remembers it."""

    role: Literal["room", "agent"]
    text: str
    speaker: str = ""
    source: Source = "voice"
    ts: float = field(default_factory=time.time)


# --- addressing -------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def is_addressed(text: str, wake_words: list[str], my_name: str = "") -> bool:
    """Whether this utterance is aimed at the agent.

    Matches on **word boundaries**, not substrings: a wake word of "bot" firing
    inside "robot" or "bottle" is how an agent ends up answering a conversation that
    had nothing to do with it, and in a room of strangers that reads as broken rather
    than eager.
    """
    haystack = f" {_norm(text)} "
    candidates = [w for w in wake_words if w]
    if my_name:
        candidates.append(my_name)
    for word in candidates:
        needle = _norm(str(word)).strip()
        if not needle:
            continue
        if f" {needle} " in haystack:
            return True
    return False


@dataclass
class Decision:
    """Why the agent is or isn't speaking. The reason is surfaced in the pane —
    a silent agent is indistinguishable from a broken one otherwise, which is the
    single most common "it doesn't respond" report."""

    respond: bool
    reason: str


def should_respond(
    config: VoiceConfig,
    text: str,
    *,
    source: Source,
    room: RoomSnapshot,
    last_reply_ts: float | None,
    now: float,
    is_self: bool = False,
) -> Decision:
    """The gate, as a pure function so the policy is testable without a model."""
    if not config.enabled:
        return Decision(False, "agent disabled")
    if is_self:
        # The agent's own TTS is published into the room, so it comes back through
        # the same transcription path. Answering it is an infinite loop that sounds
        # exactly like a broken agent talking to itself.
        return Decision(False, "own speech")
    if not text.strip():
        return Decision(False, "empty utterance")
    if source == "voice" and not config.respond_to_voice:
        return Decision(False, "voice replies off")
    if source == "chat" and not config.respond_to_chat:
        return Decision(False, "chat replies off")

    addressed = is_addressed(text, config.wake_words, room.my_name)
    # A command is always honored: someone typed it deliberately.
    if text.strip().startswith("/agent"):
        return Decision(True, "command")
    if config.posture == "addressed" and not addressed:
        return Decision(False, "not addressed")
    if not addressed and last_reply_ts is not None:
        elapsed = now - last_reply_ts
        if elapsed < config.cooldown_s:
            return Decision(
                False, f"cooldown ({config.cooldown_s - elapsed:.0f}s left)"
            )
    return Decision(True, "addressed" if addressed else config.posture)


# --- commands ---------------------------------------------------------------------


@dataclass
class Command:
    """A parsed ``/agent …`` directive. Deterministic: the model never decides
    whether to moderate a room, because a hallucinated ``invite_speaker`` acts on a
    real person."""

    name: str
    arg: str = ""
    # Whether it changes the room for everyone (as opposed to the agent's own state).
    moderator_only: bool = False


_MOD_COMMANDS = {"topic", "chat", "handraise", "invite", "questionnaire"}


def parse_command(text: str) -> Command | None:
    """``/agent <name> <arg…>`` → a Command, or None when it isn't one."""
    stripped = text.strip()
    if not stripped.startswith("/agent"):
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return Command("help")
    name = parts[1].lower()
    return Command(
        name=name,
        arg=" ".join(parts[2:]).strip(),
        moderator_only=name in _MOD_COMMANDS,
    )


# --- prompt assembly ---------------------------------------------------------------


def render_room_brief(room: RoomSnapshot) -> str:
    """The room, as prose the model can actually use.

    Rendered every turn rather than fetched by a tool: "who is here" is the single
    most-asked question of a room agent, and making it cost a tool round would mean
    either seconds of silence or (with no tools at all) a confident invention.
    """
    lines = [f"You are in a live Clubhouse room. You are '{room.my_name}'."]
    if room.topic:
        lines.append(f"Room topic: {room.topic}")
    if room.club:
        lines.append(f"Hosted by club: {room.club}")

    stage = [m for m in room.members if m.is_speaker]
    audience = [m for m in room.members if not m.is_speaker]
    hands = [m for m in audience if m.hand_raised]
    talking = [m for m in stage if m.speaking]

    def describe(m: RoomMember) -> str:
        marks = []
        if m.is_moderator:
            marks.append("moderator")
        if m.is_muted:
            marks.append("muted")
        if m.speaking:
            marks.append("speaking now")
        name = m.name or f"user {m.user_id}"
        return f"{name} ({', '.join(marks)})" if marks else name

    if stage:
        lines.append(
            f"On stage ({len(stage)}): " + ", ".join(describe(m) for m in stage)
        )
    else:
        lines.append("Nobody is on stage.")
    # The audience is a count, not a roster: a room of 300 would bury the prompt, and
    # the names of silent listeners are not something the agent should be reciting.
    if audience:
        named = [m for m in audience if m.name][:8]
        detail = ", ".join(m.name for m in named)
        more = len(audience) - len(named)
        lines.append(
            f"In the audience ({len(audience)}): {detail}"
            + (f", and {more} others" if more > 0 else "")
        )
    if hands:
        lines.append(
            "Hands raised (wanting to speak): "
            + ", ".join(m.name or str(m.user_id) for m in hands)
        )
    if talking:
        lines.append("Currently talking: " + ", ".join(m.name or "?" for m in talking))

    role = (
        "You are a moderator of this room."
        if room.am_i_moderator()
        else (
            "You are on stage and can be heard."
            if room.am_i_speaker()
            else "You are in the audience and cannot be heard aloud — "
            "your replies go to the room's text chat only."
        )
    )
    lines.append(role)
    return "\n".join(lines)


def render_bios(room: RoomSnapshot) -> str | None:
    """Profile and learned memory lines for people currently present."""
    try:
        from backend.modules.clubhouse.people_memory import people_memory_store

        user_ids = [m.user_id for m in room.members if m.user_id]
        if memory_brief := people_memory_store.format_room_memory(user_ids):
            return memory_brief
    except Exception:
        pass

    withbio = [m for m in room.members if m.bio and m.name]
    if not withbio:
        return None
    lines = ["Who these people are:"]
    for m in withbio[:10]:
        bio = " ".join(str(m.bio).split())[:240]
        lines.append(f"- {m.name}: {bio}")
    return "\n".join(lines)


def build_messages(
    config: VoiceConfig,
    room: RoomSnapshot,
    history: list[Turn],
    utterance: str,
    *,
    speaker: str = "",
    source: Source = "voice",
    retrieval: str | None = None,
) -> list[dict[str, str]]:
    """The full prompt for one turn.

    History is replayed as real ``user``/``assistant`` messages rather than pasted
    into one block, because a model that sees a transcript-shaped blob answers *about*
    the transcript instead of continuing it.
    """
    system_parts = [config.persona.strip(), SPEECH_RULES, render_room_brief(room)]
    if bios := render_bios(room):
        system_parts.append(bios)
    if retrieval:
        system_parts.append(retrieval)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "\n\n".join(p for p in system_parts if p)}
    ]
    for turn in history[-config.memory_turns :]:
        if turn.role == "agent":
            messages.append({"role": "assistant", "content": turn.text})
        else:
            who = turn.speaker or "Someone"
            messages.append({"role": "user", "content": f"{who}: {turn.text}"})
    who = speaker or "Someone"
    channel = "in the room chat" if source == "chat" else "out loud"
    messages.append({"role": "user", "content": f"{who} said {channel}: {utterance}"})
    return messages


# --- reply hygiene ------------------------------------------------------------------

_SPEAKER_PREFIX = re.compile(r"^\s*(?:agent|assistant|bot|you)\s*[:\-—]\s*", re.I)
_STAGE_DIRECTION = re.compile(r"^\s*[\(\[\*].{0,80}?[\)\]\*]\s*")


def clean_reply(text: str, *, max_chars: int = 700) -> str:
    """Strip what a speech synthesizer would embarrass you by reading aloud.

    Small models reliably emit ``Agent:`` prefixes and ``*laughs*`` stage directions
    no matter what the prompt says, and TTS pronounces both. Truncation is on a
    sentence boundary — a reply cut mid-word sounds like the connection dropped.
    """
    cleaned = (text or "").strip()
    # Models emit these one *after* the other ("Agent: *laughs* …"), so alternate
    # until neither matches rather than stripping each once.
    for _ in range(4):
        before = cleaned
        cleaned = _SPEAKER_PREFIX.sub("", cleaned)
        cleaned = _STAGE_DIRECTION.sub("", cleaned)
        cleaned = cleaned.strip()
        if cleaned == before:
            break
    # Markdown emphasis and list bullets read as literal characters.
    cleaned = re.sub(r"^\s*[-*•]\s+", "", cleaned, flags=re.M)
    cleaned = re.sub(r"[*_`#]+", "", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()
    if len(cleaned) > max_chars:
        cut = cleaned[:max_chars]
        for end in (". ", "! ", "? ", ".", "!", "?"):
            idx = cut.rfind(end)
            if idx > max_chars * 0.5:
                return cut[: idx + len(end)].strip()
        return cut.rsplit(" ", 1)[0].strip() + "…"
    return cleaned


# --- session store -------------------------------------------------------------------


@dataclass
class VoiceSession:
    """One room's conversation. Process-global, keyed by channel."""

    channel: str
    config: VoiceConfig = field(default_factory=VoiceConfig)
    history: deque[Turn] = field(default_factory=lambda: deque(maxlen=200))
    room: RoomSnapshot | None = None
    last_reply_ts: float | None = None
    # Text the agent has spoken, kept briefly so its own voice coming back through
    # transcription can be recognized and ignored.
    spoken: deque[str] = field(default_factory=lambda: deque(maxlen=8))

    def remember(self, turn: Turn) -> None:
        self.history.append(turn)

    def is_own_speech(self, text: str) -> bool:
        """Whether this transcript is (a garbled version of) something we just said.

        Compared on a normalized word set with a similarity floor, not equality:
        speech recognition of synthesized speech is close but never exact, and an
        exact-match check would let every echo straight through.
        """
        words = set(_norm(text).split())
        if len(words) < 3:
            return False
        for said in self.spoken:
            mine = set(_norm(said).split())
            if not mine:
                continue
            overlap = len(words & mine) / len(words)
            if overlap > 0.6:
                return True
        return False


_sessions: dict[str, VoiceSession] = {}


def session_for(channel: str) -> VoiceSession:
    """The session for a channel, created on first use."""
    if channel not in _sessions:
        _sessions[channel] = VoiceSession(channel=channel)
    return _sessions[channel]


def drop_session(channel: str) -> None:
    _sessions.pop(channel, None)


def reset_all() -> None:
    """Test seam — the store is process-global by design."""
    _sessions.clear()
