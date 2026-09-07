import { afterEach, describe, expect, it, vi } from 'vitest';

import { bindClubhouse, clubhouseAction, clubhouseIsLive } from '../actions';

afterEach(() => bindClubhouse(null));

describe('the clubhouse action handle', () => {
  it('does nothing when no room is live', () => {
    // The point of the pattern: `1` pressed outside a room is a no-op, not a crash
    // and not a branch on some joined flag kept in step by hand.
    expect(clubhouseIsLive()).toBe(false);
    expect(() => clubhouseAction('toggleMic')).not.toThrow();
  });

  it('calls through to the mounted room', () => {
    const toggleMic = vi.fn();
    bindClubhouse({ toggleMic });
    clubhouseAction('toggleMic');
    expect(toggleMic).toHaveBeenCalledTimes(1);
  });

  it('stops calling a room that has gone away', () => {
    const toggleMic = vi.fn();
    bindClubhouse({ toggleMic });
    bindClubhouse(null);
    clubhouseAction('toggleMic');
    expect(toggleMic).not.toHaveBeenCalled();
    expect(clubhouseIsLive()).toBe(false);
  });
});
