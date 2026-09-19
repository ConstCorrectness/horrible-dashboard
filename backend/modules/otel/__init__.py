"""OpenTelemetry for agents: an OTLP receiver and the node's own tracer.

Two directions, one span table (`otel_spans` in `app.db`):

- **In** — `POST /api/otel/v1/traces` is an OTLP/HTTP collector endpoint. Agents
  the user writes (Pydantic AI, LangGraph via OpenInference, the OpenAI Agents SDK,
  Google ADK, raw `opentelemetry-sdk`) point `OTEL_EXPORTER_OTLP_ENDPOINT` at
  `/api/otel` and their traces land here, then get *projected* into trajectories
  (`mapping.py`, `materialize.py`). Raw spans are the truth; a trajectory run is a
  view of them, rebuilt whenever a batch for its trace arrives.
- **Out** — `tracing.py` gives the built-in agent loop GenAI-semconv spans, always
  stored locally and optionally exported to an external collector.

`turn_id` stays the one identity: a built-in turn's trace id is *derived* from it
(`ids.trace_id_for_turn`), never stored beside it. See docs/modules/otel.mdx.
"""
