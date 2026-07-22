/**
 * Which section the single **Games pane** is showing.
 *
 * The Games pane (`games.lobby`, `GamesPanel`) is one pane with three internal
 * sections — Play (pick a game / find a match), Game Board (the live match), and
 * Build your agent (the code editor). They used to be three separately-openable
 * panes (`games.lobby`/`games.board`/`games.loadout`) tiled side by side; they're
 * merged now, so "open the board" means "switch this pane's section", not "open
 * another pane".
 *
 * The auxiliary tools (Ladder, Challenges, Replays, Players, Profile) and the two
 * spectator surfaces (Games Log, Episodes) remain their own panes — they're things
 * you watch *alongside* the games pane, not sections of it.
 *
 * Module-level store (useSyncExternalStore), same pattern as game-ws.ts, so the
 * section survives the frame unmounting an inactive pane.
 */
import { useSyncExternalStore } from 'react';

import { registry } from '../../registry';

export type GamesSection = 'play' | 'board' | 'build' | 'replays' | 'career' | 'social';

export const SECTION_LABEL: Record<GamesSection, string> = {
  play: 'Play',
  board: 'Game Board',
  build: 'Build',
  replays: 'Replays',
  career: 'Career',
  social: 'Social',
};

export const SECTION_ICON: Record<GamesSection, string> = {
  play: '🕹',
  board: '▦',
  build: '🛠',
  replays: '📼',
  career: '🪪',
  social: '🏛',
};

let section: GamesSection = 'play';
const listeners = new Set<() => void>();

export function getGamesSection(): GamesSection {
  return section;
}

/** Switch the Games pane's section (no-op if unchanged). */
export function setGamesSection(next: GamesSection): void {
  if (section === next) return;
  section = next;
  for (const l of listeners) l();
}

export function useGamesSection(): GamesSection {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => section,
    () => section,
  );
}

/** Open (or focus) the Games pane on `section` — the one entry point every
 * "show me the board / the builder" hand-off goes through. */
export function openGamesSection(next: GamesSection): void {
  setGamesSection(next);
  registry.openPanel('games.lobby');
}

/** Open (or focus) the Games pane on its Play section. */
export function openGamesHub(): void {
  openGamesSection('play');
}
