/**
 * What an MCP action step looks like from the server's side.
 *
 * A step records what the *agent* saw: arguments in, flattened result out. The call
 * summary adds what the step cannot know — which server answered, how long the round
 * trip took on the wire, whether a failure was the tool or the connection — and the
 * JSON-RPC exchange itself, for as long as the in-memory transcript still holds it.
 *
 * The summary is durable; the wire is not. That asymmetry is shown rather than hidden:
 * "no longer in memory" is a real state, and an empty exchange would misreport it as a
 * call that sent nothing.
 */
import { useState } from 'react';

import { Button, Chip } from '../../../Primitives';
import { getStepWire, type McpCallSummary, type McpWireMessage } from '../api';
import { mono, ms } from './common';

function kindChip(call: McpCallSummary) {
  if (call.ok) return <Chip kind="ok">ok</Chip>;
  return call.error_kind === 'tool' ? (
    <Chip kind="warn" title="The server answered and reported the call failed">
      tool error
    </Chip>
  ) : (
    <Chip
      kind="fail"
      title="The server never answered usefully: not connected, timed out, or the session failed"
    >
      transport error
    </Chip>
  );
}

function WireRow({ message }: { message: McpWireMessage }) {
  let pretty = message.payload;
  try {
    pretty = JSON.stringify(JSON.parse(message.payload), null, 2);
  } catch {
    // Left as sent: a payload that does not parse is itself worth seeing verbatim.
  }
  return (
    <div style={{ marginTop: 'var(--space-2)' }}>
      <div style={{ ...mono, display: 'flex', gap: 'var(--space-3)' }}>
        <span>{message.direction === 'out' ? '→ request' : '← response'}</span>
        <span>{message.method}</span>
        <span>id {message.id}</span>
        {message.truncated ? <span>(truncated)</span> : null}
      </div>
      <pre
        style={{
          ...mono,
          margin: 'var(--space-1) 0 0',
          padding: 'var(--space-3)',
          background: 'var(--bg-primary)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          overflow: 'auto',
          maxHeight: 220,
        }}
      >
        {pretty}
      </pre>
    </div>
  );
}

export function McpStepDetail({
  runId,
  seq,
  call,
}: {
  runId: string;
  seq: number;
  call: McpCallSummary;
}) {
  const [wire, setWire] = useState<McpWireMessage[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadWire = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await getStepWire(runId, seq);
      setWire(res.messages);
      setAvailable(res.available);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        marginTop: 'var(--space-2)',
        paddingLeft: 'var(--space-3)',
        borderLeft: '2px solid var(--accent)',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 'var(--space-3)',
        }}
      >
        <span style={mono}>mcp · {call.server_id}</span>
        {kindChip(call)}
        <span style={mono}>{ms(call.duration_ms)} on the wire</span>
        {call.content_blocks != null ? (
          <span style={mono}>
            {call.content_blocks} content {call.content_blocks === 1 ? 'block' : 'blocks'}
          </span>
        ) : null}
        {wire === null ? (
          <Button intent="ghost" size="sm" disabled={loading} onClick={() => void loadWire()}>
            {loading ? 'loading…' : 'show wire'}
          </Button>
        ) : null}
      </div>
      {error ? (
        <div role="alert" style={{ ...mono, color: 'var(--danger)', marginTop: 'var(--space-1)' }}>
          {error}
        </div>
      ) : null}
      {wire !== null && !available ? (
        <div style={{ ...mono, marginTop: 'var(--space-2)' }}>
          The exchange is no longer in memory. The JSON-RPC transcript is a small in-process ring —
          it forgets older calls and does not survive a restart — so only this summary is kept.
        </div>
      ) : null}
      {wire?.map((message, i) => (
        <WireRow key={`${message.id}-${message.direction}-${i}`} message={message} />
      ))}
    </div>
  );
}
