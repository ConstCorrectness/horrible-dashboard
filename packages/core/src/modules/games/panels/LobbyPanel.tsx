import { useCallback, useEffect, useState } from 'react';

import { registry } from '../../../registry';
import {
  gamesConnect,
  gamesCreateTable,
  gamesDisconnect,
  gamesJoinTable,
  gamesListTables,
  useGames,
} from '../game-ws';
import {
  fetchGamesCatalog,
  fetchStatus,
  signInWithGitHub,
  signOut,
  type GameCatalogEntry,
} from '../games-api';

/** Sign-in status + GitHub device-flow sign-in. Identity lives on the node (the JWT
 * is held server-side); this just reflects and toggles it. */
function SignIn() {
  const [name, setName] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<{ code: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const refresh = useCallback(() => {
    fetchStatus()
      .then((s) => setName(s.signed_in ? s.display_name : null))
      .catch(() => setName(null));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const signIn = async () => {
    setBusy(true);
    setErr('');
    try {
      const display = await signInWithGitHub((code, url) => setPrompt({ code, url }));
      setName(display);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
      setPrompt(null);
    }
  };

  if (name) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.78rem' }}>
        <span style={{ color: 'var(--text-dim)' }}>
          Signed in as <strong>{name}</strong>
        </span>
        <button type="button" onClick={() => void signOut().then(() => setName(null))}>
          Sign out
        </button>
      </div>
    );
  }
  return (
    <div style={{ fontSize: '0.78rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <button type="button" onClick={signIn} disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in with GitHub'}
        </button>
        <span style={{ color: 'var(--text-dim)' }}>or play with the dev token</span>
      </div>
      {prompt && (
        <div style={{ color: 'var(--text-dim)' }}>
          Enter code <strong>{prompt.code}</strong> at{' '}
          <a href={prompt.url} target="_blank" rel="noreferrer">
            {prompt.url}
          </a>
        </div>
      )}
      {err && <div style={{ color: 'var(--danger, #e5534b)' }}>{err}</div>}
    </div>
  );
}

/**
 * The games lobby: connect this node to the central server, start or join a table,
 * and open the board to watch. In phase 1 auth is a dev token (settings
 * `games.devToken`); OAuth login lands in a later phase. "Self-play" seats a
 * sparring partner from this same node so a match runs with a single user.
 */
export function LobbyPanel() {
  const { connected, accountId, selfPlay, tables } = useGames();
  const [games, setGames] = useState<GameCatalogEntry[]>([]);

  useEffect(() => {
    fetchGamesCatalog().then(setGames);
  }, []);

  useEffect(() => {
    if (connected) gamesListTables();
  }, [connected]);

  const startMatch = (gameId: string) => {
    registry.openPanel('games.board');
    gamesCreateTable(gameId);
  };

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
      }}
    >
      <SignIn />

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        {connected ? (
          <>
            <span style={{ color: 'var(--text-dim)' }}>
              Connected as <strong>{accountId}</strong>
              {selfPlay ? ' · self-play' : ''}
            </span>
            <button type="button" onClick={() => gamesDisconnect()}>
              Disconnect
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={() => gamesConnect(true)}>
              Connect (self-play)
            </button>
            <button type="button" onClick={() => gamesConnect(false)}>
              Connect
            </button>
          </>
        )}
      </div>

      {connected && (
        <>
          <div>
            <div style={{ color: 'var(--text-dim)', marginBottom: '0.3rem' }}>New match</div>
            <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
              {games.map((g) => (
                <button key={g.id} type="button" onClick={() => startMatch(g.id)}>
                  {g.name}
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.4rem' }}>
              <button
                type="button"
                onClick={() => registry.openPanel('games.loadout')}
                style={{ fontSize: '0.72rem' }}
              >
                Edit agent harness →
              </button>
              <button
                type="button"
                onClick={() => registry.openPanel('games.leaderboard')}
                style={{ fontSize: '0.72rem' }}
              >
                Ladder →
              </button>
              <button
                type="button"
                onClick={() => registry.openPanel('games.challenges')}
                style={{ fontSize: '0.72rem' }}
              >
                Challenges →
              </button>
            </div>
          </div>

          <div>
            <div style={{ color: 'var(--text-dim)', marginBottom: '0.3rem' }}>
              Open tables{' '}
              <button
                type="button"
                onClick={() => gamesListTables()}
                style={{ fontSize: '0.7rem' }}
              >
                refresh
              </button>
            </div>
            {tables.length === 0 ? (
              <div style={{ color: 'var(--text-dim)' }}>No tables yet.</div>
            ) : (
              <ul
                style={{
                  listStyle: 'none',
                  padding: 0,
                  margin: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.25rem',
                }}
              >
                {tables.map((t) => (
                  <li key={t.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <code>{t.game_id}</code>
                    <span style={{ color: 'var(--text-dim)' }}>
                      {t.seats.filter(Boolean).length}/{t.capacity} · {t.status}
                    </span>
                    {t.status === 'open' && (
                      <button
                        type="button"
                        onClick={() => {
                          registry.openPanel('games.board');
                          gamesJoinTable(t.id);
                        }}
                      >
                        Join
                      </button>
                    )}
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
