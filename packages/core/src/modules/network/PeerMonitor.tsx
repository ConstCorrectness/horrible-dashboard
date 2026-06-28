import { useEffect, useState } from 'react';

import type { PeerMetrics } from './api';
import { initNetwork, requestMetrics, subscribeMetrics } from './ws';

const STATUS_COLOR: Record<PeerMetrics['status'], string> = {
  connected: '#3fb950',
  connecting: '#d29922',
  disconnected: 'var(--text-dim)',
  blocked: '#f85149',
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtRtt(rtt: number | null): string {
  return rtt === null || rtt === undefined ? '—' : `${rtt} ms`;
}

const TH: React.CSSProperties = {
  textAlign: 'left',
  padding: '0.25rem 0.5rem',
  color: 'var(--text-dim)',
  fontWeight: 500,
  borderBottom: '1px solid var(--border)',
};
const TD: React.CSSProperties = { padding: '0.25rem 0.5rem', whiteSpace: 'nowrap' };

/**
 * Live link health for the distributed peer fabric: per-peer transport, round-trip
 * time, and throughput (bytes/messages in and out), refreshed by the backend Peer
 * Monitor heartbeat over the `/ws` `network` channel. See docs/modules/network.mdx.
 */
export function PeerMonitor() {
  const [metrics, setMetrics] = useState<PeerMetrics[]>([]);

  useEffect(() => {
    initNetwork();
    const unsub = subscribeMetrics(setMetrics);
    requestMetrics();
    return unsub;
  }, []);

  return (
    <div style={{ padding: '1rem', height: '100%', overflow: 'auto' }}>
      <h3 style={{ margin: '0 0 0.5rem' }}>Peer Monitor</h3>
      {metrics.length === 0 ? (
        <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
          No peers connected. Link a node from the Peers widget to see live metrics.
        </p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr>
              <th style={TH}>Peer</th>
              <th style={TH}>Transport</th>
              <th style={TH}>RTT</th>
              <th style={TH}>In</th>
              <th style={TH}>Out</th>
              <th style={TH}>Msgs</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.node_id}>
                <td style={TD}>
                  <span
                    aria-label={m.status}
                    style={{
                      display: 'inline-block',
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: STATUS_COLOR[m.status],
                      marginRight: '0.4rem',
                    }}
                  />
                  {m.node_name}
                </td>
                <td style={TD}>{m.transport}</td>
                <td style={TD}>{fmtRtt(m.rtt_ms)}</td>
                <td style={TD}>{fmtBytes(m.bytes_in)}</td>
                <td style={TD}>{fmtBytes(m.bytes_out)}</td>
                <td style={TD}>
                  {m.msgs_in} / {m.msgs_out}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
