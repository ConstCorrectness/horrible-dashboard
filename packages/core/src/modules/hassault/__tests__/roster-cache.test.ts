/**
 * `RosterCache`: a server sending slim rows (`join.caps: ['roster']`) must
 * leave the client with exactly the rows an older server sends in full.
 */
import { describe, expect, it } from 'vitest';

import { RosterCache, WIRE_CAPS, type PlayerRow, type RosterEntry, type Snapshot } from '../net';

function slimRow(id: string, x: number): PlayerRow {
  return { id, x, y: 0, z: 0, yaw: 0, pitch: 0, hp: 100, alive: true } as unknown as PlayerRow;
}

function snapshot(players: PlayerRow[], extra: Partial<Snapshot> = {}): Snapshot {
  return { room: 'r1', tick: 1, t: 0, ack: 0, players, ...extra };
}

const alice: RosterEntry = { id: 'a', name: 'alice', team: 0, kills: 2, deaths: 1, bot: false };
const bob: RosterEntry = { id: 'b', name: 'bob', team: 1, kills: 0, deaths: 3, bot: true };

describe('RosterCache', () => {
  it('declares the roster capability', () => {
    expect(WIRE_CAPS).toContain('roster');
  });

  it('fills slim rows from the roster that came with them', () => {
    const cache = new RosterCache();
    const s = snapshot([slimRow('a', 1), slimRow('b', 2)], { rv: 1, roster: [alice, bob] });
    cache.merge(s);
    expect(s.players[0]).toMatchObject({
      name: 'alice',
      team: 0,
      kills: 2,
      deaths: 1,
      bot: false,
      x: 1,
    });
    expect(s.players[1]).toMatchObject({ name: 'bob', team: 1, bot: true, x: 2 });
  });

  it('keeps filling from the last roster on ticks that carry only the version', () => {
    const cache = new RosterCache();
    cache.merge(snapshot([slimRow('a', 1)], { rv: 1, roster: [alice] }));
    const next = snapshot([slimRow('a', 5)], { rv: 1 });
    cache.merge(next);
    expect(next.players[0]).toMatchObject({ name: 'alice', kills: 2, x: 5 });
  });

  it('replaces the roster when a new version arrives', () => {
    const cache = new RosterCache();
    cache.merge(snapshot([slimRow('a', 1)], { rv: 1, roster: [alice] }));
    const next = snapshot([slimRow('a', 1)], { rv: 2, roster: [{ ...alice, kills: 3 }] });
    cache.merge(next);
    expect(next.players[0].kills).toBe(3);
  });

  it('leaves full rows from an older server alone', () => {
    const cache = new RosterCache();
    cache.merge(snapshot([slimRow('a', 1)], { rv: 1, roster: [alice] }));
    const full = {
      ...slimRow('a', 1),
      name: 'from the row',
      team: 1,
      kills: 9,
      deaths: 9,
      bot: false,
    };
    const s = snapshot([full as PlayerRow]);
    cache.merge(s);
    expect(s.players[0].name).toBe('from the row');
  });

  it('forgets the last room on reset', () => {
    const cache = new RosterCache();
    cache.merge(snapshot([slimRow('a', 1)], { rv: 1, roster: [alice] }));
    cache.reset();
    const s = snapshot([slimRow('a', 1)], { rv: 1 });
    cache.merge(s);
    expect(s.players[0].name).toBeUndefined();
  });
});
