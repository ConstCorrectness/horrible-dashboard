import { beforeEach, describe, expect, it } from 'vitest';

import {
  cachedLoad,
  clearLoadCache,
  forgetFailure,
  lastFailure,
  rememberFailure,
  rememberLoad,
} from '../load-cache';

describe('stepper load cache', () => {
  beforeEach(clearLoadCache);

  it('misses as undefined, so a cached null still counts as read', () => {
    expect(cachedLoad('t:1:plane')).toBeUndefined();
    rememberLoad('t:1:plane', null);
    expect(cachedLoad('t:1:plane')).toBeNull();
  });

  it('keeps a failure apart from results until a success clears it', () => {
    rememberFailure('t:0:0:delta', 'not recorded');
    expect(lastFailure('t:0:0:delta')).toBe('not recorded');
    expect(cachedLoad('t:0:0:delta')).toBeUndefined(); // still retried
    forgetFailure('t:0:0:delta');
    expect(lastFailure('t:0:0:delta')).toBeUndefined();
  });

  it('drops the least recently used entry past its bound', () => {
    rememberLoad('first', 1);
    for (let i = 0; i < 199; i++) rememberLoad(`k${i}`, i);
    // Touching `first` makes k0 the oldest instead.
    expect(cachedLoad('first')).toBe(1);
    rememberLoad('one-more', 0);
    expect(cachedLoad('first')).toBe(1);
    expect(cachedLoad('k0')).toBeUndefined();
  });
});
