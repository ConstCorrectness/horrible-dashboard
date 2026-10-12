"""Rate-limit retries at the provider chokepoint (`providers.chat` / `chat_stream`).

Found live: an evals sweep against NVIDIA NIM lost 8 of 15 cases to `429 Too Many
Requests`, and ordinary chat turns hit the same wall — nothing retried. These run
the real `chat`/`chat_stream` over an `httpx.MockTransport`, with the wait replaced
so nothing actually sleeps.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from backend.modules.agent import providers as P

INFO = P.PROVIDERS["lmstudio"]  # an openai-dialect provider with no key to look up
ENDPOINT = "http://test-host:1234"
MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    seen: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(P, "_retry_sleep", fake_sleep)
    return seen


def _client(responses: list[httpx.Response]) -> tuple[httpx.AsyncClient, list[int]]:
    """A client that plays `responses` in order and counts the requests."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def _ok_json(content: str = "hello") -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


def _limited(retry_after: str | None = None, status: int = 429) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(
        status, headers=headers, json={"status": status, "title": "Too Many Requests"}
    )


def _sse(*contents: str) -> httpx.Response:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": c}}]})
        for c in contents
    ]
    body = "\n\n".join([*lines, "data: [DONE]"]) + "\n\n"
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )


async def _chat(client: httpx.AsyncClient) -> P.ChatResult:
    async with client:
        return await P.chat(client, INFO, ENDPOINT, "m", MESSAGES, [])


# --- chat ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_429_then_success_returns_the_answer(waits):
    client, calls = _client([_limited(), _ok_json("hello")])
    result = await _chat(client)
    assert result.content == "hello"
    assert len(calls) == 2 and len(waits) == 1
    assert 0.5 <= waits[0] <= 1.0  # first backoff: ~1s with jitter


@pytest.mark.anyio
async def test_retry_after_seconds_is_honoured(waits):
    client, _ = _client([_limited("7"), _ok_json()])
    await _chat(client)
    assert waits == [7.0]


@pytest.mark.anyio
async def test_a_503_is_retried_too(waits):
    client, calls = _client([_limited(status=503), _ok_json()])
    await _chat(client)
    assert len(calls) == 2


@pytest.mark.anyio
async def test_retries_are_bounded_and_the_last_429_still_raises(waits):
    client, calls = _client([_limited()])
    with pytest.raises(httpx.HTTPStatusError) as err:
        await _chat(client)
    assert err.value.response.status_code == 429
    assert len(calls) == P.RETRY_ATTEMPTS + 1
    assert len(waits) == P.RETRY_ATTEMPTS
    assert sum(waits) <= P.RETRY_BUDGET_S


@pytest.mark.anyio
async def test_a_retry_after_beyond_the_budget_raises_now(waits):
    # A provider asking for two minutes is told no, immediately: an interactive
    # turn that silently waits that long reads as a hang.
    client, calls = _client([_limited("120"), _ok_json()])
    with pytest.raises(httpx.HTTPStatusError):
        await _chat(client)
    assert len(calls) == 1 and waits == []


@pytest.mark.anyio
async def test_a_client_error_is_not_retried(waits):
    client, calls = _client([httpx.Response(400, json={"error": "bad tool name"})])
    with pytest.raises(httpx.HTTPStatusError):
        await _chat(client)
    assert len(calls) == 1 and waits == []


# --- chat_stream ---------------------------------------------------------------


@pytest.mark.anyio
async def test_a_stream_refused_before_any_token_is_retried(waits):
    client, calls = _client([_limited("2"), _sse("Hel", "lo")])
    deltas: list[str] = []

    async def on_delta(reasoning: str, content: str) -> None:
        deltas.append(content)

    async with client:
        result = await P.chat_stream(
            client, INFO, ENDPOINT, "m", MESSAGES, [], on_delta
        )
    assert result.content == "Hello"
    assert "".join(deltas) == "Hello"  # emitted once, not twice
    assert len(calls) == 2 and waits == [2.0]


# --- the wrapper's rules, on a stand-in call --------------------------------------


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://test-host/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.mark.anyio
async def test_a_stream_that_already_emitted_is_never_replayed(waits):
    calls: list[int] = []

    @P._retry_rate_limits
    async def streaming(client, info, endpoint, model, messages, tools, on_delta):
        calls.append(1)
        await on_delta("", "half an answer")
        raise _status_error(503)

    async def sink(_r: str, _c: str) -> None:
        return None

    with pytest.raises(httpx.HTTPStatusError):
        await streaming(None, INFO, ENDPOINT, "m", MESSAGES, [], sink)
    assert len(calls) == 1 and waits == []


@pytest.mark.anyio
async def test_a_litellm_style_rate_limit_is_retried(waits):
    class RateLimitError(Exception):
        status_code = 429

    calls: list[int] = []

    @P._retry_rate_limits
    async def hosted(client, info, endpoint, model, messages, tools):
        calls.append(1)
        if len(calls) == 1:
            raise RateLimitError("rate limited")
        return "ok"

    assert await hosted(None, INFO, ENDPOINT, "m", MESSAGES, []) == "ok"
    assert len(calls) == 2


@pytest.mark.anyio
async def test_cancelling_during_the_wait_stops_at_once(monkeypatch):
    waiting = asyncio.Event()
    calls: list[int] = []

    async def blocking_sleep(_seconds: float) -> None:
        waiting.set()
        await asyncio.Event().wait()  # never wakes on its own

    monkeypatch.setattr(P, "_retry_sleep", blocking_sleep)

    @P._retry_rate_limits
    async def limited(client, info, endpoint, model, messages, tools):
        calls.append(1)
        raise _status_error(429)

    task = asyncio.create_task(limited(None, INFO, ENDPOINT, "m", MESSAGES, []))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1


# --- Retry-After parsing ----------------------------------------------------------


def test_retry_after_accepts_an_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=10)
    exc = _status_error(429)
    exc.response.headers["retry-after"] = format_datetime(when, usegmt=True)
    seconds = P._retry_after_s(exc)
    assert seconds is not None and 8 <= seconds <= 10


def test_retry_after_garbage_and_absence_mean_no_hint():
    exc = _status_error(429)
    assert P._retry_after_s(exc) is None
    exc.response.headers["retry-after"] = "soon-ish"
    assert P._retry_after_s(exc) is None
