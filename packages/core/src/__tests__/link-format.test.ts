import { describe, expect, it } from 'vitest';

import {
  displayUrl,
  EXPIRY_WARN_S,
  formatRemaining,
  inviteText,
  secondsLeft,
} from '../link-format';

const NOW_MS = 1_700_000_000_000;
const NOW_S = NOW_MS / 1000;

describe('formatRemaining', () => {
  it('is null for a link that never expires', () => {
    expect(formatRemaining(0, NOW_MS)).toBeNull();
    expect(secondsLeft(0, NOW_MS)).toBeNull();
  });

  it('reads in hours and minutes, never seconds', () => {
    expect(formatRemaining(NOW_S + 3 * 3600 + 41 * 60 + 59, NOW_MS)).toBe('3h 41m');
    expect(formatRemaining(NOW_S + 2 * 3600, NOW_MS)).toBe('2h');
    expect(formatRemaining(NOW_S + 12 * 60, NOW_MS)).toBe('12m');
    expect(formatRemaining(NOW_S + 30, NOW_MS)).toBe('under a minute');
  });

  it('says expired rather than a negative duration', () => {
    expect(formatRemaining(NOW_S - 5, NOW_MS)).toBe('expired');
  });

  it('warns inside the last ten minutes', () => {
    expect(EXPIRY_WARN_S).toBe(600);
    expect(secondsLeft(NOW_S + 599, NOW_MS)! < EXPIRY_WARN_S).toBe(true);
  });
});

describe('inviteText', () => {
  const url = 'https://horrible-share.fly.dev/k7m2x9qp';

  it('leads with the link', () => {
    expect(inviteText({ url, lead: 'Watch my screen', nowMs: NOW_MS })).toBe(
      `Watch my screen: ${url}`,
    );
  });

  it('says when it expires', () => {
    const text = inviteText({ url, expiresAt: NOW_S + 90 * 60, nowMs: NOW_MS });
    expect(text).toContain('expires in 1h 30m');
  });

  it('says a passphrase is needed without ever containing one', () => {
    const text = inviteText({ url, passphrase: true, nowMs: NOW_MS });
    expect(text).toContain('passphrase');
    expect(text).toContain('ask me');
  });

  it('does not advertise an expiry that has already passed', () => {
    expect(inviteText({ url, expiresAt: NOW_S - 1, nowMs: NOW_MS })).not.toContain('expires');
  });
});

describe('displayUrl', () => {
  it('drops the scheme', () => {
    expect(displayUrl('https://horrible-share.fly.dev/k7m2x9qp')).toBe(
      'horrible-share.fly.dev/k7m2x9qp',
    );
  });
});
