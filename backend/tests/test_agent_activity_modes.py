"""The chat's activity timeline and its Plan / Agent / Auto modes.

`tool_start`/`tool_end` frame every call; the `ask` frame's `mode` decides how the
turn is gated; plan mode hides what acts and ends on `submit_plan`; `ask_user` and
`update_todos` talk to the chat without acting on anything.
"""

import asyncio
import json
from typing import Any

import httpx

from backend.modules.agent import orchestrator, permission_store, permissions, roster
from backend.modules.agent.permissions import Mode
from backend.tests.test_agent_orchestrator import (
    _DANGER_MANIFEST,
    FakeConn,
    _calls_danger_then_answers,
    _configure,
    _mock_ollama,
)


def _mode(monkeypatch, mode: Mode) -> None:
    monkeypatch.setattr(permission_store, "load_mode", lambda: mode)
    monkeypatch.setattr(permission_store, "load_rules", lambda: permissions.RuleSet())


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": args}}


def _scripted(*rounds: list[dict[str, Any]] | str, seen: list[dict[str, Any]]):
    """A model that plays `rounds` in order: a list is that round's tool calls, a
    string is the final answer. Every request body lands in `seen`."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        step = rounds[min(len(seen), len(rounds)) - 1]
        if isinstance(step, str):
            message: dict[str, Any] = {"role": "assistant", "content": step}
        else:
            message = {"role": "assistant", "content": "", "tool_calls": step}
        return httpx.Response(200, json={"message": message})

    return handler


def _offered(body: dict[str, Any]) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


class AskingConn(FakeConn):
    """A FakeConn with a person behind it: answers each `question` with `reply`."""

    def __init__(self, reply: dict[str, Any], **kw: Any) -> None:
        super().__init__(**kw)
        self.pending_questions: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.reply = reply

    async def send_json(self, data: dict[str, Any]) -> None:
        await super().send_json(data)
        if data.get("event") == "question":
            await orchestrator.handle_agent_message(
                self,
                {
                    "event": "question_response",
                    "data": {"questionId": data["data"]["questionId"], **self.reply},
                },
            )


def test_every_call_is_framed_by_tool_start_and_tool_end(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    _mock_ollama(
        monkeypatch,
        _scripted(
            [_call("show", {"target": "Clock"}), _call("list_tool_groups", {})],
            "Shown.",
            seen=seen,
        ),
    )
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "show the clock"))
    events = [(ev, d) for ev, d in conn.events() if ev in ("tool_start", "tool_end")]

    assert [ev for ev, _ in events] == [
        "tool_start",
        "tool_end",
        "tool_start",
        "tool_end",
    ]
    start, end = events[0][1], events[1][1]
    assert start["name"] == "show" and start["args"] == {"target": "Clock"}
    assert start["turnId"] == "t" and end["callId"] == start["callId"]
    assert end["ok"] is True and end["denied"] is False
    assert isinstance(end["durationMs"], int)
    # The meta tool is framed too — the browser relay never saw those.
    assert events[2][1]["name"] == "list_tool_groups"
    assert "groups" in events[3][1]["preview"]


def test_a_denied_call_is_marked_denied(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    handler, _ = _calls_danger_then_answers()
    _mock_ollama(monkeypatch, handler)
    conn = FakeConn(agent_tools=_DANGER_MANIFEST, approval={"decision": "deny"})
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "go"))

    prompt = next(d for ev, d in conn.events() if ev == "approval_request")
    start = next(d for ev, d in conn.events() if ev == "tool_start")
    # The approval names the row it is for, and what the call would do.
    assert prompt["callId"] == start["callId"]
    assert prompt["args"] == {"path": "/x"}
    end = next(d for ev, d in conn.events() if ev == "tool_end")
    assert end["ok"] is False and end["denied"] is True


def test_plan_mode_from_the_chat_hides_what_acts(monkeypatch) -> None:
    # The global setting says Agent; the chat's picker says Plan, and wins.
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    _mock_ollama(monkeypatch, _scripted("Here is what I found.", seen=seen))
    conn = FakeConn(agent_tools=_DANGER_MANIFEST)
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "plan a danger", mode=Mode.PLAN))
    body = seen[0]
    offered = _offered(body)
    assert "submit_plan" in offered and "ask_user" in offered
    assert "danger.do" not in offered
    assert "PLAN MODE" in body["messages"][0]["content"]


def test_agent_mode_offers_no_submit_plan(monkeypatch) -> None:
    _mode(monkeypatch, Mode.PLAN)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    _mock_ollama(monkeypatch, _scripted("ok", seen=seen))
    asyncio.run(
        orchestrator.run_agent_turn(FakeConn(), "t", "hi", mode=Mode.AUTONOMOUS)
    )
    assert "submit_plan" not in _offered(seen[0])
    assert "update_todos" in _offered(seen[0])
    assert "PLAN MODE" not in seen[0]["messages"][0]["content"]


def test_a_desktop_verb_is_refused_in_plan_mode_only(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)

    class Call:
        id = "0"
        name = "desktop.set_theme"
        arguments = {"id": "dark"}

    conn = FakeConn()
    assert asyncio.run(orchestrator._gate(conn, "t", Call(), Mode.PLAN)) is False
    assert asyncio.run(orchestrator._gate(conn, "t", Call(), Mode.DEFAULT)) is True


def test_force_mode_still_beats_the_chat(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    conn = FakeConn()
    conn.force_mode = Mode.PLAN  # type: ignore[attr-defined]
    assert orchestrator._effective_mode(conn, Mode.AUTONOMOUS) is Mode.PLAN
    assert orchestrator._effective_mode(FakeConn(), Mode.AUTONOMOUS) is Mode.AUTONOMOUS
    assert orchestrator._effective_mode(FakeConn(), None) is Mode.DEFAULT


def test_the_ask_frame_carries_mode(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_turn(conn, turn_id, prompt, history, context, **kw):
        seen.update(kw)

    monkeypatch.setattr(orchestrator, "run_agent_turn", fake_turn)

    async def go(mode: Any) -> None:
        await orchestrator.handle_agent_message(
            FakeConn(),
            {"event": "ask", "data": {"turnId": "x", "prompt": "p", "mode": mode}},
        )
        await asyncio.sleep(0)

    asyncio.run(go("plan"))
    assert seen["mode"] is Mode.PLAN
    asyncio.run(go("nonsense"))
    assert seen["mode"] is None


def test_submit_plan_ends_the_turn_with_the_plan(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    plan = "# Plan\n\n1. Do the thing."
    _mock_ollama(
        monkeypatch,
        _scripted([_call("submit_plan", {"plan": plan})], "never asked", seen=seen),
    )
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "plan it", mode=Mode.PLAN))

    assert len(seen) == 1  # no round after the plan
    events = conn.events()
    assert [d["text"] for ev, d in events if ev == "plan"] == [plan]
    assert [d["text"] for ev, d in events if ev == "answer"] == [plan]


def test_ask_user_waits_for_the_answer(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    question = {
        "question": "Which layout?",
        "header": "Layout",
        "options": [{"label": "Grid", "description": "tiles"}, {"label": "List"}],
    }
    _mock_ollama(
        monkeypatch,
        _scripted(
            [_call("ask_user", {"questions": [question]})], "Grid it is.", seen=seen
        ),
    )
    conn = AskingConn({"answers": {"Which layout?": "Grid"}})
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "make a page"))

    asked = next(d for ev, d in conn.events() if ev == "question")
    assert asked["questions"][0]["options"][1] == {"label": "List", "description": ""}
    tool_msg = next(m for m in seen[1]["messages"] if m.get("role") == "tool")
    assert json.loads(tool_msg["content"]) == {"answers": {"Which layout?": "Grid"}}
    assert not conn.pending_questions


def test_ask_user_dismissed_tells_the_model_to_decide(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    question = {"question": "A or B?", "options": [{"label": "A"}, {"label": "B"}]}
    _mock_ollama(
        monkeypatch,
        _scripted([_call("ask_user", {"questions": [question]})], "A.", seen=seen),
    )
    asyncio.run(
        orchestrator.run_agent_turn(AskingConn({"dismissed": True}), "t", "pick")
    )
    tool_msg = next(m for m in seen[1]["messages"] if m.get("role") == "tool")
    assert json.loads(tool_msg["content"])["dismissed"] is True


def test_ask_user_with_nobody_to_ask_does_not_wait(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    question = {"question": "A or B?", "options": ["A", "B"]}
    _mock_ollama(
        monkeypatch,
        _scripted([_call("ask_user", {"questions": [question]})], "A.", seen=seen),
    )
    # A plain FakeConn has no `pending_questions` — an eval, a replay, a peer.
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "pick"))
    end = next(d for ev, d in conn.events() if ev == "tool_end")
    assert end["ok"] is False and "no one to ask" in end["preview"]


def test_update_todos_reaches_the_chat_cleaned(monkeypatch) -> None:
    _mode(monkeypatch, Mode.DEFAULT)
    _configure(monkeypatch)
    seen: list[dict[str, Any]] = []
    items = json.dumps(
        [
            {"content": "Read", "status": "completed"},
            {"content": "Write", "status": "doing"},
            {"content": ""},
        ]
    )
    _mock_ollama(
        monkeypatch,
        _scripted([_call("update_todos", {"items": items})], "ok", seen=seen),
    )
    conn = FakeConn()
    asyncio.run(orchestrator.run_agent_turn(conn, "t", "do the steps"))
    todos = next(d for ev, d in conn.events() if ev == "todos")
    assert todos["items"] == [
        {"content": "Read", "status": "completed"},
        {"content": "Write", "status": "pending"},
    ]


def test_a_delegate_gets_no_interaction_tools() -> None:
    tools = orchestrator._core_tools(roster.get_agent("main"))
    shaped = orchestrator._shape_for_turn(FakeConn(), tools, Mode.PLAN, delegated=True)
    names = {t["function"]["name"] for t in shaped}
    assert not names & orchestrator.INTERACTION_TOOL_NAMES
