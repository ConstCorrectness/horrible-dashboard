import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const sent = vi.hoisted(() => [] as Array<{ to: string; frame: Record<string, unknown> }>);

// `settings` pulls in the registry and React; only the ICE values are read.
vi.mock('../../../settings', () => ({ getSetting: () => '' }));
vi.mock('../ws', () => ({
  sendShareSignal: (to: string, frame: Record<string, unknown>) => sent.push({ to, frame }),
}));

import { SharePublisher, ShareSubscriber } from '../rtc';
import { ANSWER_TIMEOUT_MS, DISCONNECT_GRACE_MS, READY_MIN_INTERVAL_MS } from '../signal';
import { FakePeer, fakeStream } from './fake-rtc';

/**
 * Recovery on the fabric mesh.
 *
 * The host used to re-offer only when the participant list changed, and a guest
 * could only receive an offer while its pane was mounted — so one lost offer or
 * one failed connection left the guest on a black stage for the rest of the
 * session, with the host's connection sitting in `have-local-offer` forever.
 */

function offersTo(node: string) {
  return sent.filter((s) => s.to === node && s.frame.kind === 'offer');
}

/** Let the publisher's createOffer/setLocalDescription awaits settle. */
async function settle() {
  await vi.advanceTimersByTimeAsync(0);
}

describe('SharePublisher', () => {
  let pub: SharePublisher;

  beforeEach(async () => {
    vi.useFakeTimers();
    FakePeer.made = [];
    sent.length = 0;
    vi.stubGlobal('RTCPeerConnection', FakePeer);
    pub = new SharePublisher();
    pub.setStream('s1', fakeStream());
    pub.syncGuests(['g1']);
    await settle();
  });

  afterEach(() => {
    pub.closeAll(null);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('offers each guest a connection tagged with its epoch', () => {
    const [offer] = offersTo('g1');
    expect(offer?.frame).toMatchObject({ kind: 'offer', sessionId: 's1', sdp: 'offer-0' });
    expect(typeof offer?.frame.epoch).toBe('number');
    FakePeer.last().gather();
    expect(sent.at(-1)?.frame).toMatchObject({ kind: 'ice', epoch: offer?.frame.epoch });
  });

  it('re-offers on a fresh connection after a failure, backing off', async () => {
    const first = FakePeer.last();
    first.become('failed');
    expect(first.closed).toBe(true);
    // No `bye`: the share is still live and the picture is on its way back.
    expect(sent.some((s) => s.frame.kind === 'bye')).toBe(false);

    await vi.advanceTimersByTimeAsync(999);
    expect(offersTo('g1')).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(offersTo('g1')).toHaveLength(2);
    expect(FakePeer.last()).not.toBe(first);

    // A second failure waits twice as long.
    FakePeer.last().become('failed');
    await vi.advanceTimersByTimeAsync(1999);
    expect(offersTo('g1')).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(offersTo('g1')).toHaveLength(3);
  });

  it('resets the backoff once a connection comes up', async () => {
    FakePeer.last().become('failed');
    await vi.advanceTimersByTimeAsync(1000);
    FakePeer.last().become('connected');
    FakePeer.last().become('failed');
    await vi.advanceTimersByTimeAsync(1000);
    expect(offersTo('g1')).toHaveLength(3);
  });

  it('does not let a participant emit skip the backoff', async () => {
    // syncGuests runs on every share-state emit — every guest cursor move.
    FakePeer.last().become('failed');
    pub.syncGuests(['g1']);
    pub.syncGuests(['g1']);
    await settle();
    expect(offersTo('g1')).toHaveLength(1);
  });

  it('waits out a disconnect, then re-offers if it has not recovered', async () => {
    const pc = FakePeer.last();
    pc.become('connected');
    pc.become('disconnected');
    await vi.advanceTimersByTimeAsync(DISCONNECT_GRACE_MS - 1);
    expect(pc.closed).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(pc.closed).toBe(true);
    // Recovered from `connected`, so the backoff starts at its first step.
    await vi.advanceTimersByTimeAsync(1000);
    expect(offersTo('g1')).toHaveLength(2);
  });

  it('leaves a disconnect alone that recovers inside the grace', async () => {
    const pc = FakePeer.last();
    const epoch = offersTo('g1')[0]?.frame.epoch as number;
    await pub.accept('g1', { kind: 'answer', sessionId: 's1', sdp: 'ans', epoch });
    pc.become('disconnected');
    await vi.advanceTimersByTimeAsync(DISCONNECT_GRACE_MS / 2);
    pc.become('connected');
    await vi.advanceTimersByTimeAsync(DISCONNECT_GRACE_MS * 4);
    expect(pc.closed).toBe(false);
    expect(offersTo('g1')).toHaveLength(1);
  });

  it('offers again when an offer goes unanswered', async () => {
    const pc = FakePeer.last();
    await vi.advanceTimersByTimeAsync(ANSWER_TIMEOUT_MS);
    expect(pc.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(offersTo('g1')).toHaveLength(2);
  });

  it('does not time out an offer that was answered', async () => {
    const epoch = offersTo('g1')[0]?.frame.epoch as number;
    await pub.accept('g1', { kind: 'answer', sessionId: 's1', sdp: 'ans', epoch });
    await vi.advanceTimersByTimeAsync(ANSWER_TIMEOUT_MS * 2);
    expect(FakePeer.last().remote).toBe('ans');
    expect(offersTo('g1')).toHaveLength(1);
  });

  it('answers a ready from a guest with a fresh offer, at once', async () => {
    const before = FakePeer.last();
    await pub.accept('g1', { kind: 'ready', sessionId: 's1' });
    expect(before.closed).toBe(true);
    expect(offersTo('g1')).toHaveLength(2);
  });

  it('ignores a ready from a node that is not a guest', async () => {
    // `share_signal` reaches the host from any trusted peer.
    await pub.accept('stranger', { kind: 'ready', sessionId: 's1' });
    await pub.accept('g1', { kind: 'ready', sessionId: 'other-session' });
    expect(sent.filter((s) => s.frame.kind === 'offer')).toHaveLength(1);
  });

  it('ignores a ready while not sharing', async () => {
    pub.closeAll(null);
    await pub.accept('g1', { kind: 'ready', sessionId: 's1' });
    expect(offersTo('g1')).toHaveLength(1);
  });

  it('rate-limits ready per guest', async () => {
    await pub.accept('g1', { kind: 'ready', sessionId: 's1' });
    await pub.accept('g1', { kind: 'ready', sessionId: 's1' });
    expect(offersTo('g1')).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(READY_MIN_INTERVAL_MS);
    await pub.accept('g1', { kind: 'ready', sessionId: 's1' });
    expect(offersTo('g1')).toHaveLength(3);
  });

  it('drops an answer addressed to the connection it replaced', async () => {
    const stale = offersTo('g1')[0]?.frame.epoch as number;
    await pub.accept('g1', { kind: 'ready', sessionId: 's1' });
    const fresh = offersTo('g1')[1]?.frame.epoch as number;
    expect(fresh).not.toBe(stale);

    await pub.accept('g1', { kind: 'answer', sessionId: 's1', sdp: 'old', epoch: stale });
    expect(FakePeer.last().remote).toBeNull();
    await pub.accept('g1', { kind: 'ice', sessionId: 's1', candidate: {}, epoch: stale });
    expect(FakePeer.last().candidates).toHaveLength(0);

    await pub.accept('g1', { kind: 'answer', sessionId: 's1', sdp: 'new', epoch: fresh });
    expect(FakePeer.last().remote).toBe('new');
  });

  it('cancels a pending re-offer when the guest leaves', async () => {
    FakePeer.last().become('failed');
    pub.syncGuests([]);
    expect(sent.at(-1)?.frame).toMatchObject({ kind: 'bye', sessionId: 's1' });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(offersTo('g1')).toHaveLength(1);
  });

  it('stops every timer on closeAll', async () => {
    FakePeer.last().become('failed');
    pub.closeAll('bye');
    await vi.advanceTimersByTimeAsync(60_000);
    expect(offersTo('g1')).toHaveLength(1);
    expect(FakePeer.live()).toHaveLength(0);
  });
});

describe('ShareSubscriber', () => {
  let streams: Array<MediaStream | null>;
  let ended: number;
  let sub: ShareSubscriber;

  beforeEach(() => {
    FakePeer.made = [];
    sent.length = 0;
    streams = [];
    ended = 0;
    vi.stubGlobal('RTCPeerConnection', FakePeer);
    sub = new ShareSubscriber(
      (s) => streams.push(s),
      () => (ended += 1),
    );
    sub.expect('s1', 'host');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('says ready to the host', () => {
    sub.ready();
    expect(sent).toEqual([{ to: 'host', frame: { kind: 'ready', sessionId: 's1' } }]);
  });

  it('echoes the offer epoch on its answer and candidates', async () => {
    await sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 9 });
    expect(sent.at(-1)?.frame).toMatchObject({ kind: 'answer', sdp: 'answer-0', epoch: 9 });
    FakePeer.last().gather();
    expect(sent.at(-1)?.frame).toMatchObject({ kind: 'ice', epoch: 9 });
  });

  it('omits the epoch for a host that never sent one', async () => {
    await sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'o' });
    expect(sent.at(-1)?.frame).not.toHaveProperty('epoch');
    await sub.accept('host', { kind: 'ice', sessionId: 's1', candidate: { candidate: 'c' } });
    expect(FakePeer.last().candidates).toHaveLength(1);
  });

  it("drops candidates from the host's previous connection", async () => {
    await sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 2 });
    await sub.accept('host', { kind: 'ice', sessionId: 's1', candidate: {}, epoch: 1 });
    expect(FakePeer.last().candidates).toHaveLength(0);
  });

  it('asks for a new offer when its connection fails, without claiming the share ended', async () => {
    await sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 1 });
    sent.length = 0;
    FakePeer.last().become('failed');
    expect(FakePeer.last().closed).toBe(true);
    expect(streams.at(-1)).toBeNull();
    expect(ended).toBe(0);
    expect(sent).toEqual([{ to: 'host', frame: { kind: 'ready', sessionId: 's1' } }]);
  });

  it('reports the share ended on bye', async () => {
    await sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 1 });
    await sub.accept('host', { kind: 'bye', sessionId: 's1' });
    expect(ended).toBe(1);
    expect(streams.at(-1)).toBeNull();
  });

  it('never answers an offer that a newer one superseded mid-negotiation', async () => {
    const first = sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'a', epoch: 1 });
    const second = sub.accept('host', { kind: 'offer', sessionId: 's1', sdp: 'b', epoch: 2 });
    await Promise.all([first, second]);
    const answers = sent.filter((s) => s.frame.kind === 'answer');
    expect(answers.map((a) => a.frame.epoch)).toEqual([2]);
  });

  it('ignores frames from anyone but the host', async () => {
    await sub.accept('impostor', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 1 });
    expect(FakePeer.made).toHaveLength(0);
  });
});
