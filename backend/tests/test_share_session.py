"""Share sessions: the grant ladder, the gate, and the trust boundary.

No sockets. The session's broadcasts go through `backend.modules.ws`, which is
patched to a capture list, and the peer sends go through `peer_hub`, patched the
same way — so these tests exercise the real `ShareManager` and the real fabric
handlers without a fabric.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.modules.share import fabric
from backend.modules.share.gate import allows, require, rung
from backend.modules.share.models import (
    GRANT_LADDER,
    Participant,
    RemoteSession,
    ShareInvite,
)
from backend.modules.share.session import ShareManager

HOST_NODE = "hostnode00000000"
GUEST_NODE = "guestnode0000000"


class FakeSessionInfo:
    def __init__(self, node_id: str, trusted: bool, name: str = "guest-box") -> None:
        self.node_id = node_id
        self.trusted = trusted
        self.node_name = name


class FakePeerSession:
    def __init__(self, node_id: str, trusted: bool) -> None:
        self.info = FakeSessionInfo(node_id, trusted)


class FakeEnvelope:
    def __init__(self, src: str, data: dict, msg_id: str = "m1") -> None:
        self.src = src
        self.data = data
        self.msg_id = msg_id
        self.type = "share_join"


class FakeHub:
    """Captures what would have gone out over the peer wire."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []

    async def send_to(self, node_id, msg_type, data, re=None):
        self.sent.append((node_id, msg_type, data))

    def types(self) -> list[str]:
        return [t for _n, t, _d in self.sent]


@pytest.fixture
def captured(monkeypatch):
    """Capture `/ws` broadcasts and stub the bits of the fabric these tests do
    not exercise (identity, the roster, the peer hub)."""
    events: list[tuple[str, str, dict]] = []

    async def fake_broadcast(channel, event, data):
        events.append((channel, event, data))

    monkeypatch.setattr("backend.modules.ws.broadcast_event", fake_broadcast)

    class FakeIdentity:
        node_id = HOST_NODE

    class FakeHubGlobal(FakeHub):
        def identity(self):
            return FakeIdentity()

        def list_peers(self):
            return []

    hub = FakeHubGlobal()
    monkeypatch.setattr("backend.modules.network.hub.peer_hub", hub)

    class FakeProfile:
        person_id = "hostperson"
        display_name = "Host"

    monkeypatch.setattr(
        "backend.modules.social.roster.self_profile", lambda: FakeProfile()
    )
    # `_display_name` consults the roster store; a node nobody knows falls through
    # to the device name, which is the path a stranger-shaped guest takes.
    monkeypatch.setattr(
        "backend.modules.social.store.person_for_node", lambda node_id: None
    )
    return events, hub


def events_named(events, name):
    return [d for _c, e, d in events if e == name]


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_ladder_is_ordered_weakest_first():
    assert GRANT_LADDER[0] == "view"
    assert GRANT_LADDER[-1] == "control"
    assert rung("view") < rung("edit") < rung("terminal") < rung("control")


def test_a_rung_answers_everything_below_it_and_nothing_above():
    assert allows("terminal", "edit")
    assert allows("terminal", "terminal")
    assert not allows("terminal", "agent")
    assert not allows("view", "cursor")


def test_an_unknown_rung_reads_as_the_weakest():
    """A peer on a newer build can name a rung this one has never heard of. The
    safe reading of 'something I do not understand' is `view`, not `control`."""
    assert rung("superuser") == 0  # type: ignore[arg-type]
    assert not allows("superuser", "edit")  # type: ignore[arg-type]


def test_gate_refuses_a_non_participant():
    ok, reason = require(None, "view")
    assert not ok
    assert reason


def test_gate_lets_the_host_do_anything():
    host = Participant(
        person_id="p",
        node_id=HOST_NODE,
        name="Host",
        role="host",
        grant="view",  # deliberately low: role wins, so a stale rung cannot lock out the host
        joined_at=time.time(),
    )
    ok, _ = require(host, "control")
    assert ok


def test_gate_refuses_a_guest_reaching_above_their_rung():
    guest = Participant(
        person_id="p", node_id=GUEST_NODE, name="G", grant="edit", joined_at=time.time()
    )
    assert require(guest, "edit")[0]
    ok, reason = require(guest, "terminal")
    assert not ok
    assert "terminal" in (reason or "")


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


def test_start_seeds_the_host_and_publishes(captured):
    events, _hub = captured
    mgr = ShareManager()
    session = asyncio.run(mgr.start("debugging", "semantic"))

    assert session.title == "debugging"
    assert len(session.participants) == 1
    assert session.participants[0].role == "host"
    assert session.revision == 1
    assert events_named(events, "session")


def test_restarting_retitles_rather_than_orphaning_guests(captured):
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        first = await mgr.start("one", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="Guest")
        second = await mgr.start("two", "semantic")
        return first, second

    first, second = asyncio.run(go())
    assert first.id == second.id
    assert second.title == "two"
    assert any(p.node_id == GUEST_NODE for p in second.participants)


def test_a_guest_joins_at_the_lowest_rung(captured):
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        return await mgr.add_participant(
            person_id="gp", node_id=GUEST_NODE, name="Guest"
        )

    participant = asyncio.run(go())
    assert participant is not None
    assert participant.grant == "view"
    assert participant.role == "guest"


def test_rejoining_from_one_machine_refreshes_rather_than_duplicating(captured):
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="Guest")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="Guest v2")

    asyncio.run(go())
    guests = [p for p in mgr.hosting.participants if p.role == "guest"]
    assert len(guests) == 1
    assert guests[0].name == "Guest v2"


def test_a_grant_moves_every_machine_of_one_person(captured):
    """You decide about a human. Asking which of their laptops may use the
    terminal is a question with no good answer."""
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id="box-a", name="Guest")
        await mgr.add_participant(person_id="gp", node_id="box-b", name="Guest")
        return await mgr.set_grant("gp", "terminal")

    assert asyncio.run(go()) is True
    assert all(
        p.grant == "terminal" for p in mgr.hosting.participants if p.role == "guest"
    )


def test_revoke_all_drops_guests_but_keeps_the_session_and_the_host(captured):
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="Guest")
        await mgr.set_grant("gp", "control")
        await mgr.revoke_all()

    asyncio.run(go())
    assert mgr.hosting is not None  # nobody is dumped out
    host = next(p for p in mgr.hosting.participants if p.role == "host")
    guest = next(p for p in mgr.hosting.participants if p.role == "guest")
    assert guest.grant == "view"
    assert host.grant == "control"


def test_every_mutation_bumps_the_revision(captured):
    """A client drops an out-of-order broadcast instead of rendering a
    participant list that jumps backwards."""
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        first = mgr.hosting.revision
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
        second = mgr.hosting.revision
        await mgr.set_grant("gp", "edit")
        return first, second, mgr.hosting.revision

    a, b, c = asyncio.run(go())
    assert a < b < c


def test_stopping_tells_every_guest(captured):
    events, hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
        hub.sent.clear()
        await mgr.stop()

    asyncio.run(go())
    assert mgr.hosting is None
    assert "share_end" in hub.types()
    assert events_named(events, "ended")


def test_invites_expire_and_are_pruned_on_read(captured):
    _events, _hub = captured
    mgr = ShareManager()
    now = time.time()
    asyncio.run(
        mgr.record_invite(
            ShareInvite(
                session_id="live",
                title="t",
                host=HOST_NODE,
                host_name="H",
                ts=now,
                expires_at=now + 300,
            )
        )
    )
    asyncio.run(
        mgr.record_invite(
            ShareInvite(
                session_id="stale",
                title="t",
                host=HOST_NODE,
                host_name="H",
                ts=now - 600,
                expires_at=now - 1,
            )
        )
    )
    ids = [i.session_id for i in mgr.live_invites()]
    assert ids == ["live"]


def test_a_guest_learns_their_own_rung_from_the_hosts_state(captured):
    """The one thing a guest cannot work out for themselves."""
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.adopt_remote(
            RemoteSession(
                id="s1",
                title="theirs",
                host_node="theirnode",
                host_name="Them",
                joined_at=time.time(),
            )
        )
        await mgr.apply_remote_state(
            "theirnode",
            {
                "id": "s1",
                "title": "theirs, renamed",
                "participants": [{"node_id": HOST_NODE, "grant": "edit"}],
            },
        )

    asyncio.run(go())
    assert mgr.joined["s1"].grant == "edit"
    assert mgr.joined["s1"].title == "theirs, renamed"


def test_remote_state_from_the_wrong_host_is_ignored(captured):
    """A trusted peer is still not the host of somebody else's session."""
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.adopt_remote(
            RemoteSession(
                id="s1",
                title="theirs",
                host_node="theirnode",
                host_name="Them",
                joined_at=time.time(),
            )
        )
        await mgr.apply_remote_state(
            "someoneelse",
            {"id": "s1", "participants": [{"node_id": HOST_NODE, "grant": "control"}]},
        )

    asyncio.run(go())
    assert mgr.joined["s1"].grant == "view"


# ---------------------------------------------------------------------------
# The trust boundary
# ---------------------------------------------------------------------------


def test_an_untrusted_peer_cannot_join(captured, monkeypatch):
    _events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        session = await mgr.start("s", "semantic")
        hub.sent.clear()
        await fabric.handle_join(
            hub,
            FakePeerSession(GUEST_NODE, trusted=False),
            FakeEnvelope(GUEST_NODE, {"sessionId": session.id}),
        )

    asyncio.run(go())
    assert not any(p.role == "guest" for p in mgr.hosting.participants)
    assert hub.types() == ["share_error"]


def test_knowing_a_session_id_is_not_membership(captured, monkeypatch):
    """A trusted friend guessing the wrong id gets the same answer as a stranger:
    no such session. There is nothing to confirm the shape of."""
    _events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        await mgr.start("s", "semantic")
        hub.sent.clear()
        await fabric.handle_join(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(GUEST_NODE, {"sessionId": "not-the-real-one"}),
        )

    asyncio.run(go())
    assert not any(p.role == "guest" for p in mgr.hosting.participants)
    assert hub.types() == ["share_error"]


def test_a_trusted_peer_with_the_right_id_joins_and_gets_the_state(
    captured, monkeypatch
):
    _events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        session = await mgr.start("s", "semantic")
        hub.sent.clear()
        await fabric.handle_join(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(GUEST_NODE, {"sessionId": session.id}),
        )

    asyncio.run(go())
    guests = [p for p in mgr.hosting.participants if p.role == "guest"]
    assert len(guests) == 1
    assert guests[0].grant == "view"
    assert "share_state" in hub.types()


def test_an_action_above_a_guests_rung_is_refused(captured, monkeypatch):
    _events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
        hub.sent.clear()
        await fabric.handle_action(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(
                GUEST_NODE,
                {"name": "terminal.exec", "params": {"command": "ls"}},
            ),
        )

    asyncio.run(go())
    assert hub.types() == ["share_error"]


def test_an_action_within_a_guests_rung_is_surfaced_to_the_host(captured, monkeypatch):
    """A read-only command from a guest on the terminal rung actuates.

    `ls` passes both gates: the ladder (the guest holds `terminal`) and the host's
    own permission engine, which auto-allows a read-only shell command. It is
    relayed to the host's tabs, because actuation lives in the browser.
    """
    events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
        await mgr.set_grant("gp", "terminal")
        hub.sent.clear()
        await fabric.handle_action(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(
                GUEST_NODE,
                {"name": "terminal.exec", "params": {"command": "ls"}},
            ),
        )

    asyncio.run(go())
    assert hub.types() == []  # no error
    assert events_named(events, "action")
    assert [e.outcome for e in mgr.audit.entries()] == ["allowed"]


def test_an_untrusted_peer_cannot_relay_signaling(captured, monkeypatch):
    events, _hub = captured
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", ShareManager())

    asyncio.run(
        fabric.handle_signal(
            _hub,
            FakePeerSession(GUEST_NODE, trusted=False),
            FakeEnvelope(GUEST_NODE, {"payload": {"sdp": "..."}}),
        )
    )
    assert not events_named(events, "signal")


def test_a_departing_peer_is_swept_out_of_the_session(captured, monkeypatch):
    """A guest has no browser socket on this node, so nothing else would ever
    notice they left — they would sit in the list holding whatever rung they had."""
    _events, _hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
        fabric._on_peer_event(
            "peer_update", {"peer": {"node_id": GUEST_NODE, "status": "disconnected"}}
        )
        # `_on_peer_event` is sync and schedules the removal; let it run.
        await asyncio.sleep(0)

    asyncio.run(go())
    assert not any(p.role == "guest" for p in mgr.hosting.participants)


# ---------------------------------------------------------------------------
# The cross-language contract
# ---------------------------------------------------------------------------


def test_the_frontend_ladder_matches_this_one():
    """The rung vocabulary exists twice — here and in `api.ts` — because a
    `<select>` has to render before any fetch resolves.

    Two copies of one vocabulary is how a gap appears, and this one fails
    *silently*: a rung the backend knows and the frontend does not simply never
    appears in the picker, and a rung the frontend sends that the backend does
    not know is 422'd at the boundary or read as `view` at the gate. Same reason
    hassault pins its physics vectors and serves `plane_order` rather than
    duplicating it.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "core"
        / "src"
        / "modules"
        / "share"
        / "api.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"GRANT_LADDER\s*=\s*\[(.*?)\]\s*as const", source, re.S)
    assert match, "GRANT_LADDER not found in api.ts"
    frontend = tuple(re.findall(r"'([a-z]+)'", match.group(1)))
    # Order matters as much as membership: the ladder *is* the comparison.
    assert frontend == GRANT_LADDER


# ---------------------------------------------------------------------------
# The workspace projection
# ---------------------------------------------------------------------------


def test_a_projection_is_forwarded_to_every_guest(captured):
    events, hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
        hub.sent.clear()
        await mgr.set_mirror({"center": {"kind": "area", "tabs": []}, "redactedCount": 2})

    asyncio.run(go())
    assert "share_mirror" in hub.types()
    assert mgr.mirror is not None


def test_a_projection_with_no_session_is_dropped_not_stored(captured):
    """A projection is only meaningful alongside its session. Keeping one after
    `stop()` is how a workspace reaches the guests of the *next* session before
    its host has published anything."""
    _events, _hub = captured
    mgr = ShareManager()
    asyncio.run(mgr.set_mirror({"center": {"kind": "area", "tabs": []}}))
    assert mgr.mirror is None


def test_stopping_forgets_the_projection(captured):
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.start("s", "semantic")
        await mgr.set_mirror({"center": {"kind": "area", "tabs": []}})
        await mgr.stop()

    asyncio.run(go())
    assert mgr.mirror is None


def test_the_backend_never_reinterprets_a_projection(captured):
    """It is held opaquely on purpose.

    The redaction happened in the only place that could do it — the host's own
    browser, which is where pane declarations live. A backend that reached into
    this to help would be a second authority on a decision that already has one.
    """
    _events, _hub = captured
    mgr = ShareManager()
    payload = {"center": {"kind": "area", "tabs": [{"instanceId": "r:abc"}]}, "odd": 1}

    async def go():
        await mgr.start("s", "semantic")
        await mgr.set_mirror(payload)

    asyncio.run(go())
    assert mgr.mirror == payload


def test_a_joining_guest_is_handed_the_current_projection(captured, monkeypatch):
    """The host's layout may not change again for minutes, so waiting for the
    next projection means joining into a blank map with no way to tell that from
    a broken one."""
    _events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        session = await mgr.start("s", "semantic")
        await mgr.set_mirror({"center": {"kind": "area", "tabs": []}})
        hub.sent.clear()
        await fabric.handle_join(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(GUEST_NODE, {"sessionId": session.id}),
        )

    asyncio.run(go())
    # Two `share_state`s is correct, not a bug: admitting the guest republishes
    # the session to *everyone* (they are a participant now), and the join reply
    # is a separate correlated message the joiner is awaiting. What matters is
    # that the projection follows the state rather than racing it.
    types = hub.types()
    assert types[-1] == "share_mirror"
    assert types.count("share_mirror") == 1
    assert "share_state" in types


def test_a_projection_from_the_wrong_host_is_ignored(captured):
    """A trusted friend is still not the host of somebody else's session.
    Accepting this on `src` alone would let any friend paint the pane."""
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.adopt_remote(
            RemoteSession(
                id="s1",
                title="theirs",
                host_node="theirnode",
                host_name="Them",
                joined_at=time.time(),
            )
        )
        await mgr.apply_remote_mirror(
            "someoneelse", {"sessionId": "s1", "frame": {"center": {}}}
        )

    asyncio.run(go())
    assert "s1" not in mgr.remote_mirrors


def test_a_projection_from_the_real_host_is_adopted_and_relayed(captured):
    events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.adopt_remote(
            RemoteSession(
                id="s1",
                title="theirs",
                host_node="theirnode",
                host_name="Them",
                joined_at=time.time(),
            )
        )
        await mgr.apply_remote_mirror(
            "theirnode", {"sessionId": "s1", "frame": {"center": {}, "redactedCount": 3}}
        )

    asyncio.run(go())
    assert mgr.remote_mirrors["s1"]["redactedCount"] == 3
    assert events_named(events, "remote_mirror")


def test_leaving_forgets_the_hosts_projection(captured):
    _events, _hub = captured
    mgr = ShareManager()

    async def go():
        await mgr.adopt_remote(
            RemoteSession(
                id="s1",
                title="theirs",
                host_node="theirnode",
                host_name="Them",
                joined_at=time.time(),
            )
        )
        await mgr.apply_remote_mirror(
            "theirnode", {"sessionId": "s1", "frame": {"center": {}}}
        )
        await mgr.drop_remote("s1")

    asyncio.run(go())
    assert mgr.remote_mirrors == {}


def test_an_untrusted_peer_cannot_publish_a_projection(captured, monkeypatch):
    _events, _hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        await mgr.adopt_remote(
            RemoteSession(
                id="s1",
                title="theirs",
                host_node=GUEST_NODE,
                host_name="Them",
                joined_at=time.time(),
            )
        )
        await fabric.handle_mirror(
            _hub,
            FakePeerSession(GUEST_NODE, trusted=False),
            FakeEnvelope(GUEST_NODE, {"sessionId": "s1", "frame": {"center": {}}}),
        )

    asyncio.run(go())
    assert mgr.remote_mirrors == {}
