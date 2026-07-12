import { useEffect, useState } from 'react';

import { apiGet } from '../../../api';
import { profileGet, profileSet, useGames } from '../game-ws';
import { fetchGamesCatalog, fetchLeaderboard, type GameCatalogEntry } from '../games-api';
import { openReplay } from '../replay-focus';

interface MatchEntry {
  ts: number;
  game_id: string;
  result: string;
  loadout_version: string | null;
  model_label: string | null;
  replay_id: string | null;
  rating_delta: number | null;
  tier?: string | null;
}

interface TierCard {
  game_id: string;
  name: string;
  tier: string | null;
  rating: number | null;
  record: string;
}

/** Your arena identity: avatar + level ring, per-game tier cards, and the recent
 * match log with loadout/model attribution and one-click replays. */
export function ProfilePanel() {
  const { social, accountId } = useGames();
  const [cards, setCards] = useState<TierCard[]>([]);
  const [log, setLog] = useState<MatchEntry[]>([]);
  const [editingAvatar, setEditingAvatar] = useState(false);

  useEffect(() => {
    profileGet();
    apiGet<{ entries: MatchEntry[] }>('/games/match-log?limit=30')
      .then((r) => setLog(r.entries))
      .catch(() => setLog([]));
  }, []);

  useEffect(() => {
    if (!accountId) return;
    fetchGamesCatalog().then(async (games: GameCatalogEntry[]) => {
      const rows = await Promise.all(
        games.map(async (g) => {
          try {
            const lb = await fetchLeaderboard(g.id);
            const me = lb.entries.find((e) => e.account_id === accountId);
            if (!me) return null;
            return {
              game_id: g.id,
              name: g.name,
              tier: me.tier ?? null,
              rating: me.rating,
              record: `${me.wins}W/${me.losses}L/${me.draws}D`,
            };
          } catch {
            return null;
          }
        }),
      );
      setCards(rows.filter((r): r is TierCard => r !== null));
    });
  }, [accountId]);

  const profile = social.profile;
  const pct =
    profile && profile.next_level_xp !== null
      ? Math.min(
          100,
          Math.round(
            ((profile.xp - profile.level_floor) / (profile.next_level_xp - profile.level_floor)) *
              100,
          ),
        )
      : 100;

  return (
    <div
      style={{
        padding: '0.8rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.8rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      {profile ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
          <button
            type="button"
            className="games-profile-avatar"
            title="change avatar"
            onClick={() => setEditingAvatar((v) => !v)}
          >
            {profile.avatar}
          </button>
          <div>
            <div style={{ fontWeight: 800, fontSize: '1.05rem' }}>
              {profile.handle ?? profile.account_id}
            </div>
            <div style={{ color: 'var(--text-dim)' }}>
              Level {profile.level} · {profile.xp} XP
            </div>
            <div className="games-xp-track" style={{ width: '14rem' }}>
              <div className="games-xp-fill" style={{ width: `${Math.max(4, pct)}%` }} />
            </div>
          </div>
        </div>
      ) : (
        <div style={{ color: 'var(--text-dim)' }}>
          Connect to the game server to load your profile.
        </div>
      )}
      {editingAvatar && (
        <div className="games-onboard-avatars">
          {['🤖', '🦾', '🧠', '👾', '🐙', '🦊', '🐲', '⚡', '🛠', '🎯', '🃏', '🚀'].map((a) => (
            <button
              key={a}
              type="button"
              className="games-avatar-pick"
              onClick={() => {
                profileSet(a);
                setEditingAvatar(false);
              }}
            >
              {a}
            </button>
          ))}
        </div>
      )}

      <div>
        <strong>Ranked tiers</strong>
        {cards.length === 0 ? (
          <div style={{ color: 'var(--text-dim)' }}>No rated games yet — hit Find match.</div>
        ) : (
          <div className="games-cards" style={{ marginTop: '0.3rem' }}>
            {cards.map((c) => (
              <div key={c.game_id} className="games-card">
                <span className="games-card-name">{c.name}</span>
                <span className="games-tier-chip" data-tier={c.tier ?? undefined}>
                  {c.tier ?? '—'}
                </span>
                <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                  {c.rating ?? '···'} · {c.record}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <strong>Recent matches</strong>
        {log.length === 0 ? (
          <div style={{ color: 'var(--text-dim)' }}>Nothing yet.</div>
        ) : (
          <table style={{ borderCollapse: 'collapse', width: '100%', marginTop: '0.3rem' }}>
            <tbody>
              {log.map((e, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.2rem 0.4rem' }}>
                    {e.result === 'win' ? '🏆' : e.result === 'loss' ? '💥' : '🤝'} {e.game_id}
                  </td>
                  <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>
                    {e.loadout_version ?? '—'}
                    {e.model_label ? ` · ${e.model_label}` : ''}
                  </td>
                  <td style={{ padding: '0.2rem 0.4rem' }}>
                    {e.rating_delta !== null && (
                      <span style={{ color: e.rating_delta >= 0 ? '#3fb950' : '#e5534b' }}>
                        {e.rating_delta >= 0 ? '+' : ''}
                        {e.rating_delta}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '0.2rem 0.4rem', color: 'var(--text-dim)' }}>
                    {new Date(e.ts * 1000).toLocaleString()}
                  </td>
                  <td style={{ padding: '0.2rem 0.4rem' }}>
                    {e.replay_id && (
                      <button type="button" onClick={() => openReplay(e.replay_id!)}>
                        📼
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
