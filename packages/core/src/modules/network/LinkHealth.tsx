import { useEffect, useState, useSyncExternalStore } from 'react';

import type { BenchResult } from './api';
import { runBench } from './api';
import { getNetworkState, initNetwork, requestPeers, subscribeNetwork } from './ws';

/**
 * **Link health** — measure the fabric rather than guess at it.
 *
 * The Peer Monitor's RTT is one sample from a heartbeat; this runs the backend
 * bench and reports the distribution. Everything here is percentiles and **never a
 * mean**: the distribution goes bimodal the moment something is blocking the
 * pump, and a mean is exactly the statistic that hides it.
 *
 * `local` mode needs no peer. It measures this machine's own sign/serialize/verify
 * floor, which is the only way to tell "that peer is far away" from "this box is
 * slow" — and the floor is real: Ed25519 hashes the whole message, so signing is
 * linear in payload at roughly 525 MB/s per core.
 */
export function LinkHealth() {
  const { peers } = useNetworkState();
  const connected = Object.values(peers).filter((p) => p.status === 'connected');
  const [node, setNode] = useState('');
  const [mode, setMode] = useState<'echo' | 'sweep' | 'local'>('echo');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<BenchResult[]>([]);

  useEffect(() => {
    initNetwork();
    requestPeers();
  }, []);
  useEffect(() => {
    if (!node && connected.length > 0) setNode(connected[0].node_id);
  }, [node, connected]);

  const measure = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await runBench({ node_id: node, mode, count: mode === 'sweep' ? 40 : 100 });
      setResults(res.results);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'bench failed');
    } finally {
      setRunning(false);
    }
  };

  const needsPeer = mode !== 'local';

  return (
    <div style={{ padding: '0.5rem 0', fontSize: '0.8rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as 'echo' | 'sweep' | 'local')}
          style={{ height: 30, borderRadius: 6 }}
        >
          <option value="echo">Echo (round trips)</option>
          <option value="sweep">Sweep (64 B → 1 MB)</option>
          <option value="local">Local (this machine only)</option>
        </select>
        <select
          value={node}
          disabled={!needsPeer}
          onChange={(e) => setNode(e.target.value)}
          style={{ flex: 1, minWidth: 140, height: 30, borderRadius: 6 }}
        >
          {connected.length === 0 && <option value="">No connected peers</option>}
          {connected.map((p) => (
            <option key={p.node_id} value={p.node_id}>
              {p.node_name}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={running || (needsPeer && !node)}
          onClick={() => void measure()}
          style={{ height: 30, borderRadius: 6 }}
        >
          {running ? 'Measuring…' : 'Measure'}
        </button>
      </div>

      {error ? <p style={{ color: '#f85149', margin: '0.5rem 0 0' }}>{error}</p> : null}
      {!error && results.length === 0 ? (
        <p style={{ color: 'var(--text-dim)', margin: '0.5rem 0 0' }}>
          A sweep says whether bulk work belongs on this link at all: every byte is signed, so cost
          grows with payload.
        </p>
      ) : null}

      {results.map((r, i) => (
        <div key={i} style={{ marginTop: '0.75rem' }}>
          <div style={{ ...MONO_LINE }}>
            {r.mode} · {r.transport} · {fmtBytes(r.payloadBytes)} ·{' '}
            {r.errors > 0 ? `${r.errors} errors · ` : ''}
            {r.rtt ? `${r.rtt.p50Ms.toFixed(3)} ms p50` : 'no round trips'}
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '0.25rem' }}>
            <thead>
              <tr>
                <th style={TH}>Phase</th>
                <th style={TH}>p50</th>
                <th style={TH}>p90</th>
                <th style={TH}>p99</th>
                <th style={TH}>max</th>
              </tr>
            </thead>
            <tbody>
              {(r.rtt ? [r.rtt, ...r.phases] : r.phases).map((p) => (
                <tr key={p.phase}>
                  <td style={TD}>{p.phase}</td>
                  <td style={MONO}>{p.p50Ms.toFixed(3)}</td>
                  <td style={MONO}>{p.p90Ms.toFixed(3)}</td>
                  <td style={MONO}>{p.p99Ms.toFixed(3)}</td>
                  <td style={MONO}>{p.maxMs.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {r.wireResidualMs !== null ? (
            <p style={{ color: 'var(--text-dim)', margin: '0.25rem 0 0', fontSize: '0.7rem' }}>
              {r.wireResidualMs.toFixed(3)} ms unaccounted for — the wire plus the peer&apos;s own
              sign, serialize and verify. Not a measured wire time: the two clocks are not synced.
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function useNetworkState() {
  return useSyncExternalStore(subscribeNetwork, getNetworkState, getNetworkState);
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const TH: React.CSSProperties = {
  textAlign: 'left',
  padding: '0.15rem 0.4rem',
  color: 'var(--text-dim)',
  fontWeight: 500,
  borderBottom: '1px solid var(--border)',
  fontSize: '0.7rem',
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
};
const TD: React.CSSProperties = { padding: '0.15rem 0.4rem' };
const MONO: React.CSSProperties = {
  ...TD,
  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
  textAlign: 'right',
  color: 'var(--text-secondary, var(--text-dim))',
};
const MONO_LINE: React.CSSProperties = {
  fontFamily: 'var(--font-mono, ui-monospace, monospace)',
  fontSize: '0.7rem',
  color: 'var(--text-dim)',
};
