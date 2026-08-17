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
    # The live room, pushed by the pane — it holds the PubNub feed and the Agora
    # volume indicator, so it is seconds fresher than anything this server could poll.
    room: dict[str, Any] = Field(default_factory=dict)


class TurnResponse(BaseModel):
    spoke: bool
    reason: str
    reply: str = ""
    # A line for the pane to show (and optionally post to chat) that is *not* the
    # agent speaking: command confirmations, failures, "found nothing".
    notice: str | None = None
    retrieved: bool = False


class ConfigRequest(BaseModel):
    channel: str
    config: dict[str, Any] = Field(default_factory=dict)


@router.post("/turn", response_model=TurnResponse)
async def turn(req: TurnRequest) -> TurnResponse:
    """One utterance in, one decision out."""
    session = V.session_for(req.channel)
    room = V.RoomSnapshot.from_dict({**req.room, "channel": req.channel})
    source: V.Source = "chat" if req.source == "chat" else "voice"

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
        )
    if not decision.respond:
        # Still remembered: the agent should know what was said in the room even on
        # the turns it stays out of, or "what were we just talking about?" has no
        # answer and it re-asks a question somebody already answered.
        if not is_self and req.text.strip():
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
                    "/agent invite <name>, /agent forget"
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
                spoke=False, reason="command", notice=handled.get("notice")
            )

    result = await R.run_turn(
        session, room, req.text, speaker=req.speaker, source=source
    )
    return TurnResponse(
        spoke=bool(result.get("spoke")),
        reason=str(result.get("reason", "")),
        reply=str(result.get("reply", "")),
        notice=result.get("notice"),
        retrieved=bool(result.get("retrieved")),
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
def reset(req: ConfigRequest) -> dict[str, Any]:
    """Forget the conversation but keep the settings."""
    session = V.session_for(req.channel)
    session.history.clear()
    session.spoken.clear()
    session.last_reply_ts = None
    return {"cleared": True}
