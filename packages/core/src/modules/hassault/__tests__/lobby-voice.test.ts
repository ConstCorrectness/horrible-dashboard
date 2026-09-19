import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LobbyVoice, isOfferer, voicePeers } from '../lobby-voice';
import type { LobbyMember, LobbyState } from '../session';

function member(id: string, voice = true, muted = false): LobbyMember {
  return { id, name: id, ready: false, host: false, remote: false, voice, muted };
}

function lobby(you: string, members: LobbyMember[]): LobbyState {
  return {
    id: 'L1',
    map: 'hd_assault',
    mode: '',
    room: '',
    revision: 1,
    maxPlayers: 16,
    members,
    chat: [],
    you,
    isHost: false,
    host: '',
  };
}

/** Just enough of an RTCPeerConnection to watch the handshake go by. */
class FakePC {
  static made: FakePC[] = [];
  signalingState = 'stable';
  connectionState = 'new';
  remoteDescription: unknown = null;
  closed = false;
  candidates: unknown[] = [];
  onicecandidate: unknown = null;
  ontrack: unknown = null;
  onconnectionstatechange: unknown = null;
  constructor() {
    FakePC.made.push(this);
  }
  addTrack() {}
  async createOffer() {
    return { type: 'offer', sdp: 'offer-sdp' };
  }
  async createAnswer() {
    return { type: 'answer', sdp: 'answer-sdp' };
  }
  async setLocalDescription(d: { type: string }) {
    if (d.type === 'offer') this.signalingState = 'have-local-offer';
  }
  async setRemoteDescription(d: unknown) {
    this.remoteDescription = d;
    this.signalingState = 'stable';
  }
  async addIceCandidate(c: unknown) {
    this.candidates.push(c);
  }
  close() {
    this.closed = true;
  }
}

/** Start voice without a microphone or an AudioContext. */
function activeVoice(): { voice: LobbyVoice; sent: [string, Record<string, unknown>][] } {
  const voice = new LobbyVoice();
  const sent: [string, Record<string, unknown>][] = [];
  voice.sendSignal = (to, signal) => sent.push([to, signal]);
  // The private fields `start` would fill; the handshake logic needs none of them.
  (voice as unknown as { view: { active: boolean } }).view.active = true;
  return { voice, sent };
}

beforeEach(() => {
  FakePC.made = [];
  vi.stubGlobal('RTCPeerConnection', FakePC);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('who connects to whom', () => {
  it('lists everyone else in voice, and nobody out of it', () => {
    const state = lobby('b', [member('a'), member('b'), member('c', false), member('d')]);
    expect(voicePeers(state)).toEqual(['a', 'd']);
    expect(voicePeers(null)).toEqual([]);
  });

  it('gives each pair exactly one offerer', () => {
    for (const [x, y] of [
      ['a', 'b'],
      ['9f', '1c'],
    ]) {
      expect(isOfferer(x, y)).not.toBe(isOfferer(y, x));
    }
  });
});

describe('the handshake', () => {
  it('offers only to peers with a larger id, and waits for the rest', async () => {
    const { voice, sent } = activeVoice();
    voice.sync(lobby('m', [member('a'), member('m'), member('z')]));
    await Promise.resolve();
    await Promise.resolve();
    expect(sent.filter(([, s]) => s.kind === 'offer').map(([to]) => to)).toEqual(['z']);
  });

  it('answers an offer from a smaller id, and ignores one from a larger id', async () => {
    const { voice, sent } = activeVoice();
    voice.sync(lobby('m', [member('a'), member('m'), member('z')]));
    await new Promise((r) => setTimeout(r, 0)); // let our own offer to `z` go out
    sent.length = 0;
    await voice.accept('a', { kind: 'offer', sdp: 'x' });
    expect(sent).toEqual([['a', { kind: 'answer', sdp: 'answer-sdp' }]]);
    sent.length = 0;
    // `z` should be answering *our* offer; its offer would be glare.
    await voice.accept('z', { kind: 'offer', sdp: 'x' });
    expect(sent).toEqual([]);
  });

  it('holds ICE candidates until the remote description is set', async () => {
    const { voice } = activeVoice();
    voice.sync(lobby('a', [member('a'), member('b')]));
    await Promise.resolve();
    const pc = FakePC.made[0];
    await voice.accept('b', { kind: 'ice', candidate: { candidate: 'c1' } });
    expect(pc.candidates).toEqual([]);
    await voice.accept('b', { kind: 'answer', sdp: 'y' });
    expect(pc.candidates).toEqual([{ candidate: 'c1' }]);
  });

  it('hangs up on someone who leaves voice', async () => {
    const { voice } = activeVoice();
    voice.sync(lobby('a', [member('a'), member('b')]));
    await Promise.resolve();
    voice.sync(lobby('a', [member('a'), member('b', false)]));
    expect(FakePC.made[0].closed).toBe(true);
    expect(voice.current.links).toEqual({});
  });

  it('ignores signals while not in voice', async () => {
    const voice = new LobbyVoice();
    const sent: unknown[] = [];
    voice.sendSignal = (...args) => sent.push(args);
    await voice.accept('a', { kind: 'offer', sdp: 'x' });
    expect(FakePC.made).toEqual([]);
    expect(sent).toEqual([]);
  });
});
