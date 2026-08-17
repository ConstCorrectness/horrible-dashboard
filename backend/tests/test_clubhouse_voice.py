"""The Clubhouse voice agent's policy: when it speaks, what it knows, what it says.

Every test here runs without a model, a network, or a connected account — that split
is the reason `voice.py` is pure and `voice_runtime.py` is not.
"""

from __future__ import annotations

import pytest

from backend.modules.clubhouse import voice as V
from backend.modules.clubhouse import voice_runtime as R


@pytest.fixture(autouse=True)
def _clean_sessions():
    V.reset_all()
    yield
    V.reset_all()


def _room(**kw) -> V.RoomSnapshot:
    members = kw.pop(
        "members",
        [
            V.RoomMember(user_id=1, name="Ada", is_speaker=True, is_moderator=True),
            V.RoomMember(user_id=2, name="Grace", is_speaker=True, speaking=True),
            V.RoomMember(user_id=3, name="Linus", hand_raised=True),
        ],
    )
    return V.RoomSnapshot(
        channel=kw.pop("channel", "x1"),
        topic=kw.pop("topic", "Compilers"),
        members=members,
        my_user_id=kw.pop("my_user_id", 9),
        my_name=kw.pop("my_name", "Sidekick"),
        **kw,
    )


# --- addressing --------------------------------------------------------------------


def test_wake_word_matches_whole_words_only():
    """A substring match is how an agent joins a conversation it wasn't in: 'bot'
    inside 'robot' would fire on a room discussing robotics."""
    assert V.is_addressed("hey agent, what do you think?", ["agent"])
    assert not V.is_addressed("we were discussing robotics", ["bot"])
    assert not V.is_addressed("pass me the bottle", ["bot"])
    assert V.is_addressed("bot, say something", ["bot"])


def test_the_agents_own_name_addresses_it():
    assert V.is_addressed("what does Sidekick reckon", [], "Sidekick")


def test_punctuation_and_case_do_not_defeat_addressing():
    assert V.is_addressed("AGENT!! are you there?", ["agent"])


# --- the gate ----------------------------------------------------------------------


def _decide(config: V.VoiceConfig, text: str, **kw):
    return V.should_respond(
        config,
        text,
        source=kw.pop("source", "voice"),
        room=kw.pop("room", _room()),
        last_reply_ts=kw.pop("last_reply_ts", None),
        now=kw.pop("now", 1000.0),
        is_self=kw.pop("is_self", False),
    )


def test_addressed_posture_stays_quiet_until_named():
    config = V.VoiceConfig(enabled=True, posture="addressed")
    assert not _decide(config, "I think compilers are great").respond
    assert _decide(config, "agent, what do you think?").respond


def test_disabled_agent_never_speaks():
    assert not _decide(V.VoiceConfig(enabled=False), "agent hello").respond


def test_the_agent_never_answers_its_own_voice():
    """Its TTS is published into the room and comes back through the same
    transcription path — answering it is a loop that sounds like a broken agent."""
    config = V.VoiceConfig(enabled=True, posture="always")
    assert not _decide(config, "anything at all", is_self=True).respond


def test_echo_detection_survives_imperfect_transcription():
    """Recognition of synthesized speech is close but never exact, so an
    equality check would let every echo through."""
    session = V.VoiceSession(channel="x1")
    session.spoken.append("I think compilers are mostly about tradeoffs")
    assert session.is_own_speech("i think compilers are mostly about trade offs")
    assert not session.is_own_speech("what did you all have for lunch today")


def test_short_utterances_are_never_treated_as_echo():
    """'yes' matching something the agent said would silence a real person."""
    session = V.VoiceSession(channel="x1")
    session.spoken.append("yes")
    assert not session.is_own_speech("yes")


def test_cooldown_holds_the_floor_open_but_addressing_overrides_it():
    config = V.VoiceConfig(enabled=True, posture="conversational", cooldown_s=6)
    assert not _decide(config, "interesting point", last_reply_ts=998.0).respond
    # Being named cuts through: someone asked it directly.
    assert _decide(config, "agent, thoughts?", last_reply_ts=998.0).respond
    assert _decide(config, "interesting point", last_reply_ts=990.0).respond


def test_a_command_is_always_honored():
    """Someone typed it deliberately; posture is about unsolicited speech."""
    config = V.VoiceConfig(enabled=True, posture="addressed")
    assert _decide(config, "/agent topic Compilers").respond


def test_source_switches_are_respected():
    config = V.VoiceConfig(
        enabled=True, posture="always", respond_to_chat=False, respond_to_voice=True
    )
    assert not _decide(config, "hello", source="chat").respond
    assert _decide(config, "hello", source="voice").respond


def test_a_refusal_always_carries_a_reason():
    """A silent agent and a broken one look identical without this — it is the
    single most common 'it doesn't respond' report."""
    config = V.VoiceConfig(enabled=True, posture="addressed")
    decision = _decide(config, "just chatting")
    assert not decision.respond
    assert decision.reason == "not addressed"


# --- room knowledge ------------------------------------------------------------------


def test_the_room_brief_names_who_is_where():
    brief = V.render_room_brief(_room())
    assert "Compilers" in brief
    assert "Ada (moderator)" in brief
    assert "Grace (speaking now)" in brief
    assert "Linus" in brief  # in the audience
    assert "Hands raised" in brief


def test_the_brief_tells_the_agent_whether_it_can_be_heard():
    """An audience agent that thinks it is speaking aloud writes for the wrong
    medium — and its replies only ever reach the text chat."""
    audience = V.render_room_brief(_room())
    assert "cannot be heard aloud" in audience

    on_stage = _room(
        members=[V.RoomMember(user_id=9, name="Sidekick", is_speaker=True)]
    )
    assert "on stage and can be heard" in V.render_room_brief(on_stage)


def test_a_large_audience_is_counted_not_recited():
    """A room of 300 would bury the prompt, and reciting silent listeners' names is
    not something the agent should be doing out loud."""
    members = [V.RoomMember(user_id=i, name=f"P{i}") for i in range(100)]
    brief = V.render_room_brief(_room(members=members))
    assert "In the audience (100)" in brief
    assert "and 92 others" in brief


def test_bios_reach_the_prompt_when_known():
    room = _room(members=[V.RoomMember(user_id=1, name="Ada", bio="Writes compilers.")])
    assert "Ada: Writes compilers." in (V.render_bios(room) or "")


# --- prompt assembly -----------------------------------------------------------------


def test_history_is_replayed_as_turns_not_pasted_as_a_transcript():
    """A model shown a transcript-shaped blob answers *about* the transcript
    instead of continuing the conversation."""
    config = V.VoiceConfig(enabled=True)
    history = [
        V.Turn(role="room", text="what's the topic", speaker="Ada"),
        V.Turn(role="agent", text="Compilers."),
    ]
    messages = V.build_messages(
        config, _room(), history, "and who's here?", speaker="Ada"
    )
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"].startswith("Ada:")
    assert messages[2]["content"] == "Compilers."


def test_memory_is_capped_to_the_configured_window():
    config = V.VoiceConfig(enabled=True, memory_turns=4)
    history = [V.Turn(role="room", text=f"line {i}", speaker="A") for i in range(20)]
    messages = V.build_messages(config, _room(), history, "now what")
    assert len(messages) == 1 + 4 + 1  # system + window + the new utterance


def test_the_speech_rules_survive_a_user_edited_persona():
    """The user owns the persona; they must not be able to delete the rules that
    keep a reply speakable — every model reaches for bullet lists otherwise."""
    config = V.VoiceConfig(enabled=True, persona="You are a pirate.")
    system = V.build_messages(config, _room(), [], "hi")[0]["content"]
    assert "You are a pirate." in system
    assert "No markdown" in system


# --- reply hygiene ---------------------------------------------------------------------


def test_speaker_prefixes_and_stage_directions_are_stripped():
    """TTS reads both aloud. Small models emit them regardless of the prompt."""
    assert V.clean_reply("Agent: hello there") == "hello there"
    assert V.clean_reply("*laughs* that's funny") == "that's funny"
    assert V.clean_reply("Agent: *pauses* right") == "right"


def test_markdown_is_flattened_for_speech():
    cleaned = V.clean_reply("- **first** point\n- second point")
    assert "*" not in cleaned and "-" not in cleaned.split()[0]


def test_truncation_lands_on_a_sentence_boundary():
    """A reply cut mid-word sounds like the connection dropped."""
    text = "One sentence here. " * 40
    out = V.clean_reply(text, max_chars=100)
    assert out.endswith(".")
    assert len(out) <= 100


# --- commands ----------------------------------------------------------------------------


def test_commands_parse_with_their_argument():
    cmd = V.parse_command("/agent topic Compilers and type systems")
    assert cmd and cmd.name == "topic"
    assert cmd.arg == "Compilers and type systems"
    assert cmd.moderator_only


def test_search_is_not_moderator_gated():
    cmd = V.parse_command("/agent search who won the world cup")
    assert cmd and not cmd.moderator_only


def test_non_commands_parse_as_none():
    assert V.parse_command("I was going to say /agent is useful") is None


def test_an_unrecognized_toggle_argument_reads_as_off():
    """These are also spoken through imperfect transcription, and the safe default
    for 'enable chat for the whole room' is not to."""
    assert R._is_on("on") and R._is_on("yes")
    assert not R._is_on("orn")  # a plausible mistranscription of "on"
    assert not R._is_on("")


def test_invite_only_resolves_someone_actually_in_the_room():
    room = _room()
    assert R._resolve_member(room, "Ada").user_id == 1
    assert R._resolve_member(room, "@grace").user_id == 2
    assert R._resolve_member(room, "Nobody") is None


def test_an_ambiguous_invite_resolves_to_nothing():
    """Inviting the wrong person onto a stage is not an error you take back
    quietly, so ambiguity refuses rather than picking the first match."""
    room = _room(
        members=[
            V.RoomMember(user_id=1, name="Chris P"),
            V.RoomMember(user_id=2, name="Chris M"),
        ]
    )
    assert R._resolve_member(room, "Chris") is None


# --- retrieval gating --------------------------------------------------------------------


def test_retrieval_is_off_unless_configured():
    assert not R.wants_retrieval("who is Ada Lovelace", V.VoiceConfig(retrieval="off"))


def test_command_mode_only_retrieves_for_an_explicit_search():
    config = V.VoiceConfig(retrieval="command")
    assert R.wants_retrieval("/agent search who is Ada Lovelace", config)
    assert not R.wants_retrieval("who is Ada Lovelace", config)


def test_auto_mode_retrieves_for_question_shaped_utterances():
    config = V.VoiceConfig(retrieval="auto")
    assert R.wants_retrieval("who is Ada Lovelace", config)
    assert not R.wants_retrieval("I agree with that completely", config)


def test_stripping_a_command_leaves_the_query():
    assert (
        R._strip_command("/agent search best coffee in Rome") == "best coffee in Rome"
    )
    assert R._strip_command("plain text") == "plain text"


# --- sessions ------------------------------------------------------------------------------


def test_a_session_is_shared_per_channel_not_per_pane():
    """A reload, a second pane, or a workspace switch must rejoin the same
    conversation rather than resetting it."""
    a = V.session_for("room-1")
    a.remember(V.Turn(role="room", text="hello", speaker="Ada"))
    assert len(V.session_for("room-1").history) == 1
    assert len(V.session_for("room-2").history) == 0
