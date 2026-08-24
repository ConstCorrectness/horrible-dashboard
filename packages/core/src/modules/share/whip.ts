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

function iceConfig(): RTCConfiguration {
  return buildIceConfig({
    stunServer: getSetting<string>('network.stunServer'),
    turnUrl: getSetting<string>('network.turnUrl'),
    turnUsername: getSetting<string>('network.turnUsername'),
    turnCredential: getSetting<string>('network.turnCredential'),
  });
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

export class WhipPublisher {
  private pc: RTCPeerConnection | null = null;
  private ingestUrl = '';

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
   */
  async publish(ingestUrl: string, stream: MediaStream): Promise<void> {
    await this.stop();
    this.ingestUrl = ingestUrl;

    const pc = new RTCPeerConnection(iceConfig());
    this.pc = pc;
    // `sendonly`, explicitly. Adding a track without a direction gives
    // `sendrecv`, and the relay would then answer with transceivers it is never
    // going to use — harmless in practice and noise in every SDP dump forever.
    for (const track of stream.getTracks()) {
      pc.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
    }

    await pc.setLocalDescription(await pc.createOffer());
    await gathered(pc);

    let res: Response;
    try {
      res = await fetch(ingestUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: pc.localDescription?.sdp ?? '',
      });
    } catch (err) {
      await this.stop();
      throw new Error(`Could not reach the share relay: ${(err as Error).message}`);
    }

    if (!res.ok) {
      await this.stop();
      throw new Error(
        res.status === 404
          ? 'The relay does not know this link any more. Mint a new one.'
          : `The relay refused the stream (${res.status}).`,
      );
    }

    const answer = await res.text();
    await pc.setRemoteDescription({ type: 'answer', sdp: answer });
  }

  /**
   * Stop pushing, and tell the relay.
   *
   * The DELETE is best effort and deliberately not awaited past its failure: the
   * relay drops a publisher whose connection dies anyway, and a host closing a
   * laptop lid should not be waiting on an HTTP round trip.
   */
  async stop(): Promise<void> {
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
}
