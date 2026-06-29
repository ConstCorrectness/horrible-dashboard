import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { toastsStore } from '../../toasts';
import {
  commonsBlock,
  commonsConnect,
  commonsRefresh,
  commonsRequest,
  commonsRespond,
  commonsSearch,
  commonsUnblock,
  getCommonsState,
  initCommons,
  subscribeCommons,
  type CommonsProfile,
} from './commons';

const TIER_BADGE: Record<string, { label: string; color: string }> = {
  known: { label: 'known', color: '#3fb950' },
  blocked: { label: 'blocked', color: '#f85149' },
};

function useCommons() {
  return useSyncExternalStore(subscribeCommons, getCommonsState, getCommonsState);
}

function ProfileCard({
  profile,
  score,
  canMeet,
}: {
  profile: CommonsProfile;
  score?: number;
  canMeet: boolean;
}) {
  const online = profile.status === 'connected';
  const blocked = profile.trust_tier === 'blocked';
  const badge = profile.trust_tier ? TIER_BADGE[profile.trust_tier] : undefined;
  return (
    <li
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.25rem',
        padding: '0.5rem 0.6rem',
        border: '1px solid var(--border, #2a2a2a)',
        borderRadius: 6,
        fontSize: '0.85rem',
        opacity: blocked ? 0.55 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        {profile.status && (
          <span
            aria-label={online ? 'online' : 'offline'}
            title={online ? 'online' : 'offline'}
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: online ? '#3fb950' : 'var(--text-dim)',
              flex: '0 0 auto',
            }}
          />
        )}
        <strong style={{ color: 'var(--text)' }}>{profile.display_name}</strong>
        {badge && (
          <span
            style={{
              fontSize: '0.65rem',
              textTransform: 'uppercase',
              letterSpacing: '0.03em',
              padding: '0.05rem 0.3rem',
              borderRadius: 4,
              border: `1px solid ${badge.color}`,
              color: badge.color,
            }}
          >
            {badge.label}
          </span>
        )}
        {typeof score === 'number' && (
          <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>{score.toFixed(2)}</span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem' }}>
          {blocked ? (
            <button style={{ fontSize: '0.75rem' }} onClick={() => commonsUnblock(profile.node_id)}>
              Unblock
            </button>
          ) : (
            canMeet && (
              <>
                <button
                  style={{ fontSize: '0.75rem' }}
                  title="Send a request to meet — they must accept"
                  onClick={() => {
                    commonsRequest(profile.node_id);
                    toastsStore.add(
                      'info',
                      'Commons',
                      `Requested to meet ${profile.display_name}.`,
                    );
                  }}
                >
                  Meet
                </button>
                <button
                  style={{ fontSize: '0.75rem' }}
                  title="Block — auto-declines their requests and refuses the peer link"
                  onClick={() => commonsBlock(profile.node_id)}
                >
                  Block
                </button>
              </>
            )
          )}
        </span>
      </div>
      {profile.headline && <span style={{ color: 'var(--text-dim)' }}>{profile.headline}</span>}
      {profile.tags.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
          {profile.tags.map((t) => (
            <span
              key={t}
              style={{
                fontSize: '0.7rem',
                padding: '0.05rem 0.35rem',
                borderRadius: 999,
                background: 'var(--bg-elevated, #1c1c1c)',
                color: 'var(--text-dim)',
              }}
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

/**
 * The agent commons directory: browse + search public profiles (cosine match over the
 * index's vectordb), request to meet someone, and accept/decline inbound requests — the
 * two-sided consent handshake. See docs/modules/commons.mdx.
 */
export function CommonsDirectory() {
  const { connected, url, directory, results, requests, self } = useCommons();
  const [query, setQuery] = useState('');
  const selfId = self?.node_id;

  useEffect(() => {
    initCommons();
    commonsRefresh();
  }, []);

  const searching = query.trim().length > 0;
  const list = useMemo(
    () =>
      searching
        ? results.map((r) => ({ profile: r.profile, score: r.score }))
        : directory.map((p) => ({ profile: p, score: undefined })),
    [searching, results, directory],
  );

  return (
    <div
      className="commons-widget"
      style={{
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <section style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span
          aria-label={connected ? 'connected' : 'disconnected'}
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: connected ? '#3fb950' : 'var(--text-dim)',
          }}
        />
        <strong>{connected ? 'Commons connected' : 'Commons offline'}</strong>
        {url && <code style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>{url}</code>}
        {!connected && (
          <button style={{ marginLeft: 'auto' }} onClick={() => commonsConnect()}>
            Connect
          </button>
        )}
      </section>

      {requests.length > 0 && (
        <section>
          <h3 style={{ margin: '0 0 0.5rem' }}>Requests to meet ({requests.length})</h3>
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
            {requests.map((r) => (
              <li
                key={r.request_id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  fontSize: '0.85rem',
                  padding: '0.4rem 0.6rem',
                  border: '1px solid var(--border, #2a2a2a)',
                  borderRadius: 6,
                }}
              >
                <span>
                  <strong>{r.from.display_name ?? r.from.node_id}</strong>
                  {r.note ? ` — “${r.note}”` : ' wants to meet'}
                </span>
                <button
                  style={{ marginLeft: 'auto' }}
                  onClick={() => commonsRespond(r.request_id, true)}
                >
                  Accept
                </button>
                <button onClick={() => commonsRespond(r.request_id, false)}>Decline</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <form
        style={{ display: 'flex', gap: '0.5rem' }}
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) commonsSearch(query.trim());
        }}
      >
        <input
          value={query}
          placeholder="Find an agent — e.g. “rust data viz”…"
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={!connected || !query.trim()}>
          Search
        </button>
        <button type="button" title="Refresh directory" onClick={() => commonsRefresh()}>
          ↻
        </button>
      </form>

      <section>
        <h3 style={{ margin: '0 0 0.5rem' }}>
          {searching ? `Matches (${list.length})` : `Directory (${list.length})`}
        </h3>
        {list.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
            {connected
              ? searching
                ? 'No matches — try different words.'
                : 'No profiles yet.'
              : 'Connect to a commons to discover agents.'}
          </p>
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
            {list.map(({ profile, score }) => (
              <ProfileCard
                key={profile.node_id}
                profile={profile}
                score={score}
                canMeet={connected && profile.node_id !== selfId}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
