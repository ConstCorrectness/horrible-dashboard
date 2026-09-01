import { useEffect, useState, useSyncExternalStore } from 'react';

import type { BorrowedLease } from './api';
import { askPeer, getLeases } from './api';
import {
  getNetworkState,
  initNetwork,
  requestPeers,
  subscribeLeases,
  subscribeNetwork,
} from './ws';

function useNetworkState() {
  return useSyncExternalStore(subscribeNetwork, getNetworkState, getNetworkState);
}

interface Exchange {
  peerName: string;
  prompt: string;
  answer?: string;
  error?: string;
  pending: boolean;
}

/**
 * Agent-to-agent relay: ask a connected peer's agent a question and read its
 * answer. The remote turn runs gated and read-only-by-default on the peer's node
 * (it must have `network.allowRemoteAgent` on), surfacing the cross-agent Q&A that
 * the backend `agent.ask_peer` tool drives. See docs/modules/agent-chat.mdx
 * (agent-to-agent) and docs/modules/network.mdx.
 */
export function AgentRelayPanel() {
  const { peers } = useNetworkState();
  const peerList = Object.values(peers).filter((p) => p.capabilities.includes('agent'));
  const [active, setActive] = useState('');
  const [prompt, setPrompt] = useState('');
  const [log, setLog] = useState<Exchange[]>([]);
  const [borrowed, setBorrowed] = useState<BorrowedLease[]>([]);

  useEffect(() => {
    initNetwork();
    requestPeers();
    // This is the pane about routing turns off-node, so it is where a borrowed
    // model belongs on screen: an agent whose provider is `peer` answers from
    // somebody else's GPU, and nothing else in the UI says so.
    const unsub = subscribeLeases((snap) => setBorrowed(snap.borrowed ?? []));
    void getLeases()
      .then((snap) => setBorrowed(snap.borrowed ?? []))
      .catch(() => undefined);
    return unsub;
  }, []);

  useEffect(() => {
    if (!active && peerList.length > 0) setActive(peerList[0].node_id);
  }, [active, peerList]);

  const ask = async () => {
    const text = prompt.trim();
    const peer = peers[active];
    if (!text || !peer) return;
    const entry: Exchange = { peerName: peer.node_name, prompt: text, pending: true };
    setLog((prev) => [...prev, entry]);
    setPrompt('');
    const result = await askPeer(active, text);
    setLog((prev) =>
      prev.map((e) =>
        e === entry
          ? {
              ...e,
              pending: false,
              answer: result.ok ? (result.answer ?? '') : undefined,
              error: result.ok ? undefined : (result.error ?? 'ask failed'),
            }
          : e,
      ),
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.4rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.8rem',
        }}
      >
        <span style={{ color: 'var(--text-dim)' }}>Ask</span>
        <select value={active} onChange={(e) => setActive(e.target.value)} style={{ flex: 1 }}>
          {peerList.length === 0 && <option value="">No agent-capable peers</option>}
          {peerList.map((p) => (
            <option key={p.node_id} value={p.node_id}>
              {p.node_name}&apos;s agent
            </option>
          ))}
        </select>
      </div>

      {borrowed.length > 0 ? (
        <div
          style={{
            padding: '0.3rem 0.5rem',
            borderBottom: '1px solid var(--border)',
            borderLeft: '2px solid var(--accent, #3B82F6)',
            fontSize: '0.75rem',
            fontFamily: 'var(--font-mono, ui-monospace, monospace)',
            color: 'var(--text-secondary, var(--text-dim))',
          }}
        >
          {borrowed.map((l) => (
            <div key={l.leaseId}>
              borrowing {l.service}
              {l.model ? ` (${l.model})` : ''} from {peers[l.nodeId]?.node_name ?? l.nodeId}
            </div>
          ))}
        </div>
      ) : null}

      <div style={{ flex: 1, overflow: 'auto', padding: '0.5rem' }}>
        {log.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
            Ask a peer&apos;s agent a question. Their node answers read-only; it never acts on your
            behalf.
          </p>
        ) : (
          log.map((e, i) => (
            <div key={i} style={{ marginBottom: '0.75rem', fontSize: '0.85rem' }}>
              <div style={{ color: 'var(--text-dim)' }}>
                → {e.peerName}: {e.prompt}
              </div>
              {e.pending ? (
                <div style={{ color: 'var(--text-dim)', fontStyle: 'italic' }}>thinking…</div>
              ) : e.error ? (
                <div style={{ color: '#f85149' }}>⚠ {e.error}</div>
              ) : (
                <div style={{ whiteSpace: 'pre-wrap' }}>{e.answer}</div>
              )}
            </div>
          ))
        )}
      </div>

      <form
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.5rem',
          borderTop: '1px solid var(--border)',
        }}
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <input
          value={prompt}
          placeholder={active ? 'Ask the peer agent…' : 'No agent peer selected'}
          disabled={!active}
          onChange={(e) => setPrompt(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={!active || !prompt.trim()}>
          Ask
        </button>
      </form>
    </div>
  );
}
