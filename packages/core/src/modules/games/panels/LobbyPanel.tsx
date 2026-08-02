import { useCallback, useEffect, useState, type CSSProperties } from 'react';

import {
  fetchAuthProviders,
  oauthSignIn,
  signOut,
  type AuthProviders,
  type SignInProvider,
  type SignInPrompt,
} from '../../../account';
import { registry } from '../../../registry';
import { gameAccent, gameIcon, gameTagline } from '../game-identity';
import { useGames, gamesDisconnect, ensureConnected } from '../game-ws';
import { fetchStatus, fetchGamesCatalog, type GameCatalogEntry } from '../games-api';
import { setActiveGame } from '../selected-game';
import { ConnectionChip } from './ConnectionChip';
import { PlaySection } from './PlaySection';

import { GITHUB_MARK, GOOGLE_MARK } from '../../../provider-marks';

/** Sign-in status + OAuth sign-in (GitHub or Google — two different Google accounts
 * are two distinct players, handy for testing across machines). The default is the
 * redirect flow (authorize on the provider, no code typing); when the game server
 * has no web-flow credentials the click falls back to the **device flow** (GitHub's
 * needs only the client id), and a provider the server supports with *neither* flow
 * renders disabled with the reason — no phantom popup that closes itself. Identity
 * lives on the node (the JWT is held server-side); this just reflects and toggles it. */
function SidebarProfile() {
  const { social } = useGames();
  const [name, setName] = useState<string | null>(null);
  // `url` is the provider page to (re)open; `code` is set only for the device-code
  // fallback. Presence of either means a sign-in is in progress.
  const [prompt, setPrompt] = useState<SignInPrompt | null>(null);
  const [busy, setBusy] = useState<SignInProvider | null>(null);
  const [err, setErr] = useState('');
  // What the game server says it can do; `{}` = unknown (keep buttons enabled).
  const [providers, setProviders] = useState<AuthProviders>({ server: '', flows: {} });

  const refresh = useCallback(() => {
    fetchStatus()
      .then((s) => setName(s.signed_in ? s.display_name : null))
      .catch(() => setName(null));
  }, []);
  useEffect(() => refresh(), [refresh]);
  useEffect(() => {
    fetchAuthProviders()
      .then(setProviders)
      .catch(() => {});
  }, []);

  // A provider is only unavailable when the server *positively* reports neither flow;
  // unknown (older server, node unreachable) stays enabled.
  const unavailable = (provider: SignInProvider): boolean => {
    const f = providers.flows[provider];
    return f != null && !f.device && !f.web;
  };

  const onSignedIn = (display: string) => {
    setName(display);
    gamesDisconnect();
    setTimeout(() => {
      void ensureConnected(false);
    }, 500);
  };

  // The popup dance, the redirect-then-device fallback, and the desktop-shell
  // branch all live in the core account service now — HorribleAssault signs into
  // the same account, and one copy of that logic is the point.
  const signIn = async (provider: SignInProvider) => {
    setBusy(provider);
    setErr('');
    try {
      onSignedIn(await oauthSignIn(provider, setPrompt));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  };

  const handleSignOut = () => {
    void signOut().then(() => {
      setName(null);
      gamesDisconnect();
      setTimeout(() => {
        void ensureConnected(false);
      }, 500);
    });
  };

  const renderAvatar = (avatarStr: string) => {
    if (
      avatarStr.startsWith('data:image/') ||
      avatarStr.startsWith('http://') ||
      avatarStr.startsWith('https://')
    ) {
      return (
        <img
          src={avatarStr}
          alt="Avatar"
          style={{
            width: '2.2rem',
            height: '2.2rem',
            borderRadius: '50%',
            objectFit: 'cover',
            border: '2px solid var(--accent, #6ea8fe)',
          }}
        />
      );
    }
    return (
      <div
        style={{
          width: '2.2rem',
          height: '2.2rem',
          borderRadius: '50%',
          fontSize: '1.25rem',
          background: 'rgba(110, 168, 254, 0.1)',
          border: '2px solid var(--accent, #6ea8fe)',
          color: 'var(--accent, #6ea8fe)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {avatarStr}
      </div>
    );
  };

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

  if (name) {
    return (
      <div className="games-sidebar-profile">
        <div className="games-sidebar-profile-expanded">
          <div
            className="games-sidebar-profile-avatar-container"
            onClick={() => registry.openPanel('games.profile')}
            title="Open Full Profile"
          >
            {renderAvatar(profile?.avatar ?? '👤')}
          </div>
          <div className="games-sidebar-profile-info">
            <span className="games-sidebar-profile-name" title={name}>
              {name}
            </span>
            {profile && (
              <>
                <div className="games-sidebar-profile-level">
                  <span>Lv {profile.level}</span>
                  <span style={{ opacity: 0.7, fontSize: '0.65rem' }}>{profile.xp} XP</span>
                </div>
                <div className="games-sidebar-profile-xp-bar" title={`${pct}% to next level`}>
                  <div
                    className="games-sidebar-profile-xp-progress"
                    style={{ width: `${Math.max(4, pct)}%` }}
                  />
                </div>
              </>
            )}
          </div>
        </div>
        <div className="games-sidebar-profile-actions">
          <button
            type="button"
            className="games-sidebar-profile-btn"
            onClick={() => registry.openPanel('games.profile')}
          >
            🪪 Profile
          </button>
          <button type="button" className="games-sidebar-profile-btn" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="games-sidebar-profile-signin-card">
      <div className="games-sidebar-profile-signin-title">Player Profile</div>
      <div className="games-sidebar-profile-signin-buttons">
        <button
          type="button"
          className="games-sidebar-profile-signin-btn"
          onClick={() => void signIn('github')}
          disabled={busy !== null || unavailable('github')}
          title={
            unavailable('github')
              ? 'This game server has no GitHub OAuth configured (games.github.clientId)'
              : 'Sign in with GitHub'
          }
        >
          <span className="icon">{GITHUB_MARK}</span>{' '}
          <span className="games-sidebar-profile-signin-label">GitHub</span>
        </button>
        <button
          type="button"
          className="games-sidebar-profile-signin-btn"
          onClick={() => void signIn('google')}
          disabled={busy !== null || unavailable('google')}
          title={
            unavailable('google')
              ? 'This game server has no Google OAuth configured (games.google.clientId + GAMES_GOOGLE_CLIENT_SECRET)'
              : 'Sign in with Google'
          }
        >
          <span className="icon">{GOOGLE_MARK}</span>{' '}
          <span className="games-sidebar-profile-signin-label">Google</span>
        </button>
      </div>
      {unavailable('github') && unavailable('google') && (
        <span style={{ fontSize: '0.7rem', color: 'var(--warn, #d9a441)', marginTop: '0.2rem' }}>
          {/* Both buttons are disabled, which on its own is indistinguishable from
              both buttons being broken. The reason lives on the game server, so
              name it — under `pnpm dev` that is the bundled local one, which
              ships with no OAuth credentials. */}
          No OAuth configured on{' '}
          {providers.server ? <code>{providers.server}</code> : 'this game server'} — sign in with
          email and password instead.
        </span>
      )}
      {prompt && prompt.blocked && (
        <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
          {/* Nothing opened. Say that, rather than pointing at a popup that is
              not there — the sign-in itself is still running and still polling. */}
          <span style={{ color: 'var(--warn, #d9a441)' }}>Sign-in window blocked.</span>{' '}
          <a
            href={prompt.url}
            target="_blank"
            rel="noreferrer"
            style={{ color: 'var(--accent, #6ea8fe)', fontWeight: 600 }}
          >
            Open the sign-in page
          </a>
          {prompt.code ? (
            <>
              {' '}
              and enter <strong>{prompt.code}</strong>
            </>
          ) : null}
        </span>
      )}
      {prompt &&
        !prompt.blocked &&
        (prompt.code ? (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
            code <strong>{prompt.code}</strong> at{' '}
            <a
              href={prompt.url}
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent, #6ea8fe)' }}
            >
              link
            </a>
          </span>
        ) : (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
            Finish authorizing in the popup — or{' '}
            <a
              href={prompt.url}
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent, #6ea8fe)' }}
            >
              open the sign-in page
            </a>{' '}
            if it didn’t appear.
          </span>
        ))}
      {err && <span style={{ color: 'var(--danger, #e5534b)', fontSize: '0.7rem' }}>{err}</span>}
    </div>
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
        <SidebarProfile />

        <div className="games-lib-sidebar-title">
          <span>🎮</span>
          <span className="games-lib-sidebar-title-text"> GAMES LIBRARY</span>
        </div>
        <div className="games-lib-scroll">
          <div className="games-lib-section-label">Shipped Defaults</div>
          <ul className="games-lib-cards">
            {games.map((g) => {
              const isSelected = selectedGame === g.id;
              const accent = gameAccent(g.id);
              return (
                <li key={g.id}>
                  <button
                    type="button"
                    className={`games-lib-card${isSelected ? ' selected' : ''}`}
                    style={{ '--tile-accent': accent } as CSSProperties}
                    title={g.name}
                    onClick={() => {
                      const next = isSelected ? null : g.id;
                      setSelectedGame(next);
                      // Keep the harness pane in sync so switching games here
                      // switches the "Build your agent" template too.
                      if (next) setActiveGame(next);
                    }}
                  >
                    {/* Large faded glyph reads as the card's "background image". */}
                    <span className="games-lib-card-glyph" aria-hidden>
                      {gameIcon(g.id)}
                    </span>
                    {/* Dark bottom scrim so white text stays readable over the tint. */}
                    <span className="games-lib-card-scrim" aria-hidden />
                    <span className="games-lib-card-body">
                      <span className="games-lib-card-name">{g.name}</span>
                      <span className="games-lib-card-desc">{gameTagline(g.id)}</span>
                    </span>
                    {isSelected && (
                      <span className="games-lib-card-check" aria-hidden>
                        ✓
                      </span>
                    )}
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
        </div>

        <PlaySection games={games} selectedGame={selectedGame} setSelectedGame={setSelectedGame} />
      </div>
    </div>
  );
}
