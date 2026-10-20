/**
 * The Duel row's one line (`duelRatingLabel`): progress before placement, the
 * tier and number after, an invitation when the rating is unknown.
 */
import { describe, expect, it } from 'vitest';

import type { DuelRating } from '../api';
import { duelRatingLabel } from '../MainMenu';

const rating = (over: Partial<DuelRating>): DuelRating => ({
  rating: 1500,
  rd: 350,
  games: 0,
  wins: 0,
  losses: 0,
  placed: false,
  placementGames: 10,
  tier: 'Unranked',
  tierNumber: 0,
  ...over,
});

describe('duelRatingLabel', () => {
  it('invites a player whose rating is unknown', () => {
    expect(duelRatingLabel(null)).toMatch(/sign in/i);
  });

  it('shows placement progress, not a number, before placement', () => {
    const label = duelRatingLabel(rating({ games: 3, wins: 2, losses: 1, rating: 1612 }));
    expect(label).toBe('Placement 3/10 · 2W 1L');
    expect(label).not.toContain('1612');
  });

  it('shows the tier, the rounded rating and the record once placed', () => {
    const placed = rating({
      placed: true,
      games: 14,
      wins: 9,
      losses: 5,
      rating: 1712.6,
      tier: 'Distinguished Master Guardian',
    });
    expect(duelRatingLabel(placed)).toBe('Distinguished Master Guardian · 1713 · 9W 5L');
  });
});
