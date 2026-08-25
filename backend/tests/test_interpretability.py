"""The interpretability module: block classification, token accounting, the turn
ring, the routes, and the guarantee that capture can never break a turn.

No network: the tokenizer is forced into its estimate path so tests never depend on
a Hugging Face fetch (and so the estimate path itself stays covered — it's what
users on a gated/unknown model actually get).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.modules.interpretability import recorder, tokenizer
from backend.modules.interpretability.tokenizer import Counter


class FakeConn:
    """Stands in for a WsConnection: records what the recorder pushes."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(data)


@pytest.fixture(autouse=True)
def clean():
    recorder.clear()
    tokenizer.reset_cache()
    yield
    recorder.clear()
    tokenizer.reset_cache()


@pytest.fixture
def estimating(monkeypatch: pytest.MonkeyPatch):
    """Force the no-tokenizer path: counts are estimates, `exact` is False."""

    async def _no_tokenizer(_repo: str) -> None:
        return None

    monkeypatch.setattr(tokenizer, "_load", _no_tokenizer)


def _prompt() -> list[dict[str, Any]]:
    """The assembly order run_agent_turn produces."""
    return [
        {"role": "system", "content": "You are the orchestrator."},
        {"role": "system", "content": "github guide: use searchCode first."},
        {"role": "user", "content": "an earlier question"},
        {"role": "assistant", "content": "an earlier answer"},
        {"role": "system", "content": '"a.py"\n<<<BUFFER\nprint(1)\nBUFFER>>>'},
        {"role": "user", "content": "refactor this"},
    ]


def _tools(n: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"github.tool{i}",
                "description": "does a thing",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for i in range(n)
    ]


async def _capture(conn: FakeConn, **over: Any) -> None:
    kwargs: dict[str, Any] = dict(
        turn_id="t1",
        agent_id="main",
        model="gemma4:e2b",
        provider="ollama",
        messages=_prompt(),
        tools=_tools(),
        round_no=0,
        tools_selected=2,
        tool_budget=44,
        active_groups={"github"},
        context_size=8192,
        temperature=0.6,
        top_p=0.9,
        max_tokens=None,
    )
    kwargs.update(over)
    await recorder.capture_round(conn, **kwargs)


@pytest.mark.anyio
async def test_classifies_each_part_of_the_assembled_prompt(estimating):
    """system / guides / history / editor / user must be told apart — three of them
    are `role: system` and only order plus the buffer marker separate them."""
    conn = FakeConn()
    await _capture(conn)
    [turn] = recorder.recent_turns()
    kinds = [b.kind for b in turn.rounds[0].blocks]
    assert kinds == ["system", "guides", "history", "history", "editor", "user"]


@pytest.mark.anyio
async def test_later_rounds_keep_the_prompt_classification_stable(estimating):
    """The loop appends assistant/tool messages as it runs. The originally assembled
    prompt must not get relabelled as those arrive — the boundary is pinned at
    round 0."""
    conn = FakeConn()
    await _capture(conn)
    grown = _prompt() + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "rows"},
    ]
    await _capture(conn, messages=grown, round_no=1)

    [turn] = recorder.recent_turns()
    assert [b.kind for b in turn.rounds[1].blocks] == [
        "system",
        "guides",
        "history",
        "history",
        "editor",
        "user",
        "assistant",
        "tool_result",
    ]


@pytest.mark.anyio
async def test_reports_tool_budget_truncation(estimating):
    """Truncation is currently invisible outside a log line; the snapshot is what
    makes it surfaceable."""
    conn = FakeConn()
    await _capture(conn, tools_selected=50, tool_budget=44)
    round0 = recorder.recent_turns()[0].rounds[0]
    assert round0.toolsTruncated
    assert round0.toolsSelected == 50 and round0.toolBudget == 44


@pytest.mark.anyio
async def test_does_not_flag_truncation_within_budget(estimating):
    conn = FakeConn()
    await _capture(conn, tools_selected=2, tool_budget=44)
    assert not recorder.recent_turns()[0].rounds[0].toolsTruncated


@pytest.mark.anyio
async def test_totals_are_messages_plus_tools(estimating):
    conn = FakeConn()
    await _capture(conn)
    r = recorder.recent_turns()[0].rounds[0]
    assert r.messageTokens == sum(b.tokens for b in r.blocks)
    assert r.toolTokens == sum(t.tokens for t in r.tools)
    assert r.totalTokens == r.messageTokens + r.toolTokens
    assert r.toolTokens > 0  # tool schemas are real context, not free


@pytest.mark.anyio
async def test_pushes_on_the_interpretability_channel(estimating):
    conn = FakeConn()
    await _capture(conn)
    assert len(conn.sent) == 1
    assert conn.sent[0]["channel"] == "interpretability"
    assert conn.sent[0]["event"] == "round"
    assert conn.sent[0]["data"]["turnId"] == "t1"


@pytest.mark.anyio
async def test_a_dead_socket_still_records(estimating):
    """The browser closing mid-turn must not lose the capture."""
    conn = FakeConn(fail=True)
    await _capture(conn)
    assert len(recorder.recent_turns()) == 1


@pytest.mark.anyio
async def test_capture_never_raises(estimating):
    """The hard guarantee: capture runs inside the agent loop, so no input may make
    it throw. A garbage message list costs a snapshot, never the user's answer."""
    conn = FakeConn()
    await recorder.capture_round(
        conn,
        turn_id="t9",
        agent_id="main",
        model="gemma4:e2b",
        provider="ollama",
        messages=[{"role": "user", "content": object()}],  # type: ignore[list-item]
        tools=[{"nope": True}],
        round_no=0,
        tools_selected=1,
        tool_budget=44,
        active_groups=None,
    )


@pytest.mark.anyio
async def test_capture_does_not_mutate_the_context(estimating):
    """Observing the prompt must not change it — the pane's whole premise."""
    conn = FakeConn()
    messages = _prompt()
    tools = _tools()
    before = ([dict(m) for m in messages], [dict(t) for t in tools])
    await _capture(conn, messages=messages, tools=tools)
    assert ([dict(m) for m in messages], [dict(t) for t in tools]) == before


@pytest.mark.anyio
async def test_ring_evicts_oldest_turns(estimating):
    for i in range(recorder.MAX_TURNS + 5):
        await _capture(FakeConn(), turn_id=f"t{i}")
    turns = recorder.recent_turns()
    assert len(turns) == recorder.MAX_TURNS
    assert turns[0].turnId == f"t{recorder.MAX_TURNS + 4}"  # newest first
    # Per-turn capture state must be evicted alongside, or it leaks a turn at a time.
    assert "t0" not in recorder._prompt_end


@pytest.mark.anyio
async def test_estimated_counts_are_flagged_not_silent(estimating):
    """An estimate rendered as an exact number is the failure this module exists to
    prevent, so the flag has to ride on the snapshot."""
    conn = FakeConn()
    await _capture(conn)
    assert recorder.recent_turns()[0].exact is False


@pytest.mark.anyio
async def test_long_blocks_clip_preview_but_not_token_count(estimating):
    """Clipping is a transport concern. The numbers the pane reasons about must
    still describe the whole text.

    Pinned to the estimator on purpose: a real BPE tokenizer collapses a synthetic
    run of one character to far fewer tokens than its length implies, which would
    make the assertion about *which text was counted* untestable.
    """
    conn = FakeConn()
    big = "x" * (recorder.MAX_BLOCK_CHARS * 3)
    await _capture(conn, messages=[{"role": "user", "content": big}])
    block = recorder.recent_turns()[0].rounds[0].blocks[0]
    assert block.clipped
    assert len(block.content) == recorder.MAX_BLOCK_CHARS
    assert block.fullChars == len(big)
    # The full text, not the 4k preview — the distinction the pane depends on.
    assert block.tokens == tokenizer.estimate(big)
    assert block.tokens > tokenizer.estimate(block.content)


@pytest.mark.anyio
async def test_delegated_turn_links_to_its_parent(estimating):
    """`main` delegates to a specialist via agent.delegate; the sub-agent runs its
    own loop on this connection. Without the parent link the two are unrelated
    siblings and the handoff is invisible."""
    conn = FakeConn()
    await _capture(conn, turn_id="turnA", agent_id="main", agent_name="Orchestrator")
    await _capture(
        conn,
        turn_id="turnA:coder:ab12",
        agent_id="coder",
        agent_name="Coder",
        parent_turn_id="turnA",
        tool_groups=["files", "editor"],
        permission_mode="acceptEdits",
    )

    turns = {t.turnId: t for t in recorder.recent_turns()}
    assert turns["turnA"].parentTurnId is None
    child = turns["turnA:coder:ab12"]
    assert child.parentTurnId == "turnA"
    assert child.agentId == "coder" and child.agentName == "Coder"
    assert child.toolGroups == ["files", "editor"]
    assert child.permissionMode == "acceptEdits"


@pytest.mark.anyio
async def test_unrestricted_scope_is_none_not_empty(estimating):
    """`main` has tool_groups=None (every group loadable). An empty list would mean
    the opposite — no groups at all — so the two must not collapse."""
    conn = FakeConn()
    await _capture(conn, turn_id="t-main", agent_id="main", tool_groups=None)
    await _capture(conn, turn_id="t-locked", agent_id="locked", tool_groups=[])
    turns = {t.turnId: t for t in recorder.recent_turns()}
    assert turns["t-main"].toolGroups is None
    assert turns["t-locked"].toolGroups == []


@pytest.mark.anyio
async def test_peer_ask_is_recorded_opaquely(estimating):
    """A peer turn runs on someone else's machine. It belongs in the tree so there's
    no unexplained gap, but it must carry no rounds — we have no visibility into
    the context it built, and must not imply otherwise."""
    conn = FakeConn()
    await _capture(conn, turn_id="turnA")
    await recorder.capture_peer_ask(
        conn, parent_turn_id="turnA", peer_id="node7", prompt="what do you know about X"
    )

    peer = next(t for t in recorder.recent_turns() if t.kind == "peer")
    assert peer.parentTurnId == "turnA"
    assert peer.peerId == "node7"
    assert peer.sentPrompt == "what do you know about X"
    assert peer.rounds == []  # nothing to inspect, by construction
    assert conn.sent[-1]["event"] == "peer"


@pytest.mark.anyio
async def test_peer_capture_never_raises(estimating):
    """Same hard guarantee as round capture — it runs inside a live tool call."""
    await recorder.capture_peer_ask(
        FakeConn(fail=True), parent_turn_id="t", peer_id="p", prompt="x"
    )


@pytest.mark.anyio
async def test_delegated_turn_carries_the_specialists_own_scope(estimating):
    """The sub-agent's context is assembled from ITS spec, not the parent's — the
    whole reason per-turn identity is worth capturing."""
    conn = FakeConn()
    await _capture(
        conn,
        turn_id="turnB:researcher:cd34",
        agent_id="researcher",
        agent_name="Researcher",
        parent_turn_id="turnB",
        tool_groups=["browser", "library", "github", "google"],
        active_groups={"browser", "library"},
    )
    turn = recorder.recent_turns()[0]
    assert turn.toolGroups == ["browser", "library", "github", "google"]
    assert turn.rounds[0].activeGroups == ["browser", "library"]


def test_repo_resolution_order_and_provenance():
    # An LM Studio model id is already an HF repo id — the one case where the
    # tokenizer provably matches the running weights.
    assert tokenizer.repo_for_model("google/gemma-4-12b-qat") == (
        "google/gemma-4-12b-qat",
        "model",
    )
    # The setting is the escape hatch for gated repos; it must win outright.
    assert tokenizer.repo_for_model("gemma4:e2b", "my/mirror") == (
        "my/mirror",
        "setting",
    )
    # An Ollama tag can only be family-matched, and must say so.
    assert tokenizer.repo_for_model("gemma4:e2b") == ("google/gemma-2-2b-it", "family")
    assert tokenizer.repo_for_model("qwen3:8b-instruct-q4_K_M")[1] == "family"
    assert tokenizer.repo_for_model("some-unknown-model") == (None, "none")


def test_family_tokenizer_is_not_reported_as_exact():
    """The bug this guards: matching on 'gemma' loads the Gemma 2 vocab for a
    Gemma 3/4 model. The counts look precise and are wrong, so a family match must
    never claim exactness — that is the whole failure mode this module prevents."""
    family = Counter(object(), "google/gemma-2-2b-it", "family")
    assert family.exact is False and family.source == "family"

    matched = Counter(object(), "google/gemma-4-12b-qat", "model")
    assert matched.exact is True and matched.source == "model"

    pinned = Counter(object(), "my/mirror", "setting")
    assert pinned.exact is True


def test_source_never_claims_provenance_without_a_tokenizer():
    """A failed load must reset the source, or the pane would show 'exact' for a
    tokenizer that isn't there."""
    counter = Counter(None, "google/gemma-4-12b-qat", "model")
    assert counter.exact is False and counter.source == "none"


def test_estimator_is_never_zero_for_real_text():
    assert tokenizer.estimate("") == 0
    assert tokenizer.estimate("hi") >= 1


def test_counter_without_a_tokenizer_falls_back_and_says_so():
    counter = Counter(None, None)
    assert counter.exact is False
    assert counter.count("hello world") > 0
    assert counter.count_json({"a": 1}) > 0


def test_context_length_read_from_any_family_key():
    """Ollama keys context length by family; a lookup table would need chasing."""
    assert (
        tokenizer.context_length_from_show(
            {"model_info": {"gemma2.context_length": 8192}}
        )
        == 8192
    )
    assert (
        tokenizer.context_length_from_show(
            {"model_info": {"llama.context_length": 131072}}
        )
        == 131072
    )
    assert (
        tokenizer.context_length_from_show({"model_info": {"general.name": "x"}})
        is None
    )
    assert tokenizer.context_length_from_show({}) is None


@pytest.fixture
def client() -> TestClient:
    from backend.app import app

    return TestClient(app)


def test_routes_list_and_clear(client: TestClient, estimating):
    assert client.get("/api/interpretability/turns").json() == {"turns": []}

    import anyio

    anyio.run(lambda: _capture(FakeConn()))

    listed = client.get("/api/interpretability/turns").json()["turns"]
    assert len(listed) == 1 and listed[0]["turnId"] == "t1"

    assert client.get("/api/interpretability/turns/t1").json()["turnId"] == "t1"
    assert client.get("/api/interpretability/turns/nope").status_code == 404

    assert client.delete("/api/interpretability/turns").json() == {"turns": []}
    assert client.get("/api/interpretability/turns").json() == {"turns": []}


def test_model_route_reports_error_without_a_provider(client: TestClient, monkeypatch):
    """No provider configured is a normal state (fresh install), not a 500."""
    from backend.modules.agent import routes as agent_routes

    monkeypatch.setattr(agent_routes, "_load_config", lambda: None)
    body = client.get("/api/interpretability/model").json()
    assert body["error"] and body["contextLength"] is None


# ── The durable history surface ──────────────────────────────────────────────
# `capture_round` persists through to the `agent_turns` table as well as the ring.
# These cover the half that was written but unreachable: the table had no routes,
# so a turn older than the ring's 25 existed and could not be opened.


def test_history_route_reads_the_durable_table_not_the_ring(
    client: TestClient, estimating
):
    import anyio

    anyio.run(lambda: _capture(FakeConn()))
    # Clearing the ring is exactly what a restart does — and what makes the
    # difference between the two surfaces visible.
    recorder.clear()

    assert client.get("/api/interpretability/turns").json() == {"turns": []}
    turns = client.get("/api/interpretability/turns/history").json()["turns"]
    assert [t["turnId"] for t in turns] == ["t1"]
    # A summary is metadata: a round *count*, and no context blocks anywhere in it.
    assert turns[0]["rounds"] == 1
    assert "blocks" not in str(turns[0])


def test_history_is_not_swallowed_by_the_turn_id_route(client: TestClient):
    """`/turns/history` must not resolve as a turn called "history" — the failure
    is a 404 on a route that exists, and it depends only on declaration order."""
    assert client.get("/api/interpretability/turns/history").status_code == 200


def test_get_turn_falls_back_to_the_store(client: TestClient, estimating):
    import anyio

    anyio.run(lambda: _capture(FakeConn()))
    recorder.clear()

    body = client.get("/api/interpretability/turns/t1").json()
    assert body["turnId"] == "t1" and len(body["rounds"]) == 1
    assert client.get("/api/interpretability/turns/nope").status_code == 404


def test_history_filters_pass_through(client: TestClient, estimating):
    import anyio

    anyio.run(lambda: _capture(FakeConn()))
    anyio.run(lambda: _capture(FakeConn(), turn_id="t2", parent_turn_id="t1"))

    both = client.get("/api/interpretability/turns/history").json()["turns"]
    assert {t["turnId"] for t in both} == {"t1", "t2"}
    roots = client.get(
        "/api/interpretability/turns/history", params={"roots_only": True}
    ).json()["turns"]
    assert [t["turnId"] for t in roots] == ["t1"]


# ── The model's true context window ──────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "dialect", "path", "payload", "expected"),
    [
        (
            "ollama",
            "ollama",
            "/api/show",
            {"model_info": {"llama.context_length": 8192}},
            8192,
        ),
        (
            "llamacpp",
            "openai",
            "/props",
            {"default_generation_settings": {"n_ctx": 4096}},
            4096,
        ),
        (
            "lmstudio",
            "openai",
            "/api/v0/models/m",
            {"loaded_context_length": 2048, "max_context_length": 32768},
            2048,
        ),
        (
            "vllm",
            "openai",
            "/v1/models",
            {"data": [{"id": "m", "max_model_len": 16384}]},
            16384,
        ),
        # Reached the server, server declined to say. None, never a guess.
        ("lmstudio", "openai", "/api/v0/models/m", {}, None),
        ("openrouter", "litellm", "", {}, None),
    ],
)
async def test_context_window_probe_per_provider(
    kind, dialect, path, payload, expected, monkeypatch
):
    """Every server reports the window somewhere different. Asking Ollama alone —
    the shape the `/model` route has — answers None on an LM Studio box, which is
    the most common local setup here."""
    import httpx

    from backend.modules.interpretability import window

    window.reset_cache()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path != path:
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    # `window.httpx` IS the httpx module, so the replacement has to close over the
    # real class — referring to `httpx.AsyncClient` inside it recurses.
    real = httpx.AsyncClient
    monkeypatch.setattr(
        window.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    info = type("Info", (), {"kind": kind, "dialect": dialect})()
    assert await window.context_length(info, "http://x", "m") == expected
    if dialect == "litellm":
        assert seen == []  # a hosted model's window is not ours to guess at


@pytest.mark.anyio
async def test_context_window_probe_is_cached_and_never_raises(monkeypatch):
    """It runs in `run_agent_loop`'s finally on every turn: an unreachable server
    must cost one timeout, not one per turn, and must never surface as an error."""
    import httpx

    from backend.modules.interpretability import window

    window.reset_cache()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    # `window.httpx` IS the httpx module, so the replacement has to close over the
    # real class — referring to `httpx.AsyncClient` inside it recurses.
    real = httpx.AsyncClient
    monkeypatch.setattr(
        window.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler)),
    )
    info = type("Info", (), {"kind": "ollama", "dialect": "ollama"})()
    assert await window.context_length(info, "http://x", "m") is None
    assert await window.context_length(info, "http://x", "m") is None
    assert calls == 1  # the negative answer is cached too


def test_finish_turn_stamps_the_window(estimating):
    """`finish_turn` was defined and called nowhere, so `modelContextLength` was
    always None — the budget bar had no denominator."""
    import anyio

    anyio.run(lambda: _capture(FakeConn()))
    assert recorder.recent_turns()[0].modelContextLength is None

    recorder.finish_turn("t1", 8192)
    assert recorder.recent_turns()[0].modelContextLength == 8192

    from backend.modules.interpretability import store

    assert (
        store.get_turn("t1").modelContextLength == 8192
    )  # re-persisted, not just live
