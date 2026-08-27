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
