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
import { registry } from '../../registry';

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

// Instances the harness took over via `openHarnessFor`'s replace-in-place path,
// mapped to the view they used to show — so the harness's back button can swap
// that exact instance back, and so we don't mistake a pane the harness has
// *always* occupied (e.g. the Coding Harnesses preset's dedicated slot) for one
// it merely borrowed from another view.
const replacedFrom = new Map<string, string>();

/** Set the active game AND reveal the harness editor on it — the "Edit Harness"
 * hand-off, so the builder opens on the game whose card you clicked.
 *
 * If the harness is already open anywhere, just focus it. Otherwise, when the
 * caller passes its own pane instance id (the Games pane the button lives in),
 * replace that pane's content with the harness in place — rather than opening
 * a second pane beside it — so "Edit Harness" feels like drilling into the
 * current view, not spawning a new one. Falls back to opening it standalone. */
export function openHarnessFor(gameId: string, fromInstanceId?: string | null): void {
  setActiveGame(gameId);
  const controller = registry.layoutController;
  const existing = controller?.listOpenPanes().find((p) => p.id === 'games.loadout');
  if (existing) {
    controller?.focusPane(existing.instanceId);
    return;
  }
  if (fromInstanceId && controller?.changePaneType(fromInstanceId, 'games.loadout')) {
    replacedFrom.set(fromInstanceId, 'games.lobby');
    return;
  }
  revealRegionView('games.loadout');
}

/** The view instance id `instanceId` replaced to show the harness, if any —
 * powers the harness's "back" button. */
export function harnessReplacedView(instanceId: string): string | null {
  return replacedFrom.get(instanceId) ?? null;
}

/** Swap `instanceId` back to whatever it showed before `openHarnessFor` took it
 * over. No-op if it wasn't a replace-in-place instance. */
export function goBackFromHarness(instanceId: string): void {
  const viewId = replacedFrom.get(instanceId);
  if (!viewId) return;
  replacedFrom.delete(instanceId);
  registry.layoutController?.changePaneType(instanceId, viewId);
}
