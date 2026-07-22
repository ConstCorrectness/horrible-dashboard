import { useCallback, useEffect, useState, type CSSProperties } from 'react';

import { isDesktopShell, openExternal } from '../../../external';
import { registry } from '../../../registry';
import { gameAccent, gameIcon, gameTagline } from '../game-identity';
import { useGames, gamesDisconnect, ensureConnected } from '../game-ws';
import {
  fetchAuthProviders,
  fetchStatus,
  signInWith,
  signInWithRedirect,
  signOut,
  fetchGamesCatalog,
  type AuthProviderFlows,
  type SignInProvider,
  type GameCatalogEntry,
} from '../games-api';
import { setActiveGame } from '../selected-game';
import { ConnectionChip } from './ConnectionChip';
import { PlaySection } from './PlaySection';

/** The provider marks. Inline SVG (the octocat, and Google's four-colour G) rather
 * than emoji or remote images — same rule as the home connector tiles; drawn here
 * rather than imported from `packages/ui` because core must not depend on ui. */
const GITHUB_MARK = (
  <svg viewBox="0 0 16 16" aria-hidden="true" fill="currentColor" width="14" height="14">
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
  </svg>
);
const GOOGLE_MARK = (
  // Google's brand guidelines require the four-colour mark, so it ignores currentColor.
  <svg viewBox="0 0 18 18" aria-hidden="true" width="14" height="14">
    <path
      fill="#4285F4"
      d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"
    />
    <path
      fill="#34A853"
      d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.34A9 9 0 0 0 9 18z"
    />
    <path
      fill="#FBBC05"
      d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.94H.96a9 9 0 0 0 0 8.12l3.01-2.34z"
    />
    <path
      fill="#EA4335"
      d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.94l3.01 2.34C4.68 5.16 6.66 3.58 9 3.58z"
    />
  </svg>
);

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
  const [prompt, setPrompt] = useState<{ code?: string; url: string } | null>(null);
  const [busy, setBusy] = useState<SignInProvider | null>(null);
  const [err, setErr] = useState('');
  // What the game server says it can do; `{}` = unknown (keep buttons enabled).
  const [flows, setFlows] = useState<Partial<Record<SignInProvider, AuthProviderFlows>>>({});

  const refresh = useCallback(() => {
    fetchStatus()
      .then((s) => setName(s.signed_in ? s.display_name : null))
      .catch(() => setName(null));
  }, []);
  useEffect(() => refresh(), [refresh]);
  useEffect(() => {
    fetchAuthProviders()
      .then(setFlows)
      .catch(() => {});
  }, []);

  // A provider is only unavailable when the server *positively* reports neither flow;
  // unknown (older server, node unreachable) stays enabled.
  const unavailable = (provider: SignInProvider): boolean => {
    const f = flows[provider];
    return f != null && !f.device && !f.web;
  };

  const onSignedIn = (display: string) => {
    setName(display);
    gamesDisconnect();
    setTimeout(() => {
      void ensureConnected(false);
    }, 500);
  };

  // Redirect flow first (authorize on the provider, no code typing), falling back to
  // the device flow when the web flow isn't configured on this game server. In the
  // browser the popup is opened synchronously so it isn't blocked, then pointed at
  // whichever page the flow that actually starts needs (consent page, or the
  // provider's device page). Under the desktop shell the webview can't open windows
  // at all, so URLs go straight to the system browser — which is also what OAuth
  // wants there (existing sessions; Google rejects embedded webviews).
  const signIn = async (provider: SignInProvider) => {
    setBusy(provider);
    setErr('');
    const popup = isDesktopShell()
      ? null
      : window.open('', 'games-oauth', 'popup,width=600,height=760');
    let navigated = false;
    const point = (url: string) => {
      navigated = true;
      if (popup && !popup.closed) popup.location.href = url;
      else void openExternal(url);
    };
    try {
      let display: string;
      try {
        display = await signInWithRedirect(provider, (url) => {
          setPrompt({ url });
          point(url);
        });
      } catch (e) {
        // Failed before the consent page ever opened => the web flow isn't available
        // on this server (missing client id/secret). Fall back to the device flow —
        // GitHub's needs only the client id. A failure *after* navigation (timeout,
        // user cancelled) is real and must not restart as a different flow.
        if (navigated) throw e;
        display = await signInWith(provider, (code, url) => {
          setPrompt({ code, url });
          point(url);
        });
      }
      onSignedIn(display);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      if (popup && !popup.closed) popup.close();
      setBusy(null);
      setPrompt(null);
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
              ? 'This game server has no Google OAuth configured (games.google.clientId / clientSecret)'
              : 'Sign in with Google'
          }
        >
          <span className="icon">{GOOGLE_MARK}</span>{' '}
          <span className="games-sidebar-profile-signin-label">Google</span>
        </button>
      </div>
      {prompt &&
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
