"""HTTP surface for the Clubhouse voice agent.

Mounted on the same ``/clubhouse`` router. The pane drives one endpoint per turn
(``/voice/turn``) and the server answers with *what to do*: speak this, post that,
or stay quiet for this reason. Deciding server-side is what lets the same policy
serve voice and chat, and what makes "why didn't it answer?" a testable question
rather than an inspection of React state.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.modules.clubhouse import voice as V
from backend.modules.clubhouse import voice_runtime as R

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clubhouse/voice", tags=["clubhouse"])


class TurnRequest(BaseModel):
    channel: str
    text: str
    speaker: str = ""
    speaker_id: int | None = None
    source: str = "voice"
    # A human pressed "Speak Now". Skips the posture/cooldown gate — those exist to
    # stop the agent interrupting people, and an explicit request is not that. It
    # does **not** skip the self-speech check: an echo is a bug at any posture.
    force: bool = False
    # The speaker has not finished — the pane flushed this mid-breath because they
    # have held the floor a while. Only the "interject" posture acts on one.
    partial: bool = False
    # The live room, pushed by the pane — it holds the PubNub feed and the Agora
    # volume indicator, so it is seconds fresher than anything this server could poll.
    room: dict[str, Any] = Field(default_factory=dict)
    # The active client-side voice agent configuration, preventing state desync
    config: dict[str, Any] | None = None
    # Explicit override from client: true if the client knows it's agent echo,
    # false if known human operator. If omitted, uses speaker_id matching and text overlap.
    is_self: bool | None = None


class TurnResponse(BaseModel):
    spoke: bool
    reason: str
    reply: str = ""
    # A line for the pane to show (and optionally post to chat) that is *not* the
    # agent speaking: command confirmations, failures, "found nothing".
    notice: str | None = None
    retrieved: bool = False
    filler: str | None = None
    # Things for the pane to do besides speaking -- today, room music
    # (`music.play` / `music.stop` / `music.pause` / `music.resume` / `music.volume`).
    # Declared here or FastAPI's response model silently drops it.
    actions: list[dict[str, Any]] = Field(default_factory=list)


class MusicStatus(BaseModel):
    song_id: str
    status: str
    title: str = ""
    error: str | None = None


class ConfigRequest(BaseModel):
    channel: str
    config: dict[str, Any] = Field(default_factory=dict)


class ResetRequest(BaseModel):
    channel: str
    reset_persona: bool = False
    persona: str | None = None


@router.post("/turn", response_model=TurnResponse)
async def turn(req: TurnRequest) -> TurnResponse:
    """One utterance in, one decision out."""
    session = V.session_for(req.channel)
    if req.config:
        session.config = V.VoiceConfig.from_dict(req.config)
    room = V.RoomSnapshot.from_dict({**req.room, "channel": req.channel})
    source: V.Source = (
        req.source if req.source in ("chat", "nudge") else "voice"  # type: ignore[assignment]
    )

    if req.is_self is not None:
        is_self = req.is_self or session.is_own_speech(req.text)
    else:
        is_self = (
            req.speaker_id is not None and req.speaker_id == room.my_user_id
        ) or session.is_own_speech(req.text)

    if req.force and not is_self:
        decision = V.Decision(True, "asked to speak")
    else:
        decision = V.should_respond(
            session.config,
            req.text,
            source=source,
            room=room,
            last_reply_ts=session.last_reply_ts,
            now=time.time(),
            is_self=is_self,
            partial=req.partial,
        )
    if not decision.respond:
        # Still remembered: the agent should know what was said in the room even on
        # the turns it stays out of, or "what were we just talking about?" has no
        # answer and it re-asks a question somebody already answered.
        # A partial is deliberately *not* remembered: the finished utterance follows
        # seconds later and contains it, so keeping both puts the same sentence in the
        # history twice — once truncated mid-word — and the model answers the fragment.
        # A nudge is excluded for the same reason it is on the answering path: it was
        # never said in the room, so remembering it puts an instruction the agent was
        # given into the transcript of what people supposedly told each other.
        if not is_self and not req.partial and source != "nudge" and req.text.strip():
            session.remember(
                V.Turn(role="room", text=req.text, speaker=req.speaker, source=source)
            )
        return TurnResponse(spoke=False, reason=decision.reason)

    command = V.parse_command(req.text)
    if command is not None:
        if command.name == "help":
            return TurnResponse(
                spoke=False,
                reason="help",
                notice=(
                    "Commands: /agent search <query>, /agent topic <text>, "
                    "/agent chat on|off, /agent handraise on|off, "
                    "/agent invite <name>, /agent play <song>, /agent stop, "
                    "/agent forget"
                ),
            )
        if command.name == "forget":
            session.history.clear()
            session.spoken.clear()
            return TurnResponse(
                spoke=False, reason="forgot", notice="Forgot the conversation so far."
            )
        handled = await R.run_command(command, room, session)
        if handled is not None:
            return TurnResponse(
                spoke=False,
                reason="command",
                notice=handled.get("notice"),
                actions=handled.get("actions") or [],
            )

    result = await R.run_turn(
        session,
        room,
        req.text,
        speaker=req.speaker,
        source=source,
        speaker_id=req.speaker_id,
    )
    return TurnResponse(
        spoke=bool(result.get("spoke")),
        reason=str(result.get("reason", "")),
        reply=str(result.get("reply", "")),
        notice=result.get("notice"),
        retrieved=bool(result.get("retrieved")),
        filler=result.get("filler"),
        actions=result.get("actions") or [],
    )


@router.get("/music/{song_id}", response_model=MusicStatus)
def music_status(song_id: str) -> MusicStatus:
    """Whether a song the agent asked for has finished downloading.

    Polled by the pane after a `music.play` action with `ready: false`: the file is
    fetched in the background so the turn can answer immediately.
    """
    from fastapi import HTTPException

    from backend.modules.karaoke import store

    song = store.get_song(song_id)
    if song is None:
        raise HTTPException(status_code=404, detail="song not found")
    return MusicStatus(
        song_id=song_id,
        status=str(song.get("status") or ""),
        title=str(song.get("title") or ""),
        error=song.get("error"),
    )


@router.post("/config")
def set_config(req: ConfigRequest) -> dict[str, Any]:
    """Replace a room's agent config. The pane owns these knobs."""
    session = V.session_for(req.channel)
    session.config = V.VoiceConfig.from_dict(req.config)
    return session.config.to_dict()


@router.get("/config")
def get_config(channel: str) -> dict[str, Any]:
    return V.session_for(channel).config.to_dict()


@router.get("/state")
def state(channel: str) -> dict[str, Any]:
    """What the agent currently remembers — rendered in the pane's Agent tab so the
    conversation it thinks it is having is inspectable."""
    session = V.session_for(channel)
    return {
        "channel": channel,
        "config": session.config.to_dict(),
        "turns": [
            {
                "role": t.role,
                "text": t.text,
                "speaker": t.speaker,
                "source": t.source,
                "ts": t.ts,
            }
            for t in list(session.history)[-40:]
        ],
        "lastReplyTs": session.last_reply_ts,
    }


@router.post("/reset")
def reset(req: ResetRequest) -> dict[str, Any]:
    """Forget the conversation and optionally reset persona."""
    session = V.session_for(req.channel)
    session.history.clear()
    session.spoken.clear()
    session.last_reply_ts = None
    if req.reset_persona:
        session.config.persona = (
            req.persona if req.persona is not None else V.DEFAULT_PERSONA
        )
    return {"cleared": True, "persona": session.config.persona}
