import { useEffect } from 'react';

import { requestChallengeDraft } from '../challenge-draft';
import { openGamesSection } from '../hub-section';
import {
  friendAccept,
  friendRemove,
  friendRequest,
  profileGet,
  useGames,
  type Profile,
} from '../game-ws';

/** XP progress bar toward the next level — the gamified core of the profile. */
function LevelBar({ profile }: { profile: Profile }) {
  const floor = profile.level_floor;
  const next = profile.next_level_xp;
  const pct = next === null ? 100 : Math.round(((profile.xp - floor) / (next - floor)) * 100);
  return (
    <div className="games-profile">
      <span className="games-profile-avatar">{profile.avatar}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="games-profile-line">
          <span className="games-level-badge">Lv {profile.level}</span>
          <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
            {next === null ? `${profile.xp} XP · max` : `${profile.xp} / ${next} XP`}
          </span>
        </div>
        <div className="games-xp-track">
          <div className="games-xp-fill" style={{ width: `${Math.max(4, pct)}%` }} />
        </div>
      </div>
    </div>
  );
}

/**
 * Who's online + friends — the social directory that makes active players easy to
 * find. Shows your gamified level/XP, the live roster (with each player's room and
 * current activity), your friends (online first), and incoming friend requests.
 * "Challenge" (⚔️) opens the lobby's negotiation card pre-targeted at that player:
 * you propose the game and terms, they accept/decline/counter.
 */
export function RosterPanel() {
  const { social, accountId } = useGames();

  useEffect(() => {
    profileGet();
  }, []);

  const roomName = (id: string) => social.rooms.find((r) => r.id === id)?.name ?? id;
  const friendIds = new Set(social.friends.map((f) => f.account_id));
  const challenge = (id: string, name: string) => {
    requestChallengeDraft({ accountId: id, name });
  };

  return (
    <div className="games-roster">
      {social.profile && <LevelBar profile={social.profile} />}

      {!social.joined && (
        <div className="games-roster-hint">
          <span>Enter the Plaza to see who's online and hang out.</span>
          <button type="button" onClick={() => openGamesSection('social')}>
            Open the Plaza →
          </button>
        </div>
      )}

      {/* Incoming friend requests */}
      {social.pending.length > 0 && (
        <section>
          <div className="games-roster-head">Friend requests</div>
          <ul className="games-roster-list">
            {social.pending.map((p) => (
              <li key={p.account_id}>
                <span className="games-roster-who">
                  <span className="games-roster-av">{p.avatar}</span>
                  <span>{p.display_name}</span>
                  <span className="games-level-badge sm">Lv {p.level}</span>
                </span>
                <button
                  type="button"
                  className="games-play-btn"
                  onClick={() => friendAccept(p.account_id)}
                >
                  Accept
                </button>
                <button type="button" onClick={() => friendRemove(p.account_id)} title="Decline">
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Friends */}
      <section>
        <div className="games-roster-head">
          Friends {social.friends.length > 0 && <span>({social.friends.length})</span>}
        </div>
        {social.friends.length === 0 ? (
          <div className="games-roster-empty">
            No friends yet — add someone from the roster below.
          </div>
        ) : (
          <ul className="games-roster-list">
            {[...social.friends]
              .sort((a, b) => Number(b.online) - Number(a.online))
              .map((f) => (
                <li key={f.account_id}>
                  <span className="games-roster-who">
                    <span className={`games-online-dot ${f.online ? 'on' : ''}`} />
                    <span className="games-roster-av">{f.avatar}</span>
                    <span>{f.display_name}</span>
                    <span className="games-level-badge sm">Lv {f.level}</span>
                  </span>
                  {f.online && (
                    <button
                      type="button"
                      className="games-chip-btn"
                      onClick={() => challenge(f.account_id, f.display_name)}
                    >
                      ⚔️
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => friendRemove(f.account_id)}
                    title="Remove friend"
                  >
                    ✕
                  </button>
                </li>
              ))}
          </ul>
        )}
      </section>

      {/* Everyone online */}
      <section>
        <div className="games-roster-head">
          Online now {social.roster.length > 0 && <span>({social.roster.length})</span>}
        </div>
        {social.roster.length === 0 ? (
          <div className="games-roster-empty">Nobody's around yet.</div>
        ) : (
          <ul className="games-roster-list">
            {social.roster.map((p) => {
              const isMe = p.account_id === accountId;
              const isFriend = friendIds.has(p.account_id);
              return (
                <li key={p.account_id} className={isMe ? 'me' : ''}>
                  <span className="games-roster-who">
                    <span className="games-roster-av">{p.avatar}</span>
                    <span>
                      {p.name}
                      {isMe && <em style={{ color: 'var(--text-dim)' }}> (you)</em>}
                    </span>
                    <span className="games-level-badge sm">Lv {p.level}</span>
                    <span className="games-roster-activity">
                      {roomName(p.room)} · {p.activity}
                    </span>
                  </span>
                  {!isMe && (
                    <>
                      {!isFriend && (
                        <button
                          type="button"
                          onClick={() => friendRequest(p.account_id)}
                          title="Add friend"
                        >
                          🤝
                        </button>
                      )}
                      <button
                        type="button"
                        className="games-chip-btn"
                        onClick={() => challenge(p.account_id, p.name)}
                        title="Challenge"
                      >
                        ⚔️
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
