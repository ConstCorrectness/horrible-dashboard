import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const share = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  const signals = new Set<(from: string, payload: unknown) => void>();
  return {
    joined: [] as Array<{ id: string; host_node: string }>,
    sent: [] as Array<{ to: string; frame: Record<string, unknown> }>,
    listeners,
    signals,
    emit: () => listeners.forEach((l) => l()),
    signal: (from: string, payload: unknown) => signals.forEach((h) => h(from, payload)),
  };
});

vi.mock('../../../settings', () => ({ getSetting: () => '' }));
vi.mock('../ws', () => ({
  getShareSnapshot: () => ({ joined: share.joined }),
  subscribeShare: (l: () => void) => {
    share.listeners.add(l);
    return () => share.listeners.delete(l);
  },
  onShareSignal: (h: (from: string, payload: unknown) => void) => {
    share.signals.add(h);
    return () => share.signals.delete(h);
  },
  sendShareSignal: (to: string, frame: Record<string, unknown>) => share.sent.push({ to, frame }),
}));

import { bindGuestMedia, getWatchState, streamFor, subscribeWatch } from '../watch';
import { FakePeer, fakeStream } from './fake-rtc';

/**
 * The guest's connection outlives the mirror pane.
 *
 * The bug this pins: the subscriber lived inside `MirrorPanel`, so a guest who
 * joined and opened the pane later — or whose pane tab was inactive, which
 * unmounts it — had no listener when the host's one-shot offer arrived. Nothing
 * here mounts a pane at all; the stream must arrive anyway.
 */

async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

describe('guest media', () => {
  let teardown: () => void;

  beforeEach(() => {
    FakePeer.made = [];
    share.joined = [];
    share.sent.length = 0;
    vi.stubGlobal('RTCPeerConnection', FakePeer);
    teardown = bindGuestMedia();
  });

  afterEach(() => {
    teardown();
    vi.unstubAllGlobals();
  });

  it('asks the host for an offer as soon as a session is joined', () => {
    share.joined = [{ id: 's1', host_node: 'host' }];
    share.emit();
    expect(share.sent).toEqual([{ to: 'host', frame: { kind: 'ready', sessionId: 's1' } }]);
    // Further emits (cursor moves, grants) must not repeat it.
    share.emit();
    expect(share.sent).toHaveLength(1);
  });

  it('answers an offer and holds the stream with no pane mounted', async () => {
    share.joined = [{ id: 's1', host_node: 'host' }];
    share.emit();
    const changed = vi.fn();
    const off = subscribeWatch(changed);

    share.signal('host', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 4 });
    await settle();
    expect(share.sent.at(-1)?.frame).toMatchObject({ kind: 'answer', epoch: 4 });

    const stream = fakeStream();
    FakePeer.last().ontrack?.({ streams: [stream] });
    expect(streamFor('s1')).toBe(stream);
    expect(getWatchState().streams).toEqual({ s1: stream });
    expect(changed).toHaveBeenCalled();
    off();
  });

  it('routes frames by session and ignores sessions it has not joined', async () => {
    share.joined = [{ id: 's1', host_node: 'host' }];
    share.emit();
    share.signal('host', { kind: 'offer', sessionId: 'elsewhere', sdp: 'o', epoch: 1 });
    share.signal('host', { kind: 'nonsense', sessionId: 's1' });
    await settle();
    expect(FakePeer.made).toHaveLength(0);
  });

  it('closes the connection and drops the stream on leaving', async () => {
    share.joined = [{ id: 's1', host_node: 'host' }];
    share.emit();
    share.signal('host', { kind: 'offer', sessionId: 's1', sdp: 'o', epoch: 1 });
    await settle();
    FakePeer.last().ontrack?.({ streams: [fakeStream()] });

    share.joined = [];
    share.emit();
    expect(FakePeer.last().closed).toBe(true);
    expect(streamFor('s1')).toBeNull();
  });

  it('rebuilds the subscriber when the session moves to another host', () => {
    share.joined = [{ id: 's1', host_node: 'host' }];
    share.emit();
    share.joined = [{ id: 's1', host_node: 'host-2' }];
    share.emit();
    expect(share.sent.map((s) => s.to)).toEqual(['host', 'host-2']);
  });

  it('binds once', () => {
    expect(bindGuestMedia()).toBe(teardown);
    expect(share.signals.size).toBe(1);
  });
});
