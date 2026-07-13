import { useEffect, useMemo, useState } from 'react';

import {
  dismissInvite,
  friendRequest,
  gamesJoinTable,
  profileGet,
  revealBoard,
  socialInvite,
  socialJoin,
  socialLeave,
  socialMove,
  socialRoom,
  socialSay,
  useGames,
} from '../game-ws';
import { fetchGamesCatalog, fetchStatus, type GameCatalogEntry } from '../games-api';
import { PlazaCanvas } from './PlazaCanvas';

/** Human avatars for the Plaza (distinct from AgentTown's animal residents). */
const AVATARS = ['🙂', '😎', '🦸', '🧙', '🥷', '🤖', '👽', '🐱', '🦊', '🐼', '🐸', '🦄'];
const QUICK_EMOTES = ['👋', '🎉', '😂', '❤️', '👍', '🤔'];

/**
 * The Plaza — the human social lobby. Enter as an avatar, walk around a room by
 * clicking the floor, chat in speech bubbles, hop between rooms to declutter, and
 * challenge anyone in the room straight to a game. The who's-online roster and
 * friends live in the companion Roster panel (right dock).
 */
export function PlazaPanel() {
  const { social, accountId } = useGames();
  const [avatar, setAvatar] = useState(AVATARS[0]);
  const [text, setText] = useState('');
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [challengeGame, setChallengeGame] = useState('tictactoe');
  // Who you'll appear as: the signed-in account's username (GitHub/Google),
  // resolved server-side — there is no separate display name to pick.
  const [whoami, setWhoami] = useState<string | null>(null);

  useEffect(() => {
    fetchGamesCatalog().then((g) => {
      setGames(g);
      if (g[0]) setChallengeGame(g[0].id);
    });
    fetchStatus()
      .then((s) => setWhoami(s.signed_in ? s.display_name : null))
      .catch(() => setWhoami(null));
  }, []);

  // Prefill the avatar from the saved profile once it arrives.
  useEffect(() => {
    if (social.profile?.avatar) setAvatar(social.profile.avatar);
    if (!social.joined) profileGet();
  }, [social.profile?.avatar, social.joined]);

  const rooms = social.rooms;
  const others = useMemo(
    () => social.occupants.filter((o) => o.account_id !== accountId),
    [social.occupants, accountId],
  );

  const send = () => {
    const t = text.trim();
    if (t) {
      socialSay(t);
      setText('');
    }
  };

  const acceptInvite = () => {
    if (social.invite) {
      gamesJoinTable(social.invite.table_id);
      revealBoard();
      dismissInvite();
    }
  };

  return (
    <div className="games-plaza">
      {/* Room switcher + join/leave */}
      <div className="games-plaza-topbar">
        <strong>The Plaza</strong>
        <div className="games-plaza-rooms">
          {rooms.map((r) => (
            <button
              key={r.id}
              type="button"
              className={`games-plaza-room ${r.id === social.room ? 'active' : ''}`}
              onClick={() => social.joined && socialRoom(r.id)}
              disabled={!social.joined}
              title={r.name}
            >
              <span>{r.icon}</span> {r.name}
            </button>
          ))}
        </div>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem', marginLeft: 'auto' }}>
          {social.roster.length} online
        </span>
        {social.joined && (
          <button type="button" onClick={() => socialLeave()}>
            Leave
          </button>
        )}
      </div>

      {!social.joined ? (
        <form
          className="games-plaza-join"
          onSubmit={(e) => {
            e.preventDefault();
            socialJoin(avatar);
          }}
        >
          <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
            {whoami ? (
              <>
                Step into the lobby as <strong>{whoami}</strong> — pick an avatar and say hi. Real
                people, live rooms, speech bubbles.
              </>
            ) : (
              <>
                Step into the lobby under your account name — pick an avatar and say hi. Real
                people, live rooms, speech bubbles. (Sign in in Games to use your GitHub/Google
                username.)
              </>
            )}
          </div>
          <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <div className="games-avatar-picker">
              {AVATARS.map((a) => (
                <button
                  key={a}
                  type="button"
                  className={`games-avatar-opt ${a === avatar ? 'active' : ''}`}
                  onClick={() => setAvatar(a)}
                >
                  {a}
                </button>
              ))}
            </div>
            <button type="submit">Enter the Plaza →</button>
          </div>
        </form>
      ) : (
        <>
          {social.invite && (
            <div className="games-plaza-invite">
              <span>
                ⚔️ <strong>{social.invite.from_name}</strong> challenged you to{' '}
                <strong>{social.invite.game_name}</strong>
              </span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem' }}>
                <button type="button" className="games-play-btn" onClick={acceptInvite}>
                  Join
                </button>
                <button type="button" onClick={() => dismissInvite()}>
                  Dismiss
                </button>
              </span>
            </div>
          )}

          <div className="games-plaza-floor">
            <PlazaCanvas
              occupants={social.occupants}
              bubbles={social.bubbles}
              accountId={accountId}
              roomName={rooms.find((r) => r.id === social.room)?.name ?? social.room}
              onMove={socialMove}
            />
          </div>

          {/* Say + quick emotes */}
          <div className="games-plaza-saybar">
            <form
              style={{ display: 'flex', gap: '0.35rem', flex: 1 }}
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={`Say something in ${rooms.find((r) => r.id === social.room)?.name ?? 'the room'}…`}
                style={{ flex: 1 }}
              />
              <button type="submit" disabled={!text.trim()}>
                💬 Say
              </button>
            </form>
            <div className="games-plaza-emotes">
              {QUICK_EMOTES.map((em) => (
                <button key={em} type="button" onClick={() => socialSay(em, true)} title="emote">
                  {em}
                </button>
              ))}
            </div>
          </div>

          {/* Who's in this room + quick actions */}
          <div className="games-plaza-here">
            <div className="games-plaza-here-head">
              <span style={{ color: 'var(--text-dim)' }}>In this room</span>
              <label style={{ marginLeft: 'auto', fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                challenge with{' '}
                <select value={challengeGame} onChange={(e) => setChallengeGame(e.target.value)}>
                  {games.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {others.length === 0 ? (
              <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
                Nobody else here yet. Switch rooms or invite a friend from the roster.
              </div>
            ) : (
              <ul className="games-plaza-people">
                {others.map((o) => (
                  <li key={o.account_id}>
                    <span className="games-plaza-person">
                      <span style={{ fontSize: '1.1rem' }}>{o.avatar}</span> {o.name}
                    </span>
                    <button
                      type="button"
                      title="Add friend"
                      onClick={() => friendRequest(o.account_id)}
                    >
                      🤝
                    </button>
                    <button
                      type="button"
                      className="games-chip-btn"
                      title={`Challenge to ${games.find((g) => g.id === challengeGame)?.name ?? challengeGame}`}
                      onClick={() => {
                        socialInvite(o.account_id, challengeGame);
                        revealBoard();
                      }}
                    >
                      ⚔️ Challenge
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
