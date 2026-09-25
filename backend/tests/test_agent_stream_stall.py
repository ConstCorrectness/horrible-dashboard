"""A hosted model that never answers must end the turn, and the chat must be able
to stop a turn itself. Before this, litellm's 6000 s default left the pane on its
typing indicator with no way out."""

import asyncio
from typing import Any

import pytest

from backend.modules.agent import orchestrator, providers
from backend.tests.test_agent_orchestrator import FakeConn


async def _chunks(delays: list[float]):
    for i, d in enumerate(delays):
        await asyncio.sleep(d)
        yield i


def _drain(stream) -> list[Any]:
    async def run() -> list[Any]:
        return [c async for c in stream]

    return asyncio.run(run())


def test_watch_stream_passes_a_healthy_stream_through() -> None:
    out = _drain(providers._watch_stream(_chunks([0, 0, 0]), "m", 1.0, 1.0))
    assert out == [0, 1, 2]


def test_watch_stream_raises_when_the_first_chunk_never_comes() -> None:
    with pytest.raises(providers.StreamStalled, match="sent nothing for 120s"):
        _drain(
            providers._watch_stream(_chunks([5]), "m", 0.05, 1.0, first_reported_s=120)
        )


def test_watch_stream_raises_when_a_started_stream_goes_silent() -> None:
    with pytest.raises(providers.StreamStalled, match="stopped streaming"):
        _drain(providers._watch_stream(_chunks([0, 5]), "m", 1.0, 0.05))


def test_stall_is_a_timeout_error() -> None:
    # The turn's catch-all reports it; nothing else needs to learn a new type.
    assert issubclass(providers.StreamStalled, TimeoutError)


def test_litellm_calls_carry_an_explicit_timeout(monkeypatch) -> None:
    info = providers.provider_for("openrouter")
    monkeypatch.setattr(providers, "api_key_for", lambda _info: "k")
    kwargs = providers.litellm_call_kwargs(info)
    assert kwargs["timeout"] == providers.HOSTED_FIRST_CHUNK_S


def test_cancel_stops_the_askers_turn_only(monkeypatch) -> None:
    started = asyncio.Event()

    async def hang(conn, turn_id, *a: Any, **kw: Any) -> None:
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(orchestrator, "run_agent_turn", hang)

    async def run() -> tuple[FakeConn, FakeConn]:
        owner, other = FakeConn(), FakeConn()
        ask = {"event": "ask", "data": {"turnId": "t1", "prompt": "hi"}}
        await orchestrator.handle_agent_message(owner, ask)
        await started.wait()
        task = orchestrator._turns_by_id["t1"][1]

        # Another connection knowing the id is not enough.
        cancel = {"event": "cancel", "data": {"turnId": "t1"}}
        await orchestrator.handle_agent_message(other, cancel)
        await asyncio.sleep(0)
        assert not task.done()

        await orchestrator.handle_agent_message(owner, cancel)
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.01)  # let the done notice go out
        assert "t1" not in orchestrator._turns_by_id
        return owner, other

    owner, other = asyncio.run(run())
    assert ("done", {"turnId": "t1", "agentId": "main"}) in owner.events()
    assert other.events() == []
