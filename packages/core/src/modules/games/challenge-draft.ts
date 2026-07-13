/**
 * Cross-panel hand-off for challenge negotiation: the roster's ⚔️ (or a future
 * profile page) opens the lobby with a challenge draft pre-targeted at a player.
 * Same claim-on-mount + live-broadcast pattern as challenge-focus.ts.
 */
import { openGamesHub } from './hub-section';

export interface ChallengeTarget {
  accountId: string;
  name: string;
}

let pending: ChallengeTarget | null = null;
const listeners = new Set<(target: ChallengeTarget) => void>();

/** Open the hub's Play tab with a challenge draft aimed at `target`. */
export function requestChallengeDraft(target: ChallengeTarget): void {
  pending = target;
  openGamesHub('play');
  listeners.forEach((l) => l(target));
}

/** The lobby claims the buffered draft once, on mount. */
export function claimChallengeDraft(): ChallengeTarget | null {
  const p = pending;
  pending = null;
  return p;
}

/** Live updates for an already-mounted lobby. Returns an unsubscribe. */
export function onChallengeDraft(listener: (target: ChallengeTarget) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
