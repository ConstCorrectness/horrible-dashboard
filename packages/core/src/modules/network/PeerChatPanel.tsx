import { useEffect, useRef, useState } from 'react';

import { chatClose, chatOpen, chatSend, subscribeChat, type ChatMessage } from './peerchat';
import { getNetworkState, initNetwork, requestPeers, subscribeNetwork } from './ws';
import { useSyncExternalStore } from 'react';

function useNetworkState() {
  return useSyncExternalStore(subscribeNetwork, getNetworkState, getNetworkState);
}

/**
 * Direct 1:1 chat with a connected peer. Pick a peer, and messages relay over the
 * signed peer wire (mirrored to your own tabs, fanned out to the peer's). The
 * append-only conversational counterpart to a shared `collab` pane. See
 * docs/modules/network.mdx (Peer Chat).
 */
export function PeerChatPanel() {
  const { peers } = useNetworkState();
  const peerList = Object.values(peers);
  const [active, setActive] = useState<string>('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    initNetwork();
    requestPeers();
  }, []);

  // Default to the first peer once one connects.
  useEffect(() => {
    if (!active && peerList.length > 0) setActive(peerList[0].node_id);
  }, [active, peerList]);

  // Subscribe to chat events and (re)open the active conversation.
  useEffect(() => {
    if (!active) return;
    setMessages([]);
    setError(null);
    const unsub = subscribeChat((event) => {
      if (event.nodeId !== active) return;
      if (event.kind === 'history') setMessages(event.messages ?? []);
      else if (event.kind === 'message' && event.message)
        setMessages((prev) => [...prev, event.message as ChatMessage]);
      else if (event.kind === 'error') setError(event.error ?? 'send failed');
    });
    chatOpen(active);
    return () => {
      chatClose(active);
      unsub();
    };
  }, [active]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const send = () => {
    const text = draft.trim();
    if (!text || !active) return;
    chatSend(active, text);
    setDraft('');
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
        <span style={{ color: 'var(--text-dim)' }}>Peer</span>
        <select value={active} onChange={(e) => setActive(e.target.value)} style={{ flex: 1 }}>
          {peerList.length === 0 && <option value="">No peers connected</option>}
          {peerList.map((p) => (
            <option key={p.node_id} value={p.node_id}>
              {p.node_name}
            </option>
          ))}
        </select>
      </div>

      <div ref={scrollRef} style={{ flex: 1, overflow: 'auto', padding: '0.5rem' }}>
        {messages.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
            {active ? 'No messages yet — say hello.' : 'Connect a peer to start chatting.'}
          </p>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              style={{
                marginBottom: '0.4rem',
                textAlign: m.direction === 'out' ? 'right' : 'left',
              }}
            >
              <span
                style={{
                  display: 'inline-block',
                  padding: '0.3rem 0.6rem',
                  borderRadius: '0.6rem',
                  fontSize: '0.85rem',
                  maxWidth: '80%',
                  background:
                    m.direction === 'out' ? 'var(--accent, #2f6fed)' : 'var(--surface, #2a2a2a)',
                  color: m.direction === 'out' ? '#fff' : 'var(--text)',
                }}
                title={new Date(m.ts * 1000).toLocaleTimeString()}
              >
                {m.text}
              </span>
            </div>
          ))
        )}
      </div>

      {error && (
        <div style={{ color: '#f85149', fontSize: '0.75rem', padding: '0 0.5rem' }}>{error}</div>
      )}

      <form
        style={{
          display: 'flex',
          gap: '0.5rem',
          padding: '0.5rem',
          borderTop: '1px solid var(--border)',
        }}
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={draft}
          placeholder={active ? 'Message…' : 'No peer selected'}
          disabled={!active}
          onChange={(e) => setDraft(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={!active || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
