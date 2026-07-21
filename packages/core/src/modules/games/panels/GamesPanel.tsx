import { useRef, type CSSProperties } from 'react';

import { useGames } from '../game-ws';
import {
  SECTION_ICON,
  SECTION_LABEL,
  setGamesSection,
  useGamesSection,
  type GamesSection,
} from '../hub-section';
import { AgentBuilderPanel } from './AgentBuilderPanel';
import { GameBoardPanel } from './GameBoardPanel';
import { LobbyPanel } from './LobbyPanel';

/**
 * **The Games pane** (`games.lobby`) — one pane, three sections: Play, Game Board,
 * and Build your agent. These were three separate panes tiled beside each other;
 * merging them means the whole play loop (pick a game → engineer your agent → watch
 * it play) is one surface you switch between, and the frame only has to place one
 * pane. The live spectator surfaces (Games Log, Episodes) stay separate panes.
 *
 * Sections are **mounted lazily but never unmounted** once visited: the builder holds
 * unsaved code and the board holds canvas/animation state, and neither should be lost
 * by looking at the other. Hidden sections are `display: none` rather than torn down.
 *
 * See docs/modules/games.mdx.
 */

const SECTIONS: GamesSection[] = ['play', 'board', 'build'];

/* Longhand border properties, not the `border` shorthand: `activeTab` below spreads
   this and overrides only the colour, and React warns on every switch between the two
   ("Removing a style property during rerender (borderColor) when a conflicting
   property is set (border)…") when a shorthand and its longhand are mixed. */
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
  // Sections render only after they've been visited (so opening the Games pane
  // doesn't boot the board's canvases), then stay mounted.
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
  if (section === 'play') return <LobbyPanel />;
  if (section === 'board') return <GameBoardPanel />;
  return <AgentBuilderPanel />;
}
