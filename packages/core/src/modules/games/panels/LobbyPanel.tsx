import { useEffect, useState, type CSSProperties } from 'react';

import { signOut } from '../../../account';
import { useAccount } from '../../../useAccount';
import {
  decisionClassOf,
  gameAccent,
  gameIcon,
  gameTagline,
  GAME_CATEGORIES,
} from '../game-identity';
import { useGames, gamesDisconnect, ensureConnected } from '../game-ws';
import { fetchGamesCatalog, type GameCatalogEntry } from '../games-api';
import { openGamesSection } from '../hub-section';
import { setActiveGame } from '../selected-game';
import { ConnectionChip } from './ConnectionChip';
import { PlaySection } from './PlaySection';

/** Profile card and sign-out for the games sidebar.
 *
 * There is no sign-in branch here any more: the pane is gated (`GamesSignIn`), so
 * this only ever renders for a signed-in node. The state still comes from the shared
 * account store rather than a private copy, which is what makes a sign-out here — or
 * an `auth_invalid` arriving on the games channel — put the gate back up immediately.
 * Identity lives on the node (the JWT is held server-side); this reflects and
 * toggles it. */
function SidebarProfile() {
  const { social } = useGames();
  // The shared account, not a private copy: signing in on the home page or in
  // HorribleAssault must land here too, and this panel used to keep its own
  // `name` state that only a remount would correct.
  const { account, signedIn, refresh: refreshAccount } = useAccount();
  const name = signedIn ? account?.display_name : null;

  /** The play socket authenticates on connect, so a change of identity means
   * dropping it and reconnecting — otherwise the old session keeps playing. */
  const reconnect = () => {
    gamesDisconnect();
    setTimeout(() => {
      void ensureConnected(false);
    }, 500);
  };

  const handleSignOut = () => {
    void signOut().then(() => {
      refreshAccount();
      reconnect();
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

  // Belt and braces: the gate above means `name` is set, but the account can go away
  // mid-session (sign-out, or a token the server rejects) and this renders one frame
  // before the gate does.
  if (!name) return null;

  return (
    <div className="games-sidebar-profile">
      <div className="games-sidebar-profile-expanded">
        <div
          className="games-sidebar-profile-avatar-container"
          onClick={() => openGamesSection('career')}
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
          onClick={() => openGamesSection('career')}
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
          {/* The library is split by CATEGORY rather than listed flat, because the
              two halves are different activities: picking a shelf is picking whether
              you're writing a policy or engineering a prompt. A flat list of twelve
              with a badge on each made that a detail you had to notice. */}
          {GAME_CATEGORIES.map(({ cls, icon, label, blurb }) => {
            const inCategory = games.filter(
              (g) => (g.decision_class ?? decisionClassOf(g.id)) === cls,
            );
            if (!inCategory.length) return null;
            return (
              <div key={cls}>
                <div className="games-lib-section-label" title={blurb}>
                  {icon} {label}
                </div>
                <ul className="games-lib-cards">
                  {inCategory.map((g) => {
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
              </div>
            );
          })}

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
