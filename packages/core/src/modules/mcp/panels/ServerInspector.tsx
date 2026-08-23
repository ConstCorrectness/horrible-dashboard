import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, EmptyState } from '../../../Primitives';
import {
  clearTranscript,
  readResource,
  serverCost,
  serverTranscript,
  type McpCost,
  type McpServer,
  type McpWireMessage,
} from '../api';
import { ConformanceView } from './ConformanceView';
import { ToolInvoker } from './ToolInvoker';

/**
 * What one connected server actually is: its tools with their real context cost, which
 * agents can reach them, and the JSON-RPC conversation that produced all of it.
 *
 * The three views answer three different questions that used to have no answer here.
 * "What did I get" — the tool list, which the pane already had. "What is it costing
 * me" — the serialized schema in real tokens, because a server with eight tools and
 * forty-property inputs is expensive in a way a tool *count* never shows. "Why is it
 * behaving like that" — the wire, which is the only thing that separates "the request
 * never went out" from "the server answered an error".
 */

/**
 * Two more views arrived with the authoring half, and they are the ones used while
 * building a server rather than while diagnosing one. "Does it work" — Run, which
 * invokes a tool from a form generated off its own schema, with no model in the loop.
 * "Is it correct" — Check, the conformance suite.
 */
type View = 'tools' | 'resources' | 'cost' | 'wire' | 'run' | 'check';

const LABEL: Record<View, string> = {
  tools: 'Tools',
  resources: 'Resources',
  cost: 'Context cost',
  wire: 'Wire',
  run: 'Run',
  check: 'Check',
};

function fmtTime(at: number): string {
  return new Date(at * 1000).toLocaleTimeString();
}

function ToolsView({ server }: { server: McpServer }) {
  return (
    <ul style={{ margin: 0, paddingLeft: '1rem' }}>
      {server.tools.map((t) => (
        <li key={t.name} style={{ marginBottom: '0.2rem' }}>
          <code>{`${server.group}.${t.name}`}</code>{' '}
          <span style={{ color: 'var(--text-dim)' }}>
            {/* `readOnlyHint` is what decides whether the agent's permission gate
                fires, so it is stated plainly rather than left to a subtle style. */}
            {t.readOnly ? '(read-only)' : '(gated)'} {t.description}
          </span>
        </li>
      ))}
      {server.prompts.length > 0 && (
        <li style={{ marginTop: '0.3rem', color: 'var(--text-dim)' }}>
          Prompts: {server.prompts.map((p) => p.name).join(', ')}
        </li>
      )}
      {server.resources.length > 0 && (
        <li style={{ color: 'var(--text-dim)' }}>
          {server.resources.length} resource{server.resources.length === 1 ? '' : 's'} — see the
          Resources view
        </li>
      )}
    </ul>
  );
}

/**
 * Browse the server's resources, and read one.
 *
 * `readResource` has been exported from the API client since the module landed with
 * **no consumer at all** — the tool list showed a count and the first URI followed
 * by an ellipsis, so a server offering forty documents was indistinguishable from
 * one offering two, and neither could be opened.
 *
 * Text content is rendered raw. A resource is whatever the server says it is, and
 * prettifying it here would hide exactly the malformed payload you opened this view
 * to see. Anything without text (a blob) is reported by type rather than decoded —
 * guessing at an encoding is how a binary lands in a `<pre>` as mojibake.
 */
function ResourcesView({ server }: { server: McpServer }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = async (uri: string) => {
    setSelected(uri);
    setBusy(true);
    setError(null);
    setBody(null);
    try {
      const res = await readResource(server.id, uri);
      if (res.error) {
        setError(res.error);
        return;
      }
      const parts = (res.contents ?? []).map((c) => {
        const item = c as { text?: string; blob?: string; mimeType?: string };
        if (typeof item.text === 'string') return item.text;
        if (item.blob)
          return `[${item.mimeType || 'binary'} — ${item.blob.length} bytes, not decoded]`;
        return JSON.stringify(item, null, 2);
      });
      setBody(parts.join('\n\n') || '(the server returned no content)');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (server.resources.length === 0) {
    return <EmptyState title="No resources">This server exposes none.</EmptyState>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
        {server.resources.map((r) => (
          <Button
            key={r.uri}
            size="sm"
            intent={selected === r.uri ? 'primary' : 'default'}
            disabled={busy}
            title={r.uri}
            onClick={() => void open(r.uri)}
          >
            {r.name || r.uri}
          </Button>
        ))}
      </div>

      {selected && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
          <code style={{ fontSize: 'var(--fs-micro)', color: 'var(--text-dim)' }}>{selected}</code>
          {busy && <Chip>reading</Chip>}
        </div>
      )}

      {error && <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>{error}</div>}

      {body !== null && (
        <pre
          style={{
            margin: 0,
            padding: 'var(--space-3)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-inset)',
            whiteSpace: 'pre-wrap',
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--fs-micro)',
            maxHeight: 320,
            overflow: 'auto',
          }}
        >
          {body}
        </pre>
      )}
    </div>
  );
}

function CostView({ cost }: { cost: McpCost | null }) {
  if (!cost) return <div style={{ color: 'var(--text-dim)' }}>Measuring…</div>;
  const rows = [...cost.tools].sort((a, b) => b.tokens - a.tokens);
  return (
    <div>
      <div style={{ marginBottom: '0.35rem' }}>
        <strong>{cost.totalTokens.toLocaleString()}</strong> tokens when this group loads
        {' — '}
        {cost.toolTokens.toLocaleString()} schema + {cost.guideTokens.toLocaleString()} guide{' '}
        {/* An estimate rendered as a precise number is the exact failure the
            interpretability module exists to prevent; say which this is. */}
        {!cost.exact && (
          <span style={{ color: 'var(--warn, #d29922)' }}>· estimated (no tokenizer)</span>
        )}
      </div>
      <div style={{ color: 'var(--text-dim)', marginBottom: '0.35rem' }}>
        In scope for:{' '}
        {cost.agents.length === 0
          ? 'no agent'
          : cost.agents.map((a) => `${a.name}${a.explicit ? '' : ' (unrestricted)'}`).join(', ')}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <tbody>
          {rows.map((t) => (
            <tr key={t.name}>
              <td>
                <code>{t.name}</code>
              </td>
              <td style={{ textAlign: 'right', color: 'var(--text-dim)' }}>{t.tokens}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WireView({ messages, onClear }: { messages: McpWireMessage[]; onClear: () => void }) {
  if (messages.length === 0) {
    return <div style={{ color: 'var(--text-dim)' }}>Nothing recorded yet.</div>;
  }
  return (
    <div>
      <button onClick={onClear} style={{ marginBottom: '0.35rem' }}>
        Clear
      </button>
      <div style={{ maxHeight: 300, overflow: 'auto' }}>
        {messages.map((m, i) => (
          <div key={`${m.at}-${i}`} style={{ marginBottom: '0.25rem' }}>
            <span
              style={{
                color: m.direction === 'out' ? 'var(--accent, #58a6ff)' : 'var(--ok, #3fb950)',
              }}
            >
              {m.direction === 'out' ? '→' : '←'}
            </span>{' '}
            <code>{m.method}</code>{' '}
            <span style={{ color: 'var(--text-dim)' }}>
              {fmtTime(m.at)}
              {m.id ? ` #${m.id}` : ''}
            </span>
            <pre
              style={{
                margin: '0.1rem 0 0 1rem',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                color: 'var(--text-dim)',
                fontSize: '0.68rem',
              }}
            >
              {m.payload}
              {m.truncated ? ' …' : ''}
            </pre>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ServerInspector({ server }: { server: McpServer }) {
  const [view, setView] = useState<View>('tools');
  const [cost, setCost] = useState<McpCost | null>(null);
  const [messages, setMessages] = useState<McpWireMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      if (view === 'cost') setCost(await serverCost(server.id));
      if (view === 'wire') setMessages((await serverTranscript(server.id)).messages);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [server.id, view]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div style={{ marginTop: '0.4rem', fontSize: '0.75rem' }}>
      <div style={{ display: 'flex', gap: '0.35rem', marginBottom: '0.35rem' }}>
        {(['tools', 'resources', 'cost', 'wire', 'run', 'check'] as const).map((v) => (
          <button key={v} onClick={() => setView(v)} style={{ fontWeight: view === v ? 600 : 400 }}>
            {v === 'tools' ? `${server.tools.length} tools` : LABEL[v]}
          </button>
        ))}
        {(view === 'cost' || view === 'wire') && (
          <button onClick={() => void load()}>Refresh</button>
        )}
      </div>
      {error && <div style={{ color: 'var(--danger, #f85149)' }}>{error}</div>}
      {view === 'tools' && <ToolsView server={server} />}
      {view === 'resources' && <ResourcesView server={server} />}
      {view === 'cost' && <CostView cost={cost} />}
      {view === 'wire' && (
        <WireView
          messages={messages}
          onClear={async () => setMessages((await clearTranscript(server.id)).messages)}
        />
      )}
      {/* Both need a live session — offering the button on a stopped server would
          produce a 409 the user has to translate. */}
      {(view === 'run' || view === 'check') &&
        (server.state !== 'ready' ? (
          <div style={{ color: 'var(--text-dim)' }}>Connect the server first.</div>
        ) : view === 'run' ? (
          <ToolInvoker server={server} />
        ) : (
          <ConformanceView server={server} />
        ))}
    </div>
  );
}
