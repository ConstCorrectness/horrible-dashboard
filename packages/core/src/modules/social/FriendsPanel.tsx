import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

import { useAgentContext } from '../../agent-context';
import { chatOpen, chatSend } from '../network/peerchat';
import { addFriend, linkDevice, updateSelfProfile, type Friend } from './api';
import {
  blockViaChannel,
  getSocialState,
  initSocial,
  removeViaChannel,
  requestRoster,
  respondViaChannel,
  subscribeSocial,
} from './ws';

function useSocialState() {
  return useSyncExternalStore(subscribeSocial, getSocialState, getSocialState);
}

const DOT = (online: boolean) => ({
  width: 8,
  height: 8,
  borderRadius: '50%',
  background: online ? '#3fb950' : 'var(--text-dim)',
  flexShrink: 0,
});

/** A friend's first connected machine — the one to route a message or invite to. */
function liveNode(friend: Friend): string | null {
  return friend.devices.find((d) => d.online)?.node_id ?? null;
}

function FriendRow({ friend }: { friend: Friend }) {
  const [draft, setDraft] = useState('');
  const [composing, setComposing] = useState(false);
  const node = liveNode(friend);

  const send = useCallback(() => {
    if (!node || !draft.trim()) return;
    chatOpen(node);
    chatSend(node, draft.trim());
    setDraft('');
    setComposing(false);
  }, [node, draft]);

  return (
    <li
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.35rem',
        padding: '0.5rem 0',
        borderBottom: '1px solid var(--border, #2a2a2a)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem' }}>
        <span aria-label={friend.presence} style={DOT(friend.presence === 'online')} />
        <strong style={{ color: 'var(--text)' }}>{friend.display_name}</strong>
        {friend.is_self && (
          <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>(you)</span>
        )}
        <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
          {friend.devices.length} device{friend.devices.length === 1 ? '' : 's'}
        </span>

        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem' }}>
          {friend.status === 'pending_in' && (
            <>
              <button onClick={() => respondViaChannel(friend.person_id, true)}>Accept</button>
              <button onClick={() => respondViaChannel(friend.person_id, false)}>Decline</button>
            </>
          )}
          {friend.status === 'pending_out' && (
            <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>requested…</span>
          )}
          {friend.status === 'accepted' && !friend.is_self && (
            <button
              disabled={!node}
              title={node ? 'Send a message' : 'Offline'}
              onClick={() => setComposing((c) => !c)}
            >
              Message
            </button>
          )}
          {friend.status !== 'pending_in' && (
            <button onClick={() => removeViaChannel(friend.person_id)} title="Remove">
              ✕
            </button>
          )}
          {friend.status !== 'blocked' && !friend.is_self && (
            <button onClick={() => blockViaChannel(friend.person_id)} title="Block">
              ⊘
            </button>
          )}
        </span>
      </div>

      {/* Each machine listed under the person, so "my other computer" is dialable. */}
      {friend.devices.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', paddingLeft: '1rem' }}>
          {friend.devices.map((d) => (
            <span
              key={d.node_id}
              title={d.node_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.3rem',
                fontSize: '0.72rem',
                color: 'var(--text-dim)',
              }}
            >
              <span style={DOT(d.online)} />
              {d.label}
            </span>
          ))}
        </div>
      )}

      {composing && (
        <form
          style={{ display: 'flex', gap: '0.4rem', paddingLeft: '1rem' }}
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <input
            autoFocus
            value={draft}
            placeholder={`Message ${friend.display_name}…`}
            onChange={(e) => setDraft(e.target.value)}
            style={{ flex: 1, fontSize: '0.8rem' }}
          />
          <button type="submit" disabled={!draft.trim()}>
            Send
          </button>
        </form>
      )}
    </li>
  );
}

/**
 * The friends roster: who you know, which of their machines are online, and the
 * actions that follow from that (accept, message, remove).
 *
 * Friending is person-level — a friend with a desktop and a laptop is one row with
 * two devices, not two rows. Accepting grants those machines fabric trust, which is
 * what lets peer chat, shared panes, and agent-to-agent questions work between
 * friends without a second pairing step. See docs/modules/social.mdx.
 */
export function FriendsPanel() {
  const { roster } = useSocialState();
  const [code, setCode] = useState('');
  const [address, setAddress] = useState('');
  const [invite, setInvite] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    initSocial();
    requestRoster();
  }, []);

  const me = roster?.self_profile;
  const friends = roster?.friends ?? [];
  const pending = friends.filter((f) => f.status === 'pending_in');

  // Let the local agent see the roster, so it can resolve "ask Rob's agent" or
  // "message my laptop" to a concrete person and node.
  useAgentContext(() => ({
    self: me ? { personId: me.person_id, name: me.display_name } : null,
    friends: friends.map((f) => ({
      personId: f.person_id,
      name: f.display_name,
      status: f.status,
      presence: f.presence,
      isSelf: f.is_self,
      nodes: f.devices.map((d) => ({ nodeId: d.node_id, label: d.label, online: d.online })),
    })),
  }));

  const submitAdd = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await addFriend(code.trim(), address.trim() || undefined);
      if (!res.ok) setError(res.error ?? 'could not add that friend');
      else {
        setCode('');
        setAddress('');
      }
    } finally {
      setBusy(false);
      requestRoster();
    }
  }, [code, address]);

  const submitLink = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await linkDevice(invite.trim());
      if (!res.ok) setError(res.error ?? 'could not link that machine');
      else setInvite('');
    } finally {
      setBusy(false);
      requestRoster();
    }
  }, [invite]);

  const copyCode = useCallback(() => {
    if (!me) return;
    void navigator.clipboard?.writeText(me.friend_code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [me]);

  return (
    <div
      className="social-friends"
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
        <h3 style={{ margin: '0 0 0.35rem' }}>You</h3>
        {me ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <strong style={{ color: 'var(--text)' }}>{me.display_name}</strong>
              <code
                style={{
                  fontSize: '0.85rem',
                  letterSpacing: '0.04em',
                  color: 'var(--text)',
                  background: 'var(--bg-alt, #1a1a1a)',
                  padding: '0.15rem 0.4rem',
                  borderRadius: 4,
                }}
              >
                {me.friend_code}
              </code>
              <button onClick={copyCode}>{copied ? 'Copied' : 'Copy'}</button>
            </div>
            <p style={{ margin: '0.35rem 0 0', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
              Share this code so someone can add you. It identifies you, not one computer — every
              machine you link answers to it.
            </p>
            {!me.holds_person_key && (
              <p style={{ margin: '0.35rem 0 0', fontSize: '0.75rem', color: '#d29922' }}>
                This machine was linked by another device, so it can’t link further machines or
                rename you.
              </p>
            )}
            <form
              style={{ display: 'flex', gap: '0.4rem', marginTop: '0.5rem' }}
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) {
                  void updateSelfProfile(name.trim()).then(() => {
                    setName('');
                    requestRoster();
                  });
                }
              }}
            >
              <input
                value={name}
                placeholder={`Rename (currently “${me.display_name}”)`}
                onChange={(e) => setName(e.target.value)}
                style={{ flex: 1, fontSize: '0.8rem' }}
              />
              <button type="submit" disabled={!name.trim() || !me.holds_person_key}>
                Rename
              </button>
            </form>
          </>
        ) : (
          <p style={{ color: 'var(--text-dim)' }}>Loading your identity…</p>
        )}
      </section>

      {pending.length > 0 && (
        <section>
          <h3 style={{ margin: '0 0 0.35rem' }}>Requests ({pending.length})</h3>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {pending.map((f) => (
              <FriendRow key={f.person_id} friend={f} />
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3 style={{ margin: '0 0 0.35rem' }}>
          Friends ({friends.filter((f) => f.status === 'accepted').length})
        </h3>
        {friends.filter((f) => f.status !== 'pending_in').length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
            No friends yet. Add someone with their friend code below.
          </p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {friends
              .filter((f) => f.status !== 'pending_in')
              .map((f) => (
                <FriendRow key={f.person_id} friend={f} />
              ))}
          </ul>
        )}
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <h3 style={{ margin: 0 }}>Add a friend</h3>
        <form
          style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}
          onSubmit={(e) => {
            e.preventDefault();
            void submitAdd();
          }}
        >
          <input
            value={code}
            placeholder="HD-XXXX-XXXX-XXXX-XXXX-XXXX"
            spellCheck={false}
            onChange={(e) => setCode(e.target.value)}
          />
          <input
            value={address}
            placeholder="optional: ws://their-host:8000/peer-ws (needed off your network)"
            spellCheck={false}
            onChange={(e) => setAddress(e.target.value)}
            style={{ fontSize: '0.8rem' }}
          />
          <button type="submit" disabled={!code.trim() || busy}>
            {busy ? 'Sending…' : 'Send friend request'}
          </button>
        </form>
      </section>

      <section style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <h3 style={{ margin: 0 }}>Link another of your machines</h3>
        <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-dim)' }}>
          Generate an invite in the Peers widget on the other computer, then paste it here. It joins
          your identity rather than becoming a separate friend.
        </p>
        <form
          style={{ display: 'flex', gap: '0.4rem' }}
          onSubmit={(e) => {
            e.preventDefault();
            void submitLink();
          }}
        >
          <input
            value={invite}
            placeholder="paste that machine’s invite"
            spellCheck={false}
            onChange={(e) => setInvite(e.target.value)}
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={!invite.trim() || busy || !me?.holds_person_key}>
            Link
          </button>
        </form>
      </section>

      {error && <p style={{ color: '#f85149', fontSize: '0.8rem', margin: 0 }}>{error}</p>}
    </div>
  );
}
