"""A frontend tool that decides its gate per call (`AgentToolDecl.gate`).

The browser's click is the case: the backend sees `browser.click(ref 12)`, and only
the page knows whether ref 12 is a link or a form's submit button. The gate asks
the frontend first (`GATE_PROBE`) and uses its answer."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.modules.agent import orchestrator, permission_store, permissions
from backend.modules.agent.permissions import Mode
from backend.tests.test_agent_orchestrator import FakeConn

CLICK = {
    "name": "browser.click",
    "description": "click",
    "sideEffect": True,
    "specifierTemplate": "ref {ref}",
    "gated": True,
    "kind": "agentTool",
}


class Call:
    id = "c1"
    name = "browser.click"
    arguments = {"ref": 12}


def _setup(
    monkeypatch, *, allow=(), ask=(), mode=Mode.DEFAULT
) -> list[tuple[str, str]]:
    added: list[tuple[str, str]] = []
    monkeypatch.setattr(permission_store, "load_mode", lambda: mode)
    monkeypatch.setattr(
        permission_store,
        "load_rules",
        lambda: permissions.RuleSet.from_strings(allow=list(allow), ask=list(ask)),
    )
    monkeypatch.setattr(
        permission_store, "add_rule", lambda kind, rule: added.append((kind, rule))
    )
    return added


def _conn(probe: Any, approval: dict[str, Any] | None = None) -> FakeConn:
    return FakeConn(
        tool_results={orchestrator.GATE_PROBE: probe},
        agent_tools=[CLICK],
        approval=approval,
    )


def _approvals(conn: FakeConn) -> list[dict[str, Any]]:
    return [d for e, d in conn.events() if e == "approval_request"]


def test_a_link_click_runs_without_a_prompt(monkeypatch) -> None:
    _setup(monkeypatch)
    conn = _conn({"sideEffect": False, "specifier": 'navigate:example.com "Docs"'})
    assert asyncio.run(orchestrator._gate(conn, "t", Call())) is True
    # It asked the page what ref 12 is, with the call's own arguments.
    [probe] = [d for e, d in conn.events() if e == "tool_call"]
    assert probe["name"] == orchestrator.GATE_PROBE
    assert probe["args"] == {"tool": "browser.click", "args": {"ref": 12}}
    assert _approvals(conn) == []


def test_a_form_submission_asks_and_remembers_the_site(monkeypatch) -> None:
    added = _setup(monkeypatch)
    conn = _conn(
        {
            "sideEffect": True,
            "specifier": 'submit:github.com "Create repository"',
            "rule": "browser.click(submit:github.com *)",
        },
        approval={"decision": "allow_always"},
    )
    assert asyncio.run(orchestrator._gate(conn, "t", Call())) is True
    [prompt] = _approvals(conn)
    assert prompt["specifier"] == 'submit:github.com "Create repository"'
    # The card offers the tool's broader, per-site rule, and that is what persists.
    assert prompt["rule"] == "browser.click(submit:github.com *)"
    assert added == [("allow", "browser.click(submit:github.com *)")]


def test_the_remembered_rule_covers_the_next_submission_on_that_site(
    monkeypatch,
) -> None:
    _setup(monkeypatch, allow=["browser.click(submit:github.com *)"])
    same_site = _conn({"sideEffect": True, "specifier": 'submit:github.com "Star"'})
    assert asyncio.run(orchestrator._gate(same_site, "t", Call())) is True
    assert _approvals(same_site) == []
    # Another site still asks (and, with nobody answering here, is denied).
    other = _conn({"sideEffect": True, "specifier": 'submit:gitlab.com "Star"'})
    other.approval = {"decision": "deny"}
    assert asyncio.run(orchestrator._gate(other, "t", Call())) is False
    assert len(_approvals(other)) == 1


def test_an_ask_rule_still_catches_a_call_the_tool_calls_harmless(monkeypatch) -> None:
    _setup(monkeypatch, ask=["browser.click(action:*)"])
    conn = _conn(
        {"sideEffect": False, "specifier": 'action:example.com "Like"'},
        approval={"decision": "deny"},
    )
    assert asyncio.run(orchestrator._gate(conn, "t", Call())) is False
    assert len(_approvals(conn)) == 1


def test_a_failed_probe_falls_back_to_the_static_gate(monkeypatch) -> None:
    # The frontend couldn't describe the ref (no answer it can use): the tool's
    # static sideEffect applies, so the call asks, as it did before probes existed.
    _setup(monkeypatch)
    conn = _conn({"error": "page did not answer"}, approval={"decision": "allow_once"})
    assert asyncio.run(orchestrator._gate(conn, "t", Call())) is True
    [prompt] = _approvals(conn)
    assert prompt["specifier"] == "ref 12"


def test_a_simulated_run_does_not_probe(monkeypatch) -> None:
    _setup(monkeypatch, mode=Mode.AUTONOMOUS)
    conn = _conn({"sideEffect": False})
    assert asyncio.run(orchestrator._gate(conn, "t", Call(), probe=False)) is True
    assert [e for e, _ in conn.events() if e == "tool_call"] == []
