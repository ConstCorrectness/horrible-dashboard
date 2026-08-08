import { useRef, useState, type CSSProperties, type ReactNode } from 'react';

import { useAccount } from '../../../useAccount';
import { useGames } from '../game-ws';
import { setGamesSection, useGamesSection, type GamesSection } from '../hub-section';
import { AgentBuilderPanel } from './AgentBuilderPanel';
import { ChallengesPanel } from './ChallengesPanel';
import { GameBoardPanel } from './GameBoardPanel';
import { GamesAccountLoading, GamesNodeUnreachable, GamesSignIn } from './GamesSignIn';
import { LeaderboardPanel } from './LeaderboardPanel';
import { LobbyPanel } from './LobbyPanel';
import { PlazaPanel } from './PlazaPanel';
import { ProfilePanel } from './ProfilePanel';
import { ReplayBrowserPanel } from './ReplayBrowserPanel';
import { RosterPanel } from './RosterPanel';
import { TownPanel } from './TownPanel';
import { TrainingSection } from './TrainingSection';

/**
 * **The Games client** (`games.lobby`) — one pane that is the whole game client, in the
 * shape of a console title (Play, Board, Build, Replays, Career, Social) rather than a
 * scatter of tiled documents. Each menu section mounts the surface it owns; the
 * self-contained auxiliary panels (ladder, challenges, replays, players, profile,
 * plaza, town) are folded in here instead of opening as separate panes.
 *
 * The **menu strip and the bottom drawer are host chrome now** — `sections` and a
 * bottom `regions` strip declared on `games.lobby` — so this file renders bodies
 * only. That is what makes the choice persist across a reload and reachable from a
 * keybinding, the palette and the agent, none of which the hand-rolled versions were.
 *
 * Sections are **mounted lazily but never unmounted** once visited: the builder holds
 * unsaved code and the board holds canvas state, so hidden sections are `display: none`
 * rather than torn down. See docs/modules/games.mdx.
 *
 * **Nothing below the gate mounts until the node is signed in.** Every start flow runs
 * through the game server, and the hosted one refuses anonymous play, so a signed-out
 * Lobby is a screen of dead buttons — which is exactly what it used to be. The phase
 * ladder mirrors HorribleAssault's `bootPhase` (modules/hassault/boot.ts).
 */

const SECTIONS: GamesSection[] = ['play', 'board', 'train', 'build', 'replays', 'career', 'social'];

/* Styles for the *sub*-tabs inside the Career and Social sections. The section strip
   itself is host chrome (`SectionTabs`); these are one level down, inside a body.

   Longhand border properties, not the `border` shorthand: `activeTab` spreads this and
   overrides only the colour, and React warns when a shorthand and its longhand are mixed. */
const tab: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  padding: '0.3rem 0.7rem',
  borderRadius: 999,
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'transparent',
  background: 'transparent',
  color: 'var(--text-dim)',
  fontSize: '0.78rem',
  cursor: 'pointer',
};

const activeTab: CSSProperties = {
  ...tab,
  borderColor: 'var(--accent, #6ea8fe)',
  background: 'color-mix(in srgb, var(--accent, #6ea8fe) 14%, transparent)',
  color: 'var(--text)',
};

export function GamesPanel() {
  const section = useGamesSection();
  const { board, yourTurn, over } = useGames();
  const { signedIn, phase, refresh: refreshAccount } = useAccount();
  // Sections render only after they've been visited (so opening the client doesn't boot
  // the board's canvases or the plaza's), then stay mounted.
  const seen = useRef<Set<GamesSection>>(new Set(['play']));
  seen.current.add(section);

  // A live match is worth a badge on the Board tab when you're looking elsewhere.
  const live = board !== null || yourTurn !== null;

  // The three not-signed-in states are genuinely different and must not be collapsed:
  // `loading` is "we haven't asked yet" (a sign-in form here would flash and vanish),
  // `unavailable` is "the node didn't answer" (signing in cannot fix that), and only
  // the last is a confident signed-out. See account-store.ts.
  const gate = !signedIn ? (
    phase === 'loading' ? (
      <GamesAccountLoading />
    ) : phase === 'unavailable' ? (
      <GamesNodeUnreachable onRetry={refreshAccount} />
    ) : (
      <GamesSignIn />
    )
  ) : null;

  return (
    <div
      className="games-theme"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        position: 'relative',
      }}
    >
      <div className="games-grain" aria-hidden />
      {gate}
      {gate === null && live && section !== 'board' && (
        <button
          type="button"
          onClick={() => setGamesSection('board')}
          style={{
            flex: '0 0 auto',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '0.3rem 0.7rem',
            border: 'none',
            borderBottom: '1px solid var(--border)',
            background: 'color-mix(in srgb, var(--accent, #6ea8fe) 14%, transparent)',
            color: 'var(--text)',
            font: 'inherit',
            fontSize: '0.76rem',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: over ? 'var(--text-dim)' : 'var(--accent, #6ea8fe)',
            }}
          />
          {over ? 'Match finished' : 'Match in progress'} — open the Game Board
        </button>
      )}
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {SECTIONS.map((s) =>
          gate === null && seen.current.has(s) ? (
            <div
              key={s}
              style={{
                position: 'absolute',
                inset: 0,
                display: s === section ? 'block' : 'none',
                overflow: 'auto',
              }}
            >
              <SectionBody section={s} />
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}

function SectionBody({ section }: { section: GamesSection }) {
  switch (section) {
    case 'play':
      return <LobbyPanel />;
    case 'board':
      return <GameBoardPanel />;
    case 'train':
      return <TrainingSection />;
    case 'build':
      return <AgentBuilderPanel />;
    case 'replays':
      return <ReplayBrowserPanel />;
    case 'career':
      return (
        <SubTabbed
          tabs={[
            { id: 'profile', label: '🪪 Profile', render: () => <ProfilePanel /> },
            { id: 'ladder', label: '🏆 Ladder', render: () => <LeaderboardPanel /> },
            { id: 'challenges', label: '🎯 Challenges', render: () => <ChallengesPanel /> },
          ]}
        />
      );
    case 'social':
      return (
        <SubTabbed
          tabs={[
            { id: 'plaza', label: '🏛 Plaza', render: () => <PlazaPanel /> },
            { id: 'players', label: '👥 Players', render: () => <RosterPanel /> },
            { id: 'town', label: '🏘 AgentTown', render: () => <TownPanel /> },
          ]}
        />
      );
  }
}

/** A lightweight tabbed container for the sections that fold several self-contained
 * panels together (Career, Social). Tabs mount lazily and stay mounted once visited. */
function SubTabbed({ tabs }: { tabs: { id: string; label: string; render: () => ReactNode }[] }) {
  const [active, setActive] = useState(tabs[0].id);
  const seen = useRef<Set<string>>(new Set([tabs[0].id]));
  seen.current.add(active);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div
        style={{
          display: 'flex',
          gap: 4,
          padding: '0.35rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          flex: '0 0 auto',
        }}
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setActive(t.id)}
            style={t.id === active ? activeTab : tab}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {tabs.map((t) =>
          seen.current.has(t.id) ? (
            <div
              key={t.id}
              style={{
                position: 'absolute',
                inset: 0,
                display: t.id === active ? 'block' : 'none',
              }}
            >
              {t.render()}
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}
