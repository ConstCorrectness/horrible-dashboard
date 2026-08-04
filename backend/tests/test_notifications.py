"""Notification rules, standing watches, and the agent tools over them.

Three things these tests exist to pin, because each fails *silently* if broken:

1. **The mute check runs at the producer.** `service.notify` must return False and
   send nothing — a suppressed notification that still crosses the socket is one
   that still lit up a phone.
2. **`except_person` inverts the scope.** "mute everything except Andrew" is one
   rule; getting the inversion backwards mutes exactly the person you wanted.
3. **These tools are grouped, never core.** The always-on core is budgeted; six
   always-loaded tools for an occasional feature would cost every turn in the app.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from backend.modules.notifications import agent_tools, service, store
from backend.modules.social import store as social_store


def _friend(person_id: str, name: str) -> None:
    social_store.init_social_db()
    social_store.upsert_friend(person_id, display_name=name, status="accepted")


def _sent(monkeypatch) -> list[tuple[str, str, dict[str, Any]]]:
    """Capture what `notify` puts on the wire instead of broadcasting it."""
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake(channel: str, event: str, data: dict[str, Any]) -> None:
        calls.append((channel, event, data))

    monkeypatch.setattr(service, "broadcast_event", fake)
    return calls


# ---- mutes -------------------------------------------------------------------------


def test_category_mute_silences_only_that_category():
    store.add_mute("message")
    assert store.is_muted("message") is True
    assert store.is_muted("invite") is False


def test_all_mutes_every_category():
    store.add_mute("all")
    assert store.is_muted("message") is True
    assert store.is_muted("presence") is True


def test_person_mute_is_scoped_to_that_person():
    store.add_mute("message", person_id="p_andrew")
    assert store.is_muted("message", "p_andrew") is True
    assert store.is_muted("message", "p_someone_else") is False
    # No person on the notification at all: a per-person rule must not swallow it.
    assert store.is_muted("message") is False


def test_except_person_inverts_the_scope():
    """ "Mute any messages except for him" — the shape the whole feature turns on."""
    store.add_mute("message", except_person="p_andrew")
    assert store.is_muted("message", "p_andrew") is False
    assert store.is_muted("message", "p_other") is True
    assert store.is_muted("message") is True
    # Still scoped by category.
    assert store.is_muted("invite", "p_other") is False


def test_expired_mute_is_swept_on_read():
    store.add_mute("message", duration_s=0.001)
    time.sleep(0.02)
    assert store.is_muted("message") is False
    assert store.active_mutes() == []


def test_clear_mutes_filters_and_counts():
    store.add_mute("message")
    store.add_mute("invite")
    store.add_mute("message", person_id="p_a")
    assert store.clear_mutes("message", "p_a") == 1
    assert store.clear_mutes("message") == 1
    assert store.is_muted("invite") is True
    assert store.clear_mutes() == 1


# ---- watches -----------------------------------------------------------------------


def test_watch_round_trips_its_predicate():
    watch = store.add_watch(
        "presence",
        subject="p_andrew",
        label="Andrew",
        predicate={"online": True},
        note="you asked",
    )
    rows = store.list_watches("presence")
    assert [r["id"] for r in rows] == [watch["id"]]
    assert rows[0]["predicate"] == {"online": True}
    assert rows[0]["label"] == "Andrew"


def test_fired_one_shot_disappears_but_a_standing_one_stays():
    once = store.add_watch(
        "presence", subject="p_a", label="A", predicate={"online": True}
    )
    forever = store.add_watch(
        "presence",
        subject="p_b",
        label="B",
        predicate={"online": True},
        one_shot=False,
    )
    store.mark_fired(once["id"])
    store.mark_fired(forever["id"])
    assert [r["id"] for r in store.list_watches("presence")] == [forever["id"]]


def test_expired_watch_is_swept():
    store.add_watch(
        "presence",
        subject="p_a",
        label="A",
        predicate={"online": True},
        duration_s=0.001,
    )
    time.sleep(0.02)
    assert store.list_watches("presence") == []


def test_cancel_watch_reports_whether_it_existed():
    watch = store.add_watch(
        "presence", subject="p_a", label="A", predicate={"online": True}
    )
    assert store.cancel_watch(watch["id"]) is True
    assert store.cancel_watch(watch["id"]) is False


# ---- delivery ----------------------------------------------------------------------


def test_notify_sends_when_nothing_is_muted(monkeypatch):
    calls = _sent(monkeypatch)

    async def go() -> bool:
        return await service.notify("message", "Hi", "body", person_id="p_a")

    assert asyncio.run(go()) is True
    assert len(calls) == 1
    channel, event, data = calls[0]
    assert (channel, event) == (service.CHANNEL, "notify")
    assert data["category"] == "message"
    assert data["person_id"] == "p_a"


def test_muted_notification_never_reaches_the_wire(monkeypatch):
    calls = _sent(monkeypatch)
    store.add_mute("message")

    async def go() -> bool:
        return await service.notify("message", "Hi", "body", person_id="p_a")

    assert asyncio.run(go()) is False
    assert calls == []


def test_presence_fires_a_matching_watch_and_retires_it(monkeypatch):
    calls = _sent(monkeypatch)
    watch = store.add_watch(
        "presence",
        subject="p_andrew",
        label="Andrew",
        predicate={"online": True},
        note="you asked me to say",
    )

    async def go() -> None:
        await service.on_presence(
            {"person_id": "p_andrew", "online": True, "display_name": "Andrew"}
        )

    asyncio.run(go())
    assert len(calls) == 1
    assert calls[0][2]["title"] == "Andrew is online"
    assert calls[0][2]["body"] == "you asked me to say"
    assert calls[0][2]["watch_id"] == watch["id"]
    # One-shot: gone afterwards.
    assert store.list_watches("presence") == []


def test_presence_ignores_the_wrong_person_and_the_wrong_direction(monkeypatch):
    calls = _sent(monkeypatch)
    store.add_watch(
        "presence", subject="p_andrew", label="Andrew", predicate={"online": True}
    )

    async def go() -> None:
        # Someone else arriving.
        await service.on_presence({"person_id": "p_other", "online": True})
        # Andrew *leaving* — the watch is for arrivals.
        await service.on_presence({"person_id": "p_andrew", "online": False})

    asyncio.run(go())
    assert calls == []
    assert len(store.list_watches("presence")) == 1


def test_a_muted_one_shot_is_still_retired(monkeypatch):
    """The user chose not to hear it; it is not still owed to them."""
    calls = _sent(monkeypatch)
    store.add_mute("watch")
    store.add_watch(
        "presence", subject="p_andrew", label="Andrew", predicate={"online": True}
    )

    async def go() -> None:
        await service.on_presence({"person_id": "p_andrew", "online": True})

    asyncio.run(go())
    assert calls == []
    assert store.list_watches("presence") == []


# ---- the presence signal itself ----------------------------------------------------


def _snapshot(*presences: tuple[str, str, str]):
    from backend.modules.social.models import Friend, RosterSnapshot, SelfProfile

    return RosterSnapshot(
        self_profile=SelfProfile(
            person_id="p_me",
            friend_code="HD-0000",
            display_name="Me",
            person_public_key="",
        ),
        friends=[
            Friend(
                person_id=pid,
                display_name=name,
                friend_code=f"HD-{pid}",
                person_public_key="",
                status="accepted",
                added_at=0.0,
                presence=presence,  # type: ignore[arg-type]
            )
            for pid, name, presence in presences
        ],
    )


def test_presence_is_emitted_only_when_it_changes(monkeypatch):
    """The roster snapshot was all that ever went out, so nothing could tell
    "Andrew is here" from "Andrew is still here"."""
    from backend.modules.social import roster

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(roster, "_last_presence", {})
    unsubscribe = roster.subscribe(lambda e, d: events.append((e, d)))
    try:
        states = [_snapshot(("p_a", "Andrew", "offline"))]
        monkeypatch.setattr(roster, "snapshot", lambda: states[0])

        # First sight of anyone announces nothing — otherwise a restart declares
        # every already-connected friend to have just arrived.
        roster.broadcast_roster()
        assert [e for e, _ in events] == ["roster"]

        # Now they arrive.
        events.clear()
        states[0] = _snapshot(("p_a", "Andrew", "online"))
        roster.broadcast_roster()
        presence = [d for e, d in events if e == "presence"]
        assert len(presence) == 1
        assert presence[0]["person_id"] == "p_a"
        assert presence[0]["display_name"] == "Andrew"
        assert presence[0]["online"] is True

        # Still online: no second announcement.
        events.clear()
        roster.broadcast_roster()
        assert [e for e, _ in events] == ["roster"]

        # And leaving is its own event.
        events.clear()
        states[0] = _snapshot(("p_a", "Andrew", "offline"))
        roster.broadcast_roster()
        assert [d["online"] for e, d in events if e == "presence"] == [False]
    finally:
        unsubscribe()


# ---- agent tools -------------------------------------------------------------------


def test_watch_create_resolves_a_display_name():
    _friend("p_andrew", "Andrew")
    result = agent_tools.watch_create({"who": "andrew"})
    assert result["ok"] is True
    assert result["watching"] == "Andrew"
    assert result["for"] == "coming online"
    watches = store.list_watches("presence")
    assert watches[0]["subject"] == "p_andrew"


def test_watch_create_refuses_an_unknown_name():
    social_store.init_social_db()
    result = agent_tools.watch_create({"who": "nobody"})
    assert "error" in result
    assert store.list_watches("presence") == []


def test_watch_list_and_cancel_round_trip():
    _friend("p_andrew", "Andrew")
    created = agent_tools.watch_create({"who": "Andrew", "expires_in_minutes": 60})
    listed = agent_tools.watch_list({})["watches"]
    assert listed[0]["id"] == created["watch_id"]
    assert listed[0]["expires_in_minutes"] == 60
    assert agent_tools.watch_cancel({"watch_id": created["watch_id"]}) == {"ok": True}
    assert agent_tools.watch_list({})["watches"] == []


def test_notify_mute_except_who_is_the_sentence_shape():
    """ "mute any messages except for him for a bit" as the model would call it."""
    _friend("p_andrew", "Andrew")
    result = agent_tools.notify_mute(
        {"category": "message", "except_who": "Andrew", "duration_minutes": 30}
    )
    assert result["ok"] is True
    assert result["who"] == "everyone except Andrew"
    assert result["for_minutes"] == 30
    assert store.is_muted("message", "p_andrew") is False
    assert store.is_muted("message", "p_other") is True


def test_notify_mute_rejects_an_unknown_category():
    assert "error" in agent_tools.notify_mute({"category": "carrier-pigeon"})
    assert store.active_mutes() == []


def test_duration_is_capped_at_a_day():
    assert agent_tools._duration(90) == 90 * 60
    assert agent_tools._duration(60 * 24 * 7) == 24 * 60 * 60
    # "for a bit" that arrives as nonsense must not fail the whole instruction.
    assert agent_tools._duration("a while") is None
    assert agent_tools._duration(0) is None


def test_notify_status_and_unmute():
    agent_tools.notify_mute({"category": "message", "duration_minutes": 10})
    status = agent_tools.notify_status({})["mutes"]
    assert status[0]["category"] == "message"
    assert status[0]["expires_in_minutes"] == 10
    assert agent_tools.notify_unmute({"category": "message"}) == {
        "ok": True,
        "lifted": 1,
    }
    assert agent_tools.notify_status({})["mutes"] == []


# ---- registration ------------------------------------------------------------------


def test_every_tool_is_grouped_and_its_group_matches_its_prefix():
    from backend.modules.agent.orchestrator import (
        _GROUP_DESCRIPTIONS,
        _GROUP_KEYWORDS,
        _group_of,
    )
    from backend.sdk.registry import registry

    agent_tools.register_notification_tools()
    names = [n for n in registry.agent_tools if n.startswith(("watch.", "notify."))]
    assert len(names) == 6
    for name in names:
        tool = registry.agent_tools[name]
        # Grouped, never core — the budget is the reason.
        assert tool.group, f"{name} would load on every turn"
        # The orchestrator derives the group from the prefix, ignoring `group=`.
        assert _group_of(name) == tool.group
        assert tool.group in _GROUP_DESCRIPTIONS
        assert tool.group in _GROUP_KEYWORDS


def test_the_users_literal_sentence_preloads_both_groups():
    """One ask, one turn: the sentence must reach `watch.*` *and* `notify.*`."""
    from backend.modules.agent.orchestrator import _GROUP_KEYWORDS

    text = (
        "let me know when andrew logs in, and mute any messages except for him "
        "for a bit"
    ).lower()
    for group in ("watch", "notify"):
        assert group in text or any(kw in text for kw in _GROUP_KEYWORDS[group]), group
