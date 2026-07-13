/**
 * Cross-panel hand-off for the challenge track: a Play card's 🎯 shortcut lands
 * on the hub's Challenges tab *pre-set to that game*. The requested game id is
 * buffered (the section may not be mounted yet — hub tabs mount lazily)
 * and also broadcast live for an already-visible section. See docs/architecture/panel-groups.mdx.
 */
import { openGamesHub } from './hub-section';

let pending: string | null = null;
const listeners = new Set<(gameId: string) => void>();

/** Open the hub's Challenges tab focused on `gameId`'s scenario set. */
export function requestChallenges(gameId: string): void {
  pending = gameId;
  openGamesHub('challenges');
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
