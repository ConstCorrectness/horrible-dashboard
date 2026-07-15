/**
 * Shared "active game" selection for the games module: the Games Library (lobby)
 * and the **Build your agent** harness pane both read/write it, so picking a game
 * in one place switches the other. Without this the harness had its own private
 * game state and stayed stuck on the wrong game when you switched games in the
 * library — the starter/template you were editing didn't follow your selection.
 *
 * Same module-level store pattern as game-ws.ts (useSyncExternalStore) and the
 * challenge-draft hand-off.
 */
import { useSyncExternalStore } from 'react';

import { revealRegionView } from '../../layout/controller';

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

/** Set the active game AND reveal the harness editor on it — the "Edit Harness"
 * hand-off, so the builder opens on the game whose card you clicked. */
export function openHarnessFor(gameId: string): void {
  setActiveGame(gameId);
  revealRegionView('games.loadout');
}
