import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import type { LeaseSnapshot, PeerCapability, PeerMetrics } from './api';
import { endLease, getLeases } from './api';
import {
  getNetworkState,
  initNetwork,
  requestMetrics,
  requestPeers,
  subscribeLeases,
  subscribeMetrics,
  subscribeNetwork,
} from './ws';

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

/** Time left on a lease, in the coarsest unit that is still honest. */
function fmtExpiry(expiresAt: number): string {
  const left = expiresAt * 1000 - Date.now();
  if (left <= 0) return 'expired';
  const mins = Math.floor(left / 60000);
  return mins >= 1 ? `${mins}m` : `${Math.floor(left / 1000)}s`;
}

/**
 * A capability rendered as a chip: the id, plus the one or two attributes that
 * make it actionable.
 *
 * Per-capability rather than a generic key dump, because the useful summary
 * differs: what you want to know about `inference` is the VRAM and which model is
 * hot, and about `hassault` it is whether a match is open. An unknown capability
 * still renders — it just shows its id, which is what the flat `capabilities`
 * list gave before.
 */
function chipLabel(cap: PeerCapability): string {
  const a = cap.attrs;
  if (cap.id === 'inference') {
    const parts: string[] = [];
    const vram = typeof a.vramMb === 'number' ? a.vramMb : null;
    if (vram) parts.push(`${(vram / 1024).toFixed(1)} GB`);
    else if (typeof a.accelerator === 'string') parts.push(a.accelerator);
    if (typeof a.serving === 'string' && a.serving) parts.push(`serving ${a.serving}`);
    else if (Array.isArray(a.models)) parts.push(`${a.models.length} models`);
    return parts.length ? `inference · ${parts.join(' · ')}` : 'inference';
  }
  if (cap.id === 'extras' && Array.isArray(a.installed)) {
    return `extras · ${(a.installed as unknown[]).join(', ')}`;
  }
  if (cap.id === 'hassault' && typeof a.openMatches === 'number') {
    return a.openMatches > 0 ? `hassault · ${a.openMatches} open` : 'hassault';
  }
  return cap.id;
}

const TH: React.CSSProperties = {
  textAlign: 'left',
  padding: '0.25rem 0.5rem',
  color: 'var(--text-dim)',
  fontWeight: 500,
  borderBottom: '1px solid var(--border)',
};
const TD: React.CSSProperties = { padding: '0.25rem 0.5rem', whiteSpace: 'nowrap' };
const MONO: React.CSSProperties = {
  ...TD,
  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
  color: 'var(--text-secondary, var(--text-dim))',
};
const HEADING: React.CSSProperties = {
  margin: '1.25rem 0 0.5rem',
  fontSize: '0.7rem',
  fontWeight: 600,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-dim)',
};
const CHIP: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.05rem 0.4rem',
  marginRight: '0.3rem',
  borderRadius: 4,
  border: '1px solid var(--border)',
  background: 'var(--surface-2, transparent)',
  fontSize: '0.7rem',
  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
  color: 'var(--text-secondary, var(--text-dim))',
};

function useNetworkState() {
  return useSyncExternalStore(subscribeNetwork, getNetworkState, getNetworkState);
}

/**
 * Live link health for the distributed peer fabric: per-peer transport, round-trip
 * time, throughput, what each peer offers, and every compute lease in either
 * direction. Metrics come from the backend Peer Monitor heartbeat and leases from
 * `lease_update`, both on the `/ws` `network` channel. See docs/modules/network.mdx.
 */
export function PeerMonitor() {
  const [metrics, setMetrics] = useState<PeerMetrics[]>([]);
  const [leases, setLeases] = useState<LeaseSnapshot | null>(null);
  const [ending, setEnding] = useState<string | null>(null);
  const { peers } = useNetworkState();

  useEffect(() => {
    initNetwork();
    const unsubMetrics = subscribeMetrics(setMetrics);
    // Pushed for every later change; fetched once here because a lease granted
    // before this pane opened produces no event of its own.
    const unsubLeases = subscribeLeases(setLeases);
    requestMetrics();
    requestPeers();
    void getLeases()
      .then(setLeases)
      .catch(() => undefined);
    return () => {
      unsubMetrics();
      unsubLeases();
    };
  }, []);

  const revoke = useCallback(async (leaseId: string) => {
    setEnding(leaseId);
    try {
      await endLease(leaseId);
      // No optimistic removal: the backend broadcasts the new snapshot, and a row
      // that vanished locally while the lease survived would be a lie in the one
      // view whose job is saying what is currently granted.
      setLeases(await getLeases());
    } catch {
      // Left as-is; the next push corrects it.
    } finally {
      setEnding(null);
    }
  }, []);

  const granted = leases?.granted ?? [];
  const borrowed = leases?.borrowed ?? [];

  return (
    <div style={{ padding: '1rem', height: '100%', overflow: 'auto' }}>
      <h3 style={{ margin: '0 0 0.5rem' }}>Peer Monitor</h3>
      {metrics.length === 0 ? (
        <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
          No peers connected. Link a node from the Peers widget to see live metrics.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr>
                <th style={TH}>Peer</th>
                <th style={TH}>Transport</th>
                <th style={TH}>RTT</th>
                <th style={TH}>In</th>
                <th style={TH}>Out</th>
                <th style={TH}>Msgs</th>
                <th style={TH}>Offers</th>
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
                  <td style={MONO}>{fmtRtt(m.rtt_ms)}</td>
                  <td style={MONO}>{fmtBytes(m.bytes_in)}</td>
                  <td style={MONO}>{fmtBytes(m.bytes_out)}</td>
                  <td style={MONO}>
                    {m.msgs_in} / {m.msgs_out}
                  </td>
                  <td style={{ ...TD, whiteSpace: 'normal' }}>
                    {(peers[m.node_id]?.caps ?? []).map((cap) => (
                      <span key={cap.id} style={CHIP} title={JSON.stringify(cap.attrs)}>
                        {chipLabel(cap)}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 style={HEADING}>Leases</h4>
      {granted.length === 0 && borrowed.length === 0 ? (
        <p style={{ color: 'var(--text-dim)', fontSize: '0.8rem', margin: 0 }}>
          {leases?.lending && !leases.lending.enabled
            ? 'No leases. This node does not lend compute (network.allowComputeLending is off).'
            : 'No compute leases in either direction.'}
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
            <thead>
              <tr>
                <th style={TH}>Direction</th>
                <th style={TH}>Peer</th>
                <th style={TH}>Service</th>
                <th style={TH}>Model</th>
                <th style={TH}>Expires</th>
                <th style={TH}>Bytes</th>
                <th style={TH} />
              </tr>
            </thead>
            <tbody>
              {granted.map((l) => (
                <tr key={l.leaseId}>
                  <td style={TD}>lent</td>
                  <td style={MONO}>{l.holder}</td>
                  <td style={TD}>{l.service}</td>
                  <td style={MONO}>{l.model ?? '—'}</td>
                  <td style={MONO}>{fmtExpiry(l.expiresAt)}</td>
                  <td style={MONO}>{fmtBytes(l.bytesUsed)}</td>
                  <td style={TD}>
                    <button
                      type="button"
                      disabled={ending === l.leaseId}
                      onClick={() => void revoke(l.leaseId)}
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
              {borrowed.map((l) => (
                <tr key={l.leaseId}>
                  <td style={TD}>borrowed</td>
                  <td style={MONO}>{l.nodeId}</td>
                  <td style={TD}>{l.service}</td>
                  <td style={MONO}>{l.model ?? '—'}</td>
                  <td style={MONO}>{fmtExpiry(l.expiresAt)}</td>
                  <td style={MONO}>{l.endpoint || '—'}</td>
                  <td style={TD}>
                    <button
                      type="button"
                      disabled={ending === l.leaseId}
                      onClick={() => void revoke(l.leaseId)}
                    >
                      Release
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
