import { describe, expect, it } from 'vitest';

import { buildIceConfig, parseSignal, turnIsIncomplete } from '../signal';

/**
 * The signalling vocabulary and the ICE config.
 *
 * Both fail *silently* when they are wrong — a malformed frame that throws takes
 * down a working session, and a malformed `RTCConfiguration` makes ICE simply not
 * connect with nothing in any log to say why. So both are pure, and both are
 * pinned here.
 */

describe('parseSignal', () => {
  it('accepts a well-formed offer and answer', () => {
    expect(parseSignal({ kind: 'offer', sessionId: 's1', sdp: 'v=0' })).toEqual({
      kind: 'offer',
      sessionId: 's1',
      sdp: 'v=0',
    });
    expect(parseSignal({ kind: 'answer', sessionId: 's1', sdp: 'v=0' })?.kind).toBe('answer');
  });

  it('accepts an ice candidate and a bye', () => {
    expect(parseSignal({ kind: 'ice', sessionId: 's1', candidate: { candidate: 'x' } })?.kind).toBe(
      'ice',
    );
    expect(parseSignal({ kind: 'bye', sessionId: 's1' })).toEqual({ kind: 'bye', sessionId: 's1' });
  });

  it('rejects a frame with no session id', () => {
    // Without it a frame cannot be matched to a session, and acting on one would
    // mean letting any signal reach any connection.
    expect(parseSignal({ kind: 'offer', sdp: 'v=0' })).toBeNull();
  });

  it('rejects an offer with no sdp', () => {
    expect(parseSignal({ kind: 'offer', sessionId: 's1' })).toBeNull();
    expect(parseSignal({ kind: 'offer', sessionId: 's1', sdp: '' })).toBeNull();
  });

  it('returns null for an unknown kind rather than throwing', () => {
    // A peer on a newer build may send a frame this one has never heard of. The
    // right response is to ignore it, not to tear down a working session.
    expect(parseSignal({ kind: 'renegotiate', sessionId: 's1' })).toBeNull();
  });

  it('survives junk', () => {
    for (const junk of [null, undefined, 42, 'offer', [], {}]) {
      expect(parseSignal(junk)).toBeNull();
    }
  });
});

describe('buildIceConfig', () => {
  it('adds the stun: scheme, matching the backend transport', () => {
    // The setting is a bare host:port on both sides. Two readers of one setting
    // that disagreed about the scheme would fail as "ICE just does not connect".
    expect(buildIceConfig({ stunServer: 'stun.l.google.com:19302' }).iceServers).toEqual([
      { urls: ['stun:stun.l.google.com:19302'] },
    ]);
  });

  it('passes a TURN url through unchanged', () => {
    const { iceServers } = buildIceConfig({
      turnUrl: 'turn:relay.example.com:3478',
      turnUsername: 'u',
      turnCredential: 'p',
    });
    expect(iceServers).toEqual([
      { urls: ['turn:relay.example.com:3478'], username: 'u', credential: 'p' },
    ]);
  });

  it('drops a TURN entry that has no credentials', () => {
    // Browsers reject the whole RTCConfiguration on a malformed server entry, so
    // one half-configured TURN would take STUN down with it and break the case
    // that was already working.
    const { iceServers } = buildIceConfig({
      stunServer: 'stun.example.com:3478',
      turnUrl: 'turn:relay.example.com:3478',
    });
    expect(iceServers).toEqual([{ urls: ['stun:stun.example.com:3478'] }]);
  });

  it('is empty when nothing is configured', () => {
    expect(buildIceConfig({}).iceServers).toEqual([]);
  });

  it('ignores whitespace-only settings', () => {
    expect(buildIceConfig({ stunServer: '   ', turnUrl: '  ' }).iceServers).toEqual([]);
  });
});

describe('turnIsIncomplete', () => {
  it('is false when TURN is not configured at all', () => {
    expect(turnIsIncomplete({})).toBe(false);
  });

  it('is true when a url is set but a credential is missing', () => {
    // TURN is what makes a symmetric NAT work, so "I set up TURN and it still
    // fails" is exactly where a missing username has to be visible.
    expect(turnIsIncomplete({ turnUrl: 'turn:x', turnUsername: 'u' })).toBe(true);
    expect(turnIsIncomplete({ turnUrl: 'turn:x', turnCredential: 'p' })).toBe(true);
  });

  it('is false when TURN is fully configured', () => {
    expect(turnIsIncomplete({ turnUrl: 'turn:x', turnUsername: 'u', turnCredential: 'p' })).toBe(
      false,
    );
  });
});
