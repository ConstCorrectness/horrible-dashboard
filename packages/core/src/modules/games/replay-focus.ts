/**
 * Cross-panel hand-off for the replay viewer: leaderboard rows, the replay
 * browser, and the game-over banner all open a *specific* replay. The requested
 * id is buffered (the viewer may not be mounted yet) and also broadcast live for
 * an already-open viewer — the challenge-focus pattern.
 */
import { registry } from '../../registry';

let pending: string | null = null;
const listeners = new Set<(replayId: string) => void>();

/** Open the replay viewer pane on `replayId`. */
export function openReplay(replayId: string): void {
  pending = replayId;
  registry.openPanel('games.replay');
  listeners.forEach((l) => l(replayId));
}

/** The viewer claims the buffered request once, on mount. */
export function claimReplayFocus(): string | null {
  const p = pending;
  pending = null;
  return p;
}

/** Live updates for a viewer that is already mounted. Returns an unsubscribe. */
export function onReplayFocus(listener: (replayId: string) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
