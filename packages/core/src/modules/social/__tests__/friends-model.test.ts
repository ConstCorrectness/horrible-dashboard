import { describe, expect, it } from 'vitest';

import type { Friend } from '../api';
import { durationSince, groupFriends, personaTone, statusLine } from '../friends-model';

function friend(name: string, patch: Partial<Friend> = {}): Friend {
  return {
    person_id: name,
    display_name: name,
    friend_code: '',
    person_public_key: '',
    status: 'accepted',
    note: null,
    added_at: 0,
    presence: 'online',
    persona: 'online',
    activity: null,
    devices: [],
    is_self: false,
    handle: null,
    account_id: null,
    ...patch,
  };
}

const game = { kind: 'game' as const, title: 'HorribleAssault', detail: 'de_dust2', since: 0 };

describe('groupFriends', () => {
  it('puts each person in exactly one Steam group, in order', () => {
    const groups = groupFriends([
      friend('Off', { presence: 'offline', persona: 'offline' }),
      friend('Gamer', { activity: game }),
      friend('Req', { status: 'pending_in' }),
      friend('Here'),
      friend('Sent', { status: 'pending_out' }),
      friend('Gone', { status: 'blocked' }),
    ]);
    expect(groups.map((g) => [g.id, g.friends.map((f) => f.display_name)])).toEqual([
      ['requests', ['Req']],
      ['ingame', ['Gamer']],
      ['online', ['Here']],
      ['offline', ['Off']],
      ['sent', ['Sent']],
    ]);
  });

  it('sorts the reachable above away and do-not-disturb', () => {
    const online = groupFriends([
      friend('Aaron', { persona: 'away' }),
      friend('Bea', { persona: 'busy' }),
      friend('Zed'),
    ]).find((g) => g.id === 'online');
    expect(online?.friends.map((f) => f.display_name)).toEqual(['Zed', 'Bea', 'Aaron']);
  });

  it('keeps an empty Online group, because "(0)" is the answer', () => {
    const groups = groupFriends([friend('Off', { presence: 'offline', persona: 'offline' })]);
    expect(groups.map((g) => g.id)).toEqual(['online', 'offline']);
  });

  it('filters by name and username', () => {
    const groups = groupFriends([friend('Rob', { handle: 'horrible' }), friend('Ann')], 'HORR');
    expect(groups.flatMap((g) => g.friends).map((f) => f.display_name)).toEqual(['Rob']);
  });

  it('does not treat an app activity as in-game', () => {
    const groups = groupFriends([friend('Nb', { activity: { ...game, kind: 'app' } })]);
    expect(groups.map((g) => g.id)).toEqual(['online']);
  });
});

describe('statusLine', () => {
  it('names the activity first', () => {
    expect(statusLine(friend('a', { activity: game }), 0)).toBe('HorribleAssault — de_dust2');
  });

  it('names a chosen persona', () => {
    expect(statusLine(friend('a', { persona: 'busy' }), 0)).toBe('Do Not Disturb');
  });

  it('says when someone offline was last around', () => {
    const f = friend('a', {
      presence: 'offline',
      persona: 'offline',
      devices: [
        {
          node_id: 'n',
          person_id: 'a',
          node_public_key: '',
          label: 'desk',
          online: false,
          last_seen: 1000,
          last_address: null,
          capabilities: [],
        },
      ],
    });
    expect(statusLine(f, 1000 + 3 * 3600)).toBe('Last online 3 h ago');
  });
});

describe('personaTone', () => {
  it('is offline for anyone not present, whatever they announced', () => {
    expect(personaTone(friend('a', { presence: 'offline', persona: 'busy' }))).toBe('offline');
    expect(personaTone(friend('a', { activity: game }))).toBe('ingame');
  });
});

describe('durationSince', () => {
  it('uses the fewest true words', () => {
    expect(durationSince(0, 30)).toBe('just now');
    expect(durationSince(0, 12 * 60)).toBe('12 min');
    expect(durationSince(0, 125 * 60)).toBe('2 h 5 min');
    expect(durationSince(0, 3 * 86400)).toBe('3 d');
  });
});
