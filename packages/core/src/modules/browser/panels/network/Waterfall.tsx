/**
 * Where the time actually went, per request.
 *
 * The single most educational view in the app: a URL bar makes fetching a page look
 * atomic, and this shows that before one byte of HTML moved there was a DNS lookup,
 * a TCP handshake, a TLS negotiation, and then a wait while the server thought about
 * it. Seeing 180ms of DNS and 90ms of TLS in front of a 40ms download explains more
 * about the web than any amount of prose.
 *
 * Phases that didn't happen are drawn as nothing rather than as zero-width bars. A
 * connection reused from the pool has no DNS or TLS phase at all, and that absence
 * is information — it's *why* the second request to a host is faster.
 */
import { useMemo, useState } from 'react';

import { NET_PHASES, type IoEvent, type NetPhase } from '../../../../telemetry';

const PHASE_LABEL: Record<NetPhase, string> = {
  dns: 'DNS lookup',
  connect: 'TCP connect',
  tls: 'TLS handshake',
  send: 'Request sent',
  wait: 'Server thinking (TTFB)',
};

/**
 * Distinct hues per phase. Chosen so the "before any data moved" phases (dns,
 * connect, tls) read as one warm group and the server's own time reads separately —
 * the point being made is how much happens *before* the response starts.
 */
const PHASE_COLOR: Record<NetPhase, string> = {
  dns: '#c084fc',
  connect: '#fb923c',
  tls: '#facc15',
  send: '#38bdf8',
  wait: '#4ade80',
};

function total(event: IoEvent): number {
  const timing = event.timing;
  if (!timing) return 0;
  return NET_PHASES.reduce((sum, phase) => sum + (timing[phase] ?? 0), 0);
}

function leaf(url: string): string {
  try {
    const { pathname, host } = new URL(url);
    const name = pathname.split('/').filter(Boolean).pop();
    return name || host;
  } catch {
    return url;
  }
}

export function Waterfall({ events }: { events: IoEvent[] }) {
  const [hovered, setHovered] = useState<string | null>(null);

  const rows = useMemo(() => events.filter((e) => e.timing && total(e) > 0).slice(0, 60), [events]);
  // A shared axis across rows is what makes the bars comparable; scaling each row
  // to its own width would make a 4ms cache hit look like a 900ms fetch.
  const widest = useMemo(() => Math.max(1, ...rows.map(total)), [rows]);

  if (!rows.length) {
    return (
      <div className="dashboard-hint" style={{ padding: '0.6rem' }}>
        No timing captured yet. Navigate in the browser pane — each request records its DNS, TCP,
        TLS and server-wait phases here.
        <br />
        <br />
        Nothing appears in iframe mode: this comes from the embedded Chromium, which needs{' '}
        <code>HORRIBLE_ENABLE_SERVER_BROWSER=1</code>.
      </div>
    );
  }

  return (
    <div style={{ padding: '0.4rem', fontSize: '0.75rem' }}>
      <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '0.4rem', flexWrap: 'wrap' }}>
        {NET_PHASES.map((phase) => (
          <span key={phase} style={{ display: 'flex', alignItems: 'center', gap: '0.2rem' }}>
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: 2,
                background: PHASE_COLOR[phase],
                display: 'inline-block',
              }}
            />
            <span style={{ color: 'var(--text-dim)' }}>{PHASE_LABEL[phase]}</span>
          </span>
        ))}
      </div>

      {rows.map((event) => {
        const key = `${event.id}`;
        const timing = event.timing ?? {};
        const sum = total(event);
        return (
          <div
            key={key}
            onMouseEnter={() => setHovered(key)}
            onMouseLeave={() => setHovered(null)}
            style={{ marginBottom: '0.3rem' }}
          >
            <div style={{ display: 'flex', gap: '0.4rem', color: 'var(--text-dim)' }}>
              <span
                style={{
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={event.target}
              >
                {leaf(event.target)}
              </span>
              {event.http_protocol && <span>{event.http_protocol}</span>}
              <span>{Math.round(sum)}ms</span>
            </div>
            <div
              style={{
                display: 'flex',
                height: 10,
                borderRadius: 2,
                overflow: 'hidden',
                background: 'var(--bg-hover, rgba(128,128,128,0.12))',
                width: `${(sum / widest) * 100}%`,
                minWidth: 2,
              }}
            >
              {NET_PHASES.map((phase) => {
                const ms = timing[phase];
                if (!ms) return null;
                return (
                  <div
                    key={phase}
                    title={`${PHASE_LABEL[phase]}: ${ms}ms`}
                    style={{
                      width: `${(ms / sum) * 100}%`,
                      background: PHASE_COLOR[phase],
                    }}
                  />
                );
              })}
            </div>
            {hovered === key && (
              <div style={{ color: 'var(--text-dim)', paddingLeft: '0.2rem' }}>
                {NET_PHASES.filter((p) => timing[p]).map((p) => (
                  <span key={p} style={{ marginRight: '0.6rem' }}>
                    {p} {timing[p]}ms
                  </span>
                ))}
                {event.remote_ip && <span>→ {event.remote_ip}</span>}
                {event.from_cache && <span> · served from cache (no network)</span>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
