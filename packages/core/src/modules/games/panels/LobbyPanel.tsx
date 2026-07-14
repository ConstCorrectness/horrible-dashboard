import { useCallback, useEffect, useState } from 'react';

import { registry } from '../../../registry';
import { useGames, gamesDisconnect, ensureConnected } from '../game-ws';
import { fetchStatus, signInWith, signOut, fetchGamesCatalog, type SignInProvider, type GameCatalogEntry } from '../games-api';
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
};

/** Sign-in status + device-flow sign-in (GitHub or Google — two different Google
 * accounts are two distinct players, handy for testing across machines). Identity
 * lives on the node (the JWT is held server-side); this just reflects and toggles it. */
function SignIn() {
  const { social } = useGames();
  const [name, setName] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<{ code: string; url: string } | null>(null);
  const [busy, setBusy] = useState<SignInProvider | null>(null);
  const [err, setErr] = useState('');

  const refresh = useCallback(() => {
    fetchStatus()
      .then((s) => setName(s.signed_in ? s.display_name : null))
      .catch(() => setName(null));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const signIn = async (provider: SignInProvider) => {
    setBusy(provider);
    setErr('');
    try {
      const display = await signInWith(provider, (code, url) => setPrompt({ code, url }));
      setName(display);
      gamesDisconnect();
      setTimeout(() => {
        void ensureConnected(false);
      }, 500);
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
      {prompt && (
        <span style={{ color: 'var(--text-dim)' }}>
          code <strong>{prompt.code}</strong> at{' '}
          <a href={prompt.url} target="_blank" rel="noreferrer">
            {prompt.url}
          </a>
        </span>
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
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left Sidebar: Games Library */}
      <div
        style={{
          width: '240px',
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--bg-raised, #1d2026)',
          flexShrink: 0,
        }}
      >
        <div style={{ padding: '0.8rem', borderBottom: '1px solid var(--border)', fontWeight: 800, fontSize: '0.85rem', color: 'var(--text-dim)' }}>
          🎮 GAMES LIBRARY
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0.5rem' }}>
          <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', color: 'var(--text-dim)', padding: '0.3rem 0.5rem', fontWeight: 700 }}>
            Shipped Defaults
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {games.map((g) => {
              const isSelected = selectedGame === g.id;
              return (
                <li key={g.id} style={{ margin: '0.2rem 0' }}>
                  <button
                    type="button"
                    style={{
                      width: '100%',
                      textAlign: 'left',
                      background: isSelected ? 'var(--bg-hover, #262a32)' : 'transparent',
                      border: 'none',
                      borderRadius: '6px',
                      padding: '0.4rem 0.6rem',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      color: isSelected ? 'var(--text, #d7dae0)' : 'var(--text-dim, #8a909c)',
                      fontWeight: isSelected ? 800 : 500,
                      transition: 'all 0.15s ease',
                    }}
                    onClick={() => setSelectedGame(isSelected ? null : g.id)}
                  >
                    <span style={{ fontSize: '1.2rem' }}>{GAME_ICONS[g.id] ?? '🎲'}</span>
                    <span style={{ fontSize: '0.82rem', flex: 1 }}>{g.name}</span>
                    {isSelected && <span style={{ color: 'var(--accent, #6ea8fe)', fontSize: '0.75rem' }}>●</span>}
                  </button>
                </li>
              );
            })}
          </ul>

          <div style={{ borderTop: '1px solid var(--border)', margin: '0.6rem 0.4rem', paddingTop: '0.6rem' }}>
            <button
              type="button"
              style={{
                width: '100%',
                background: 'rgba(110, 168, 254, 0.08)',
                border: '1px dashed var(--border)',
                borderRadius: '6px',
                padding: '0.5rem',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '0.3rem',
                color: 'var(--accent, #6ea8fe)',
                fontSize: '0.75rem',
                fontWeight: 700,
              }}
              onClick={() => {
                alert("Importing custom games and the creator marketplace will be available in a future update!");
              }}
            >
              ➕ Import Custom Game
            </button>
          </div>
        </div>
      </div>

      {/* Right Main Content Panel */}
      <div
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', borderBottom: '1px solid var(--border)', paddingBottom: '0.6rem' }}>
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

        <PlaySection
          games={games}
          selectedGame={selectedGame}
          setSelectedGame={setSelectedGame}
        />
      </div>
    </div>
  );
}
