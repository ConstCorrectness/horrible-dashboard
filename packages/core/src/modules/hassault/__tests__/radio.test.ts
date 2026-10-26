/**
 * The radio indicator: who is talking, from the three places that can say so.
 */
import { describe, expect, it } from 'vitest';

import { radioSpeakers } from '../radio';
import { MatchSession } from '../session';

const LOBBY = {
  you: 'm-me',
  members: [
    { id: 'm-me', name: 'rob', ready: true, host: true, remote: false, voice: true, muted: false },
    { id: 'm-kim', name: 'kim', ready: true, host: false, remote: true, voice: true, muted: false },
  ],
};

describe('radio speakers', () => {
  it('is empty when nobody is talking', () => {
    expect(radioSpeakers({ talking: false, speaking: [] }, {}, null)).toEqual([]);
  });

  it('shows you while the radio key is held, even outside a lobby', () => {
    expect(radioSpeakers({ talking: true, speaking: [] }, {}, null)).toEqual([
      { id: 'self', name: 'You', self: true },
    ]);
  });

  it('shows you when your open mic is measured, and names lobby speakers', () => {
    const out = radioSpeakers({ talking: false, speaking: ['m-kim', 'm-me'] }, {}, LOBBY);
    expect(out.map((s) => s.name)).toEqual(['You', 'kim']);
  });

  it('lists a teammate once when both the relay and the mesh report them', () => {
    const out = radioSpeakers({ talking: false, speaking: ['m-kim'] }, { p7: 'Kim' }, LOBBY);
    expect(out).toEqual([{ id: 'p7', name: 'Kim', self: false }]);
  });
});

describe('session radio relay', () => {
  function joined() {
    const session = new MatchSession();
    session.state.status = 'joined';
    session.state.playerId = 'p1';
    const deliver = (session as unknown as { receive: (m: unknown) => void }).receive.bind(session);
    return { session, deliver };
  }

  it('lights a teammate on key-down and clears them on key-up', () => {
    const { session, deliver } = joined();
    deliver({ event: 'voice', data: { playerId: 'p2', playerName: 'kim', transmitting: true } });
    expect(session.state.radio).toEqual({ p2: 'kim' });
    deliver({ event: 'voice', data: { playerId: 'p2', playerName: 'kim', transmitting: false } });
    expect(session.state.radio).toEqual({});
  });

  it('clears a teammate who leaves mid-transmission', () => {
    const { session, deliver } = joined();
    deliver({ event: 'voice', data: { playerId: 'p2', playerName: 'kim', transmitting: true } });
    deliver({ event: 'left', data: { playerId: 'p2' } });
    expect(session.state.radio).toEqual({});
  });

  it('ignores an echo of our own key', () => {
    const { session, deliver } = joined();
    deliver({ event: 'voice', data: { playerId: 'p1', playerName: 'rob', transmitting: true } });
    expect(session.state.radio).toEqual({});
  });
});
