/**
 * The Games client's **bottom drawer** — the live spectator surfaces (Games Log,
 * Episodes) folded into the one Games pane instead of being their own tiled documents.
 *
 * This was a module-level store and a hand-rolled strip inside `GamesPanel`. It is
 * now the frame engine's **bottom region** of `games.lobby`, which the engine
 * already renders, resizes, collapses and **persists per pane instance** — the
 * three things the hand-rolled version didn't do. `games.log` and `games.episodes`
 * stay registered as ordinary panes, so a power user can still pull either out into
 * an area of its own; the region is where they live by default.
 *
 * The function shapes are unchanged so every hand-off (`revealBoard`, the board's
 * "watch the log" button, the builder's run output) kept working untouched.
 * See docs/modules/games.mdx and docs/architecture/windowing.mdx.
 */
import { useSyncExternalStore } from 'react';

import { setRegionView, toggleRegion } from '../../layout/controller';
import { findPaneAnywhere, listPanes } from '../../layout/model';
import { layoutStore } from '../../layout/store';

export type DrawerTab = 'log' | 'episodes';

/** The region view id backing each drawer tab. */
const TAB_VIEW: Record<DrawerTab, string> = {
  log: 'games.log',
  episodes: 'games.episodes',
};

interface DrawerState {
  open: boolean;
  tab: DrawerTab;
}

function gamesInstanceId(): string | null {
  const frame = layoutStore.getSnapshot().frame;
  return listPanes(frame).find((p) => p.pane.viewId === 'games.lobby')?.pane.instanceId ?? null;
}

// `useSyncExternalStore` compares snapshots by reference, so this must hand back
// the *same* object while nothing has changed — deriving a fresh one per call is
// an infinite render loop, not a slow path.
let cached: DrawerState = { open: false, tab: 'log' };

export function getDrawer(): DrawerState {
  const id = gamesInstanceId();
  const pane = id ? findPaneAnywhere(layoutStore.getSnapshot().frame, id)?.pane : null;
  const region = pane?.regions?.bottom ?? null;
  const open = Boolean(region?.open && !region.collapsed);
  const tab: DrawerTab = region?.activeView === TAB_VIEW.episodes ? 'episodes' : 'log';
  if (cached.open !== open || cached.tab !== tab) cached = { open, tab };
  return cached;
}

/** Open the drawer on a tab (or toggle it closed if already open on that tab). */
export function openDrawer(tab: DrawerTab): void {
  const id = gamesInstanceId();
  if (!id) return;
  const current = getDrawer();
  if (current.open && current.tab === tab) {
    toggleRegion(id, 'bottom', false);
    return;
  }
  setRegionView(id, TAB_VIEW[tab]);
}

/** Switch the active tab without closing. */
export function setDrawerTab(tab: DrawerTab): void {
  const id = gamesInstanceId();
  if (id) setRegionView(id, TAB_VIEW[tab]);
}

export function closeDrawer(): void {
  const id = gamesInstanceId();
  if (id) toggleRegion(id, 'bottom', false);
}

export function useDrawer(): DrawerState {
  return useSyncExternalStore(layoutStore.subscribe, getDrawer, getDrawer);
}
