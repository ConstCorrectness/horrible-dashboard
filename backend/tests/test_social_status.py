"""Personas and activity: what you set yourself to, and what friends are told.

The rules worth pinning: `invisible` never reaches the wire, an invisible friend is
offline all the way down (devices included), idle demotes only a chosen `online`,
and a status goes only to trusted sessions.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import pytest

from backend.modules.network.models import PeerEnvelope
from backend.modules.social import identity as person_identity
from backend.modules.social import roster, store
from backend.modules.social import status as social_status


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    person_identity._cached_identity.cache_clear()
    from backend.modules.network import identity as node_identity

    node_identity._cached_identity.cache_clear()
    store.init_social_db()
    social_status.reset_for_tests()
    yield tmp_path
    social_status.reset_for_tests()
    person_identity._cached_identity.cache_clear()
    node_identity._cached_identity.cache_clear()


class FakeHub:
    def __init__(self, connected: list[str] | None = None) -> None:
        self.peers = {n: object() for n in (connected or [])}
        self.strangers: dict[str, object] = {}
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def send_to(self, node_id: str, msg_type: str, data: dict[str, Any]) -> None:
        if node_id not in self.peers:
            raise KeyError(node_id)
        self.sent.append((node_id, msg_type, data))


@pytest.fixture()
def hub(monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(roster, "peer_hub", fake)
    return fake


def _friend(name: str, *nodes: str) -> str:
    person_id = person_identity.fingerprint(base64.b64encode(os.urandom(32)).decode())
    store.upsert_friend(person_id, display_name=name, status="accepted")
    for node in nodes:
        store.upsert_device(node, person_id, f"k-{node}", node)
    return person_id


def _status(node: str, persona: str, activity: dict | None = None) -> PeerEnvelope:
    return PeerEnvelope(
        type=roster.SOCIAL_STATUS,
        src=node,
        data={"persona": persona, "activity": activity},
    )


def _row(person_id: str, online: set[str]):
    return next(f for f in store.list_friends(online) if f.person_id == person_id)


# ---- what friends are told --------------------------------------------------------


def test_invisible_is_sent_as_offline_without_activity(data_dir):
    social_status.set_activity({"title": "HorribleAssault", "detail": "de_dust2"})
    social_status.set_persona("invisible")
    assert social_status.wire_payload() == {"persona": "offline", "activity": None}
    # ...and it is still *your* setting, shown to you as what you chose.
    assert roster.self_profile().persona == "invisible"


def test_persona_persists_but_activity_does_not(data_dir):
    social_status.set_persona("busy")
    social_status.set_activity({"title": "Notebook"})
    social_status._activity = None  # what a restart does to an in-memory field
    assert social_status.own_persona() == "busy"
    assert social_status.own_activity() is None


def test_idle_demotes_online_only(data_dir):
    social_status.set_idle(True)
    assert social_status.shared_persona() == "away"
    social_status.set_persona("busy")
    assert social_status.shared_persona() == "busy", "idle must not override busy"
    social_status.set_persona("online")
    social_status.set_idle(False)
    assert social_status.shared_persona() == "online"


def test_reannouncing_an_activity_keeps_its_start_time(data_dir):
    social_status.set_activity(
        {"title": "HorribleAssault", "detail": "warmup", "since": 100}
    )
    social_status.set_activity(
        {"title": "HorribleAssault", "detail": "3 - 2", "since": 900}
    )
    activity = social_status.own_activity()
    assert activity is not None and activity.detail == "3 - 2"
    assert activity.since == 100


def test_activity_from_untrusted_input_is_cleaned(data_dir):
    assert social_status.clean_activity({"title": "   "}) is None
    assert social_status.clean_activity("nope") is None
    long = social_status.clean_activity({"title": "x" * 500, "kind": "weird"})
    assert long is not None
    assert len(long.title) == social_status.TITLE_MAX
    assert long.kind == "game"


# ---- what we show of friends ------------------------------------------------------


def test_friend_announcement_shows_persona_and_activity(data_dir, hub):
    person_id = _friend("Rob", "rob-desktop")

    async def go():
        await roster.handle_status(
            hub,
            None,
            _status("rob-desktop", "busy", {"title": "HorribleAssault", "since": 5}),
        )

    asyncio.run(go())
    row = _row(person_id, {"rob-desktop"})
    assert row.presence == "online"
    assert row.persona == "busy"
    assert row.activity is not None and row.activity.title == "HorribleAssault"


def test_invisible_friend_is_offline_all_the_way_down(data_dir, hub):
    person_id = _friend("Rob", "rob-desktop")
    asyncio.run(roster.handle_status(hub, None, _status("rob-desktop", "offline")))
    row = _row(person_id, {"rob-desktop"})
    assert row.presence == "offline"
    assert row.persona == "offline"
    assert not any(d.online for d in row.devices), (
        "a green device dot would give them away"
    )


def test_most_available_machine_wins(data_dir, hub):
    person_id = _friend("Rob", "desk", "laptop")

    async def go():
        await roster.handle_status(hub, None, _status("desk", "away"))
        await roster.handle_status(hub, None, _status("laptop", "online"))

    asyncio.run(go())
    assert _row(person_id, {"desk", "laptop"}).persona == "online"
    # With the laptop gone, the desktop's `away` is all that is left.
    assert _row(person_id, {"desk"}).persona == "away"


def test_a_machine_that_never_announced_reads_as_online(data_dir):
    """An older build has no `social_status` at all; it must look as it did before."""
    person_id = _friend("Rob", "old-build")
    row = _row(person_id, {"old-build"})
    assert row.presence == "online" and row.persona == "online"


def test_a_stale_announcement_does_not_outlive_the_session(data_dir, hub):
    person_id = _friend("Rob", "desk")
    asyncio.run(roster.handle_status(hub, None, _status("desk", "busy")))
    assert _row(person_id, set()).persona == "offline"


# ---- the wire ---------------------------------------------------------------------


def test_status_change_is_announced_to_trusted_sessions_only(data_dir, hub):
    hub.peers["friend-node"] = object()
    hub.strangers["stranger-node"] = object()

    asyncio.run(roster.update_status(persona="busy"))

    targets = [node for node, kind, _ in hub.sent if kind == roster.SOCIAL_STATUS]
    assert targets == ["friend-node"]
    assert hub.sent[-1][2]["persona"] == "busy"


def test_a_change_friends_cannot_see_is_not_sent(data_dir, hub):
    hub.peers["friend-node"] = object()
    asyncio.run(roster.update_status(persona="busy"))
    hub.sent.clear()
    # Going idle while busy changes nothing on the wire.
    asyncio.run(roster.update_status(idle=True))
    assert hub.sent == []


def test_status_is_not_open_to_strangers(data_dir, monkeypatch):
    class Recorder:
        def __init__(self) -> None:
            self.handlers: dict[str, Any] = {}
            self.stranger_types: set[str] = set()

        def register_handler(self, msg_type: str, handler: Any) -> None:
            self.handlers[msg_type] = handler

        def allow_from_strangers(self, *types: str) -> None:
            self.stranger_types.update(types)

        def subscribe(self, cb: Any) -> Any:
            return lambda: None

    rec = Recorder()
    monkeypatch.setattr(roster, "peer_hub", rec)
    roster.register(rec)  # type: ignore[arg-type]
    assert roster.SOCIAL_STATUS in rec.handlers
    assert roster.SOCIAL_STATUS not in rec.stranger_types


def test_status_route_round_trips(data_dir, hub):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.modules.social.routes import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    body = client.post(
        "/social/status",
        json={"persona": "away", "activity": {"title": "Notebook", "kind": "app"}},
    ).json()
    # Through the HTTP response, not the function: response_model drops fields it
    # does not declare.
    assert body["persona"] == "away"
    assert body["activity"]["title"] == "Notebook"
    body = client.post("/social/status", json={"clear_activity": True}).json()
    assert body["activity"] is None
