/**
 * The Games client's **bottom drawer** — the live spectator surfaces (Games Log,
 * Episodes) folded into the one Games pane instead of being their own tiled documents.
 *
 * A tiny module-level store (same pattern as hub-section.ts) so it survives the pane
 * unmounting and so any hand-off (`revealBoard`, a board button) can pop the drawer to
 * a given tab without a pane open. See GamesPanel's ClientDrawer and docs/modules/games.mdx.
 */
import { useSyncExternalStore } from 'react';

export type DrawerTab = 'log' | 'episodes';

interface DrawerState {
  open: boolean;
  tab: DrawerTab;
}

let state: DrawerState = { open: false, tab: 'log' };
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export function getDrawer(): DrawerState {
  return state;
}

/** Open the drawer on a tab (or toggle it closed if already open on that tab). */
export function openDrawer(tab: DrawerTab): void {
  state = state.open && state.tab === tab ? { ...state, open: false } : { open: true, tab };
  emit();
}

/** Switch the active tab without closing. */
export function setDrawerTab(tab: DrawerTab): void {
  if (state.tab === tab && state.open) return;
  state = { open: true, tab };
  emit();
}

export function closeDrawer(): void {
  if (!state.open) return;
  state = { ...state, open: false };
  emit();
}

export function useDrawer(): DrawerState {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => state,
    () => state,
  );
}
