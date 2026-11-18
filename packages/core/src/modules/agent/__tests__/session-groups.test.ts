/**
 * The sessions rail's recency buckets. The traps are units (the backend's
 * `updated` is epoch seconds) and calendar days versus 24-hour windows.
 */
import { describe, expect, it } from 'vitest';

import { filterSessions, groupSessions, shortAge } from '../session-groups';
import type { ChatSessionMeta } from '../sessions';

/** 2026-11-17 10:00 local. */
const NOW = new Date(2026, 10, 17, 10, 0, 0).getTime();

function at(date: Date, id: string, title = id): ChatSessionMeta {
  return { id, title, agent_id: 'main', updated: date.getTime() / 1000 };
}

describe('groupSessions', () => {
  it('returns nothing for no sessions', () => {
    expect(groupSessions([], NOW)).toEqual([]);
  });

  it('splits on local midnight, not on a 24-hour window', () => {
    const groups = groupSessions(
      [
        at(new Date(2026, 10, 17, 0, 0, 1), 'just-after-midnight'),
        at(new Date(2026, 10, 16, 23, 59, 0), 'last-night'),
      ],
      NOW,
    );
    expect(groups.map((g) => [g.label, g.sessions.map((s) => s.id)])).toEqual([
      ['Today', ['just-after-midnight']],
      ['Yesterday', ['last-night']],
    ]);
  });

  it('puts exactly seven calendar days back in the 7-day bucket and eight in 30', () => {
    const groups = groupSessions(
      [at(new Date(2026, 10, 10, 9, 0), 'seven'), at(new Date(2026, 10, 9, 9, 0), 'eight')],
      NOW,
    );
    expect(groups.map((g) => g.label)).toEqual(['Previous 7 days', 'Previous 30 days']);
  });

  it('orders newest first and leaves empty buckets out', () => {
    const groups = groupSessions(
      [
        at(new Date(2025, 0, 1), 'old'),
        at(new Date(2026, 10, 17, 9, 0), 'a'),
        at(new Date(2026, 10, 17, 9, 30), 'b'),
      ],
      NOW,
    );
    expect(groups.map((g) => g.label)).toEqual(['Today', 'Older']);
    expect(groups[0].sessions.map((s) => s.id)).toEqual(['b', 'a']);
  });
});

describe('shortAge', () => {
  it('reads epoch seconds', () => {
    expect(shortAge(NOW / 1000 - 30, NOW)).toBe('now');
    expect(shortAge(NOW / 1000 - 5 * 60, NOW)).toBe('5m');
    expect(shortAge(NOW / 1000 - 3 * 3600, NOW)).toBe('3h');
    expect(shortAge(NOW / 1000 - 2 * 86_400, NOW)).toBe('2d');
  });
});

describe('filterSessions', () => {
  const list = [at(new Date(NOW), '1', 'Fix the build'), at(new Date(NOW), '2', 'Layout tidy')];
  it('keeps everything for a blank query', () => {
    expect(filterSessions(list, '  ')).toHaveLength(2);
  });
  it('matches titles case-insensitively', () => {
    expect(filterSessions(list, 'BUILD').map((s) => s.id)).toEqual(['1']);
  });
});
