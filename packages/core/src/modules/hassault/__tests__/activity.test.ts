import { describe, expect, it } from 'vitest';

import { activityDetail } from '../activity';
import { MatchSession } from '../session';

describe('activityDetail', () => {
  it('says nothing outside a match', () => {
    expect(activityDetail(new MatchSession().state)).toBeNull();
  });

  it('names the map, the kind of match and the score', () => {
    const state = {
      ...new MatchSession().state,
      status: 'joined' as const,
      map: 'de_dust2',
      ranked: true,
      scores: [7, 5] as [number, number],
    };
    expect(activityDetail(state)).toBe('de_dust2 · Ranked · 7–5');
  });

  it('calls a friend-hosted match what it is', () => {
    const state = {
      ...new MatchSession().state,
      status: 'joined' as const,
      map: 'mirage',
      host: 'relay:abc',
    };
    expect(activityDetail(state)).toBe('mirage · with friends · 0–0');
  });
});
