"""An invite has to name a person, and it has to land somewhere.

Two defects these pin, both of which were invisible rather than loud:

1. The invite carried `node_identity.node_name()`, so an invitation from a friend
   read *"horribleComputer invited you"* — a machine, not a person. Nothing
   errored; the wrong noun was simply the only one in scope where the payload was
   built.

2. It was broadcast on the `hassault` channel, which only the game pane
   subscribes to. With the pane closed — a different workspace, an inactive tab,
   the app not open on that screen — the invite arrived, updated a dict, and was
   seen by nobody. Again: no error, no dropped message, just no audience.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from backend.modules.hassault import fabric
from backend.modules.notifications import store as notif_store


class _Info:
    def __init__(self, node_id: str, node_name: str, trusted: bool = True) -> None:
        self.node_id = node_id
        self.node_name = node_name
        self.trusted = trusted


class _Session:
    def __init__(
        self, node_id: str = "node-abc", node_name: str = "horribleComputer"
    ) -> None:
        self.info = _Info(node_id, node_name)


class _Envelope:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Every `/ws` broadcast, and a scratch app.db for the inbox."""
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    from backend import paths

    paths.data_dir.cache_clear() if hasattr(paths.data_dir, "cache_clear") else None
    notif_store.init_notifications_db()

    events: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_broadcast(channel: str, event: str, data: dict[str, Any]) -> None:
        events.append((channel, event, data))

    import backend.modules.ws as ws_module
    from backend.modules.notifications import service as notif_service

    # Patched in **both** places on purpose: `fabric` imports `broadcast_event`
    # inside the function (so the module attribute is what it reaches), while
    # `notifications.service` imports it at module scope, which binds the name at
    # import time and makes it immune to patching the source module. Patch only
    # the first and this file silently stops observing half of what it is here to
    # observe.
    monkeypatch.setattr(ws_module, "broadcast_event", fake_broadcast)
    monkeypatch.setattr(notif_service, "broadcast_event", fake_broadcast)
    fabric._invites.clear()
    fabric._pending.clear()
    return events


async def _invite(data: dict[str, Any], session: _Session | None = None) -> None:
    await fabric.handle_invite(None, session or _Session(), _Envelope(data))


@pytest.mark.anyio
async def test_an_invite_names_the_person_not_the_machine(
    captured, monkeypatch
) -> None:
    # No roster entry for this node, so the sender's stamped username is what is
    # left — and it must still beat the device name, which is the only thing the
    # old payload carried.
    monkeypatch.setattr(
        "backend.modules.social.store.person_for_node", lambda node_id: None
    )
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"})

    invite = fabric._invites["r1"]
    assert invite["hostName"] == "@rob"
    # The machine is kept, but demoted: an invite fans out to every device a
    # person has online, so which one it came from is worth saying — second.
    assert invite["hostDevice"] == "horribleComputer"


@pytest.mark.anyio
async def test_the_roster_beats_the_senders_own_claim(captured, monkeypatch) -> None:
    """The name we resolved ourselves wins over the one they told us.

    Not pedantry: the stamp is a claim, and the roster's `handle` is the one we
    checked. Preferring the claim would let an authenticated friend render under
    somebody else's username in your invite list.
    """
    monkeypatch.setattr(
        "backend.modules.social.store.person_for_node", lambda node_id: "person-1"
    )
    monkeypatch.setattr(
        "backend.modules.social.store.get_friend_row",
        lambda person_id: {"handle": "real-rob"},
    )
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "not-rob"})

    assert fabric._invites["r1"]["hostName"] == "@real-rob"
    assert fabric._invites["r1"]["personId"] == "person-1"


@pytest.mark.anyio
async def test_an_invite_reaches_the_shell_not_only_the_game_pane(captured) -> None:
    """The regression that made invites invisible.

    The `hassault` broadcast is still sent — the pane uses it — but it is no
    longer the only one, because that channel has exactly one subscriber and it
    unmounts on a workspace switch.
    """
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"})

    channels = {channel for channel, _event, _data in captured}
    assert "hassault" in channels
    assert "notifications" in channels

    notify = next(d for c, e, d in captured if c == "notifications" and e == "notify")
    assert "@rob" in notify["title"]
    # The Join button on the toast needs both the action name and something to act
    # on; either missing and the toast is a dead end.
    assert notify["action"] == "hassault.joinInvite"
    assert notify["invite"]["room"] == "r1"


@pytest.mark.anyio
async def test_an_invite_survives_a_reload(captured) -> None:
    """It is in the database, not only on the socket — the whole point of the
    inbox. A toast missed while the app was closed used to cost the invite."""
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"})

    rows = notif_store.feed()
    assert [r["title"] for r in rows if r["category"] == "invite"]
    assert rows[0]["dedupe"] == "hassault-invite:node-abc:r1"


@pytest.mark.anyio
async def test_re_inviting_refreshes_rather_than_stacks(captured) -> None:
    """One person, one match, one card — however many times they press Invite."""
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"})
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"})

    invites = [r for r in notif_store.feed() if r["category"] == "invite"]
    assert len(invites) == 1


@pytest.mark.anyio
async def test_answering_clears_every_surface(captured) -> None:
    """`retract` is what stops three stale copies outliving the one you answered."""
    from backend.modules.notifications import service

    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"})
    await service.retract("hassault-invite:node-abc:r1")

    assert not [r for r in notif_store.feed() if r["category"] == "invite"]
    assert any(e == "retract" for _c, e, _d in captured)


@pytest.mark.anyio
async def test_an_untrusted_peer_cannot_invite(captured) -> None:
    """Knowing a room id is not friendship — the existing gate, still shut."""
    session = _Session()
    session.info.trusted = False
    await _invite({"room": "r1", "map": "hd_crossing", "fromUsername": "rob"}, session)

    assert not fabric._invites
    assert not captured


def test_an_offline_friend_is_queued_on_the_sender() -> None:
    """There is no relay in the path.

    `peer_hub.send_to` is a direct node-to-node send, so an offline target means
    nothing is sent at all — the receiver cannot hold what never arrived. The
    sender holds it, because the sender is the only party that can.
    """
    fabric._pending.clear()
    fabric.queue_invite("node-x", "r1", "hd_crossing")
    fabric.queue_invite("node-x", "r1", "hd_crossing")
    # Same room twice while they were away is one invite, not two.
    assert len(fabric._pending["node-x"]) == 1

    fabric.queue_invite("node-x", "r2", "hd_pit")
    assert len(fabric._pending["node-x"]) == 2

    # And it expires rather than delivering into a match that ended an hour ago.
    fabric._pending["node-x"][0]["ts"] = time.time() - fabric.INVITE_TTL - 1
    fabric.queue_invite("node-x", "r3", "hd_atrium")
    assert {i["room"] for i in fabric._pending["node-x"]} == {"r2", "r3"}
