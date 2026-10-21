/**
 * Pushing the host's capture to the public relay over **WHIP**.
 *
 * WHIP is deliberately boring: one HTTP POST whose body is an SDP offer and whose
 * reply is the answer. No signalling channel, no library, no state machine — which
 * is exactly why it is the seam the plan picked. The fabric path (`rtc.ts`) is a
 * different animal: it trickles ICE over the peer socket because it has one.
 *
 * The two consequences of having no trickle channel are the whole of this file:
 *
 * 1. **The offer must wait for ICE gathering.** A one-shot POST carries whatever
 *    candidates exist at the moment it is sent, and an offer sent immediately
 *    carries none. It then connects only if the relay happens to reach the host
 *    unaided, which on a laptop behind NAT is "never, but it looked fine in
 *    development".
 * 2. **Gathering has to be bounded.** A network where one candidate type never
 *    resolves leaves `icegatheringstatechange` pending forever, so the wait is capped
 *    and sends what it has. Fewer candidates is a worse connection; no offer at
 *    all is no stream.
 */

import { getSetting } from '../../settings';

import { buildIceConfig } from './signal';

/** How long to wait for ICE gathering before sending the offer anyway. */
const GATHER_TIMEOUT_MS = 2500;

/** How long to wait for the relay's ICE list before publishing without it. */
const ICE_FETCH_TIMEOUT_MS = 3000;

/**
 * How long `disconnected` may last before the connection counts as lost.
 *
 * `disconnected` is often a blip ICE repairs by itself, so reacting at once
 * would republish over a path that was about to come back. It can also be
 * where a dead path settles for good instead of reaching `failed`, so it cannot
 * be ignored either.
 */
export const DISCONNECT_GRACE_MS = 5000;

/**
 * The relay's own ICE servers for this stream: TURN, usually.
 *
 * The node's `network.*` settings rarely carry TURN, and the host is half of
 * the path — a host behind a strict NAT that offers only host and reflexive
 * candidates reaches a relay on Fly only when both NATs happen to cooperate,
 * which is how a public link came to be black "sometimes". Best effort: a relay
 * that predates this route, or a slow answer, publishes with the settings alone.
 */
async function relayIce(ingestUrl: string): Promise<RTCIceServer[]> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ICE_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${ingestUrl}/ice`, { signal: ctrl.signal });
    if (!res.ok) return [];
    const body = (await res.json()) as { iceServers?: unknown };
    return Array.isArray(body.iceServers) ? (body.iceServers as RTCIceServer[]) : [];
  } catch {
    return [];
  } finally {
    clearTimeout(timer);
  }
}

function iceConfig(extra: RTCIceServer[]): RTCConfiguration {
  const base = buildIceConfig({
    stunServer: getSetting<string>('network.stunServer'),
    turnUrl: getSetting<string>('network.turnUrl'),
    turnUsername: getSetting<string>('network.turnUsername'),
    turnCredential: getSetting<string>('network.turnCredential'),
  });
  return { ...base, iceServers: [...base.iceServers, ...extra] };
}

export interface WhipOptions {
  /**
   * Called when a published connection dies: `failed`, or `disconnected` for
   * longer than `DISCONNECT_GRACE_MS`. Never for a connection this publisher
   * closed itself, and never for a superseded one.
   *
   * Without it a dead host→relay leg was invisible here: WebRTC to a departed
   * peer does not raise, the relay closed the room, and every viewer sat on
   * "Paused" over a share the host believed was live.
   */
  onLost?: () => void;
}

async function gathered(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise<void>((resolve) => {
    const done = () => {
      if (pc.iceGatheringState !== 'complete') return;
      pc.removeEventListener('icegatheringstatechange', done);
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      pc.removeEventListener('icegatheringstatechange', done);
      resolve();
    }, GATHER_TIMEOUT_MS);
    pc.addEventListener('icegatheringstatechange', done);
  });
}

/**
 * A publish the relay answered and refused, carrying its status.
 *
 * The watchdog needs to tell "the relay forgot this link" (404 — retrying only
 * earns another 404; the host has to mint a new one) from everything worth
 * another attempt.
 */
export class WhipRefused extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'WhipRefused';
  }
}

export class WhipPublisher {
  private pc: RTCPeerConnection | null = null;
  private ingestUrl = '';
  /**
   * Bumped by every `publish`. Two calls can overlap — a share starting while
   * a link is being minted publishes from both paths — and the second one's
   * `stop()` closes the first's connection mid-await. Without this the first
   * then failed on its own closed connection and reported that as a relay
   * error, over a stream the second call had published perfectly well.
   */
  private generation = 0;
  private graceTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly options: WhipOptions = {}) {}

  /** Whether media is currently being pushed to a relay. */
  get live(): boolean {
    return this.pc !== null;
  }

  /**
   * Start pushing `stream` at `ingestUrl`.
   *
   * Throws with a message worth showing. A public link that silently fails to
   * carry video is the worst outcome here: the host believes strangers are
   * watching and they are looking at a spinner.
   *
   * Resolves `false` when a later `publish` or a `stop` superseded this one —
   * not an error, and the caller should leave the outcome to whoever won.
   */
  async publish(ingestUrl: string, stream: MediaStream): Promise<boolean> {
    const gen = ++this.generation;
    await this.stop(gen);
    this.ingestUrl = ingestUrl;
    const stale = () => gen !== this.generation;

    const extraIce = await relayIce(ingestUrl);
    if (stale()) return false;

    const pc = new RTCPeerConnection(iceConfig(extraIce));
    this.pc = pc;
    pc.onconnectionstatechange = () => {
      if (stale()) return;
      this.watch(pc.connectionState);
    };
    // `sendonly`, explicitly. Adding a track without a direction gives
    // `sendrecv`, and the relay would then answer with transceivers it is never
    // going to use — harmless in practice and noise in every SDP dump forever.
    for (const track of stream.getTracks()) {
      pc.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
    }

    await pc.setLocalDescription(await pc.createOffer());
    await gathered(pc);
    if (stale()) return false;

    let res: Response;
    try {
      res = await fetch(ingestUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: pc.localDescription?.sdp ?? '',
      });
    } catch (err) {
      if (stale()) return false;
      await this.stop();
      throw new Error(`Could not reach the share relay: ${(err as Error).message}`);
    }
    if (stale()) return false;

    if (!res.ok) {
      await this.stop();
      throw new WhipRefused(
        res.status === 404
          ? 'The relay does not know this link any more. Mint a new one.'
          : `The relay refused the stream (${res.status}).`,
        res.status,
      );
    }

    const answer = await res.text();
    if (stale()) return false;
    await pc.setRemoteDescription({ type: 'answer', sdp: answer });
    return true;
  }

  /**
   * Stop pushing, and tell the relay.
   *
   * The DELETE is best effort and deliberately not awaited past its failure: the
   * relay drops a publisher whose connection dies anyway, and a host closing a
   * laptop lid should not be waiting on an HTTP round trip.
   */
  async stop(generation?: number): Promise<void> {
    // An outside stop supersedes any publish still in flight; the stop inside
    // `publish` passes its own generation so it does not supersede itself.
    if (generation === undefined) this.generation += 1;
    this.clearGrace();
    const pc = this.pc;
    this.pc = null;
    if (!pc) return;
    try {
      pc.close();
    } catch {
      // A connection that is already gone is the state we wanted.
    }
    if (this.ingestUrl) {
      void fetch(this.ingestUrl, { method: 'DELETE' }).catch(() => {});
      this.ingestUrl = '';
    }
  }

  /** React to the live connection's state; only ever called for the current one. */
  private watch(state: RTCPeerConnectionState): void {
    if (state === 'connected') {
      this.clearGrace();
    } else if (state === 'failed') {
      this.clearGrace();
      this.options.onLost?.();
    } else if (state === 'disconnected' && this.graceTimer === null) {
      const gen = this.generation;
      this.graceTimer = setTimeout(() => {
        this.graceTimer = null;
        if (gen !== this.generation || this.pc?.connectionState === 'connected') return;
        this.options.onLost?.();
      }, DISCONNECT_GRACE_MS);
    }
  }

  private clearGrace(): void {
    if (this.graceTimer !== null) clearTimeout(this.graceTimer);
    this.graceTimer = null;
  }
}
