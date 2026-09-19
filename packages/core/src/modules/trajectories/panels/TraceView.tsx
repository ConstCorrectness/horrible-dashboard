/**
 * The OTel span tree behind a run: nesting and concurrency, which the round-by-round
 * Timeline above it cannot draw.
 *
 * For a built-in turn these are the node's own spans (`invoke_agent` → `chat` /
 * `execute_tool`), plus anything a user's MCP server or a friend's node sent back
 * under the propagated trace id. For a received run they are the sender's spans,
 * verbatim — the steps list is a projection of them, this is the source.
 *
 * Renders nothing at all when the run has no spans: an import, or a turn from before
 * the node traced, has no trace, and an empty box would read as a broken one.
 */
import { useEffect, useState } from 'react';

import type { TrajectoryRun } from '../api';
import { getRunSpans, type OtelSpan } from '../otel-api';
import { heading, Json, mono, ms } from './common';
import { buildTrace, type TraceRow } from './trace-model';

function tone(row: TraceRow): string {
  if (row.failed) return 'var(--danger)';
  if (row.cls === 'llm') return 'var(--text-secondary)';
  if (row.cls === 'agent') return 'var(--accent)';
  if (row.cls === 'tool') return 'var(--success)';
  return 'var(--border-strong)';
}

function SpanDetail({ span }: { span: OtelSpan }) {
  return (
    <div style={{ marginTop: 'var(--space-3)' }}>
      <div style={{ ...mono, display: 'flex', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
        <span>span {span.span_id}</span>
        {span.parent_span_id ? <span>parent {span.parent_span_id}</span> : null}
        <span>{span.origin}</span>
        {span.scope ? <span>{span.scope}</span> : null}
        {span.resource['service.name'] ? (
          <span>{String(span.resource['service.name'])}</span>
        ) : null}
      </div>
      {span.status_message ? (
        <div style={{ ...mono, color: 'var(--danger)', marginTop: 'var(--space-2)' }}>
          {span.status_message}
        </div>
      ) : null}
      <div style={{ ...heading, marginTop: 'var(--space-3)' }}>Attributes</div>
      <Json value={span.attrs} />
      {span.events.length ? (
        <>
          <div style={{ ...heading, marginTop: 'var(--space-3)' }}>Events</div>
          <Json value={span.events} />
        </>
      ) : null}
    </div>
  );
}

export function TraceView({ run }: { run: TrajectoryRun }) {
  const [spans, setSpans] = useState<OtelSpan[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  // Refetched as a live run grows: `steps` and `status` are what the socket moves.
  useEffect(() => {
    const pending = getRunSpans(run);
    if (!pending) {
      setSpans([]);
      return;
    }
    let cancelled = false;
    pending
      .then((result) => !cancelled && setSpans(result))
      .catch(() => !cancelled && setSpans([]));
    return () => {
      cancelled = true;
    };
    // `run` itself is a new object on every live frame; these are what change it.
  }, [run.id, run.steps, run.status]);

  if (!spans || !spans.length) return null;
  const model = buildTrace(spans);
  const active = spans.find((s) => s.span_id === selected) ?? null;
  const traceId = spans[0]?.trace_id ?? '';

  return (
    <div style={{ marginBottom: 'var(--space-5)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <span style={heading}>Trace</span>
        <span style={mono}>
          {model.rows.length} spans · {ms(model.spanMs)} · {traceId.slice(0, 12)}
        </span>
      </div>
      <div className="traj-timeline">
        {model.rows.map((row) => (
          <div key={row.span.span_id} className="traj-lane traj-span-lane">
            <span
              className="traj-span-name"
              title={row.span.name}
              style={{
                ...mono,
                paddingLeft: `calc(${row.depth} * var(--space-4))`,
                color: row.failed ? 'var(--danger)' : undefined,
              }}
            >
              {row.orphan ? '↳ ' : ''}
              {row.span.name || row.span.span_id}
            </span>
            <div className="traj-track">
              <button
                className="traj-bar"
                title={`${row.span.name} — ${row.durationMs == null ? 'instant' : ms(row.durationMs)}`}
                onClick={() => setSelected(selected === row.span.span_id ? null : row.span.span_id)}
                style={{
                  left: `${row.left * 100}%`,
                  width: `${row.width * 100}%`,
                  background: tone(row),
                  opacity: row.durationMs == null ? 0.55 : 1,
                  outline: selected === row.span.span_id ? `2px solid ${tone(row)}` : 'none',
                }}
              >
                <span className="traj-bar-label">{ms(row.durationMs)}</span>
              </button>
            </div>
          </div>
        ))}
      </div>
      {active ? <SpanDetail span={active} /> : null}
    </div>
  );
}
