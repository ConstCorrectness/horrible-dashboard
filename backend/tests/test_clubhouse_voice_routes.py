"""The voice agent's HTTP surface, with the model stubbed at the provider seam.

These cover the wiring the pure tests in `test_clubhouse_voice.py` cannot: that a
turn reaches the model with the room in it, that a refusal is reported rather than
silently 200-ing with an empty body, that the session survives across requests, and
that every decision lands in the debug log.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.agent import providers as P
from backend.modules.clubhouse import voice as V
from backend.modules.clubhouse import voice_runtime as R


def _said(text: str) -> P.ChatResult:
    return P.ChatResult(
        assistant_message={"role": "assistant", "content": text},
        tool_calls=[],
        content=text,
    )


ROOM = {
    "topic": "Compilers",
    "members": [
        {"user_id": 1, "name": "Ada", "is_speaker": True, "is_moderator": True},
        {"user_id": 9, "name": "Sidekick", "is_speaker": True},
    ],
    "my_user_id": 9,
    "my_name": "Sidekick",
}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    V.reset_all()
    yield TestClient(app)
    V.reset_all()


@pytest.fixture
def captured(monkeypatch) -> list[list[dict]]:
    """Stub the generation, keeping every message list it was called with."""
    seen: list[list[dict]] = []

    async def fake_generate(messages, config, tools=None):
        seen.append(messages)
        return _said("Sure, happy to help.")

    monkeypatch.setattr(R, "generate_reply", fake_generate)
    return seen


def _enable(client: TestClient, **overrides) -> None:
    config = {"enabled": True, "posture": "addressed", **overrides}
    res = client.post(
        "/api/clubhouse/voice/config", json={"channel": "c1", "config": config}
    )
    assert res.status_code == 200


def _turn(client: TestClient, text: str, **kw) -> dict:
    body = {"channel": "c1", "text": text, "room": ROOM, **kw}
    res = client.post("/api/clubhouse/voice/turn", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _events(client: TestClient, channel: str = "c1") -> list[dict]:
    res = client.get("/api/clubhouse/voice/state", params={"channel": channel})
    return res.json()["events"]


def test_an_addressed_utterance_gets_a_reply(client, captured):
    _enable(client)
    body = _turn(client, "Sidekick, what's the topic?")
    assert body["spoke"] is True
    assert body["reply"] == "Sure, happy to help."


def test_the_room_reaches_the_model(client, captured):
    """The whole point of the rewrite: the agent is told who is here rather than
    guessing. If this passes vacuously the feature does not exist."""
    _enable(client)
    _turn(client, "Sidekick, who is here?")
    # The room rides the *turn*, not the system message: it changes every turn, so
    # pinning it ahead of a twelve-turn history described a room twelve turns stale.
    turn = captured[0][-1]["content"]
    assert "Compilers" in turn
    assert "Ada (moderator)" in turn
    assert captured[0][0]["role"] == "system"
    assert "Ada (moderator)" not in captured[0][0]["content"]


def test_an_unaddressed_utterance_is_refused_with_a_reason(client, captured):
    _enable(client)
    body = _turn(client, "I think compilers are neat")
    assert body["spoke"] is False
    assert body["reason"] == "not addressed"
    assert captured == []  # the model was never called


def test_the_agent_still_remembers_turns_it_stayed_out_of(client, captured):
    """Otherwise 'what were we just talking about?' has no answer and it re-asks
    a question the room already answered."""
    _enable(client)
    _turn(client, "I think compilers are neat")
    _turn(client, "Sidekick, what were we discussing?")
    replayed = [m["content"] for m in captured[0]]
    assert any("compilers are neat" in c for c in replayed)


def test_each_line_the_agent_overheard_reaches_the_model_as_its_own_message(
    client, captured
):
    _enable(client)
    _turn(client, "first thing", speaker="Ada")
    _turn(client, "second thing", speaker="Ada")
    _turn(client, "Sidekick, thoughts?", speaker="Ada")
    messages = captured[0]
    assert [m["role"] for m in messages] == ["system", "user", "user", "user"]
    assert messages[1]["content"] == "Ada: first thing"
    assert messages[2]["content"] == "Ada: second thing"


def test_memory_persists_across_requests(client, captured):
    """The session is keyed by channel, so a pane reload rejoins the conversation."""
    _enable(client)
    _turn(client, "Sidekick, hello")
    _turn(client, "Sidekick, again")
    # Second call replays the first exchange: room turn + the agent's own reply.
    roles = [m["role"] for m in captured[1]]
    assert roles == ["system", "user", "assistant", "user"]


def test_force_bypasses_the_posture_gate(client, captured):
    """ "Speak Now" is an explicit request; posture exists to stop the agent
    interrupting people, which this is not."""
    _enable(client)
    body = _turn(client, "say something", force=True)
    assert body["spoke"] is True


def test_force_does_not_bypass_the_echo_check(client, captured):
    """An echo is a bug at any posture — the agent answering its own voice."""
    _enable(client)
    _turn(client, "Sidekick, hello")
    body = _turn(client, "Sure, happy to help.", force=True)
    assert body["spoke"] is False
    assert body["reason"] == "own speech"


def test_a_speaker_id_matching_us_is_our_own_speech(client, captured):
    _enable(client, posture="always")
    body = _turn(client, "something we said", speaker_id=9)
    assert body["spoke"] is False
    assert body["reason"] == "own speech"


def test_help_lists_the_commands_without_calling_the_model(client, captured):
    _enable(client)
    body = _turn(client, "/agent")
    assert "/agent forget" in (body["notice"] or "")
    assert captured == []


@pytest.mark.parametrize(
    "command",
    [
        "/agent play lofi",
        "/agent search who won",
        "/agent whois @ada",
        "/agent topic x",
    ],
)
def test_a_removed_command_is_reported_not_answered_in_character(
    client, captured, command
):
    """The agent has no tools, no memory of people and no moderation: those commands
    used to act, and now say they don't exist instead of reaching the model."""
    _enable(client)
    body = _turn(client, command)
    assert body["spoke"] is False
    assert body["reason"] == "unknown command"
    assert captured == []


def test_forget_clears_the_conversation(client, captured):
    _enable(client)
    _turn(client, "Sidekick, hello")
    assert client.get("/api/clubhouse/voice/state?channel=c1").json()["turns"]
    _turn(client, "/agent forget")
    assert client.get("/api/clubhouse/voice/state?channel=c1").json()["turns"] == []


def test_the_removed_endpoints_are_gone(client):
    assert client.get("/api/clubhouse/voice/music/s1").status_code == 404
    assert client.get("/api/clubhouse/people-memory").status_code == 404


def test_config_round_trips(client):
    _enable(
        client,
        posture="conversational",
        cooldownS=3,
        ttsVolume="+25%",
        endpointingDelayMs=900,
        allowBargeIn=False,
    )
    got = client.get("/api/clubhouse/voice/config?channel=c1").json()
    assert got["posture"] == "conversational"
    assert got["cooldownS"] == 3
    # Dropped by `to_dict` until 2026-11-11, so the server's copy disagreed with the
    # pane's and the volume looked like it never saved.
    assert got["ttsVolume"] == "+25%"
    assert got["endpointingDelayMs"] == 900
    assert got["allowBargeIn"] is False


def test_a_config_saved_by_an_older_pane_still_loads(client):
    """Wake words, eagerness, the filler, the silence timeout and the interject
    interval are gone. Their keys must not break a config that still carries them, and
    the interject posture becomes the nearest one that remains."""
    _enable(
        client,
        posture="interject",
        wakeWords=["hey you"],
        turnEagerness="fast",
        thinkingFiller=True,
        silenceTimeoutS=30,
        interjectAfterS=4,
        retrieval="auto",
        library="default",
    )
    got = client.get("/api/clubhouse/voice/config?channel=c1").json()
    assert got["posture"] == "conversational"
    for dead in (
        "wakeWords",
        "turnEagerness",
        "thinkingFiller",
        "silenceTimeoutS",
        "interjectAfterS",
        "retrieval",
        "library",
    ):
        assert dead not in got


def test_junk_in_a_config_falls_back_instead_of_failing(client):
    _enable(client, temperature="", maxTokens="lots", cooldownS=-5, memoryTurns=9999)
    got = client.get("/api/clubhouse/voice/config?channel=c1").json()
    assert got["temperature"] == 0.7
    assert got["maxTokens"] == V.DEFAULT_MAX_TOKENS
    assert got["cooldownS"] == 0
    assert got["memoryTurns"] == 40


def test_turn_with_config_payload_syncs_session(client, captured):
    """Passing config in TurnRequest directly synchronizes the session, preventing race conditions."""
    custom_persona = "You are an ancient Roman philosopher."
    res = client.post(
        "/api/clubhouse/voice/turn",
        json={
            "channel": "c_sync",
            "text": "Sidekick, what is truth?",
            "room": ROOM,
            "config": {
                "enabled": True,
                "posture": "addressed",
                "persona": custom_persona,
            },
        },
    ).json()
    assert res["spoke"] is True
    assert res["reply"] == "Sure, happy to help."
    system_content = captured[0][0]["content"]
    assert custom_persona in system_content
    # And state reflects the updated config
    state = client.get(
        "/api/clubhouse/voice/state", params={"channel": "c_sync"}
    ).json()
    assert state["config"]["enabled"] is True
    assert state["config"]["persona"] == custom_persona


def test_a_reply_costs_exactly_one_generation_and_offers_no_tools(client, monkeypatch):
    calls: list[tuple[list[dict], list | None]] = []

    async def fake_generate(messages, config, tools=None):
        calls.append((messages, tools))
        return _said("Hello.")

    monkeypatch.setattr(R, "generate_reply", fake_generate)
    _enable(client)
    _turn(client, "Sidekick, hello")
    assert len(calls) == 1
    assert not calls[0][1]


def test_the_first_name_wakes_the_agent_through_the_route(client, captured):
    _enable(client)
    room = {**ROOM, "my_name": "Horrible Program"}
    body = client.post(
        "/api/clubhouse/voice/turn",
        json={"channel": "c1", "text": "Horrible, you there?", "room": room},
    ).json()
    assert body["spoke"] is True


def test_an_acknowledgement_of_the_prompt_is_neither_spoken_nor_remembered(
    client, monkeypatch
):
    async def acknowledge(messages, config, tools=None):
        return _said(
            "I understand the instructions for how I should communicate in this "
            "setting. I will speak in two or three sentences of plain spoken prose."
        )

    monkeypatch.setattr(R, "generate_reply", acknowledge)
    _enable(client)
    body = _turn(client, "Sidekick, hello")
    assert body["spoke"] is False
    assert "acknowledged the prompt" in body["reason"]
    turns = client.get("/api/clubhouse/voice/state?channel=c1").json()["turns"]
    assert [t["role"] for t in turns] == ["room"]


# --- the debug log -----------------------------------------------------------------


def test_every_decision_is_a_row_in_the_log(client, captured):
    """ "Why did it not answer Chaya?" has to be a row to read."""
    _enable(client)
    _turn(client, "just chatting", speaker="Chaya")
    _turn(client, "Sidekick, hello", speaker="Chaya")
    skipped, replied = _events(client)
    assert (skipped["kind"], skipped["reason"], skipped["speaker"]) == (
        "skipped",
        "not addressed",
        "Chaya",
    )
    assert skipped["text"] == "just chatting"
    assert replied["kind"] == "replied"
    assert replied["text"] == "Sure, happy to help."
    assert replied["ms"] is not None and replied["ms"] >= 0


def test_a_failed_generation_is_logged_with_its_cause(client, monkeypatch):
    async def boom(messages, config, tools=None):
        raise RuntimeError("the model fell over")

    monkeypatch.setattr(R, "generate_reply", boom)
    _enable(client)
    body = _turn(client, "Sidekick, hello")
    assert body["reason"] == "model error"
    last = _events(client)[-1]
    assert last["kind"] == "error"
    assert last["reason"] == "model error"
    assert "the model fell over" in last["detail"]


def test_a_dropped_reply_is_logged_as_silent(client, monkeypatch):
    async def nothing(messages, config, tools=None):
        return _said("")

    monkeypatch.setattr(R, "generate_reply", nothing)
    _enable(client)
    _turn(client, "Sidekick, hello")
    last = _events(client)[-1]
    assert (last["kind"], last["reason"]) == ("silent", "model returned nothing")


def test_a_command_and_a_memory_reset_are_logged(client, captured):
    _enable(client)
    _turn(client, "/agent forget")
    client.post("/api/clubhouse/voice/reset", json={"channel": "c1"})
    kinds = [(e["kind"], e["reason"]) for e in _events(client)]
    assert kinds == [("command", "forgot"), ("command", "memory cleared")]


def test_the_log_is_bounded(client, captured):
    _enable(client, posture="always")
    for i in range(V.VoiceSession(channel="x").events.maxlen + 10):
        _turn(client, f"line {i}", speaker="Ada", force=False)
    session = V.session_for("c1")
    assert len(session.events) == session.events.maxlen
    # And the route only ever returns the newest handful.
    assert len(_events(client)) <= 60


def test_the_log_is_also_written_to_the_backend_log(client, captured, caplog):
    import logging

    _enable(client)
    with caplog.at_level(logging.INFO, logger="backend.modules.clubhouse.voice"):
        _turn(client, "just chatting", speaker="Chaya")
    assert any(
        "voice[c1] skipped" in r.getMessage() and "not addressed" in r.getMessage()
        for r in caplog.records
    )


# --- health ------------------------------------------------------------------------
#
# The room agent fails by going quiet, and the three legs of a turn — the ears, the
# model, the mouth — are separate processes that stop for unrelated reasons while
# looking identical from a chair. `GET /voice/health` is what makes them separable
# without taking a turn.


def _stub_health(
    monkeypatch,
    *,
    voice=(True, True),
    ffmpeg=(True, True),
    served: list[str] | None = None,
    raises: Exception | None = None,
    model: str = "m",
    whisper=None,
):
    """Pin every probe the health route makes. `(available, certain)` per extra.

    `whisper` stands in for the STT service the ears check imports — `None` skips
    that check entirely, because the real one would load a model into the suite.
    """
    import sys
    import types

    from backend import extras
    from backend.modules.clubhouse import voice_routes as VR
    from backend.modules.network import borrow
    from backend.modules.agent import routes as AR
    from backend.modules.agent.models import AgentConfig

    def fake_probe(name, *, refresh=False):
        avail, certain = {"voice": voice, "ffmpeg": ffmpeg}.get(name, (True, True))
        return extras.Availability(
            extra=name,
            available=avail,
            certain=certain,
            reason="" if avail else f"{name} is not installed",
            install=f"uv sync --extra {name}",
        )

    monkeypatch.setattr(extras, "probe", fake_probe)
    monkeypatch.setattr(
        AR,
        "_load_config",
        lambda: AgentConfig(model=model, provider="lmstudio", endpoint="http://x:1234"),
    )

    async def fake_list_models(client, info, endpoint):
        if raises is not None:
            raise raises
        return served if served is not None else [model]

    monkeypatch.setattr(P, "list_models", fake_list_models)

    if whisper is None:
        monkeypatch.setattr(VR, "_whisper_health", lambda ears: ears)
    else:
        monkeypatch.setattr(
            borrow, "route", lambda _cap: types.SimpleNamespace(local=True)
        )
        fake = types.ModuleType("backend.modules.agent.stt_service")
        fake.stt_service = whisper
        monkeypatch.setitem(sys.modules, "backend.modules.agent.stt_service", fake)


def test_health_is_green_when_every_leg_answers(
    client: TestClient, monkeypatch
) -> None:
    _stub_health(monkeypatch)
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ok"] is True
    assert body["ears"]["ok"] and body["model"]["ok"] and body["mouth"]["ok"]


def test_health_names_the_model_the_server_is_not_serving(
    client: TestClient, monkeypatch
) -> None:
    """The failure a person actually hits: the server is up, so nothing looks wrong,
    and every turn comes back `model error`."""
    _stub_health(monkeypatch, model="gemma-4-e2b", served=["something-else"])
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ok"] is False
    assert body["model"]["ok"] is False
    assert "gemma-4-e2b" in body["model"]["detail"]
    assert body["ears"]["ok"] and body["mouth"]["ok"]


def test_health_reports_a_provider_it_cannot_reach(
    client: TestClient, monkeypatch
) -> None:
    import httpx

    _stub_health(monkeypatch, raises=httpx.ConnectError("refused"))
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ok"] is False
    assert "cannot reach" in body["model"]["detail"]


def test_health_checks_the_rooms_own_model_not_the_orchestrators(
    client: TestClient, monkeypatch
) -> None:
    """`config.model` is a per-room override. Reporting the orchestrator's model
    healthy while the room points at one the server lacks is a green light for a
    turn that cannot happen."""
    _stub_health(monkeypatch, model="orchestrator-model", served=["orchestrator-model"])
    client.post(
        "/api/clubhouse/voice/config",
        json={"channel": "r1", "config": {"enabled": True, "model": "room-model"}},
    )
    assert client.get("/api/clubhouse/voice/health").json()["model"]["ok"] is True
    scoped = client.get("/api/clubhouse/voice/health?channel=r1").json()
    assert scoped["model"]["ok"] is False
    assert "room-model" in scoped["model"]["detail"]


def test_the_ears_do_not_need_ffmpeg(client: TestClient, monkeypatch) -> None:
    """The pane sends 16 kHz PCM WAV, which the STT service reads itself, so a
    machine without ffmpeg still hears the room."""
    _stub_health(monkeypatch, ffmpeg=(False, True))
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ears"]["ok"] is True


class _FakeWhisper:
    def __init__(self, *, loaded=False, load_error=None):
        self.loaded = loaded
        self.load_error = load_error
        self.warmed = 0

    async def warm(self):
        self.warmed += 1
        self.loaded = True


def test_a_whisper_model_that_will_not_load_is_not_ready(
    client: TestClient, monkeypatch
) -> None:
    """An installed extra is not a usable model. A load that threw (Errno 22 from a
    dead stderr, 2026-10-02) left this saying "speech-to-text ready" over an agent
    that failed every chunk."""
    whisper = _FakeWhisper(load_error="OSError: [Errno 22] Invalid argument")
    _stub_health(monkeypatch, whisper=whisper)
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ears"]["ok"] is False
    assert "Errno 22" in body["ears"]["detail"]
    assert body["mouth"]["ok"] is True


def test_health_warms_whisper_before_anyone_speaks(
    client: TestClient, monkeypatch
) -> None:
    """A cold load is seconds long and otherwise lands on the first sentence."""
    whisper = _FakeWhisper()
    _stub_health(monkeypatch, whisper=whisper)
    assert client.get("/api/clubhouse/voice/health").json()["ears"]["ok"] is True
    # The warm-up is a background task; a second request gives the loop a turn.
    client.get("/api/clubhouse/voice/health")
    assert whisper.warmed == 1


def test_a_probe_that_could_not_ask_is_unknown_not_broken(
    client: TestClient, monkeypatch
) -> None:
    """The hardware module's rule: "we asked and it is absent" and "we could not
    ask" are different facts, and rendering the second as the first tells a working
    node to reinstall what it already has."""
    _stub_health(monkeypatch, voice=(False, False))
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ears"]["certain"] is False
    assert body["mouth"]["certain"] is False
