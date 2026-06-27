import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import { useAgentContext } from '../../agent-context';
import { createInvite, type PeerInfo } from './api';
import {
  connectViaChannel,
  getNetworkState,
  initNetwork,
  redeemViaChannel,
  requestPeers,
  subscribeNetwork,
} from './ws';

const STATUS_COLOR: Record<PeerInfo['status'], string> = {
  connected: '#3fb950',
  connecting: '#d29922',
  disconnected: 'var(--text-dim)',
  blocked: '#f85149',
};

function useNetworkState() {
  return useSyncExternalStore(subscribeNetwork, getNetworkState, getNetworkState);
}

/**
 * Presence for the distributed peer fabric: this node's identity plus the other
 * users' nodes currently connected. Connect by address or pair via an invite link;
 * live updates arrive over the `/ws` `network` channel.
 */
export function PeersWidget() {
  const { self, peers } = useNetworkState();
  const peerList = Object.values(peers);
  const [address, setAddress] = useState('');
  const [invite, setInvite] = useState('');
  const [minted, setMinted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    initNetwork();
    requestPeers();
  }, []);

  // Let the local agent see who's reachable, so it can fill ask_peer(peerId).
  useAgentContext(() => ({
    self: self ? { nodeId: self.node_id, name: self.node_name } : null,
    peerCount: peerList.length,
    peers: peerList.map((p) => ({
      nodeId: p.node_id,
      name: p.node_name,
      status: p.status,
      capabilities: p.capabilities,
    })),
  }));

  const mintInvite = useCallback(async () => {
    setBusy(true);
    try {
      const res = await createInvite();
      setMinted(res.invite);
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div
      className="network-widget"
      style={{
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <section>
        <h3 style={{ margin: '0 0 0.25rem' }}>This node</h3>
        {self ? (
          <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>
            <strong style={{ color: 'var(--text)' }}>{self.node_name}</strong>
            <code style={{ marginLeft: '0.5rem' }}>{self.node_id}</code>
          </div>
        ) : (
          <p style={{ color: 'var(--text-dim)' }}>Starting peer fabric…</p>
        )}
      </section>

      <section>
        <h3 style={{ margin: '0 0 0.5rem' }}>Peers ({peerList.length})</h3>
        {peerList.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>No peers connected.</p>
        ) : (
          <ul
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: '0.4rem',
            }}
          >
            {peerList.map((p) => (
              <li
                key={p.node_id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  fontSize: '0.85rem',
                }}
              >
                <span
                  aria-label={p.status}
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: STATUS_COLOR[p.status],
                    flexShrink: 0,
                  }}
                />
                <strong style={{ color: 'var(--text)' }}>{p.node_name}</strong>
                <span style={{ color: 'var(--text-dim)' }}>{p.transport}</span>
                <code style={{ color: 'var(--text-dim)', marginLeft: 'auto' }}>{p.node_id}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <h3 style={{ margin: 0 }}>Connect</h3>
        <form
          style={{ display: 'flex', gap: '0.5rem' }}
          onSubmit={(e) => {
            e.preventDefault();
            if (address.trim()) {
              connectViaChannel(address.trim());
              setAddress('');
            }
          }}
        >
          <input
            value={address}
            placeholder="ws://host:8000/peer-ws"
            spellCheck={false}
            onChange={(e) => setAddress(e.target.value)}
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={!address.trim()}>
            Dial
          </button>
        </form>

        <form
          style={{ display: 'flex', gap: '0.5rem' }}
          onSubmit={(e) => {
            e.preventDefault();
            if (invite.trim()) {
              redeemViaChannel(invite.trim());
              setInvite('');
            }
          }}
        >
          <input
            value={invite}
            placeholder="paste an invite link"
            spellCheck={false}
            onChange={(e) => setInvite(e.target.value)}
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={!invite.trim()}>
            Pair
          </button>
        </form>

        <div>
          <button onClick={() => void mintInvite()} disabled={busy}>
            {busy ? 'Minting…' : 'Generate invite'}
          </button>
          {minted && (
            <textarea
              readOnly
              value={minted}
              onFocus={(e) => e.currentTarget.select()}
              style={{
                width: '100%',
                marginTop: '0.5rem',
                fontSize: '0.75rem',
                fontFamily: 'monospace',
              }}
              rows={3}
            />
          )}
        </div>
      </section>
    </div>
  );
}
