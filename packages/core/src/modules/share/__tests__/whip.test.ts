import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// `settings` pulls in the registry and React; only the ICE values are read.
vi.mock('../../../settings', () => ({ getSetting: () => '' }));

import { WhipPublisher } from '../whip';

/**
 * Overlapping publishes. A share that starts while a link is being minted
 * publishes from both paths, and the second call's `stop()` closes the first
 * one's connection mid-await. The first then failed on its own closed
 * connection and reported that as a relay error — over a stream the second
 * call had published perfectly well.
 */

class FakePeer {
  static made: FakePeer[] = [];
  iceGatheringState = 'complete';
  localDescription: { sdp: string } | null = null;
  closed = false;
  remote: string | null = null;

  constructor() {
    FakePeer.made.push(this);
  }
  addTransceiver() {}
  addEventListener() {}
  removeEventListener() {}
  async createOffer() {
    return { type: 'offer', sdp: 'offer' };
  }
  async setLocalDescription(d: { sdp: string }) {
    this.localDescription = d;
  }
  async setRemoteDescription(d: { sdp: string }) {
    if (this.closed) throw new Error('InvalidStateError: closed');
    this.remote = d.sdp;
  }
  close() {
    this.closed = true;
  }
}

const stream = { getTracks: () => [] } as unknown as MediaStream;

/** A fetch whose POSTs resolve only when the test says so. */
function gatedFetch() {
  const pending: Array<() => void> = [];
  const calls: string[] = [];
  const fn = vi.fn((url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    if (init?.method !== 'POST') return Promise.resolve(new Response(null));
    return new Promise<Response>((resolve) => {
      pending.push(() => resolve(new Response('answer', { status: 201 })));
    });
  });
  return { fn, pending, calls };
}

describe('WhipPublisher', () => {
  let net: ReturnType<typeof gatedFetch>;

  beforeEach(() => {
    FakePeer.made = [];
    net = gatedFetch();
    vi.stubGlobal('RTCPeerConnection', FakePeer);
    vi.stubGlobal('fetch', net.fn);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('lets the newer of two overlapping publishes win, without an error from the older', async () => {
    const whip = new WhipPublisher();
    const first = whip.publish('https://relay/whip/t', stream);
    await vi.waitFor(() => expect(net.pending).toHaveLength(1));
    const second = whip.publish('https://relay/whip/t', stream);
    await vi.waitFor(() => expect(net.pending).toHaveLength(2));

    net.pending.forEach((answer) => answer());

    await expect(first).resolves.toBe(false);
    await expect(second).resolves.toBe(true);
    expect(whip.live).toBe(true);
    // The survivor is the second connection, and it got its answer.
    expect(FakePeer.made[1].remote).toBe('answer');
    expect(FakePeer.made[0].closed).toBe(true);
  });

  it('treats an outside stop as superseding a publish still in flight', async () => {
    const whip = new WhipPublisher();
    const publishing = whip.publish('https://relay/whip/t', stream);
    await vi.waitFor(() => expect(net.pending).toHaveLength(1));
    await whip.stop();
    net.pending[0]();
    await expect(publishing).resolves.toBe(false);
    expect(whip.live).toBe(false);
  });

  it('still reports a real refusal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 404 })),
    );
    const whip = new WhipPublisher();
    await expect(whip.publish('https://relay/whip/t', stream)).rejects.toThrow(/Mint a new one/);
  });
});
