/**
 * **Me** — the one place that answers "what do I tell someone so they can add me?"
 *
 * Before this, the answer lived in three panes with three different ideas of
 * identity: a friend code in the Friends panel, an "Account ID" in the games
 * profile, and a node id in Peers. This shows both names that matter, says which
 * is which, and makes the binding between them explicit.
 *
 * **Username** is the convenient name (globally unique, from the game server).
 * **Friend code** is the durable one (derived from your own key, works offline and
 * on a LAN, and cannot be forged by a directory). Neither replaces the other, so
 * the pane degrades honestly when signed out: the code is always there.
 */
import { useCallback, useEffect, useState } from 'react';

import { CommonsProfileEditor } from '../commons';
import { AgentRelayPanel } from '../network/AgentRelayPanel';
import { LinkHealth } from '../network/LinkHealth';
import { PeerMonitor } from '../network/PeerMonitor';
import { bindHandle, getSelfProfile, updateSelfProfile, type SelfProfile } from '../social/api';
import { getSocialState, subscribeSocial } from '../social/ws';

export function MeSection() {
  const [me, setMe] = useState<SelfProfile | null>(
    () => getSocialState().roster?.self_profile ?? null,
  );
  const [name, setName] = useState('');
  const [binding, setBinding] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // The roster push carries the self profile, so a username claimed on another
  // machine appears here without a refetch.
  useEffect(
    () =>
      subscribeSocial(() => {
        const next = getSocialState().roster?.self_profile;
        if (next) setMe(next);
      }),
    [],
  );
  useEffect(() => {
    if (!me)
      void getSelfProfile()
        .then(setMe)
        .catch(() => undefined);
  }, [me]);
  useEffect(() => {
    if (me) setName(me.display_name);
  }, [me?.display_name]);

  const copy = useCallback((label: string, value: string) => {
    void navigator.clipboard?.writeText(value).then(
      () => {
        setCopied(label);
        setTimeout(() => setCopied(null), 1500);
      },
      () => setCopied(null),
    );
  }, []);

  const claim = useCallback(async () => {
    setBinding(true);
    setNote(null);
    try {
      const res = await bindHandle();
      setNote(
        res.error
          ? res.error
          : `Linked — people can now add you as @${res.handle ?? 'your username'}.`,
      );
      await getSelfProfile()
        .then(setMe)
        .catch(() => undefined);
    } finally {
      setBinding(false);
    }
  }, []);

  if (!me) return <p className="people-hint">Loading your identity…</p>;

  return (
    <div className="people-section">
      <label className="people-field">
        <span className="people-label">Display name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => {
            if (name.trim() && name !== me.display_name) {
              void updateSelfProfile(name.trim()).then(setMe);
            }
          }}
        />
      </label>

      <div className="people-identity">
        <div className="people-identity-row">
          <span className="people-label">Username</span>
          {me.handle ? (
            <>
              <code className="people-handle">@{me.handle}</code>
              <button type="button" onClick={() => copy('username', `@${me.handle}`)}>
                {copied === 'username' ? 'Copied' : 'Copy'}
              </button>
            </>
          ) : (
            <span className="people-dim">
              Sign in to the game server to claim one — it is the name people can search for.
            </span>
          )}
        </div>
        <div className="people-identity-row">
          <span className="people-label">Friend code</span>
          <code>{me.friend_code}</code>
          <button type="button" onClick={() => copy('code', me.friend_code)}>
            {copied === 'code' ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p className="people-hint">
          Your username is the easy name; the friend code always works, including offline and on a
          LAN, because it comes from your own key rather than from a directory.
        </p>
      </div>

      {me.holds_person_key ? (
        <div className="people-field">
          <button type="button" disabled={binding} onClick={() => void claim()}>
            {binding ? 'Linking…' : 'Link my username to this identity'}
          </button>
          <p className="people-hint">
            Tells the game server that @{me.handle ?? 'yourusername'} and this machine&rsquo;s
            identity are the same person, so searching your username finds you. Safe to press more
            than once.
          </p>
        </div>
      ) : (
        <p className="people-hint">
          This machine is linked to your identity but does not hold the key, so the username link
          has to be made from your primary machine.
        </p>
      )}

      {note ? <p className="people-note">{note}</p> : null}

      <div className="people-field">
        <span className="people-label">My devices</span>
        <ul className="people-list">
          {me.devices.map((d) => (
            <li key={d.node_id} className="people-row">
              <div className="people-row-main">
                <span>{d.label}</span>
                <span className="people-dim">{d.node_id}</span>
              </div>
              <span className={d.online ? 'people-online' : 'people-dim'}>
                {d.online ? 'online' : 'offline'}
              </span>
            </li>
          ))}
          {me.devices.length === 0 ? <li className="people-hint">No linked devices.</li> : null}
        </ul>
      </div>

      <details className="people-fold">
        <summary>Commons profile</summary>
        <div className="people-embed">
          <CommonsProfileEditor />
        </div>
      </details>

      {/* The fabric diagnostics that used to be two panes of their own (Peer
          Monitor, Agent Relay). They are readouts, not destinations — folded away
          here rather than deleted, because "is the relay actually carrying
          anything" is a real question with nowhere else to ask it yet. */}
      <details className="people-fold">
        <summary>Connection diagnostics</summary>
        <div className="people-embed">
          <PeerMonitor />
        </div>
      </details>
      <details className="people-fold">
        <summary>Link health</summary>
        <div className="people-embed">
          <LinkHealth />
        </div>
      </details>
      <details className="people-fold">
        <summary>Agent relay</summary>
        <div className="people-embed">
          <AgentRelayPanel />
        </div>
      </details>
    </div>
  );
}
