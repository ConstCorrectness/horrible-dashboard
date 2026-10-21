import { describe, expect, it } from 'vitest';

import type { LinkStatus } from '../api';
import {
  judgePublish,
  reconcileRelay,
  REPUBLISH_AFTER,
  republishDelay,
  type RelayView,
} from '../relay-status';

/**
 * The chip used to be latched by a successful WHIP POST and never revisited, so
 * it was a claim about the past. These cases are the four things the relay can
 * say and what each one should do to it.
 */

const RELAYING: RelayView = {
  relaying: true,
  relayState: 'live',
  relayViewers: 4,
  relayError: null,
};

function status(over: Partial<LinkStatus>): LinkStatus {
  return { state: 'live', live: true, viewers: 0, expires_at: 0, detail: '', ...over };
}

describe('reconcileRelay', () => {
  it('reports a relay that is holding the media, with its own viewer count', () => {
    const next = reconcileRelay(RELAYING, status({ state: 'live', viewers: 7 }));
    expect(next.relaying).toBe(true);
    expect(next.relayState).toBe('live');
    expect(next.relayViewers).toBe(7);
    expect(next.relayError).toBeNull();
  });

  it('drops the claim when the relay says it has forgotten the token', () => {
    // The OOM case: the relay is up and has never heard of this token, so every
    // viewer holding the URL is on an expired page.
    const next = reconcileRelay(
      RELAYING,
      status({ state: 'gone', live: false, viewers: 0, detail: 'Mint a new one.' }),
    );
    expect(next.relaying).toBe(false);
    expect(next.relayState).toBe('gone');
    expect(next.relayError).toBe('Mint a new one.');
  });

  it('separates a live link with no picture from a dead one', () => {
    // Different advice: `idle` is fixed by republishing, `gone` by re-minting.
    // A single boolean cannot tell a host which of those to do.
    const next = reconcileRelay(
      RELAYING,
      status({ state: 'idle', live: false, detail: 'Receiving nothing.' }),
    );
    expect(next.relaying).toBe(false);
    expect(next.relayState).toBe('idle');
    expect(next.relayState).not.toBe('gone');
    expect(next.relayError).toBe('Receiving nothing.');
  });

  it('changes nothing but the state when it could not ask', () => {
    // THE case. A flaky hop must not read as "your link is dead" -- that trades
    // a stale truth for a fresh lie and sends the host off to re-mint a link
    // that was working the whole time.
    const next = reconcileRelay(RELAYING, status({ state: 'unknown', live: false }));
    expect(next.relayState).toBe('unknown');
    expect(next.relaying).toBe(true);
    expect(next.relayViewers).toBe(4);
    expect(next.relayError).toBeNull();
  });

  it('does not resurrect a dead link just because the next poll failed', () => {
    // The same rule read from the other side: `unknown` preserves whatever was
    // last actually observed, including bad news.
    const dead: RelayView = {
      relaying: false,
      relayState: 'gone',
      relayViewers: 0,
      relayError: 'Mint a new one.',
    };
    const next = reconcileRelay(dead, status({ state: 'unknown', live: false }));
    expect(next.relaying).toBe(false);
    expect(next.relayError).toBe('Mint a new one.');
  });
});

/**
 * The host's watchdog. A WHIP publish is one POST and nothing afterwards
 * reports the path dying, so the relay's own status decides when to publish
 * again — and when doing so would be pointless.
 */
describe('judgePublish', () => {
  it('republishes once the relay has lost our publisher for two polls', () => {
    let verdict = judgePublish(0, 'idle');
    expect(verdict.republish).toBe(false);
    verdict = judgePublish(verdict.misses, 'idle');
    expect(verdict.republish).toBe(true);
    expect(verdict.misses).toBe(0);
  });

  it('gives a forming ICE path longer than a missing publisher', () => {
    let misses = 0;
    for (let i = 1; i < (REPUBLISH_AFTER.connecting ?? 0); i++) {
      const v = judgePublish(misses, 'connecting');
      expect(v.republish).toBe(false);
      misses = v.misses;
    }
    expect(judgePublish(misses, 'connecting').republish).toBe(true);
  });

  it('resets on a live reading', () => {
    expect(judgePublish(1, 'live')).toEqual({ misses: 0, republish: false });
  });

  it('holds the count when it could not ask', () => {
    // Not being able to reach our own node is not evidence about the relay.
    expect(judgePublish(1, 'unknown')).toEqual({ misses: 1, republish: false });
  });

  it('never republishes to a link the relay has forgotten', () => {
    // That earns a 404 every time; the fix is a new link, which is the host's call.
    expect(judgePublish(5, 'gone')).toEqual({ misses: 0, republish: false });
  });
});

describe('republishDelay', () => {
  it('backs off 2s, 4s, 8s, then settles at 30s', () => {
    expect([0, 1, 2, 3, 10].map(republishDelay)).toEqual([2000, 4000, 8000, 30_000, 30_000]);
  });
});
