"""The context inspector's record: literal provider requests.

Driven through the real `providers.chat_stream` against a mock transport, inside a
real telemetry turn stamp, because every way this feature fails is invisible to a
unit test of one helper: the stamp is what decides whether anything is recorded, the
dialect is what decides the body, and the 400 → no-`stream_options` retry is what
decides which body the server accepted.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.modules.agent import providers as P
from backend.modules.interpretability import recorder, requests, store, tokenizer
from backend.modules.telemetry import turn as telemetry_turn


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch):
    async def _no_tokenizer(_repo: str) -> None:
        return None

    monkeypatch.setattr(tokenizer, "_load", _no_tokenizer)
    recorder.clear()
    requests._attempts.clear()
    P._NO_STREAM_USAGE.clear()
    yield
    recorder.clear()
    requests._attempts.clear()


def _sse(*frames: dict) -> bytes:
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames)
    return (body + "data: [DONE]\n\n").encode()


async def _noop(_reasoning: str, _content: str) -> None:
    return None


def _prompt() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are the orchestrator."},
        {"role": "system", "content": "github guide: use searchCode first."},
        {"role": "user", "content": "refactor this"},
    ]


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "files.read",
                "description": "read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def _ok(prompt_tokens: int = 120, tool_call: bool = False) -> httpx.Response:
    delta: dict[str, Any] = {"content": "done"}
    if tool_call:
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "files.read", "arguments": "{}"},
                }
            ]
        }
    return httpx.Response(
        200,
        content=_sse(
            {"choices": [{"delta": delta}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5},
            },
        ),
        headers={"content-type": "text/event-stream"},
    )


async def _round(
    client: httpx.AsyncClient,
    conn: FakeConn,
    messages: list[dict[str, Any]],
    round_no: int,
) -> P.ChatResult:
    """One loop round the way `run_agent_loop` does it: stamp, snapshot, call."""
    telemetry_turn.mark_round("t1", round_no)
    await recorder.capture_round(
        conn,
        turn_id="t1",
        agent_id="main",
        model="m",
        provider="lmstudio",
        messages=messages,
        tools=_tools(),
        round_no=round_no,
        tools_selected=1,
        tool_budget=44,
        active_groups=None,
    )
    return await P.chat_stream(
        client, P.provider_for("lmstudio"), "http://x", "m", messages, _tools(), _noop
    )


# ── normalization keeps its seams ────────────────────────────────────────────


def test_mapped_normalize_joins_the_system_tier_and_records_where_each_part_sits():
    messages = [
        {"role": "system", "content": "AAA"},
        {"role": "system", "content": ""},  # dropped: empty
        {"role": "system", "content": "BB"},
        {"role": "user", "content": "q"},
        {"role": "system", "content": "nudge"},
    ]
    sent, spans = P.normalize_system_messages_mapped(messages)

    assert [m["role"] for m in sent] == ["system", "user", "user"]
    assert sent[0]["content"] == "AAA\n\nBB"
    # Each part's range, with the joining blank line belonging to neither.
    assert spans[0] == [(0, 0, 3), (2, 5, 7)]
    assert sent[0]["content"][5:7] == "BB"
    # The nudge kept its position and its source index.
    assert spans[2] == [(4, 0, 5)]
    # The unmapped wrapper is the same function.
    assert P.normalize_system_messages(messages) == sent


# ── capture ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_nothing_is_recorded_outside_an_agent_turn():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: _ok())) as c:
        await P.chat_stream(
            c, P.provider_for("lmstudio"), "http://x", "m", _prompt(), [], _noop
        )
    assert store.list_requests("t1") == []


@pytest.mark.anyio
async def test_records_the_literal_request_with_provenance_and_usage():
    conn = FakeConn()
    token = telemetry_turn.enter("t1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: _ok(321))
        ) as client:
            await _round(client, conn, _prompt(), 0)
    finally:
        telemetry_turn.leave(token)

    [row] = store.list_requests("t1")
    detail = requests.get(row["id"])
    assert detail is not None
    # What the model got: one system message, not two.
    assert [m.role for m in detail.messages] == ["system", "user"]
    system = detail.messages[0]
    assert [s.kind for s in system.segments] == ["system", "guides"]
    assert [s.label for s in system.segments] == ["System prompt", "Tool guides"]
    text = system.body["content"]
    guide = system.segments[1]
    assert text[guide.start : guide.end] == "github guide: use searchCode first."
    # The body params are what the dialect sent, stream_options included.
    assert detail.params["stream"] is True
    assert detail.params["stream_options"] == {"include_usage": True}
    assert detail.tools[0]["function"]["name"] == "files.read"
    # The provider's count, beside the estimate.
    assert detail.promptTokens == 321
    assert detail.estTokens > 0 and detail.exact is False
    assert detail.status == "ok" and detail.response["content"] == "done"
    # Pushed twice on the interpretability channel: out, then back.
    frames = [f for f in conn.sent if f["event"] == "request"]
    assert [f["data"]["status"] for f in frames] == ["pending", "ok"]


@pytest.mark.anyio
async def test_a_second_call_in_one_round_is_its_own_attempt():
    """The forced-tool retry: same round, nudge appended as `system`, sent as `user`."""
    conn = FakeConn()
    token = telemetry_turn.enter("t1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: _ok())
        ) as client:
            first = await _round(client, conn, _prompt(), 0)
            retry = [
                *_prompt(),
                first.assistant_message,
                {"role": "system", "content": "Call the tool now."},
            ]
            await P.chat_stream(
                client, P.provider_for("lmstudio"), "http://x", "m", retry, [], _noop
            )
    finally:
        telemetry_turn.leave(token)

    rows = store.list_requests("t1")
    assert [(r["round"], r["attempt"]) for r in rows] == [(0, 0), (0, 1)]
    nudge = requests.get(rows[1]["id"]).messages[-1]
    assert nudge.role == "user" and nudge.fromRole == "system"
    assert nudge.segments[0].kind == "nudge"


@pytest.mark.anyio
async def test_history_is_stored_once_across_rounds():
    conn = FakeConn()
    token = telemetry_turn.enter("t1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: _ok(tool_call=True))
        ) as client:
            messages = _prompt()
            first = await _round(client, conn, messages, 0)
            messages = [
                *messages,
                first.assistant_message,
                {"role": "tool", "content": '"file body"', "tool_call_id": "call_1"},
            ]
            await _round(client, conn, messages, 1)
    finally:
        telemetry_turn.leave(token)

    r0, r1 = store.list_requests("t1")
    h0 = json.loads(r0["message_hashes"])
    h1 = json.loads(r1["message_hashes"])
    assert h1[: len(h0)] == h0  # carried over, byte-identical
    with store.get_db_conn() as conn_db:
        bodies = conn_db.execute("SELECT COUNT(*) AS n FROM ctx_bodies").fetchone()["n"]
    # 2 prompt messages + assistant + tool result + one tool list. Not 2 + 4 + 1.
    assert bodies == 5


@pytest.mark.anyio
async def test_the_accepted_payload_is_the_one_on_record():
    """A server refusing `stream_options` is retried without it; the request on
    record must say what was accepted, not what was refused."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "stream_options" in json.loads(request.content):
            return httpx.Response(400, json={"error": "unknown field"})
        return _ok()

    token = telemetry_turn.enter("t1")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _round(client, FakeConn(), _prompt(), 0)
    finally:
        telemetry_turn.leave(token)

    [row] = store.list_requests("t1")
    assert "stream_options" not in requests.get(row["id"]).params


@pytest.mark.anyio
async def test_a_failed_call_is_recorded_and_the_error_still_raises():
    token = telemetry_turn.enter("t1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(500, json={"error": "boom"})
            )
        ) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await _round(client, FakeConn(), _prompt(), 0)
    finally:
        telemetry_turn.leave(token)

    [row] = store.list_requests("t1")
    assert row["status"] == "error" and "500" in row["error"]


def test_credentials_never_reach_params():
    pending = requests._Pending(
        id="x",
        turn_id="t",
        round=0,
        attempt=0,
        ts=0,
        started=0,
        row={},
        sent=[],
        tools=[],
    )
    token = requests._current.set(pending)
    try:
        requests.note_payload(
            {"model": "m", "messages": [], "api_key": "sk-secret", "timeout": 30}
        )
    finally:
        requests._current.reset(token)
    assert pending.params == {"model": "m", "timeout": 30}


@pytest.mark.anyio
async def test_prune_collects_bodies_no_request_references():
    token = telemetry_turn.enter("t1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: _ok())
        ) as client:
            await _round(client, FakeConn(), _prompt(), 0)
    finally:
        telemetry_turn.leave(token)

    store.prune(keep=0)
    assert store.list_requests("t1") == []
    with store.get_db_conn() as conn_db:
        assert (
            conn_db.execute("SELECT COUNT(*) AS n FROM ctx_bodies").fetchone()["n"] == 0
        )


# ── the HTTP surface (the real response, not the model) ─────────────────────


@pytest.fixture
def client() -> TestClient:
    from backend.app import app

    return TestClient(app)


def test_routes_serve_every_field(client: TestClient):
    import anyio

    async def run() -> None:
        token = telemetry_turn.enter("t1")
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: _ok(77))
            ) as c:
                await _round(c, FakeConn(), _prompt(), 0)
        finally:
            telemetry_turn.leave(token)

    anyio.run(run)

    listed = client.get("/api/interpretability/turns/t1/requests").json()["requests"]
    assert len(listed) == 1
    summary = listed[0]
    for key in ("attempt", "estTokens", "promptTokens", "reconstructed", "toolCalls"):
        assert key in summary
    assert summary["promptTokens"] == 77 and summary["reconstructed"] is False
    assert summary["responsePreview"] == "done" and summary["toolNames"] == []

    body = client.get(f"/api/interpretability/requests/{summary['id']}").json()
    assert body["messages"][0]["segments"][1]["label"] == "Tool guides"
    assert body["params"]["stream"] is True
    assert body["response"]["content"] == "done"
    assert client.get("/api/interpretability/requests/nope").status_code == 404


def test_a_turn_from_before_capture_is_reconstructed_and_says_so(client: TestClient):
    import anyio

    async def snapshot_only() -> None:
        conn = FakeConn()
        await recorder.capture_round(
            conn,
            turn_id="old",
            agent_id="main",
            model="m",
            provider="ollama",
            messages=_prompt(),
            tools=_tools(),
            round_no=0,
            tools_selected=1,
            tool_budget=44,
            active_groups=None,
        )

    anyio.run(snapshot_only)

    [summary] = client.get("/api/interpretability/turns/old/requests").json()[
        "requests"
    ]
    assert summary["reconstructed"] is True
    body = client.get(f"/api/interpretability/requests/{summary['id']}").json()
    assert body["reconstructed"] is True and body["notes"]
    # Flattened the way the wire would have.
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert [s["kind"] for s in body["messages"][0]["segments"]] == ["system", "guides"]
