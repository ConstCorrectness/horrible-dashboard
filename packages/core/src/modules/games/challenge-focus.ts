/**
 * Cross-panel hand-off for the challenge track: a Play card's 🎯 shortcut opens
 * the Challenges panel *pre-set to that game*. The requested game id is buffered
 * (the panel may not be mounted yet) and also broadcast live for an
 * already-visible panel. See docs/architecture/panel-groups.mdx.
 */
import { openGamesSection } from './hub-section';

let pending: string | null = null;
const listeners = new Set<(gameId: string) => void>();

/** Open the Challenges panel focused on `gameId`'s scenario set. */
export function requestChallenges(gameId: string): void {
  pending = gameId;
  // `games.challenges` is a retired view id: the panel became a section of
  // `games.lobby`. `openPanel` does no alias resolution and returns null in
  // silence, so this button did nothing at all.
  openGamesSection('career');
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
