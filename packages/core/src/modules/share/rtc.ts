/**
 * The media path: browser to browser, with the fabric carrying only the
 * handshake.
 *
 * This is the repo's first browser `RTCPeerConnection`. The one in
 * `backend/modules/network/transport/webrtc.py` is aiortc, carries a *data*
 * channel, and never sees media — routing pixels through it would mean a
 * browser-to-node leg, a node-to-node leg, and PyAV in the middle: two hops and a
 * near-certain re-encode of a stream the browser has already encoded.
 *
 * So the nodes carry SDP and ICE and nothing else (`share_signal`, a documented
 * pass-through), and the media rides DTLS-SRTP directly between the two
 * browsers. It is also why a guest needs no relay account and a stranger will
 * need no dashboard: both ends of every media link are ordinary browsers.
 *
 * **A mesh, not a broadcast.** The host holds one connection per guest and sends
 * the same track down each. That is the right shape for the handful of people a
 * workspace session has, and the wrong shape for an audience — which is exactly
 * what the relay in the next phase is for, and why this file has no fan-out
 * logic to grow into one.
 */
import { getSetting } from '../../settings';

import { buildIceConfig, parseSignal, type IceConfig, type SignalFrame } from './signal';
import { sendShareSignal } from './ws';

function iceConfig(): IceConfig {
  return buildIceConfig({
    stunServer: getSetting<string>('network.stunServer'),
    turnUrl: getSetting<string>('network.turnUrl'),
    turnUsername: getSetting<string>('network.turnUsername'),
    turnCredential: getSetting<string>('network.turnCredential'),
  });
}

function send(nodeId: string, frame: SignalFrame): void {
  sendShareSignal(nodeId, frame);
}

/**
 * The host's side: publish one stream to any number of guests.
 *
 * Guests are added and removed as the participant list changes, so a person who
 * joins mid-stream gets an offer immediately rather than waiting for the host to
 * do something.
 */
export class SharePublisher {
  private readonly peers = new Map<string, RTCPeerConnection>();
  private stream: MediaStream | null = null;
  private sessionId = '';

  /** Start publishing `stream` for `sessionId`. Replaces any previous stream. */
  setStream(sessionId: string, stream: MediaStream | null): void {
    this.sessionId = sessionId;
    this.stream = stream;
    if (stream === null) {
      this.closeAll('bye');
      return;
    }
    // Replace the track on live connections rather than renegotiating: switching
    // what you are sharing should not drop everyone's picture for a second.
    for (const [nodeId, pc] of this.peers) {
      const sender = pc.getSenders().find((s) => s.track?.kind === 'video');
      const track = stream.getVideoTracks()[0] ?? null;
      if (sender && track) void sender.replaceTrack(track);
      else void this.offer(nodeId);
    }
  }

  /** Bring the connection set in line with the session's guests. */
  syncGuests(nodeIds: string[]): void {
    const wanted = new Set(nodeIds);
    for (const nodeId of [...this.peers.keys()]) {
      if (!wanted.has(nodeId)) this.close(nodeId, 'bye');
    }
    if (!this.stream) return;
    for (const nodeId of wanted) {
      if (!this.peers.has(nodeId)) void this.offer(nodeId);
    }
  }

  private peer(nodeId: string): RTCPeerConnection {
    const existing = this.peers.get(nodeId);
    if (existing) return existing;
    const pc = new RTCPeerConnection(iceConfig());
    pc.onicecandidate = (e) => {
      if (e.candidate) {
        send(nodeId, {
          kind: 'ice',
          sessionId: this.sessionId,
          candidate: e.candidate.toJSON(),
        });
      }
    };
    pc.onconnectionstatechange = () => {
      // `failed` is terminal; `disconnected` is often transient and recovers on
      // its own, so tearing down there would drop a picture that was about to
      // come back.
      if (pc.connectionState === 'failed') this.close(nodeId, null);
    };
    this.peers.set(nodeId, pc);
    return pc;
  }

  private async offer(nodeId: string): Promise<void> {
    const stream = this.stream;
    if (!stream) return;
    const pc = this.peer(nodeId);
    if (pc.getSenders().length === 0) {
      for (const track of stream.getTracks()) pc.addTrack(track, stream);
    }
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    send(nodeId, { kind: 'offer', sessionId: this.sessionId, sdp: offer.sdp ?? '' });
  }

  /** Handle a frame from a guest. */
  async accept(nodeId: string, frame: SignalFrame): Promise<void> {
    if (frame.sessionId !== this.sessionId) return;
    const pc = this.peers.get(nodeId);
    if (!pc) return;
    if (frame.kind === 'answer') {
      await pc.setRemoteDescription({ type: 'answer', sdp: frame.sdp });
    } else if (frame.kind === 'ice') {
      // Candidates can arrive before the answer is applied. Swallowing the
      // failure is correct: ICE tolerates a lost candidate, and throwing here
      // would abort a connection that would otherwise have succeeded on another.
      try {
        await pc.addIceCandidate(frame.candidate);
      } catch {
        /* a candidate that arrived too early or too late */
      }
    }
  }

  private close(nodeId: string, farewell: 'bye' | null): void {
    const pc = this.peers.get(nodeId);
    if (!pc) return;
    if (farewell) send(nodeId, { kind: 'bye', sessionId: this.sessionId });
    pc.close();
    this.peers.delete(nodeId);
  }

  closeAll(farewell: 'bye' | null = 'bye'): void {
    for (const nodeId of [...this.peers.keys()]) this.close(nodeId, farewell);
    this.stream = null;
  }

  get guestCount(): number {
    return this.peers.size;
  }
}

/**
 * The guest's side: receive one stream from the host.
 *
 * Answers whatever the host offers rather than initiating, so a host who
 * restarts their capture simply offers again and the guest follows — no state
 * machine on this side to fall out of step with the other.
 */
export class ShareSubscriber {
  private pc: RTCPeerConnection | null = null;
  private hostNode = '';
  private sessionId = '';

  constructor(
    private readonly onStream: (stream: MediaStream | null) => void,
    /** Called when the host stops sharing, so the pane can say so rather than
     *  leaving a frozen frame that looks like a broken connection. */
    private readonly onEnded: () => void,
  ) {}

  expect(sessionId: string, hostNode: string): void {
    this.sessionId = sessionId;
    this.hostNode = hostNode;
  }

  async accept(nodeId: string, frame: SignalFrame): Promise<void> {
    if (nodeId !== this.hostNode || frame.sessionId !== this.sessionId) return;

    if (frame.kind === 'bye') {
      this.close();
      this.onEnded();
      return;
    }

    if (frame.kind === 'offer') {
      this.close();
      const pc = new RTCPeerConnection(iceConfig());
      this.pc = pc;
      pc.onicecandidate = (e) => {
        if (e.candidate) {
          send(nodeId, {
            kind: 'ice',
            sessionId: this.sessionId,
            candidate: e.candidate.toJSON(),
          });
        }
      };
      pc.ontrack = (e) => this.onStream(e.streams[0] ?? null);
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed') {
          this.close();
          this.onEnded();
        }
      };
      await pc.setRemoteDescription({ type: 'offer', sdp: frame.sdp });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      send(nodeId, { kind: 'answer', sessionId: this.sessionId, sdp: answer.sdp ?? '' });
      return;
    }

    if (frame.kind === 'ice' && this.pc) {
      try {
        await this.pc.addIceCandidate(frame.candidate);
      } catch {
        /* see SharePublisher.accept */
      }
    }
  }

  close(): void {
    this.pc?.close();
    this.pc = null;
    this.onStream(null);
  }
}

/** Decode a raw `share_signal` payload. Exported so callers need not import both. */
export { parseSignal };
