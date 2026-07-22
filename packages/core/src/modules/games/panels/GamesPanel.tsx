import { useRef, useState, type CSSProperties, type ReactNode } from 'react';

import { closeDrawer, setDrawerTab, useDrawer, type DrawerTab } from '../client-drawer';
import { useGames } from '../game-ws';
import {
  SECTION_ICON,
  SECTION_LABEL,
  setGamesSection,
  useGamesSection,
  type GamesSection,
} from '../hub-section';
import { AgentBuilderPanel } from './AgentBuilderPanel';
import { ChallengesPanel } from './ChallengesPanel';
import { EpisodePanel } from './EpisodePanel';
import { GameBoardPanel } from './GameBoardPanel';
import { GamesLogPanel } from './GamesLogPanel';
import { LeaderboardPanel } from './LeaderboardPanel';
import { LobbyPanel } from './LobbyPanel';
import { PlazaPanel } from './PlazaPanel';
import { ProfilePanel } from './ProfilePanel';
import { ReplayBrowserPanel } from './ReplayBrowserPanel';
import { TownPanel } from './TownPanel';

/**
 * **The Games client** (`games.lobby`) — one pane that is the whole game client, in the
 * shape of a console title (Play, Board, Build, Replays, Career, Social) rather than a
 * scatter of tiled documents. Each menu section mounts the surface it owns; the
 * self-contained auxiliary panels (ladder, challenges, replays, profile, plaza, town)
 * are folded in here instead of opening as separate panes. The live spectator surfaces
 * (Games Log, Episodes) live in an in-client bottom **drawer** (see client-drawer.ts),
 * not forced sibling documents.
 *
 * Sections are **mounted lazily but never unmounted** once visited: the builder holds
 * unsaved code and the board holds canvas state, so hidden sections are `display: none`
 * rather than torn down. See docs/modules/games.mdx.
 */

const SECTIONS: GamesSection[] = ['play', 'board', 'build', 'replays', 'career', 'social'];

/* Longhand border properties, not the `border` shorthand: `activeTab` spreads this and
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
  // Sections render only after they've been visited (so opening the client doesn't boot
  // the board's canvases or the plaza's), then stay mounted.
  const seen = useRef<Set<GamesSection>>(new Set(['play']));
  seen.current.add(section);

  // A live match is worth a badge on the Board tab when you're looking elsewhere.
  const live = board !== null || yourTurn !== null;

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
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '0.35rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          flex: '0 0 auto',
          flexWrap: 'wrap',
        }}
      >
        {SECTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setGamesSection(s)}
            style={s === section ? activeTab : tab}
          >
            <span aria-hidden>{SECTION_ICON[s]}</span>
            {SECTION_LABEL[s]}
            {s === 'board' && live && section !== 'board' && (
              <span
                title={over ? 'match finished' : 'match in progress'}
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: over ? 'var(--text-dim)' : 'var(--accent, #6ea8fe)',
                }}
              />
            )}
          </button>
        ))}
      </nav>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {SECTIONS.map((s) =>
          seen.current.has(s) ? (
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
      <ClientDrawer />
    </div>
  );
}

function SectionBody({ section }: { section: GamesSection }) {
  switch (section) {
    case 'play':
      return <LobbyPanel />;
    case 'board':
      return <GameBoardPanel />;
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

/** The bottom drawer holding the live spectator surfaces (Games Log, Episodes). Collapsed
 * by default; popped by the match hand-offs (revealBoard) or the tab buttons. Contents
 * mount on first open and stay mounted so the log keeps accumulating while collapsed. */
function ClientDrawer() {
  const { open, tab: activeTab } = useDrawer();
  const seen = useRef<Set<DrawerTab>>(new Set());
  if (open) seen.current.add(activeTab);

  const drawerTab = (id: DrawerTab, icon: string, label: string): ReactNode => (
    <button
      key={id}
      type="button"
      onClick={() => setDrawerTab(id)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 5,
        padding: '0.2rem 0.6rem',
        border: 'none',
        background: 'transparent',
        color: open && activeTab === id ? 'var(--text)' : 'var(--text-dim)',
        fontSize: '0.74rem',
        cursor: 'pointer',
        borderBottom:
          open && activeTab === id ? '2px solid var(--accent, #6ea8fe)' : '2px solid transparent',
      }}
    >
      <span aria-hidden>{icon}</span>
      {label}
    </button>
  );

  return (
    <div style={{ flex: '0 0 auto', borderTop: '1px solid var(--border)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          padding: '0 0.4rem',
          background: 'var(--bg-raised, #16171d)',
        }}
      >
        {drawerTab('log', '📜', 'Games Log')}
        {drawerTab('episodes', '🎞', 'Episodes')}
        <button
          type="button"
          onClick={() => (open ? closeDrawer() : setDrawerTab('log'))}
          title={open ? 'Collapse' : 'Expand'}
          style={{
            marginLeft: 'auto',
            border: 'none',
            background: 'transparent',
            color: 'var(--text-dim)',
            cursor: 'pointer',
            fontSize: '0.8rem',
            padding: '0.2rem 0.5rem',
          }}
        >
          {open ? '▾' : '▸'}
        </button>
      </div>
      {open && (
        <div style={{ height: '38vh', minHeight: 180, position: 'relative' }}>
          {(['log', 'episodes'] as DrawerTab[]).map((t) =>
            seen.current.has(t) ? (
              <div
                key={t}
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: t === activeTab ? 'block' : 'none',
                }}
              >
                {t === 'log' ? <GamesLogPanel /> : <EpisodePanel />}
              </div>
            ) : null,
          )}
        </div>
      )}
    </div>
  );
}
