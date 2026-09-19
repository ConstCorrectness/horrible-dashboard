/**
 * Connect an agent you wrote: point any OpenTelemetry exporter at this node.
 *
 * The dashboard is an OTLP/HTTP collector (`backend/modules/otel/`). A received
 * trace becomes a run in the `otel` dataset (or the one named by the
 * `horrible.dataset` resource attribute), live, beside the node's own.
 *
 * Notebook and training kernels need none of this — the variables are set for them
 * at spawn. This section is for everything else: a script in a terminal, a service,
 * another machine. The token is only for the last case; this machine needs nothing,
 * and the backend only ever hands the token to a caller on this machine.
 */
import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, PaneHeader } from '../../../Primitives';
import { getIngestInfo, rotateIngestToken, type IngestInfo } from '../otel-api';
import { RefreshIcon } from '../icons';
import { ago, bodyScroll, card, heading, mono, SectionShell } from './common';

type Framework = 'pydantic' | 'langgraph' | 'openai' | 'adk' | 'otel';

const FRAMEWORKS: { id: Framework; label: string }[] = [
  { id: 'pydantic', label: 'Pydantic AI' },
  { id: 'langgraph', label: 'LangGraph' },
  { id: 'openai', label: 'OpenAI Agents' },
  { id: 'adk', label: 'Google ADK' },
  { id: 'otel', label: 'OTel SDK' },
];

/** Every snippet shares this: the exporter reads OTEL_EXPORTER_OTLP_ENDPOINT itself. */
const SETUP = `from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)`;

const BASE_PKGS = 'opentelemetry-sdk opentelemetry-exporter-otlp-proto-http';

const SNIPPETS: Record<Framework, { install: string; code: string }> = {
  pydantic: {
    install: `pip install pydantic-ai ${BASE_PKGS}`,
    code: `${SETUP}

from pydantic_ai import Agent
Agent.instrument_all()  # GenAI semconv spans for every agent run`,
  },
  langgraph: {
    install: `pip install langgraph openinference-instrumentation-langchain ${BASE_PKGS}`,
    code: `${SETUP}

from openinference.instrumentation.langchain import LangChainInstrumentor
LangChainInstrumentor().instrument(tracer_provider=provider)`,
  },
  openai: {
    install: `pip install openai-agents openinference-instrumentation-openai-agents ${BASE_PKGS}`,
    code: `${SETUP}

from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)`,
  },
  adk: {
    install: `pip install google-adk ${BASE_PKGS}`,
    code: `${SETUP}

# ADK emits its invoke_agent / call_llm / execute_tool spans to the
# global provider set above — no instrumentor needed.`,
  },
  otel: {
    install: `pip install ${BASE_PKGS}`,
    code: `${SETUP}

tracer = trace.get_tracer("my-agent")
with tracer.start_as_current_span(
    "invoke_agent my-agent",
    attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "my-agent"},
):
    with tracer.start_as_current_span(
        "execute_tool search",
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search"},
    ):
        ...`,
  },
};

/**
 * A recessed command/code container. The leading protocol or variable name is
 * coloured as the one thing to read first, per the house snippet style.
 */
function Snippet({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState<'' | 'ok' | 'failed'>('');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied('ok');
    } catch {
      // Blocked clipboards are common (embedded previews, insecure origins). Say so
      // rather than pretending: the text is still selectable.
      setCopied('failed');
    }
    window.setTimeout(() => setCopied(''), 1600);
  };
  return (
    <div className="traj-snippet">
      <div className="traj-snippet-bar">
        <span style={heading}>{label}</span>
        <span style={{ flex: 1 }} />
        {copied === 'failed' ? <span style={mono}>clipboard blocked — select the text</span> : null}
        <Button size="sm" intent="ghost" onClick={() => void copy()}>
          {copied === 'ok' ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <pre className="traj-snippet-body">
        {text.split('\n').map((line, i) => {
          const match = /^([A-Z_]+=|https?:\/\/|pip |from |import )/.exec(line);
          return (
            <div key={i}>
              {match ? (
                <>
                  <span className="traj-snippet-key">{match[1]}</span>
                  {line.slice(match[1].length)}
                </>
              ) : (
                line || ' '
              )}
            </div>
          );
        })}
      </pre>
    </div>
  );
}

export function ConnectSection() {
  const [info, setInfo] = useState<IngestInfo | null>(null);
  const [error, setError] = useState('');
  const [framework, setFramework] = useState<Framework>('pydantic');
  const [reveal, setReveal] = useState(false);

  const load = useCallback(async () => {
    try {
      setInfo(await getIngestInfo());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Polled, unlike Live: this is a "did my exporter reach you yet" indicator for the
  // minute you are wiring one up, and the ingest route publishes nothing to watch.
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const rotate = async () => {
    try {
      setInfo(await rotateIngestToken());
      setReveal(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const endpoint = info?.endpoint ?? '';
  const envLocal = [
    `OTEL_EXPORTER_OTLP_ENDPOINT=${endpoint}`,
    'OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf',
    'OTEL_SERVICE_NAME=my-agent',
  ].join('\n');
  const token = info?.token ?? null;
  const shownToken = token ? (reveal ? token : '•'.repeat(12)) : '<token>';
  const envRemote = [
    `OTEL_EXPORTER_OTLP_ENDPOINT=${endpoint.replace('127.0.0.1', '<this-node>')}`,
    `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20${shownToken}`,
  ].join('\n');
  const snippet = SNIPPETS[framework];

  return (
    <SectionShell
      error={error}
      header={
        <PaneHeader
          title="Connect"
          meta={info ? [`${info.received_traces} received traces`] : []}
          actions={
            <Button size="sm" intent="ghost" icon={<RefreshIcon />} onClick={() => void load()}>
              Refresh
            </Button>
          }
        />
      }
    >
      <div style={{ ...bodyScroll, padding: 'var(--space-5)' }}>
        <div className="traj-connect-grid">
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span style={heading}>OTLP/HTTP endpoint</span>
              <span style={{ flex: 1 }} />
              {info?.last_received_at ? (
                <Chip kind="ok">last span {ago(info.last_received_at)}</Chip>
              ) : (
                <Chip kind="idle">nothing received yet</Chip>
              )}
            </div>
            <div style={{ ...mono, marginTop: 'var(--space-3)' }}>
              Any OpenTelemetry exporter pointed here lands as a run in Trajectories — live,
              projected from its spans. Notebook and training kernels already have these variables
              set.
            </div>
            <div style={{ marginTop: 'var(--space-4)' }}>
              <Snippet label="This machine" text={envLocal} />
            </div>
          </div>

          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span style={heading}>From another machine</span>
              <span style={{ flex: 1 }} />
              {token ? (
                <Button size="sm" intent="ghost" onClick={() => setReveal(!reveal)}>
                  {reveal ? 'Hide token' : 'Show token'}
                </Button>
              ) : null}
              {token ? (
                <Button size="sm" intent="ghost" onClick={() => void rotate()}>
                  Rotate
                </Button>
              ) : null}
            </div>
            <div style={{ ...mono, marginTop: 'var(--space-3)' }}>
              {token
                ? 'Anything not on this machine needs the ingest token. Rotating it cuts off every exporter using the old one.'
                : 'The token is only shown on this machine itself.'}
            </div>
            <div style={{ marginTop: 'var(--space-4)' }}>
              <Snippet label="Remote exporter" text={envRemote} />
            </div>
          </div>
        </div>

        <div style={{ ...card, marginTop: 'var(--space-5)' }}>
          <div className="traj-segmented" role="tablist" aria-label="Framework">
            {FRAMEWORKS.map((f) => (
              <button
                key={f.id}
                role="tab"
                aria-selected={framework === f.id}
                className={`traj-segment${framework === f.id ? ' is-active' : ''}`}
                onClick={() => setFramework(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 'var(--space-4)' }}>
            <Snippet label="Install" text={snippet.install} />
          </div>
          <div style={{ marginTop: 'var(--space-3)' }}>
            <Snippet label="Instrument" text={snippet.code} />
          </div>
          <div style={{ ...mono, marginTop: 'var(--space-3)' }}>
            Prompts and tool arguments arrive only if your instrumentation records them. A resource
            attribute <code>horrible.dataset=&lt;id&gt;</code> picks the dataset.
          </div>
        </div>
      </div>
    </SectionShell>
  );
}
