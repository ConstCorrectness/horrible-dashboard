/**
 * Shared "active game" selection for the games module: the Play section and the
 * **Build your agent** section of the Games pane both read/write it, so picking a
 * game in one place switches the other. Without this the builder had its own
 * private game state and stayed stuck on the wrong game when you switched games in
 * the library — the starter/template you were editing didn't follow your selection.
 *
 * Same module-level store pattern as game-ws.ts (useSyncExternalStore) and the
 * challenge-draft hand-off.
 */
import { useSyncExternalStore } from 'react';

import { openGamesSection } from './hub-section';

let activeGame: string | null = null;
const listeners = new Set<() => void>();

/** Set the module-wide active game (no-op if unchanged). */
export function setActiveGame(gameId: string): void {
  if (activeGame === gameId) return;
  activeGame = gameId;
  for (const l of listeners) l();
}

export function getActiveGame(): string | null {
  return activeGame;
}

/** Subscribe a panel to the active game (null until something selects one). */
export function useActiveGame(): string | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => activeGame,
    () => activeGame,
  );
}

/** Set the active game AND show the builder on it — the "Edit agent" hand-off, so
 * the builder opens on the game whose card you clicked.
 *
 * The builder is a section of the Games pane, so this is just a section switch: no
 * second pane to find, focus, or swap back. The builder's own "← Play" button is
 * the way back. */
export function openHarnessFor(gameId: string): void {
  setActiveGame(gameId);
  openGamesSection('build');
}
