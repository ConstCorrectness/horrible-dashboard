"""OTLP spans → trajectory runs, one fixture per framework we expect to receive.

The attribute sets mirror what each framework's instrumentation actually emits
(GenAI semconv for Pydantic AI / ADK, OpenInference for LangGraph and the OpenAI
Agents SDK). Like the trajectory importers, these vocabularies belong to other
people and drift without notice, and the failure mode is a quietly empty run, not
an exception — hence fixtures, pinned field by field.
"""

from __future__ import annotations

import json

from backend.modules.otel import mapping
from backend.modules.otel.models import STATUS_ERROR, Span, SpanEvent

TRACE = "0af7651916cd43dd8448eb211c80319c"
MS = 1_000_000


def span(
    span_id: str,
    name: str,
    parent: str = "",
    start: int = 0,
    end: int = 10,
    resource: dict | None = None,
    **attrs,
) -> Span:
    return Span(
        trace_id=TRACE,
        span_id=span_id.rjust(16, "0"),
        parent_span_id=parent.rjust(16, "0") if parent else "",
        name=name,
        start_ns=1_700_000_000_000_000_000 + start * MS,
        end_ns=1_700_000_000_000_000_000 + end * MS,
        attrs=attrs,
        resource=resource or {"service.name": "my-agent"},
    )


def _kinds(write) -> list[tuple[str, str | None]]:
    return [(s.kind, s.name) for s in write.step_list]


# --- Pydantic AI (GenAI semconv) -------------------------------------------


def pydantic_ai_trace() -> list[Span]:
    user = [
        {"role": "user", "parts": [{"type": "text", "content": "weather in Paris?"}]}
    ]
    return [
        span(
            "a1",
            "invoke_agent weather_agent",
            start=0,
            end=900,
            **{
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "weather_agent",
                "gen_ai.request.model": "gpt-4o",
                # Pydantic AI repeats run totals on the agent span; they must not be
                # added to the per-call counts a second time.
                "gen_ai.usage.input_tokens": 300,
                "gen_ai.usage.output_tokens": 40,
            },
        ),
        span(
            "c1",
            "chat gpt-4o",
            parent="a1",
            start=10,
            end=300,
            **{
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.provider.name": "openai",
                "gen_ai.usage.input_tokens": 120,
                "gen_ai.usage.output_tokens": 15,
                "gen_ai.input.messages": json.dumps(user),
                "gen_ai.output.messages": json.dumps(
                    [
                        {
                            "role": "assistant",
                            "parts": [
                                {"type": "tool_call", "name": "get_weather", "id": "t1"}
                            ],
                        }
                    ]
                ),
            },
        ),
        span(
            "t1",
            "execute_tool get_weather",
            parent="a1",
            start=310,
            end=400,
            **{
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "get_weather",
                "gen_ai.tool.call.id": "t1",
                "gen_ai.tool.call.arguments": '{"city": "Paris"}',
                "gen_ai.tool.call.result": '{"temp_c": 21}',
            },
        ),
        span(
            "c2",
            "chat gpt-4o",
            parent="a1",
            start=410,
            end=890,
            **{
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.usage.input_tokens": 180,
                "gen_ai.usage.output_tokens": 25,
                "gen_ai.output.messages": json.dumps(
                    [
                        {
                            "role": "assistant",
                            "parts": [{"type": "text", "content": "It is 21°C."}],
                        }
                    ]
                ),
            },
        ),
    ]


def test_pydantic_ai_genai_semconv() -> None:
    write = mapping.project(pydantic_ai_trace())
    assert write is not None
    assert write.external_id == TRACE
    assert write.dataset_id == mapping.DEFAULT_DATASET
    assert write.agent_name == "weather_agent"
    assert write.model == "gpt-4o"
    assert write.provider == "openai"
    assert write.goal == "weather in Paris?"
    assert write.status == "complete"
    assert _kinds(write) == [
        ("message", "gpt-4o"),
        ("action", "get_weather"),
        ("message", "gpt-4o"),
    ]
    tool = write.step_list[1]
    assert tool.args == {"city": "Paris"}
    assert tool.result == {"temp_c": 21}
    assert tool.round == 0 and write.step_list[2].round == 1
    assert write.step_list[2].content == "It is 21°C."
    # 120+180 / 15+25 from the chat spans — not 600/80 from also counting the
    # agent span's repeated totals.
    assert (write.tokens_in, write.tokens_out) == (300, 40)
    assert write.rounds == 2


# --- LangGraph (OpenInference) ---------------------------------------------


def langgraph_trace() -> list[Span]:
    return [
        span(
            "g1",
            "LangGraph",
            start=0,
            end=1000,
            **{
                "openinference.span.kind": "CHAIN",
                "input.value": '{"messages": [["user", "weather in Paris?"]]}',
                "output.value": "It is 21°C.",
            },
        ),
        span(
            "n1",
            "agent",
            parent="g1",
            start=5,
            end=400,
            **{"openinference.span.kind": "CHAIN"},
        ),
        span(
            "l1",
            "ChatOpenAI",
            parent="n1",
            start=10,
            end=390,
            **{
                "openinference.span.kind": "LLM",
                "llm.model_name": "gpt-4o-mini",
                "llm.provider": "openai",
                "llm.token_count.prompt": 90,
                "llm.token_count.completion": 12,
                "llm.input_messages.0.message.role": "user",
                "llm.input_messages.0.message.content": "weather in Paris?",
                "llm.output_messages.0.message.role": "assistant",
                "llm.output_messages.0.message.content": "",
            },
        ),
        span(
            "n2",
            "tools",
            parent="g1",
            start=410,
            end=600,
            **{"openinference.span.kind": "CHAIN"},
        ),
        span(
            "x1",
            "get_weather",
            parent="n2",
            start=420,
            end=590,
            **{
                "openinference.span.kind": "TOOL",
                "tool.name": "get_weather",
                "input.value": '{"city": "Paris"}',
                "output.value": "21",
            },
        ),
    ]


def test_langgraph_openinference_nesting() -> None:
    write = mapping.project(langgraph_trace())
    assert write is not None
    # The graph's root CHAIN anchors the run and is not itself a step.
    assert write.agent_name == "LangGraph"
    assert write.model == "gpt-4o-mini"
    assert write.goal == "weather in Paris?"
    assert _kinds(write) == [
        ("observation", "agent"),
        ("message", "gpt-4o-mini"),
        ("observation", "tools"),
        ("action", "get_weather"),
    ]
    agent_node, llm, tools_node, tool = write.step_list
    assert agent_node.parent_seq is None
    assert llm.parent_seq == agent_node.seq
    assert tool.parent_seq == tools_node.seq
    assert tool.args == {"city": "Paris"}
    assert (write.tokens_in, write.tokens_out) == (90, 12)
    assert llm.args == {"input": [{"role": "user", "content": "weather in Paris?"}]}


# --- OpenAI Agents SDK (OpenInference instrumentor) -------------------------


def openai_agents_trace() -> list[Span]:
    return [
        span(
            "w1",
            "Agent workflow",
            start=0,
            end=800,
            **{"openinference.span.kind": "CHAIN"},
        ),
        span(
            "a1",
            "Assistant",
            parent="w1",
            start=5,
            end=790,
            **{"openinference.span.kind": "AGENT", "graph.node.id": "Assistant"},
        ),
        span(
            "r1",
            "response",
            parent="a1",
            start=10,
            end=300,
            **{
                "openinference.span.kind": "LLM",
                "llm.model_name": "gpt-4.1",
                "llm.system": "openai",
                "llm.token_count.prompt": 50,
                "llm.token_count.completion": 0,
            },
        ),
        span(
            "f1",
            "get_weather",
            parent="a1",
            start=310,
            end=320,
            **{
                "openinference.span.kind": "TOOL",
                "tool.name": "get_weather",
                "input.value": '{"city":"Paris"}',
                "output.value": "sunny",
            },
        ),
    ]


def test_openai_agents_sdk_anchor_is_the_agent_not_the_workflow() -> None:
    write = mapping.project(openai_agents_trace())
    assert write is not None
    assert write.agent_name == "Assistant"
    # Workflow + agent span are the anchor and its ancestor: neither is a step.
    assert _kinds(write) == [("message", "gpt-4.1"), ("action", "get_weather")]
    assert write.provider == "openai"
    # A reported zero is a measurement and stays zero.
    assert write.tokens_out == 0
    assert write.step_list[1].result == "sunny"


# --- Google ADK (GenAI semconv, older shapes) -------------------------------


def adk_trace() -> list[Span]:
    return [
        span("i1", "invocation", start=0, end=700),
        span(
            "a1",
            "invoke_agent root_agent",
            parent="i1",
            start=1,
            end=699,
            **{
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "root_agent",
            },
        ),
        # ADK's LLM span has a model but no operation name.
        span(
            "l1",
            "call_llm",
            parent="a1",
            start=5,
            end=200,
            **{
                "gen_ai.system": "gcp.vertex.agent",
                "gen_ai.request.model": "gemini-2.5-flash",
                "gen_ai.usage.input_tokens": 77,
                "gen_ai.usage.output_tokens": 9,
            },
        ),
        span(
            "t1",
            "execute_tool get_weather",
            parent="a1",
            start=210,
            end=260,
            **{
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "get_weather",
                "gcp.vertex.agent.tool_call_args": '{"city": "Paris"}',
                "gcp.vertex.agent.tool_response": '{"result": "sunny"}',
            },
        ),
    ]


def test_google_adk() -> None:
    write = mapping.project(adk_trace())
    assert write is not None
    assert write.agent_name == "root_agent"
    assert write.model == "gemini-2.5-flash"
    assert write.provider == "gcp.vertex.agent"
    assert _kinds(write) == [("message", "gemini-2.5-flash"), ("action", "get_weather")]
    assert write.step_list[1].args == {"city": "Paris"}
    assert write.step_list[1].result == {"result": "sunny"}


# --- invariants ---------------------------------------------------------------


def test_no_usage_reported_is_none_not_zero() -> None:
    spans = [
        span("a1", "invoke_agent x", **{"gen_ai.operation.name": "invoke_agent"}),
        span(
            "c1",
            "chat m",
            parent="a1",
            **{"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"},
        ),
    ]
    write = mapping.project(spans)
    assert write is not None
    assert write.tokens_in is None and write.tokens_out is None


def test_unrooted_trace_is_running_until_final() -> None:
    children = pydantic_ai_trace()[1:]
    running = mapping.project(children)
    assert running is not None and running.status == "running"
    assert running.finished_at is None
    final = mapping.project(children, final=True)
    assert final is not None and final.status == "complete"
    assert final.meta["otel"]["partial"] is True


def test_failed_root_and_exception_events() -> None:
    spans = pydantic_ai_trace()
    spans[0] = spans[0].model_copy(
        update={"status_code": STATUS_ERROR, "status_message": "boom"}
    )
    spans[2] = spans[2].model_copy(
        update={
            "status_code": STATUS_ERROR,
            "events": [
                SpanEvent(name="exception", attrs={"exception.message": "city unknown"})
            ],
        }
    )
    write = mapping.project(spans)
    assert write is not None
    assert write.status == "failed" and write.error == "boom"
    tool = write.step_list[1]
    assert tool.ok is False and tool.error == "city unknown"


def test_dataset_from_resource_attribute() -> None:
    spans = [
        span(
            "a1",
            "invoke_agent x",
            resource={"service.name": "nb", mapping.DATASET_ATTR: "my-notebook"},
            **{"gen_ai.operation.name": "invoke_agent"},
        )
    ]
    write = mapping.project(spans)
    assert write is not None and write.dataset_id == "my-notebook"
    assert write.meta["otel"]["service"] == "nb"
