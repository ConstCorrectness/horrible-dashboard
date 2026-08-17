"""The voice agent's HTTP surface, with the model stubbed at the provider seam.

These cover the wiring the pure tests in `test_clubhouse_voice.py` cannot: that a
turn reaches the model with the room in it, that a refusal is reported rather than
silently 200-ing with an empty body, and that the session survives across requests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.modules.clubhouse import voice as V
from backend.modules.clubhouse import voice_runtime as R

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

    async def fake_generate(messages, config):
        seen.append(messages)
        return "Sure, happy to help."

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
    system = captured[0][0]["content"]
    assert "Compilers" in system
    assert "Ada (moderator)" in system


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
    assert "France won the 1998 World Cup" in captured[0][0]["content"]


def test_config_round_trips(client):
    _enable(client, posture="conversational", wakeWords=["hey you"], cooldownS=3)
    got = client.get("/api/clubhouse/voice/config?channel=c1").json()
    assert got["posture"] == "conversational"
    assert got["wakeWords"] == ["hey you"]
    assert got["cooldownS"] == 3
