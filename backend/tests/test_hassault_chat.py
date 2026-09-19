"""In-match chat: Unicode kept whole, spoofing characters removed, one path."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.modules.hassault import channel, chat, fabric
from backend.modules.hassault.match import MatchRoom, match_server
from backend.modules.hassault.physics import flat_world
from backend.modules.network.models import PeerEnvelope

FAMILY = "\U0001f468‍\U0001f469‍\U0001f467‍\U0001f466"  # 👨‍👩‍👧‍👦
FLAG_US = "\U0001f1fa\U0001f1f8"
THUMB_TONE = "\U0001f44d\U0001f3fd"
ENGLAND = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
HEART_EMOJI = "❤️"


# ---- cleaning -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        FAMILY,
        FLAG_US,
        THUMB_TONE,
        ENGLAND,
        HEART_EMOJI,
        "日本語のチャット",
        "مرحبا بالعالم",
        "Привет",
        "नमस्ते",  # Devanagari, with combining vowel signs
        "می‌خواهم",  # Persian with a zero-width non-joiner
        "gg 🎉🔥💀",
    ],
)
def test_real_text_survives_untouched(text: str) -> None:
    assert chat.clean(text) == text


def test_nfc_normalises_so_one_word_is_one_string() -> None:
    assert chat.clean("é") == "é"


def test_bidi_overrides_are_removed() -> None:
    # U+202E would reverse everything after it on the line — someone else's name.
    assert chat.clean("hi‮txt.exe") == "hitxt.exe"
    assert chat.clean("a⁦b⁩c") == "abc"


def test_controls_and_newlines_become_one_line() -> None:
    assert chat.clean("one\ntwo\r\n\tthree\x00\x07") == "one two three"
    assert chat.clean("   ") == ""
    assert chat.clean(None) == ""
    assert chat.clean(42) == ""


def test_the_limit_never_splits_a_cluster() -> None:
    text = FAMILY * (chat.MAX_GRAPHEMES + 10)
    out = chat.clean(text)
    # Byte ceiling reached first (25 bytes each), and every emoji is whole.
    assert out == FAMILY * (len(out) // len(FAMILY))
    assert len(out.encode()) <= chat.MAX_BYTES
    assert chat.grapheme_count(out) <= chat.MAX_GRAPHEMES


def test_the_grapheme_limit_counts_what_a_person_sees() -> None:
    out = chat.clean("é" * 500)
    assert chat.grapheme_count(out) == chat.MAX_GRAPHEMES


def test_a_zalgo_cluster_is_bounded_by_bytes() -> None:
    zalgo = "a" + "́" * 2000
    out = chat.clean(zalgo + " ok")
    assert len(out.encode()) <= chat.MAX_BYTES


def test_rate_limit_is_a_sliding_window() -> None:
    limiter = chat.RateLimiter(count=2, window=1.0)
    assert limiter.allow("p", now=0.0)
    assert limiter.allow("p", now=0.1)
    assert not limiter.allow("p", now=0.2)
    assert limiter.allow("other", now=0.2)
    assert limiter.allow("p", now=1.2)


# ---- delivery -----------------------------------------------------------------


class Spawn:
    def __init__(self, x: float, y: float, team: int = 0) -> None:
        self.x, self.y, self.z, self.yaw, self.attr2 = x, y, 0.0, 0.0, team


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m.get("event") == name]


class FakeHub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def send_to(self, node_id: str, msg_type: str, data: dict[str, Any], re=None):
        self.sent.append((node_id, msg_type, data))


class FakeSession:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.info = type(
            "I", (), {"node_id": node_id, "trusted": trusted, "node_name": node_id}
        )()


@pytest.fixture(autouse=True)
def clean_state():
    chat.limiter._sent.clear()
    yield
    match_server.rooms.clear()
    match_server.membership.clear()
    fabric._hosted.clear()
    fabric._remote.clear()


def two_players() -> tuple[MatchRoom, FakeConn, FakeConn]:
    room = MatchRoom("r1", "testmap", flat_world(32), [Spawn(8, 8), Spawn(20, 20, 1)])
    match_server.rooms["r1"] = room
    a, b = FakeConn(), FakeConn()
    pa = room.add("rob", a)
    pb = room.add("kim", b)
    match_server.membership[id(a)] = ("r1", pa.id)
    match_server.membership[id(b)] = ("r1", pb.id)
    return room, a, b


def test_everyone_gets_the_servers_copy_including_the_sender() -> None:
    async def go():
        _, a, b = two_players()
        await channel.handle(a, {"event": "chat", "data": {"text": f" gg {FAMILY}\n"}})  # type: ignore[arg-type]
        [mine] = a.events("chat")
        [theirs] = b.events("chat")
        assert mine == theirs  # one message, identical everywhere
        assert mine["text"] == f"gg {FAMILY}"
        assert mine["id"] and mine["ts"] > 0
        assert mine["senderName"] == "rob"

    asyncio.run(go())


def test_team_chat_stays_on_the_team() -> None:
    async def go():
        room, a, b = two_players()
        # Force the two onto different teams regardless of how `add` balanced them.
        ids = list(room.players)
        room.players[ids[0]].team = 0
        room.players[ids[1]].team = 1
        await channel.handle(
            a, {"event": "chat", "data": {"text": "rotate", "team": True}}
        )  # type: ignore[arg-type]
        assert a.events("chat") and a.events("chat")[0]["isTeam"] is True
        assert b.events("chat") == []

    asyncio.run(go())


def test_flooding_is_refused_and_says_so() -> None:
    async def go():
        _, a, _ = two_players()
        for i in range(chat.RATE_COUNT + 2):
            await channel.handle(a, {"event": "chat", "data": {"text": f"m{i}"}})  # type: ignore[arg-type]
        assert len(a.events("chat")) == chat.RATE_COUNT
        assert a.events("chat_refused")

    asyncio.run(go())


def test_a_remote_player_chats_through_the_same_path() -> None:
    async def go():
        room, a, _ = two_players()
        hub = FakeHub()
        await fabric.handle_join(
            hub,
            FakeSession("nodeA"),
            PeerEnvelope(
                type="hassault_join",
                msg_id="m",
                src="nodeA",
                ts=0.0,
                data={"client": "c1", "room": "r1", "name": "sam"},
            ),
        )
        await fabric.handle_chat(
            hub,
            FakeSession("nodeA"),
            PeerEnvelope(
                type=fabric.HASSAULT_CHAT,
                msg_id="m",
                src="nodeA",
                ts=0.0,
                data={"client": "c1", "text": f"hi {FLAG_US}‮", "team": False},
            ),
        )
        [line] = a.events("chat")
        assert line["text"] == f"hi {FLAG_US}"
        assert line["senderName"] == "sam@nodeA"

    asyncio.run(go())


def test_an_untrusted_peer_cannot_chat() -> None:
    async def go():
        _, a, _ = two_players()
        await fabric.handle_chat(
            FakeHub(),
            FakeSession("x", trusted=False),
            PeerEnvelope(
                type=fabric.HASSAULT_CHAT,
                msg_id="m",
                src="x",
                ts=0.0,
                data={"text": "hi"},
            ),
        )
        assert a.events("chat") == []

    asyncio.run(go())
