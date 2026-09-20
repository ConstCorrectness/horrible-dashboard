"""How many tool-calling steps a turn gets, and how it ends when they run out.

The failure this pins: `MAX_ROUNDS` was 8, hardcoded, and a turn that reached it
returned the canned string "(stopped after too many steps)" in place of an answer.
Across recorded turns the round counts tapered off to a single turn at 7 and then
32 sat at exactly 8 — a budget being hit, not work finishing — and the user saw an
agent that quit mid-task for no stated reason.

Three things make it right, and each is a separate way to get it wrong:
the budget is a setting, discovery rounds are not charged as work, and a turn
that does run out still answers with what it gathered.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.modules.agent import orchestrator, providers as P

INFO = P.PROVIDERS["ollama"]


class ScriptedModel:
    """A `chat_stream` stand-in: a string turn is a final answer, a list of
    `(name, args)` pairs is a round of tool calls. A script that runs out keeps
    calling the same tool, which is exactly the runaway the budget guards."""

    def __init__(self, turns: list[Any], repeat: Any = None) -> None:
        self.turns = list(turns)
        self.repeat = repeat if repeat is not None else [("files.read", {"path": "a"})]
        self.seen: list[dict[str, Any]] = []

    async def __call__(
        self, client, info, endpoint, model, messages, tools, on_delta, **kw
    ):
        self.seen.append({"messages": list(messages), "tools": list(tools)})
        turn = self.turns.pop(0) if self.turns else self.repeat
        if isinstance(turn, str):
            return P.ChatResult(
                assistant_message={"role": "assistant", "content": turn},
                tool_calls=[],
                content=turn,
            )
        calls = [
            P.ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(turn)
        ]
        return P.ChatResult(
            assistant_message={"role": "assistant", "content": "", "tool_calls": []},
            tool_calls=calls,
            content="",
        )


class FakeConn:
    """Enough connection for the loop: it never reaches a browser here, because
    every call is answered by `simulate`."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


async def _noop_emit(kind: str, text: str) -> None:
    return None


def _tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "a tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No capture, no gate prompt: this is about the budget, nothing else."""

    async def _nothing(*a, **kw):
        return None

    monkeypatch.setattr(orchestrator, "_capture_context", _nothing)
    monkeypatch.setattr(orchestrator, "_finish_capture", _nothing)
    monkeypatch.setattr(orchestrator, "_begin_trajectory", lambda **kw: None)

    async def _allow(*a, **kw):
        return True

    monkeypatch.setattr(orchestrator, "_gate", _allow)


async def _simulate(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


async def _run(model: ScriptedModel, monkeypatch, **kw) -> tuple[str, list[dict]]:
    monkeypatch.setattr(P, "chat_stream", model)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "do it"}]
    answer = await orchestrator.run_agent_loop(
        FakeConn(),
        "t1",
        messages,
        [_tool("files.read")],
        INFO,
        "http://localhost:11434",
        "test-model",
        _noop_emit,
        temperature=0.0,
        simulate=_simulate,
        **kw,
    )
    return answer, messages


def _set_max_rounds(monkeypatch, value: int) -> None:
    from backend.modules.settings import routes as settings_routes

    monkeypatch.setattr(settings_routes, "get_value", lambda key, default=None: value)


def test_the_budget_is_a_setting_not_a_constant(monkeypatch) -> None:
    async def go() -> None:
        _set_max_rounds(monkeypatch, 3)
        model = ScriptedModel([])  # never stops calling tools
        await _run(model, monkeypatch)

        # Three working rounds, then the tool-less round that asks for an answer.
        assert len(model.seen) == 4
        assert model.seen[-1]["tools"] == []

    asyncio.run(go())


def test_zero_means_the_default(monkeypatch) -> None:
    async def go() -> None:
        _set_max_rounds(monkeypatch, 0)
        assert orchestrator._max_rounds() == orchestrator.MAX_ROUNDS
        _set_max_rounds(monkeypatch, -4)  # a budget of zero is a broken-looking agent
        assert orchestrator._max_rounds() == 1

    asyncio.run(go())


def test_loading_tools_is_not_charged_as_work(monkeypatch) -> None:
    """Progressive disclosure makes discovery cost rounds; a turn that loaded three
    groups had spent three of its eight before touching the task."""

    async def go() -> None:
        _set_max_rounds(monkeypatch, 2)
        meta = [("load_tools", {"groups": ["files"]})]
        model = ScriptedModel(
            [meta, meta, [("files.read", {"path": "a"})], "found it"],
        )
        answer, _ = await _run(model, monkeypatch, active_groups=set())

        assert answer == "found it"
        assert len(model.seen) == 4

    asyncio.run(go())


def test_endless_loading_still_stops(monkeypatch) -> None:
    async def go() -> None:
        _set_max_rounds(monkeypatch, 2)
        model = ScriptedModel([], repeat=[("load_tools", {"groups": ["files"]})])
        await _run(model, monkeypatch, active_groups=set())

        # The allowance, then the budget, then the final answer — never forever.
        assert len(model.seen) == orchestrator.META_ROUND_ALLOWANCE + 2 + 1

    asyncio.run(go())


def test_a_turn_that_runs_out_answers_with_what_it_has(monkeypatch) -> None:
    """The canned "(stopped after too many steps)" threw away the work: a turn that
    had read six files ended with a sentence about the machinery."""

    async def go() -> None:
        _set_max_rounds(monkeypatch, 1)
        model = ScriptedModel(
            [[("files.read", {"path": "a"})], "I read a and here is what it says"]
        )
        answer, messages = await _run(model, monkeypatch)

        assert answer == "I read a and here is what it says"
        assert "stopped after too many steps" not in answer
        # The model is told why it is answering now, and offered no tools to call.
        assert "budget of 1 tool-calling steps" in json.dumps(messages)
        assert model.seen[-1]["tools"] == []

    asyncio.run(go())


def test_a_silent_final_round_still_says_what_happened(monkeypatch) -> None:
    async def go() -> None:
        _set_max_rounds(monkeypatch, 1)
        model = ScriptedModel([[("files.read", {"path": "a"})], ""])
        answer, _ = await _run(model, monkeypatch)

        assert "used all 1 steps" in answer

    asyncio.run(go())


def test_a_failing_final_round_is_reported_not_swallowed(monkeypatch) -> None:
    async def go() -> None:
        _set_max_rounds(monkeypatch, 1)

        class Boom(ScriptedModel):
            async def __call__(self, *a, **kw):
                self.seen.append({"messages": [], "tools": list(a[5])})
                if len(self.seen) > 1:
                    raise RuntimeError("provider down")
                return await super().__call__(*a, **kw)

        model = Boom([[("files.read", {"path": "a"})]])
        answer, _ = await _run(model, monkeypatch)

        assert "stopped after 1 steps" in answer

    asyncio.run(go())
