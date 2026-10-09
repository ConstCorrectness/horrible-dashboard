/**
 * The other direction: where this node's own spans are exported to.
 *
 * One row per destination (`GET /api/otel/export`) — the generic OTLP collector,
 * Opik — with the outcome of the last export. That outcome is the reason this card
 * exists: the SDK's exporter only logs a failure, so a revoked key or a stopped
 * collector used to look exactly like a node that had nothing to send.
 *
 * Content is shown per destination because it is decided per destination: Opik can
 * receive prompts while the generic collector and the local store do not.
 */
import { Chip } from '../../../Primitives';
import type { ExportDestination } from '../otel-api';
import { ago, card, heading, mono } from './common';

function outcome(d: ExportDestination) {
  if (d.failing) {
    return (
      <Chip kind="fail" title={d.last_error}>
        last export failed
      </Chip>
    );
  }
  if (d.last_ok_at) return <Chip kind="ok">exported {ago(d.last_ok_at)}</Chip>;
  return <Chip kind="idle">nothing sent yet</Chip>;
}

export function ExportCard({ destinations }: { destinations: ExportDestination[] | null }) {
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <span style={heading}>Exporting to</span>
      </div>
      {destinations === null ? null : destinations.length === 0 ? (
        <div style={{ ...mono, marginTop: 'var(--space-3)' }}>
          Nowhere yet — the node keeps its spans to itself. Connect Opik, or any OTLP collector,
          from Home → Integrations.
        </div>
      ) : (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-3)',
            marginTop: 'var(--space-3)',
          }}
        >
          {destinations.map((d) => (
            <div
              key={d.name}
              style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1fr) auto',
                alignItems: 'center',
                gap: 'var(--space-1) var(--space-3)',
                paddingTop: 'var(--space-3)',
                borderTop: '1px solid var(--border)',
              }}
            >
              <div style={{ minWidth: 0 }}>
                <span
                  style={{
                    fontWeight: 'var(--fw-bold)',
                    letterSpacing: 'var(--tracking-display)',
                    textTransform: 'uppercase',
                    fontSize: 'var(--fs-label)',
                  }}
                >
                  {d.label || d.name}
                </span>
                <span style={{ ...mono, marginLeft: 'var(--space-3)' }}>
                  {d.host}
                  {d.detail.project ? ` · ${d.detail.project}` : ''}
                </span>
              </div>
              {outcome(d)}
              <div style={{ ...mono, gridColumn: '1 / -1' }}>
                {d.include_content ? 'prompts and tool data included' : 'no prompts or tool data'}
                {' · '}
                {d.spans_ok.toLocaleString()} spans sent
                {d.spans_failed ? ` · ${d.spans_failed.toLocaleString()} failed` : ''}
              </div>
              {d.failing && d.last_error ? (
                <div role="alert" style={{ ...mono, gridColumn: '1 / -1', color: 'var(--danger)' }}>
                  {d.last_error}
                  {d.last_error_at ? ` (${ago(d.last_error_at)})` : ''}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
