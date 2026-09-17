/**
 * Formatting guards for the trajectory run detail.
 *
 * `usd` is the one formatter where a wrong answer is not a cosmetic issue: it is the
 * only place the zero-vs-unknown distinction reaches a human, and the whole point of
 * recording `0.0` for a local model rather than NULL is that the two are different
 * facts. Rendering both as the same string throws that distinction away at the last
 * step, silently.
 */
import { describe, expect, it } from 'vitest';

import { tokens, usd } from '../panels/common';

describe('usd', () => {
  it('renders a free run as free, not as a tiny cost', () => {
    // The bug this pins: `0 < 0.01`, so zero used to fall into the `<$0.01` branch
    // and a local model read as having cost a fraction of a cent.
    expect(usd(0)).toBe('free');
  });

  it('keeps "nobody measured" distinct from "it was free"', () => {
    // null means no figure was ever recorded — the caller renders nothing at all,
    // rather than claiming the run was free.
    expect(usd(null)).toBeNull();
    expect(usd(undefined)).toBeNull();
  });

  it('still floors genuinely tiny non-zero costs', () => {
    expect(usd(0.0004)).toBe('<$0.01');
  });

  it('formats a real cost to cents', () => {
    expect(usd(1.239)).toBe('$1.24');
  });
});

describe('tokens', () => {
  it('renders both halves when both were reported', () => {
    expect(tokens(1200, 340)).toBe('1,200↓ 340↑ tok');
  });

  it('marks a half nobody reported rather than printing a measured-looking zero', () => {
    // The bug this pins: `?? 0` printed `0↓` for a provider that reported only
    // completion tokens — the same absent-means-zero mistake `usd` used to make.
    expect(tokens(null, 340)).toBe('—↓ 340↑ tok');
    expect(tokens(1200, null)).toBe('1,200↓ —↑ tok');
  });

  it('renders nothing at all when no provider reported anything', () => {
    expect(tokens(null, null)).toBeNull();
    expect(tokens(undefined, undefined)).toBeNull();
  });

  it('keeps a genuine zero as a number', () => {
    expect(tokens(0, 0)).toBe('0↓ 0↑ tok');
  });
});
