/**
 * The scheduling and nagging policy of the background update checker.
 *
 * `startAutoUpdateChecks` itself is short-circuited by `updatesSupported()`
 * (false under vitest, which is not a desktop shell), so what is pinned here is
 * the two decisions it delegates: when a check is due, and when a result is
 * worth putting on screen. Those are where the behaviour a user notices lives —
 * an app that nags every six hours about the same version, or one that goes
 * quiet forever after a single clock skew.
 */
import { describe, expect, it } from 'vitest';

import { CHECK_INTERVAL_MS, isCheckDue, shouldNotify } from '../auto';
import type { UpdateInfo } from '../api';

const NOW = 1_700_000_000_000;

function info(over: Partial<UpdateInfo> = {}): UpdateInfo {
  return {
    available: true,
    currentVersion: '0.1.0',
    version: '0.2.0',
    notes: null,
    date: null,
    channel: 'stable',
    error: null,
    ...over,
  };
}

describe('isCheckDue', () => {
  it('checks when nothing has ever been checked', () => {
    expect(isCheckDue(NOW, 0)).toBe(true);
  });

  it('waits out the interval', () => {
    expect(isCheckDue(NOW, NOW - 60_000)).toBe(false);
    expect(isCheckDue(NOW, NOW - CHECK_INTERVAL_MS)).toBe(true);
  });

  it('treats a future timestamp as due rather than trusting it', () => {
    // A clock that was wrong, or moved back. Trusting the stamp would park the
    // app for however large the skew is — potentially forever.
    expect(isCheckDue(NOW, NOW + CHECK_INTERVAL_MS * 100)).toBe(true);
  });
});

describe('shouldNotify', () => {
  it('surfaces a newer version once', () => {
    expect(shouldNotify(info(), null)).toBe(true);
    expect(shouldNotify(info(), '0.2.0')).toBe(false);
    // A version after the one already announced is a fresh event.
    expect(shouldNotify(info({ version: '0.3.0' }), '0.2.0')).toBe(true);
  });

  it('stays silent when there is nothing new', () => {
    expect(shouldNotify(info({ available: false, version: null }), null)).toBe(false);
    expect(shouldNotify(null, null)).toBe(false);
  });

  it('never surfaces a failed check in the background', () => {
    // The settings section reports this state, because a user who asked
    // deserves the truth. A toast every six hours from an offline laptop
    // teaches people to dismiss update notices unread.
    expect(shouldNotify(info({ error: 'network unreachable' }), null)).toBe(false);
  });

  it('does not fire on availability with no version to name', () => {
    expect(shouldNotify(info({ version: null }), null)).toBe(false);
  });
});
