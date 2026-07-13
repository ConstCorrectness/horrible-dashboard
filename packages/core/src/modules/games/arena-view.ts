/**
 * Playback position for the arena board (round index, tick, autoplay). Held in
 * a module-level store — not component state — because the frame unmounts
 * inactive tab panes: scrubbing to a tick and switching tabs must not reset the
 * viewer (the same reason match state lives in game-ws.ts). `knownRounds` lets
 * the board tell "a new round landed" (auto-follow it) apart from "I just
 * remounted" (leave the scrub position alone).
 */
import { useSyncExternalStore } from 'react';

export interface ArenaView {
  roundIdx: number;
  tick: number;
  playing: boolean;
  /** How many rounds the board has already seen (auto-follow watermark). */
  knownRounds: number;
}

const initial: ArenaView = { roundIdx: 0, tick: 0, playing: true, knownRounds: 0 };

let view: ArenaView = initial;
const listeners = new Set<() => void>();

/** Snapshot read for timers (avoids stale closures in the autoplay interval). */
export function getArenaView(): ArenaView {
  return view;
}

export function setArenaView(patch: Partial<ArenaView>): void {
  view = { ...view, ...patch };
  for (const l of listeners) l();
}

/** Back to the start — called when a new match begins (game-ws `match_info`). */
export function resetArenaView(): void {
  view = initial;
  for (const l of listeners) l();
}

export function useArenaView(): ArenaView {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => view,
  );
}
