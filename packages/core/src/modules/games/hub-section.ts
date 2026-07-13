/**
 * Which section of the Games hub is showing. Module-level (like arena-view.ts)
 * so the hub reopens on the same tab after the frame unmounts it, and so
 * commands / cross-panel handoffs can land on a specific section before the
 * pane has even mounted.
 */
import { useSyncExternalStore } from 'react';

import { registry } from '../../registry';

export type HubSection = 'play' | 'ladder' | 'challenges' | 'replays' | 'players' | 'profile';

let section: HubSection = 'play';
const listeners = new Set<() => void>();

export function setHubSection(next: HubSection): void {
  section = next;
  for (const l of listeners) l();
}

export function useHubSection(): HubSection {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => section,
  );
}

/** Open (or focus) the Games hub, optionally landing on a section. */
export function openGamesHub(target?: HubSection): void {
  if (target) setHubSection(target);
  registry.openPanel('games.lobby');
}
