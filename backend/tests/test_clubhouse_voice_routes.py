"""The voice agent's HTTP surface, with the model stubbed at the provider seam.

These cover the wiring the pure tests in `test_clubhouse_voice.py` cannot: that a
turn reaches the model with the room in it, that a refusal is reported rather than
silently 200-ing with an empty body, and that the session survives across requests.
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


def test_an_addressed_utterance_gets_a_reply(client, captured):
    _enable(client)
    body = _turn(client, "agent, what's the topic?")
    assert body["spoke"] is True
    assert body["reply"] == "Sure, happy to help."


def test_the_room_reaches_the_model(client, captured):
    """The whole point of the rewrite: the agent is told who is here rather than
    guessing. If this passes vacuously the feature does not exist."""
    _enable(client)
    _turn(client, "agent, who is here?")
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
    _turn(client, "agent, what were we discussing?")
    replayed = [m["content"] for m in captured[0]]
    assert any("compilers are neat" in c for c in replayed)


def test_memory_persists_across_requests(client, captured):
    """The session is keyed by channel, so a pane reload rejoins the conversation."""
    _enable(client)
    _turn(client, "agent, hello")
    _turn(client, "agent, again")
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
    _turn(client, "agent, hello")
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
    assert "/agent search" in (body["notice"] or "")
    assert captured == []


def test_forget_clears_the_conversation(client, captured):
    _enable(client)
    _turn(client, "agent, hello")
    assert client.get("/api/clubhouse/voice/state?channel=c1").json()["turns"]
    _turn(client, "/agent forget")
    assert client.get("/api/clubhouse/voice/state?channel=c1").json()["turns"] == []


def test_a_moderator_command_is_refused_for_a_non_moderator(
    client, captured, monkeypatch
):
    """The check reads the room snapshot, and `Sidekick` is a speaker, not a mod."""
    _enable(client)
    body = _turn(client, "/agent topic Something else")
    assert body["notice"] == "Only moderators can do that."


def test_a_moderator_command_reaches_the_clubhouse_api(client, captured, monkeypatch):
    calls: list[tuple] = []

    async def fake_topic(channel, body):
        calls.append((channel, body.topic))
        return {}

    monkeypatch.setattr(
        "backend.modules.clubhouse.routes.update_channel_topic", fake_topic
    )
    _enable(client)
    room = {
        **ROOM,
        "members": [{"user_id": 9, "name": "Sidekick", "is_moderator": True}],
    }
    res = client.post(
        "/api/clubhouse/voice/turn",
        json={"channel": "c1", "text": "/agent topic Type systems", "room": room},
    )
    assert res.json()["notice"] == "Room topic set to: Type systems"
    assert calls == [("c1", "Type systems")]


def test_search_with_no_results_says_so_instead_of_inventing(
    client, captured, monkeypatch
):
    """The exact failure `/agent search` used to have by design: it asked the model
    to 'perform a simulated web search', which is a hallucination with a prompt."""

    async def nothing(query, config):
        return None

    monkeypatch.setattr(R, "gather_context", nothing)
    _enable(client)
    body = _turn(client, "/agent search who won in 1998")
    assert body["spoke"] is False
    assert "Couldn't find anything" in (body["notice"] or "")
    assert captured == []


def test_retrieved_context_is_injected_into_the_prompt(client, captured, monkeypatch):
    async def found(query, config):
        return f"You looked these up just now: France won the 1998 World Cup. ({query})"

    monkeypatch.setattr(R, "gather_context", found)
    _enable(client)
    body = _turn(client, "/agent search who won in 1998")
    assert body["spoke"] is True
    assert body["retrieved"] is True
    assert "France won the 1998 World Cup" in captured[0][-1]["content"]


def test_config_round_trips(client):
    _enable(client, posture="conversational", wakeWords=["hey you"], cooldownS=3)
    got = client.get("/api/clubhouse/voice/config?channel=c1").json()
    assert got["posture"] == "conversational"
    assert got["wakeWords"] == ["hey you"]
    assert got["cooldownS"] == 3


def test_a_dropped_partial_leaves_no_trace_in_the_history(client, captured):
    """The finished utterance follows seconds later and contains the fragment.

    Remembering both is how the model ends up answering a sentence that was cut off
    mid-word, having seen it twice.
    """
    client.post(
        "/api/clubhouse/voice/config",
        json={"channel": "c1", "config": {"enabled": True, "posture": "always"}},
    )
    dropped = client.post(
        "/api/clubhouse/voice/turn",
        json={
            "channel": "c1",
            "text": "so the thing about compilers is",
            "speaker": "Ada",
            "partial": True,
            "room": ROOM,
        },
    ).json()
    assert dropped["spoke"] is False
    assert dropped["reason"] == "waiting for a pause"

    state = client.get("/api/clubhouse/voice/state", params={"channel": "c1"}).json()
    assert state["turns"] == []


def test_the_interject_posture_answers_a_partial(client, captured):
    client.post(
        "/api/clubhouse/voice/config",
        json={"channel": "c2", "config": {"enabled": True, "posture": "interject"}},
    )
    res = client.post(
        "/api/clubhouse/voice/turn",
        json={
            "channel": "c2",
            "text": "so the thing about compilers is",
            "speaker": "Ada",
            "partial": True,
            "room": ROOM,
        },
    ).json()
    assert res["spoke"] is True
    assert res["reply"] == "Sure, happy to help."


def test_turn_with_config_payload_syncs_session(client, captured):
    """Passing config in TurnRequest directly synchronizes the session, preventing race conditions."""
    custom_persona = "You are an ancient Roman philosopher."
    res = client.post(
        "/api/clubhouse/voice/turn",
        json={
            "channel": "c_sync",
            "text": "agent, what is truth?",
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


def test_a_plain_reply_costs_exactly_one_generation(client, captured):
    _enable(client)
    _turn(client, "agent, hello")
    assert len(captured) == 1


def test_a_tool_call_runs_and_its_result_reaches_a_second_generation(
    client, monkeypatch
):
    calls: list[tuple[list[dict], list | None]] = []

    async def fake_generate(messages, config, tools=None):
        calls.append((messages, tools))
        if len(calls) == 1:
            call = P.ToolCall(
                id="1", name="play_music", arguments={"query": "Africa by Toto"}
            )
            return P.ChatResult({"role": "assistant", "content": ""}, [call], "")
        return _said("Putting on Africa now.")

    async def fake_play(query):
        action = {"type": "music.play", "songId": "s1", "title": query, "ready": True}
        return f"Now playing '{query}'.", action

    monkeypatch.setattr(R, "generate_reply", fake_generate)
    monkeypatch.setattr(R, "play_music", fake_play)
    _enable(client)
    body = _turn(client, "agent, play Africa by Toto")

    assert body["reply"] == "Putting on Africa now."
    assert body["actions"] == [
        {"type": "music.play", "songId": "s1", "title": "Africa by Toto", "ready": True}
    ]
    assert "play_music" in [t["function"]["name"] for t in calls[0][1]]
    assert "Now playing 'Africa by Toto'." in calls[1][0][-1]["content"]
    # The follow-up offers no tools, so it has to speak rather than call again.
    assert not calls[1][1]
    roles = [m["role"] for m in calls[1][0]]
    assert roles[-1] == "user" and roles.count("user") == 1


def test_agent_play_command_needs_no_model(client, captured, monkeypatch):
    async def fake_play(query):
        action = {"type": "music.play", "songId": "s2", "title": query, "ready": False}
        return "Found it, downloading.", action

    monkeypatch.setattr(R, "play_music", fake_play)
    _enable(client)
    body = _turn(client, "/agent play lofi beats")
    assert body["notice"] == "Found it, downloading."
    assert body["actions"][0]["songId"] == "s2"
    assert captured == []


def test_the_first_name_wakes_the_agent_through_the_route(client, captured):
    _enable(client)
    room = {**ROOM, "my_name": "Horrible Program"}
    body = client.post(
        "/api/clubhouse/voice/turn",
        json={"channel": "c1", "text": "Horrible, you there?", "room": room},
    ).json()
    assert body["spoke"] is True


def test_music_volume_is_clamped():
    said, action = R.music_control("volume", 5)
    assert action == {"type": "music.volume", "value": 1.0}
    assert R.music_control("rewind")[1] is None


def test_the_music_status_route_reports_a_download(client, monkeypatch):
    def fake_get(song_id):
        if song_id != "s1":
            return None
        return {"id": "s1", "status": "downloading", "title": "Africa", "error": None}

    monkeypatch.setattr("backend.modules.karaoke.store.get_song", fake_get)
    assert client.get("/api/clubhouse/voice/music/s1").json()["status"] == "downloading"
    assert client.get("/api/clubhouse/voice/music/nope").status_code == 404


@pytest.fixture
def recorded(monkeypatch) -> list[tuple[list[dict], list | None]]:
    calls: list[tuple[list[dict], list | None]] = []

    async def fake_generate(messages, config, tools=None):
        calls.append((messages, tools))
        return _said("Done.")

    monkeypatch.setattr(R, "generate_reply", fake_generate)
    return calls


def _say(client: TestClient, text: str, **room) -> dict:
    res = client.post(
        "/api/clubhouse/voice/turn",
        json={"channel": "c1", "text": text, "room": {**ROOM, **room}},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_a_clear_music_command_is_one_generation_with_the_action_taken(
    client, recorded
):
    _enable(client)
    body = _say(client, "agent, stop the music", music="Africa")
    assert body["actions"] == [{"type": "music.stop"}]
    assert len(recorded) == 1 and not recorded[0][1]
    assert "Music stopped." in recorded[0][0][-1]["content"]


def test_stopping_music_when_none_is_playing_says_so(client, recorded):
    _enable(client)
    body = _say(client, "agent, stop the music")
    assert body["actions"] == []
    assert "Nothing is playing right now." in recorded[0][0][-1]["content"]


def test_turn_it_down_is_a_relative_step(client, recorded):
    _enable(client)
    body = _say(client, "agent, turn it down a bit", music="Africa")
    assert body["actions"] == [{"type": "music.volume", "step": -R.VOLUME_STEP}]


def test_look_up_phrasing_runs_the_search(client, recorded, monkeypatch):
    async def found(query, config):
        return f"You looked these up just now:\n- {query}: Argentina won."

    monkeypatch.setattr(R, "gather_context", found)
    _enable(client)
    _say(client, "agent, look up who won the 2022 World Cup final")
    assert len(recorded) == 1 and not recorded[0][1]
    assert "Argentina won." in recorded[0][0][-1]["content"]


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
    body = _turn(client, "agent, hello")
    assert body["spoke"] is False
    assert "acknowledged the prompt" in body["reason"]
    turns = client.get("/api/clubhouse/voice/state?channel=c1").json()["turns"]
    assert [t["role"] for t in turns] == ["room"]


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
):
    """Pin every probe the health route makes. `(available, certain)` per extra."""
    from backend import extras
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


def test_a_deaf_agent_with_the_extra_installed_blames_ffmpeg(
    client: TestClient, monkeypatch
) -> None:
    """Whisper is handed WebM/Opus from the browser and cannot decode it alone, so
    this is a perfectly installed extra that still hears nothing."""
    _stub_health(monkeypatch, ffmpeg=(False, True))
    body = client.get("/api/clubhouse/voice/health").json()
    assert body["ears"]["ok"] is False
    assert "ffmpeg" in body["ears"]["detail"]
    # The mouth does not need a decoder; only the ears are down.
    assert body["mouth"]["ok"] is True


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
