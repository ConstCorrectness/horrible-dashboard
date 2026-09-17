/**
 * **Me** — the one place that answers "what do I tell someone so they can add me?"
 *
 * Before this, the answer lived in three panes with three different ideas of
 * identity: a friend code in the Friends panel, an "Account ID" in the games
 * profile, and a node id in Peers. The answer is now one thing: your **username**,
 * chosen at sign-up and globally unique. Every machine you sign in on is enrolled
 * in your account and reachable through it, so there is nothing else to link.
 */
import { useCallback, useEffect, useState } from 'react';

import { AccountGate } from '../../AccountGate';
import { CommonsProfileEditor } from '../commons';
import { AgentRelayPanel } from '../network/AgentRelayPanel';
import { LinkHealth } from '../network/LinkHealth';
import { PeerMonitor } from '../network/PeerMonitor';
import { getSelfProfile, updateSelfProfile, type SelfProfile } from '../social/api';
import { getSocialState, subscribeSocial } from '../social/ws';

export function MeSection() {
  const [me, setMe] = useState<SelfProfile | null>(
    () => getSocialState().roster?.self_profile ?? null,
  );
  const [name, setName] = useState('');
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
            <span className="people-dim">none yet</span>
          )}
        </div>
        {me.handle ? null : (
          <AccountGate
            onDone={() =>
              void getSelfProfile()
                .then(setMe)
                .catch(() => undefined)
            }
          />
        )}
        <p className="people-hint">
          Share your username; it reaches you on every machine you are signed in on.
        </p>
      </div>

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
          {me.devices.length === 0 ? (
            <li className="people-hint">Sign in on another computer to add it here.</li>
          ) : null}
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
