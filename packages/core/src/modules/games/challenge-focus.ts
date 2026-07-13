/**
 * Cross-panel hand-off for the challenge track: a Play card's 🎯 shortcut opens
 * the Challenges panel *pre-set to that game*. The requested game id is buffered
 * (the panel may not be mounted yet) and also broadcast live for an
 * already-visible panel. See docs/architecture/panel-groups.mdx.
 */
import { registry } from '../../registry';

let pending: string | null = null;
const listeners = new Set<(gameId: string) => void>();

/** Open the Challenges panel focused on `gameId`'s scenario set. */
export function requestChallenges(gameId: string): void {
  pending = gameId;
  registry.openPanel('games.challenges');
  listeners.forEach((l) => l(gameId));
}

/** The panel claims the buffered request once, on mount. */
export function claimChallengeFocus(): string | null {
  const p = pending;
  pending = null;
  return p;
}

/** Live updates for a panel that is already mounted. Returns an unsubscribe. */
export function onChallengeFocus(listener: (gameId: string) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
