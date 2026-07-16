/**
 * In-pane network inspector: what this page is talking to, right now.
 *
 * Two halves, because they answer different questions and come from different places:
 *
 * - **Open connections** — requests currently in flight, pushed live on the `browser`
 *   channel (`connections`). These exist *only* while unresolved, so they can't come
 *   from the I/O ring buffer, which records on completion. This is the "what is this
 *   page doing right now / why is it hanging" view.
 * - **Completed** — finished requests read from the shared telemetry store, the same
 *   events the observability panel shows, filtered to `browser`. Includes requests the
 *   egress guard blocked, which is the whole point: a silently aborted request is
 *   exactly what you'd otherwise never see.
 *
 * The full inspector (headers, bodies) lives in the observability panel; this strip is
 * the glanceable version that sits next to the page it describes.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { telemetryStore, type IoEvent } from '../../../telemetry';
import { subscribeConnections, type BrowserConnection } from '../session';

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** Trailing path segment — enough to recognize a request without the full URL. */
function leaf(url: string): string {
  try {
    const { pathname, search } = new URL(url);
    const name = pathname.split('/').filter(Boolean).pop() ?? '/';
    return name + (search ? '?…' : '');
  } catch {
    return url;
  }
}

const cell: React.CSSProperties = {
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
};

export function NetworkStrip({ onClose }: { onClose: () => void }) {
  const [conns, setConns] = useState<BrowserConnection[]>([]);
  const events = useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);

  useEffect(() => subscribeConnections(setConns), []);

  const done = useMemo(
    () =>
      events
        .filter((e: IoEvent) => e.source === 'browser')
        .slice(-100)
        .reverse(),
    [events],
  );
  const blocked = done.filter((e) => e.verdict === 'blocked').length;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: 320,
        flex: '0 0 auto',
        borderLeft: '1px solid var(--border)',
        fontSize: '0.72rem',
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.3rem 0.45rem',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <strong style={{ fontSize: '0.78rem' }}>Network</strong>
        <span style={{ color: 'var(--text-dim)' }}>
          {conns.length} open · {done.length} done
          {blocked > 0 && <span className="io-status-blocked"> · {blocked} blocked</span>}
        </span>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer' }}
        >
          ×
        </button>
      </div>

      <div style={{ overflowY: 'auto', minHeight: 0 }}>
        {conns.length > 0 && (
          <div style={{ padding: '0.25rem 0.45rem', color: 'var(--text-dim)' }}>In flight</div>
        )}
        {conns.map((c) => (
          <div
            key={c.id}
            style={{ display: 'flex', gap: '0.4rem', padding: '0.15rem 0.45rem' }}
            title={c.url}
          >
            {/* A pulsing dot is the honest signal here: elapsedMs is a snapshot from
                the last push, not a live timer, so animating it would be a lie. */}
            <span className="io-status-blocked">●</span>
            <span style={{ ...cell, flex: 1 }}>{leaf(c.url)}</span>
            <span style={{ color: 'var(--text-dim)' }}>{Math.round(c.elapsedMs)}ms</span>
          </div>
        ))}

        {done.length > 0 && (
          <div style={{ padding: '0.25rem 0.45rem', color: 'var(--text-dim)' }}>Completed</div>
        )}
        {done.map((e) => (
          <div
            key={String(e.id)}
            style={{ display: 'flex', gap: '0.4rem', padding: '0.15rem 0.45rem' }}
            title={`${e.method} ${e.target}${e.error ? ` — ${e.error}` : ''}`}
          >
            <span
              style={{ width: 58, ...cell, color: 'var(--text-dim)' }}
              title={e.resource_type ?? ''}
            >
              {e.resource_type ?? ''}
            </span>
            <span style={{ ...cell, flex: 1 }}>{leaf(e.target)}</span>
            <span style={{ width: 62, ...cell, color: 'var(--text-dim)' }}>{host(e.target)}</span>
            <span
              className={
                e.verdict === 'blocked'
                  ? 'io-status-blocked'
                  : e.error || (e.status ?? 0) >= 400
                    ? 'io-status-bad'
                    : 'io-status-ok'
              }
            >
              {e.verdict === 'blocked' ? '⃠' : e.error ? '!' : (e.status ?? '')}
            </span>
          </div>
        ))}

        {conns.length === 0 && done.length === 0 && (
          <div className="dashboard-hint" style={{ padding: '0.45rem' }}>
            No requests yet. Navigate and every request this page makes appears here.
          </div>
        )}
      </div>
    </div>
  );
}
