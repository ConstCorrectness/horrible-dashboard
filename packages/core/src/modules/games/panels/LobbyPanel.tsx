import { useCallback, useEffect, useState, type CSSProperties } from 'react';

import { registry } from '../../../registry';
import { useGames, gamesDisconnect, ensureConnected } from '../game-ws';
import {
  fetchStatus,
  signInWith,
  signInWithRedirect,
  signOut,
  fetchGamesCatalog,
  type SignInProvider,
  type GameCatalogEntry,
} from '../games-api';
import { setActiveGame } from '../selected-game';
import { ConnectionChip } from './ConnectionChip';
import { PlaySection } from './PlaySection';

const GAME_ICONS: Record<string, string> = {
  tictactoe: '❌',
  connect_four: '🔴',
  holdem: '🃏',
  rag_race: '📚',
  code_golf: '⛳',
  test_duel: '⚖️',
  bug_hunt: '🐛',
  arena: '🤖',
  fighter: '🥊',
  vizdoom_toy: '🔫',
  vizdoom_duel: '💀',
};

// A per-game accent color so the library sidebar reads as a shelf of distinct
// games rather than a flat list — used for the tile's icon chip and selected state.
const GAME_ACCENT: Record<string, string> = {
  tictactoe: '#fb7185',
  connect_four: '#fbbf24',
  holdem: '#a78bfa',
  rag_race: '#60a5fa',
  code_golf: '#4ade80',
  test_duel: '#94a3b8',
  bug_hunt: '#84cc16',
  arena: '#fb923c',
  fighter: '#f87171',
  vizdoom_toy: '#dc2626',
  vizdoom_duel: '#c084fc',
};

/** Sign-in status + OAuth sign-in (GitHub or Google — two different Google accounts
 * are two distinct players, handy for testing across machines). The default is the
 * redirect flow (authorize on the provider, no code typing); a device-code fallback
 * remains for when a popup is blocked. Identity lives on the node (the JWT is held
 * server-side); this just reflects and toggles it. */
function SignIn() {
  const { social } = useGames();
  const [name, setName] = useState<string | null>(null);
  // `url` is the provider page to (re)open; `code` is set only for the device-code
  // fallback. Presence of either means a sign-in is in progress.
  const [prompt, setPrompt] = useState<{ code?: string; url: string } | null>(null);
  const [busy, setBusy] = useState<SignInProvider | null>(null);
  const [err, setErr] = useState('');

  const refresh = useCallback(() => {
    fetchStatus()
      .then((s) => setName(s.signed_in ? s.display_name : null))
      .catch(() => setName(null));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const onSignedIn = (display: string) => {
    setName(display);
    gamesDisconnect();
    setTimeout(() => {
      void ensureConnected(false);
    }, 500);
  };

  // Redirect flow: authorize on the provider, no code typing. The popup is opened
  // synchronously here so it isn't blocked, then pointed at the consent URL.
  const signIn = async (provider: SignInProvider) => {
    setBusy(provider);
    setErr('');
    const popup = window.open('', 'games-oauth', 'popup,width=600,height=760');
    try {
      const display = await signInWithRedirect(provider, (url) => {
        setPrompt({ url });
        if (popup && !popup.closed) popup.location.href = url;
        else window.open(url, '_blank', 'noopener');
      });
      onSignedIn(display);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      if (popup && !popup.closed) popup.close();
      setBusy(null);
      setPrompt(null);
    }
  };

  // Fallback: the device-code flow, for when the popup is blocked or preferred.
  const signInWithCode = async (provider: SignInProvider) => {
    setBusy(provider);
    setErr('');
    try {
      const display = await signInWith(provider, (code, url) => setPrompt({ code, url }));
      onSignedIn(display);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
      setPrompt(null);
    }
  };

  if (name) {
    return (
      <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.78rem' }}>
        {social.profile && (
          <span className="games-level-badge" title={`${social.profile.xp} XP`}>
            {social.profile.avatar} Lv {social.profile.level}
          </span>
        )}
        <span style={{ color: 'var(--text-dim)' }}>
          <strong>{name}</strong>
        </span>
        <button
          type="button"
          onClick={() =>
            void signOut().then(() => {
              setName(null);
              gamesDisconnect();
              setTimeout(() => {
                void ensureConnected(false);
              }, 500);
            })
          }
        >
          Sign out
        </button>
      </span>
    );
  }
  return (
    <span style={{ fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
      <button type="button" onClick={() => void signIn('github')} disabled={busy !== null}>
        {busy === 'github' ? 'Signing in…' : 'Sign in'}
      </button>
      <button type="button" onClick={() => void signIn('google')} disabled={busy !== null}>
        {busy === 'google' ? '…' : 'Google'}
      </button>
      {prompt &&
        (prompt.code ? (
          // Device-code fallback: show the code + provider page.
          <span style={{ color: 'var(--text-dim)' }}>
            code <strong>{prompt.code}</strong> at{' '}
            <a href={prompt.url} target="_blank" rel="noreferrer">
              {prompt.url}
            </a>
          </span>
        ) : (
          // Redirect flow: a popup is open; offer a reopen link if it was blocked.
          <span style={{ color: 'var(--text-dim)' }}>
            Waiting for authorization…{' '}
            <a href={prompt.url} target="_blank" rel="noreferrer">
              reopen
            </a>
          </span>
        ))}
      {!prompt && busy === null && (
        <button
          type="button"
          title="Use a sign-in code instead (if the popup is blocked)"
          onClick={() => void signInWithCode('github')}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-dim)',
            cursor: 'pointer',
            fontSize: '0.72rem',
            textDecoration: 'underline',
          }}
        >
          use a code
        </button>
      )}
      {err && <span style={{ color: 'var(--danger, #e5534b)' }}>{err}</span>}
    </span>
  );
}

/**
 * The Games hub — the module's Play entry point (matchmaking). Ladder,
 * Challenges, Replays, Players, and Profile used to be internal tabs here but
 * were too cluttery; they're now standalone panels on the left activity rail
 * (and the command palette). Connection is implicit (see matchmaking.ts); the
 * only connection UI left is the status chip.
 */
export function LobbyPanel() {
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [selectedGame, setSelectedGame] = useState<string | null>(null);

  useEffect(() => {
    fetchGamesCatalog().then(setGames);
  }, []);

  return (
    <div
      className="games-lobby-root"
      style={{ display: 'flex', height: '100%', overflow: 'hidden' }}
    >
      {/* Left Sidebar: Games Library — collapses to an icon rail when the whole
          pane is narrow (a dock rail), see the games-lobby-root container query. */}
      <div className="games-lib-sidebar">
        <div className="games-lib-sidebar-title">
          <span>🎮</span>
          <span className="games-lib-sidebar-title-text"> GAMES LIBRARY</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0.5rem' }}>
          <div className="games-lib-section-label">Shipped Defaults</div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {games.map((g) => {
              const isSelected = selectedGame === g.id;
              const accent = GAME_ACCENT[g.id] ?? 'var(--accent, #6ea8fe)';
              return (
                <li key={g.id} style={{ margin: '0.2rem 0' }}>
                  <button
                    type="button"
                    className={`games-lib-tile${isSelected ? ' selected' : ''}`}
                    style={{ '--tile-accent': accent } as CSSProperties}
                    onClick={() => {
                      const next = isSelected ? null : g.id;
                      setSelectedGame(next);
                      // Keep the harness pane in sync so switching games here
                      // switches the "Build your agent" template too.
                      if (next) setActiveGame(next);
                    }}
                  >
                    <span className="games-lib-tile-icon">{GAME_ICONS[g.id] ?? '🎲'}</span>
                    <span className="games-lib-tile-name">{g.name}</span>
                    {isSelected && <span className="games-lib-tile-dot" />}
                  </button>
                </li>
              );
            })}
          </ul>

          <div
            style={{
              borderTop: '1px solid var(--border)',
              margin: '0.6rem 0.4rem',
              paddingTop: '0.6rem',
            }}
          >
            <button
              type="button"
              className="games-lib-import-btn"
              onClick={() => {
                alert(
                  'Importing custom games and the creator marketplace will be available in a future update!',
                );
              }}
            >
              <span>➕</span>
              <span className="games-lib-import-label"> Import Custom Game</span>
            </button>
          </div>
        </div>
      </div>

      {/* Right Main Content Panel */}
      <div
        className="games-lobby-content"
        style={{
          flex: 1,
          padding: '0.8rem',
          fontSize: '0.85rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.8rem',
          height: '100%',
          overflowY: 'auto',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.6rem',
            flexWrap: 'wrap',
            borderBottom: '1px solid var(--border)',
            paddingBottom: '0.6rem',
          }}
        >
          <ConnectionChip />
          <SignIn />
          <button
            type="button"
            style={{ marginLeft: 'auto' }}
            title="The Plaza — hang out with real players, chat, and challenge them"
            onClick={() => registry.openPanel('games.plaza')}
          >
            🏛 Plaza
          </button>
          <button
            type="button"
            title="AgentTown — spawn your agent in the social fish tank"
            onClick={() => registry.openPanel('games.town')}
          >
            🏘 AgentTown
          </button>
        </div>

        <PlaySection games={games} selectedGame={selectedGame} setSelectedGame={setSelectedGame} />
      </div>
    </div>
  );
}
