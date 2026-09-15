import { describe, expect, it } from 'vitest';

import { matchesModelQuery } from '../model-filter';

describe('matchesModelQuery', () => {
  it('matches anywhere in the id, not only at the start', () => {
    expect(matchesModelQuery('minimax/minimax-m3:free', 'free')).toBe(true);
    expect(matchesModelQuery('qwen/qwen3-coder', 'coder')).toBe(true);
    expect(matchesModelQuery('google/gemma-3-27b-it', 'gemma')).toBe(true);
  });

  it('ignores case', () => {
    expect(matchesModelQuery('minimax/minimax-m3:free', 'FREE')).toBe(true);
  });

  it('requires every word, so extra words narrow the list', () => {
    expect(matchesModelQuery('qwen/qwen3-coder:free', 'free qwen')).toBe(true);
    expect(matchesModelQuery('qwen/qwen3-coder:free', 'free gemma')).toBe(false);
  });

  it('treats a blank query as matching everything', () => {
    expect(matchesModelQuery('anything', '')).toBe(true);
    expect(matchesModelQuery('anything', '   ')).toBe(true);
  });
});
