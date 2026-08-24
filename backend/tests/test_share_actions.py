"""Phase 5: what a guest may actually do, and what is written down about it.

The tests that matter here are the ones where a gap would be **invisible**: an
action that passes the ladder and then quietly skips the host's own rules would
look identical, from the outside, to one that was properly allowed. So several of
these assert on the tool name the action maps onto, not just on the outcome.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.agent import permissions as perms
from backend.modules.share import actions, fabric
from backend.modules.share.audit import AuditLog
from backend.modules.share.session import ShareManager
from backend.tests.test_share_session import (  # noqa: F401 -- fixtures
    GUEST_NODE,
    FakeEnvelope,
    FakePeerSession,
    captured,
    events_named,
)


@pytest.fixture
def guest(captured, monkeypatch):  # noqa: F811
    """A live session with one guest, at a rung the test picks."""
    events, hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    def setup(grant: str = "control"):
        async def go():
            await mgr.start("s", "semantic")
            await mgr.add_participant(person_id="gp", node_id=GUEST_NODE, name="G")
            await mgr.set_grant("gp", grant)
            hub.sent.clear()

        asyncio.run(go())
        return mgr

    return setup, events, hub


def act(hub, name: str, params: dict | None = None) -> None:
    asyncio.run(
        fabric.handle_action(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(GUEST_NODE, {"name": name, "params": params or {}}),
        )
    )


# ---------------------------------------------------------------------------
# The registry maps onto tool names that actually exist
# ---------------------------------------------------------------------------


def test_the_terminal_action_maps_onto_the_real_shell_tool() -> None:
    """`terminal.exec` is in `SHELL_TOOLS`, and that membership is what arms the
    circuit breakers and selects shell-aware rule matching. A share-local spelling
    like `terminal.run` would pass the ladder, match no host rule, skip the
    breakers, and read as "allowed" -- a hole with a plausible name on it."""
    spec = actions.spec("terminal.exec")
    assert spec is not None
    assert spec.tool in perms.SHELL_TOOLS


def test_the_rm_rf_breaker_still_fires_for_a_guest() -> None:
    # The proof that the mapping above is real rather than nominal.
    decision, specifier = actions.permission_decision(
        "terminal.exec", {"command": "rm -rf /"}
    )
    assert decision == "ask"
    assert specifier == "rm -rf /"


def test_a_read_only_command_is_allowed_without_a_prompt() -> None:
    assert actions.permission_decision("terminal.exec", {"command": "ls"})[0] == "allow"


def test_a_command_with_no_specifier_is_not_waved_through() -> None:
    # A missing command must not read as "nothing dangerous here".
    assert actions.permission_decision("terminal.exec", {})[0] == "ask"


# ---------------------------------------------------------------------------
# Both gates, in order
# ---------------------------------------------------------------------------


def test_an_unknown_action_is_refused_rather_than_falling_through(guest) -> None:
    setup, _events, hub = guest
    mgr = setup("control")  # the highest rung: still not enough for an unknown name
    act(hub, "terminal.sudo_everything")
    assert hub.types() == ["share_error"]
    assert [e.outcome for e in mgr.audit.entries()] == ["denied"]
    assert mgr.audit.entries()[0].reason == "unknown action"


def test_the_rung_comes_from_the_registry_not_from_the_guest(guest) -> None:
    """The `needs` field on the wire is advisory and ignored.

    Letting the caller nominate the permission its own action requires is letting
    the caller pick its own lock -- a guest on `view` could claim its terminal
    command needs `view` and sail through the ladder.
    """
    setup, _events, hub = guest
    mgr = setup("view")
    asyncio.run(
        fabric.handle_action(
            hub,
            FakePeerSession(GUEST_NODE, trusted=True),
            FakeEnvelope(
                GUEST_NODE,
                {
                    "name": "terminal.exec",
                    "needs": "view",  # the lie
                    "params": {"command": "ls"},
                },
            ),
        )
    )
    assert hub.types() == ["share_error"]
    entry = mgr.audit.entries()[0]
    assert entry.outcome == "denied"
    assert entry.needs == "terminal"


def test_the_hosts_own_rules_can_refuse_what_the_ladder_allowed(guest, monkeypatch):
    """The second gate is the one that matters: the guest is on the right rung and
    is still refused, because the host's own agent rules say so."""
    setup, events, hub = guest
    mgr = setup("terminal")
    monkeypatch.setattr(
        actions, "permission_decision", lambda name, params: ("deny", "anything")
    )
    act(hub, "terminal.exec", {"command": "ls"})

    assert hub.types() == ["share_error"]
    assert not events_named(events, "action")  # nothing actuated
    assert mgr.audit.entries()[0].outcome == "denied"


def test_an_ask_decision_stops_rather_than_actuating(guest, monkeypatch) -> None:
    """`ask` means the host's rules want a human. Running it anyway is the exact
    gap the two-gate design exists to prevent, so it stops -- and is recorded as
    `asked`, not `denied`, because the policy did not refuse it."""
    setup, events, hub = guest
    mgr = setup("terminal")
    monkeypatch.setattr(
        actions, "permission_decision", lambda name, params: ("ask", "rm -rf /")
    )
    act(hub, "terminal.exec", {"command": "rm -rf /"})

    assert not events_named(events, "action")
    entry = mgr.audit.entries()[0]
    assert entry.outcome == "asked"
    assert "approve" in entry.reason


def test_the_agent_rung_still_obeys_the_nodes_remote_agent_setting(guest) -> None:
    """A session grant is not authority either.

    `agent.ask` delegates to the admission rule `agent.ask_peer` already passes,
    so a guest holding the top-but-one rung on a node whose owner has not enabled
    remote agent access gets nothing.
    """
    setup, events, hub = guest
    mgr = setup("agent")
    act(hub, "agent.ask", {"prompt": "what is open?"})

    assert hub.types() == ["share_error"]
    assert not events_named(events, "action")
    assert "remote agent" in mgr.audit.entries()[0].reason


def test_the_agent_rung_works_once_the_node_allows_remote_agents(guest, monkeypatch):
    setup, events, hub = guest
    setup("agent")
    monkeypatch.setattr(
        "backend.modules.settings.routes.get_value",
        lambda key, default=None: (
            True if key == "network.allowRemoteAgent" else default
        ),
    )
    act(hub, "agent.ask", {"prompt": "what is open?"})

    assert hub.types() == []
    assert events_named(events, "action")


# ---------------------------------------------------------------------------
# The audit log
# ---------------------------------------------------------------------------


def test_denials_are_recorded_and_are_the_interesting_half(guest) -> None:
    setup, _events, hub = guest
    mgr = setup("view")
    act(hub, "terminal.exec", {"command": "ls"})
    act(hub, "control.openPane", {"id": "settings.home"})

    outcomes = [(e.action, e.outcome) for e in mgr.audit.entries()]
    assert outcomes == [("terminal.exec", "denied"), ("control.openPane", "denied")]


def test_cursor_movement_is_actuated_but_not_logged(guest) -> None:
    """Otherwise the log is nothing but cursor rows and the terminal command that
    matters scrolls off the top."""
    setup, events, hub = guest
    mgr = setup("cursor")
    for _ in range(5):
        act(hub, "cursor.move", {"x": 1, "y": 2})

    assert len(events_named(events, "action")) == 5
    assert len(mgr.audit.entries()) == 0


def test_the_log_is_bounded() -> None:
    log = AuditLog(limit=3)
    for i in range(10):
        log.record(
            node_id="n", name="G", action=f"a{i}", needs="view", outcome="allowed"
        )
    assert len(log) == 3
    assert [e.action for e in log.entries()] == ["a7", "a8", "a9"]


def test_stopping_a_session_clears_the_log(captured, monkeypatch) -> None:  # noqa: F811
    """The log belongs to the session. Carrying it into the next one would
    attribute a stranger's actions to whoever is in the room then."""
    _events, _hub = captured
    mgr = ShareManager()
    monkeypatch.setattr("backend.modules.share.fabric.share_manager", mgr)

    async def go():
        await mgr.start("s", "semantic")
        mgr.audit.record(
            node_id="n",
            name="G",
            action="terminal.exec",
            needs="terminal",
            outcome="allowed",
        )
        await mgr.stop()

    asyncio.run(go())
    assert len(mgr.audit) == 0


def test_the_audit_log_is_broadcast_to_the_host_only(guest) -> None:
    """It goes out on the local `/ws` channel and never to a guest: one guest
    reading it would learn what every other guest did."""
    setup, events, hub = guest
    setup("view")
    act(hub, "terminal.exec", {"command": "ls"})

    assert events_named(events, "audit")
    # `_tell_guests` is the peer path; nothing audit-shaped may go out over it.
    assert all("audit" not in str(data) for _n, _t, data in hub.sent)
