import { useEffect, useState, useSyncExternalStore } from 'react';

import { askPeer } from './api';
import { getNetworkState, initNetwork, requestPeers, subscribeNetwork } from './ws';

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

  useEffect(() => {
    initNetwork();
    requestPeers();
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
