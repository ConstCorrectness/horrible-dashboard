/**
 * Turning what the relay said into what the host's chip shows.
 *
 * Pure and in its own file for the reason `net.ts` is: `stream.ts` owns a
 * capture, a peer connection and the audio graph, so it cannot be imported in a
 * unit test — and this mapping is precisely the part that was wrong. The bug it
 * fixes is a chip latched by a successful WHIP POST and never revisited: the
 * relay keeps its registry in one process's memory, so a crash or a redeploy
 * drops every token while our peer connection sits there believing it still has
 * a peer. WebRTC to a dead relay does not raise; it just stops.
 */
import type { LinkStatus, RelayState } from './api';

/** The subset of `StreamState` this mapping owns. */
export interface RelayView {
  relaying: boolean;
  relayState: RelayState;
  relayViewers: number;
  relayError: string | null;
}

/**
 * Fold one status reading into the current view.
 *
 * `unknown` deliberately **preserves** `relaying` and `relayViewers` rather than
 * clearing them. We could not ask, which is not evidence about the relay: a
 * momentary failure to reach our own backend would otherwise flip a healthy
 * stream to "relay down" and send the host off to re-mint a link that was fine.
 * That is the same lie in the other direction, and the reason `unknown` is a
 * state of its own instead of being folded into `gone`.
 */
export function reconcileRelay(prev: RelayView, status: LinkStatus): RelayView {
  if (status.state === 'unknown') {
    return { ...prev, relayState: 'unknown' };
  }
  const live = status.state === 'live';
  return {
    relaying: live,
    relayState: status.state,
    relayViewers: status.viewers,
    // `idle` and `gone` need different advice from the host, so the relay's own
    // wording travels instead of being flattened into a boolean.
    relayError: live ? null : status.detail || null,
  };
}

/**
 * Consecutive polls a state may persist before the host republishes.
 *
 * `idle` means the relay has no publisher at all — our connection is gone and
 * nothing will bring it back — so two readings (one to rule out a race with a
 * publish in flight) are enough. `connecting` means the relay holds our offer
 * and no frame has arrived; ICE through TURN can legitimately take several
 * seconds, so it gets four polls — the first lands right after the POST, so
 * that is ~15s — before the path is written off.
 */
export const REPUBLISH_AFTER: Partial<Record<RelayState, number>> = {
  idle: 2,
  connecting: 4,
};

export interface PublishVerdict {
  /** Consecutive polls that said our stream is not reaching the relay. */
  misses: number;
  republish: boolean;
}

/**
 * Fold one status reading into the decision to republish.
 *
 * Only the two states that mean "we are publishing and the relay is not
 * receiving" count. `unknown` keeps the count where it was — not being able to
 * ask is not evidence — and `gone` resets it, because republishing to a token
 * the relay has forgotten only earns a 404; that one needs a new link, which is
 * the host's call.
 */
export function judgePublish(misses: number, state: RelayState): PublishVerdict {
  if (state === 'unknown') return { misses, republish: false };
  const limit = REPUBLISH_AFTER[state];
  if (limit === undefined) return { misses: 0, republish: false };
  const next = misses + 1;
  return next >= limit ? { misses: 0, republish: true } : { misses: next, republish: false };
}

/**
 * How long to wait before republishing attempt `attempt` (0-based).
 *
 * 2s, 4s, 8s, then every 30s. Fast at first because the usual cause is a blip
 * that is already over; slow after that because a relay that keeps refusing us
 * is not fixed by asking it more often.
 */
export function republishDelay(attempt: number): number {
  return attempt < 3 ? 2000 * 2 ** attempt : 30_000;
}
