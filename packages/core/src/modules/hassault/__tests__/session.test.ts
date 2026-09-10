import { describe, expect, it } from 'vitest';
import { MatchSession, objectiveNote } from '../session';
import type { KillFx } from '../net';

describe('objectiveNote', () => {
  it('correctly phrases objective banners', () => {
    expect(objectiveNote({ kind: 'flag_take', by: 'player-1' }, 'player-1')?.text).toBe('FLAG TAKEN');
    expect(objectiveNote({ kind: 'flag_take', by: 'player-2' }, 'player-1')?.text).toBe('OUR FLAG IS OUT');
    expect(objectiveNote({ kind: 'bomb_planted', detail: 'A' }, 'player-1')?.text).toBe('BOMB PLANTED AT A');
    expect(objectiveNote({ kind: 'round_start', detail: '2' }, 'player-1')?.text).toBe('ROUND 2');
    expect(objectiveNote({ kind: 'round_live' }, 'player-1')).toBeNull();
  });
});

describe('MatchSession roundKills & streak tracking', () => {
  it('initializes with empty roundKills', () => {
    const session = new MatchSession();
    expect(session.state.roundKills).toEqual([]);
    expect(session.state.killfeed).toEqual([]);
  });

  it('records kills by local player into roundKills and killfeed', () => {
    const session = new MatchSession();
    session.state.playerId = 'me-123';

    // Simulated snapshot message handler
    const killFx: KillFx = {
      kind: 'kill',
      killer: 'me-123',
      killerName: 'AcePlayer',
      killerTeam: 1,
      victim: 'bot-1',
      victimName: 'Bot Alpha',
      victimTeam: 0,
      weapon: 'ak47',
      head: true,
      wallbang: false,
      smoke: true,
      noscope: false,
      airborne: false,
      blind: false,
      nutshot: false,
      backstab: false,
    };

    // Trigger absorb via reflection / private call
    (session as any).absorb(killFx);

    expect(session.state.killfeed.length).toBe(1);
    expect(session.state.roundKills.length).toBe(1);
    expect(session.state.roundKills[0].victimName).toBe('Bot Alpha');
    expect(session.state.roundKills[0].isHeadshot).toBe(true);
    expect(session.state.roundKills[0].isThroughSmoke).toBe(true);

    // Enemy kill does not increment our roundKills
    const otherKill: KillFx = {
      kind: 'kill',
      killer: 'bot-2',
      killerName: 'Bot Bravo',
      killerTeam: 0,
      victim: 'bot-3',
      victimName: 'Bot Charlie',
      victimTeam: 1,
      weapon: 'm4a1',
      head: false,
    };
    (session as any).absorb(otherKill);
    expect(session.state.killfeed.length).toBe(2);
    expect(session.state.roundKills.length).toBe(1); // untouched

    // Second kill by local player (Double kill!)
    const kill2: KillFx = {
      kind: 'kill',
      killer: 'me-123',
      killerName: 'AcePlayer',
      killerTeam: 1,
      victim: 'bot-2',
      victimName: 'Bot Bravo',
      victimTeam: 0,
      weapon: 'knife',
      head: false,
      backstab: true,
    };
    (session as any).absorb(kill2);
    expect(session.state.roundKills.length).toBe(2);
    expect(session.state.roundKills[1].isBackstab).toBe(true);
  });

  it('resets roundKills on round_start or half', () => {
    const session = new MatchSession();
    session.state.playerId = 'me-123';

    (session as any).absorb({
      kind: 'kill',
      killer: 'me-123',
      killerName: 'AcePlayer',
      victim: 'bot-1',
      victimName: 'Bot Alpha',
    });
    expect(session.state.roundKills.length).toBe(1);

    (session as any).absorb({
      kind: 'round_start',
      detail: '2',
    });
    expect(session.state.roundKills.length).toBe(0);
  });
});
