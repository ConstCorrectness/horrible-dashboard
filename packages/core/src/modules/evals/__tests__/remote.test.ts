/**
 * The cost line on a friend's offer. `0` and `null` are different facts — a local
 * model is free, an unpriced hosted one is unknown — and the person deciding whether
 * to spend their tokens must be able to tell them apart.
 */
import { describe, expect, it } from 'vitest';

import { describeCost } from '../RemoteRun';

describe('describeCost', () => {
  it('says a local model is free', () => {
    expect(describeCost({ estimateTokens: 12400, estimateCostUsd: 0 })).toBe(
      'at least ~12,400 input tokens · free (local model)',
    );
  });

  it('says an unpriced model is unknown, not free', () => {
    expect(describeCost({ estimateTokens: 10, estimateCostUsd: null })).toBe(
      'at least ~10 input tokens · price unknown',
    );
  });

  it('calls a price a floor', () => {
    expect(describeCost({ estimateTokens: 10, estimateCostUsd: 0.5 })).toContain('at least $0.50');
  });
});
