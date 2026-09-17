"""Token and cost accounting.

The failure mode this guards is not a crash, it is a **confident wrong number**.
Every property here distinguishes "nobody reported it" from "it was zero", because
those two collapse into each other at every step of the pipeline if nothing stops
them, and the result is a cost readout that looks authoritative and is not.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.agent import cost, providers as P


class _Res:
    """A minimal httpx-response stand-in for the non-streaming paths."""

    def __init__(self, body: dict) -> None:
        self._body = body
        self.status_code = 200

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


# --------------------------------------------------------------------------- parse


def test_ollama_usage_comes_off_the_done_chunk():
    """Ollama's terminal chunk already carried both counts; the `break` threw it away."""
    usage = P._usage_from_ollama(
        {"done": True, "prompt_eval_count": 120, "eval_count": 45}
    )
    assert usage.tokens_in == 120
    assert usage.tokens_out == 45


def test_openai_usage_includes_the_cached_prompt_split():
    usage = P._usage_from_openai(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 768},
        }
    )
    assert (usage.tokens_in, usage.tokens_out, usage.cached_in) == (1000, 200, 768)


def test_a_missing_usage_block_is_none_not_zero():
    """The distinction the whole module rests on."""
    usage = P._usage_from_openai(None)
    assert usage.tokens_in is None
    assert usage.tokens_out is None
    assert usage.is_empty()


def test_a_nonsense_count_is_none_rather_than_a_crash_or_a_zero():
    usage = P._usage_from_openai({"prompt_tokens": "lots", "completion_tokens": None})
    assert usage.tokens_in is None
    assert usage.tokens_out is None


def test_a_genuine_zero_is_not_empty():
    """`is_empty` must mean "nobody said", not "the number happened to be 0"."""
    assert not P.Usage(tokens_in=0, tokens_out=0).is_empty()


def test_chat_result_still_constructs_positionally_without_usage():
    """Six existing construction sites pass three positional args. The new field is
    defaulted so none of them had to change."""
    result = P.ChatResult({"role": "assistant"}, [], "hi")
    assert result.usage is None


# ---------------------------------------------------------------------------- cost


def test_an_exact_model_beats_the_family_glob():
    """Longest pattern wins. First-match-wins would make the answer depend on dict
    ordering, which is a silent way for an override to be ignored."""
    assert cost.price_for("claude-sonnet-4-5-20260514") == {"in": 3.0, "out": 15.0}


def test_a_routing_prefix_is_stripped_before_matching():
    """litellm needs `anthropic/` on some ids; the table is keyed by the bare model,
    so a prefixed id would match nothing and silently show no cost."""
    assert cost.price_for("anthropic/claude-opus-4-1") == {"in": 15.0, "out": 75.0}


def test_an_unlisted_model_has_no_price():
    assert cost.price_for("qwen3:8b") is None


def test_estimate_is_per_million_tokens():
    assert cost.estimate("gpt-4o", 1_000_000, 1_000_000) == pytest.approx(12.5)


def test_estimate_of_an_unpriced_model_is_none_not_zero():
    assert cost.estimate("qwen3:8b", 1000, 1000) is None


def test_a_local_model_costs_a_known_zero():
    """`0.0` and `None` render differently and mean different things: the pane says
    `free` for this and nothing at all for the next test."""
    assert cost.resolve(None, model="qwen3:8b", provider_kind="ollama") == 0.0


def test_an_unpriced_hosted_model_costs_an_unknown_amount():
    assert cost.resolve(None, model="some-new-model", provider_kind="openai") is None


def test_nim_is_not_assumed_free():
    """It is the same dialect whether it is a local container or NVIDIA's metered
    API, and we cannot tell from here. Calling a billed API free is the worse error."""
    assert cost.resolve(None, model="mystery", provider_kind="nim") is None


def test_a_provider_reported_cost_beats_the_table():
    """litellm priced the call it actually made. Nothing we look up can beat that."""
    usage = P.Usage(tokens_in=1_000_000, tokens_out=1_000_000, cost_usd=0.42)
    assert cost.resolve(usage, model="gpt-4o", provider_kind="openai") == 0.42


def test_the_setting_overrides_the_bundled_table(monkeypatch):
    from backend.modules.settings import routes as settings_routes

    monkeypatch.setattr(
        settings_routes,
        "get_value",
        lambda key, default=None: (
            json.dumps({"gpt-4o": {"in": 99.0, "out": 99.0}})
            if key == "agent.modelPricing"
            else default
        ),
    )
    cost._bundled.cache_clear()
    assert cost.price_for("gpt-4o") == {"in": 99.0, "out": 99.0}


def test_a_malformed_pricing_setting_is_ignored_not_fatal(monkeypatch):
    """A typo in a settings box must not take down every turn's accounting."""
    from backend.modules.settings import routes as settings_routes

    monkeypatch.setattr(
        settings_routes,
        "get_value",
        lambda key, default=None: (
            "{not json" if key == "agent.modelPricing" else default
        ),
    )
    assert cost.price_for("gpt-4o") == {"in": 2.5, "out": 10.0}


# ------------------------------------------------------------------- the real wire
#
# These drive `_openai_chat_stream` / `_ollama_chat_stream` themselves against a mock
# transport, because the two ways this feature fails are both invisible to a unit
# test of the parsers: the usage frame arrives with an empty `choices` list (so the
# existing `or [{}]` swallows it), and an OpenAI-dialect stream emits no usage at all
# unless `stream_options` asks for it.


def _sse(*frames: dict) -> bytes:
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames)
    return (body + "data: [DONE]\n\n").encode()


async def _noop(_reasoning: str, _content: str) -> None:
    return None


@pytest.mark.anyio
async def test_openai_stream_asks_for_usage_and_reads_the_usage_only_frame():
    import httpx

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "hi"}}]},
                # The usage frame: no choices at all.
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 310, "completion_tokens": 12},
                },
            ),
            headers={"content-type": "text/event-stream"},
        )

    P._NO_STREAM_USAGE.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await P._openai_chat_stream(
            client, "http://x", "m", [{"role": "user", "content": "q"}], [], _noop
        )

    # Without this the server sends no usage and the parsing below is dead code.
    assert seen["stream_options"] == {"include_usage": True}
    assert result.content == "hi"
    assert result.usage.tokens_in == 310
    assert result.usage.tokens_out == 12


@pytest.mark.anyio
async def test_a_server_that_rejects_stream_options_still_streams():
    """Local servers vary. A 400 here must cost one retry per endpoint per process,
    never the turn itself — and never one failed request per turn."""
    import httpx

    attempts: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        asked = "stream_options" in payload
        attempts.append(asked)
        if asked:
            return httpx.Response(400, json={"error": "unknown field stream_options"})
        return httpx.Response(
            200,
            content=_sse({"choices": [{"delta": {"content": "ok"}}]}),
            headers={"content-type": "text/event-stream"},
        )

    P._NO_STREAM_USAGE.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await P._openai_chat_stream(
            client, "http://y", "m", [{"role": "user", "content": "q"}], [], _noop
        )
        second = await P._openai_chat_stream(
            client, "http://y", "m", [{"role": "user", "content": "q"}], [], _noop
        )

    assert first.content == "ok"
    assert second.content == "ok"
    # Asked once, refused, remembered: the second turn never asks again.
    assert attempts == [True, False, False]
    assert first.usage.is_empty()


@pytest.mark.anyio
async def test_ollama_stream_keeps_the_counts_off_its_done_chunk():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        lines = [
            json.dumps({"message": {"content": "hel"}}),
            json.dumps({"message": {"content": "lo"}}),
            json.dumps({"done": True, "prompt_eval_count": 88, "eval_count": 7}),
        ]
        return httpx.Response(200, content=("\n".join(lines) + "\n").encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await P._ollama_chat_stream(
            client, "http://z", "m", [{"role": "user", "content": "q"}], [], _noop
        )

    assert result.content == "hello"
    assert result.usage.tokens_in == 88
    assert result.usage.tokens_out == 7
