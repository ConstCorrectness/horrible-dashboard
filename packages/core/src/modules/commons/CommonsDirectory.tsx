import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { toastsStore } from '../../toasts';
import {
  commonsBlock,
  commonsConnect,
  commonsRefresh,
  commonsReport,
  commonsRequest,
  commonsSearch,
  commonsUnblock,
  commonsVouch,
  getCommonsState,
  initCommons,
  subscribeCommons,
  type CommonsProfile,
} from './commons';

const TIER_BADGE: Record<string, { label: string; color: string }> = {
  known: { label: 'known', color: '#3fb950' },
  vouched: { label: 'vouched', color: '#58a6ff' },
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
  const known = profile.trust_tier === 'known';
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
            <>
              {known && (
                <button
                  style={{ fontSize: '0.75rem' }}
                  title="Vouch — publicly attest you trust this node"
                  onClick={() => {
                    commonsVouch(profile.node_id);
                    toastsStore.add('success', 'Commons', `Vouched for ${profile.display_name}.`);
                  }}
                >
                  Vouch
                </button>
              )}
              {canMeet && (
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
                  <button
                    style={{ fontSize: '0.75rem' }}
                    title="Report this node to the index"
                    onClick={() => {
                      commonsReport(profile.node_id);
                      toastsStore.add('info', 'Commons', `Reported ${profile.display_name}.`);
                    }}
                  >
                    Report
                  </button>
                </>
              )}
            </>
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
 * index's vectordb) and act on them — request to meet, vouch, block, report. Inbound
 * meet requests live in the separate Commons Requests widget. See docs/modules/commons.mdx.
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
        <p
          style={{
            margin: 0,
            fontSize: '0.8rem',
            color: '#58a6ff',
          }}
        >
          {requests.length} pending request{requests.length === 1 ? '' : 's'} to meet — open the
          Commons Requests panel to respond.
        </p>
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
