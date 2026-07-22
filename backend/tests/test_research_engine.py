"""Deep-research engine: plan parsing/repair, findings splitting, source
numbering, citation checking, DDG parsing — all pure or provider-stubbed."""

from __future__ import annotations

import asyncio

import pytest

from backend.modules.agent import providers as P
from backend.modules.research import engine, rtools


def _plan_json(n_subagents: int = 2) -> str:
    subs = ",".join(
        f'{{"name": "s{i}", "objective": "find facet {i}", "output_format": "notes",'
        f'"tool_guidance": "web first", "boundaries": "no rabbit holes",'
        f'"max_tool_calls": 5}}'
        for i in range(n_subagents)
    )
    return f'{{"complexity": "standard", "subagents": [{subs}]}}'


def test_parse_plan_happy_path() -> None:
    plan = engine.parse_plan(f"Here is my plan:\n{_plan_json(3)}", max_subagents=4)
    assert plan["complexity"] == "standard"
    assert [s["name"] for s in plan["subagents"]] == ["s0", "s1", "s2"]
    assert all(1 <= s["max_tool_calls"] <= 25 for s in plan["subagents"])


def test_parse_plan_clamps_subagents_and_calls() -> None:
    raw = (
        '{"complexity": "deep", "subagents": ['
        '{"objective": "a", "max_tool_calls": 999},'
        '{"objective": "b"}, {"objective": "c"}]}'
    )
    plan = engine.parse_plan(raw, max_subagents=2)
    assert len(plan["subagents"]) == 2
    assert plan["subagents"][0]["max_tool_calls"] == 25


@pytest.mark.parametrize(
    "raw",
    [
        "no json here",
        '{"complexity": "standard"}',
        '{"complexity": "extreme", "subagents": [{"objective": "x"}]}',
        '{"complexity": "quick", "subagents": []}',
        '{"complexity": "quick", "subagents": [{"name": "x"}]}',
    ],
)
def test_parse_plan_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        engine.parse_plan(raw, max_subagents=4)


def test_plan_step_repairs_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(["not json at all", _plan_json(1)])

    async def fake_chat(client, info, endpoint, model, messages, tools):
        content = next(replies)
        return P.ChatResult(
            assistant_message={"role": "assistant", "content": content},
            tool_calls=[],
            content=content,
        )

    monkeypatch.setattr(engine.P, "chat", fake_chat)
    choice = engine.ModelChoice(P.provider_for("ollama"), "http://x", "m")
    run = {"id": "r1", "query": "q", "effort": "auto"}
    plan, transcript, tokens = asyncio.run(engine.run_plan_step(run, choice))
    assert plan["subagents"][0]["name"] == "s0"
    # Repair round means 2 assistant messages + the repair user message.
    assert sum(1 for m in transcript if m["role"] == "assistant") == 2
    assert tokens > 0


def test_subagent_loop_uses_tools_then_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_chat(client, info, endpoint, model, messages, tools):
        if not calls:
            calls.append("tool round")
            return P.ChatResult(
                assistant_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [],
                },
                tool_calls=[
                    P.ToolCall(id="1", name="web_search", arguments={"query": "x"})
                ],
                content="",
            )
        content = "Found things.\nSOURCES:\n- A Paper | https://a.example | supports x"
        return P.ChatResult(
            assistant_message={"role": "assistant", "content": content},
            tool_calls=[],
            content=content,
        )

    async def fake_web_search(args):
        return {
            "results": [
                {"title": "A Paper", "url": "https://a.example", "snippet": "s"}
            ]
        }

    monkeypatch.setattr(engine.P, "chat", fake_chat)
    monkeypatch.setattr(
        engine.rtools,
        "make_tools",
        lambda library: (
            [
                {
                    "type": "function",
                    "function": {"name": "web_search", "parameters": {}},
                }
            ],
            {"web_search": fake_web_search},
        ),
    )
    choice = engine.ModelChoice(P.provider_for("ollama"), "http://x", "m")
    run = {"id": "r1", "query": "q", "effort": "auto", "library": "default"}
    spec = {
        "name": "s0",
        "objective": "o",
        "output_format": "f",
        "tool_guidance": "g",
        "boundaries": "b",
        "max_tool_calls": 3,
    }
    output, transcript, _tokens = asyncio.run(
        engine.run_subagent_step(run, spec, choice, is_cancelled=lambda: False)
    )
    assert output["tool_calls_used"] == 1
    assert output["findings"] == "Found things."
    assert output["sources"] == [
        {"title": "A Paper", "url": "https://a.example", "note": "supports x"}
    ]
    assert any(m["role"] == "tool" for m in transcript)


def test_number_sources_dedupes_by_url() -> None:
    outputs = [
        {
            "name": "a",
            "findings": "fa",
            "sources": [
                {"title": "X", "url": "https://x", "note": ""},
                {"title": "Y", "url": "https://y", "note": ""},
            ],
        },
        {
            "name": "b",
            "findings": "fb",
            "sources": [{"title": "X again", "url": "https://x", "note": ""}],
        },
    ]
    numbered, block = engine.number_sources(outputs)
    assert len(numbered) == 2  # x deduped
    assert "[1] X — https://x" in block
    assert "[1] X again — https://x" in block  # same number in both blocks


def test_check_citations_flags_dangling() -> None:
    assert engine.check_citations("ok [1] and [2]", 2) == []
    assert engine.check_citations("bad [3] and [0]", 2) == [0, 3]


def test_split_findings_without_sources_line() -> None:
    findings, sources = engine._split_findings("just prose, no sources line")
    assert findings == "just prose, no sources line"
    assert sources == []


DDG_HTML = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpost&amp;rut=abc">A <b>Great</b> Post</a>
  <a class="result__snippet" href="...">Snippet <b>text</b> here</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://plain.example/x">Plain Link</a>
</div>
"""


def test_parse_ddg_results() -> None:
    results = rtools.parse_ddg_results(DDG_HTML)
    assert results[0]["url"] == "https://example.com/post"
    assert results[0]["title"] == "A Great Post"
    assert "Snippet text here" in results[0]["snippet"]
    assert results[1]["url"] == "https://plain.example/x"


def test_parse_ddg_empty_on_drift() -> None:
    assert rtools.parse_ddg_results("<html>totally different markup</html>") == []
