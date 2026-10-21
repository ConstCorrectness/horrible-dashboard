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
 *
 * **Every offer is a fresh connection, on both ends.** The guest answers each
 * offer with a new `RTCPeerConnection`, so a host that renegotiated an existing
 * one would be pairing its old DTLS session with a new fingerprint. Recovery is
 * therefore always "close and offer again", tagged with an `epoch` so frames
 * from the connection being replaced cannot land on its successor.
 */
import { getSetting } from '../../settings';

import {
  ANSWER_TIMEOUT_MS,
  buildIceConfig,
  DISCONNECT_GRACE_MS,
  parseSignal,
  READY_MIN_INTERVAL_MS,
  reofferDelay,
  sameEpoch,
  type IceConfig,
  type SignalFrame,
} from './signal';
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

/** One guest's live connection on the host, and the timers watching it. */
interface Peer {
  pc: RTCPeerConnection;
  epoch: number;
  /** Armed on `disconnected`; re-offers if ICE has not recovered when it fires. */
  grace: ReturnType<typeof setTimeout> | null;
  /** Armed when the offer goes out; re-offers if no answer has been applied. */
  unanswered: ReturnType<typeof setTimeout> | null;
}

/**
 * The host's side: publish one stream to any number of guests.
 *
 * Guests are added and removed as the participant list changes, so a person who
 * joins mid-stream gets an offer immediately rather than waiting for the host to
 * do something. A guest whose connection fails, stalls in `disconnected`, or
 * never answers gets another offer on a backoff, for as long as they remain a
 * guest — and one who says `ready` gets one at once.
 */
export class SharePublisher {
  private readonly peers = new Map<string, Peer>();
  /** Pending re-offers, by node. A node in here is being recovered, and
   *  `syncGuests` must leave it to the timer rather than offering past the
   *  backoff on the next participant-list emit (which is every cursor move). */
  private readonly retries = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly attempts = new Map<string, number>();
  private readonly lastReady = new Map<string, number>();
  private guests = new Set<string>();
  private stream: MediaStream | null = null;
  private sessionId = '';
  private epoch = 0;

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
    for (const [nodeId, peer] of this.peers) {
      const sender = peer.pc.getSenders().find((s) => s.track?.kind === 'video');
      const track = stream.getVideoTracks()[0] ?? null;
      if (sender && track) void sender.replaceTrack(track);
      else void this.offer(nodeId);
    }
  }

  /** Bring the connection set in line with the session's guests. */
  syncGuests(nodeIds: string[]): void {
    this.guests = new Set(nodeIds);
    for (const nodeId of [...this.peers.keys(), ...this.retries.keys()]) {
      if (!this.guests.has(nodeId)) this.close(nodeId, 'bye');
    }
    if (!this.stream) return;
    for (const nodeId of this.guests) {
      if (!this.peers.has(nodeId) && !this.retries.has(nodeId)) void this.offer(nodeId);
    }
  }

  /**
   * Offer `nodeId` a fresh connection, replacing any it has.
   *
   * The peer is registered before the first await, so a second call racing this
   * one (a participant emit, a `ready`) sees it and supersedes it rather than
   * building a twin — and each await re-checks that this connection is still the
   * current one before sending anything on its behalf.
   */
  private async offer(nodeId: string): Promise<void> {
    const stream = this.stream;
    if (!stream) return;
    this.cancelRetry(nodeId);
    this.discard(nodeId);

    const epoch = ++this.epoch;
    const pc = new RTCPeerConnection(iceConfig());
    const peer: Peer = { pc, epoch, grace: null, unanswered: null };
    this.peers.set(nodeId, peer);
    const current = () => this.peers.get(nodeId) === peer;

    pc.onicecandidate = (e) => {
      if (e.candidate && current()) {
        send(nodeId, {
          kind: 'ice',
          sessionId: this.sessionId,
          candidate: e.candidate.toJSON(),
          epoch,
        });
      }
    };
    pc.onconnectionstatechange = () => {
      if (!current()) return;
      this.onPeerState(nodeId, peer);
    };
    for (const track of stream.getTracks()) pc.addTrack(track, stream);

    try {
      const offer = await pc.createOffer();
      if (!current()) return;
      await pc.setLocalDescription(offer);
      if (!current()) return;
      send(nodeId, { kind: 'offer', sessionId: this.sessionId, sdp: offer.sdp ?? '', epoch });
    } catch {
      // A connection that cannot even produce an offer is as good as failed.
      if (current()) this.recover(nodeId);
      return;
    }
    peer.unanswered = setTimeout(() => {
      peer.unanswered = null;
      if (current() && pc.signalingState === 'have-local-offer') this.recover(nodeId);
    }, ANSWER_TIMEOUT_MS);
  }

  private onPeerState(nodeId: string, peer: Peer): void {
    const state = peer.pc.connectionState;
    if (state === 'connected') {
      this.attempts.delete(nodeId);
      if (peer.grace !== null) clearTimeout(peer.grace);
      peer.grace = null;
    } else if (state === 'disconnected') {
      // See DISCONNECT_GRACE_MS: wait out a blip, but do not wait on `failed`,
      // which a stalled connection need never reach.
      if (peer.grace !== null) return;
      peer.grace = setTimeout(() => {
        peer.grace = null;
        if (this.peers.get(nodeId) !== peer) return;
        const now = peer.pc.connectionState;
        if (now === 'disconnected' || now === 'failed') this.recover(nodeId);
      }, DISCONNECT_GRACE_MS);
    } else if (state === 'failed') {
      this.recover(nodeId);
    }
  }

  /**
   * Drop `nodeId`'s connection and offer again after the backoff.
   *
   * No `bye`: that tells a guest the host *stopped sharing*, and this is the
   * opposite — the share is live and the picture is on its way back.
   */
  private recover(nodeId: string): void {
    this.discard(nodeId);
    if (!this.stream || !this.guests.has(nodeId) || this.retries.has(nodeId)) return;
    const attempt = this.attempts.get(nodeId) ?? 0;
    this.attempts.set(nodeId, attempt + 1);
    this.retries.set(
      nodeId,
      setTimeout(() => {
        this.retries.delete(nodeId);
        void this.offer(nodeId);
      }, reofferDelay(attempt)),
    );
  }

  /** Handle a frame from a guest. */
  async accept(nodeId: string, frame: SignalFrame): Promise<void> {
    if (frame.sessionId !== this.sessionId) return;

    if (frame.kind === 'ready') {
      // Only a current guest, only while sharing, and not faster than a person
      // could plausibly reload: `share_signal` reaches us from any trusted peer,
      // and an offer costs an ICE gather.
      if (!this.stream || !this.guests.has(nodeId)) return;
      const now = Date.now();
      if (now - (this.lastReady.get(nodeId) ?? -Infinity) < READY_MIN_INTERVAL_MS) return;
      this.lastReady.set(nodeId, now);
      await this.offer(nodeId);
      return;
    }

    const peer = this.peers.get(nodeId);
    if (!peer) return;
    if ((frame.kind === 'answer' || frame.kind === 'ice') && !sameEpoch(frame.epoch, peer.epoch)) {
      // Addressed to a connection this one replaced.
      return;
    }
    if (frame.kind === 'answer') {
      if (peer.pc.signalingState !== 'have-local-offer') return;
      if (peer.unanswered !== null) clearTimeout(peer.unanswered);
      peer.unanswered = null;
      try {
        await peer.pc.setRemoteDescription({ type: 'answer', sdp: frame.sdp });
      } catch {
        if (this.peers.get(nodeId) === peer) this.recover(nodeId);
      }
    } else if (frame.kind === 'ice') {
      // Candidates can arrive before the answer is applied. Swallowing the
      // failure is correct: ICE tolerates a lost candidate, and throwing here
      // would abort a connection that would otherwise have succeeded on another.
      try {
        await peer.pc.addIceCandidate(frame.candidate);
      } catch {
        /* a candidate that arrived too early or too late */
      }
    }
  }

  /** Close `nodeId`'s connection and its timers, without a farewell or a retry. */
  private discard(nodeId: string): void {
    const peer = this.peers.get(nodeId);
    if (!peer) return;
    this.peers.delete(nodeId);
    if (peer.grace !== null) clearTimeout(peer.grace);
    if (peer.unanswered !== null) clearTimeout(peer.unanswered);
    peer.pc.close();
  }

  private cancelRetry(nodeId: string): void {
    const timer = this.retries.get(nodeId);
    if (timer !== undefined) clearTimeout(timer);
    this.retries.delete(nodeId);
  }

  private close(nodeId: string, farewell: 'bye' | null): void {
    const known = this.peers.has(nodeId) || this.retries.has(nodeId);
    this.cancelRetry(nodeId);
    this.discard(nodeId);
    this.attempts.delete(nodeId);
    if (known && farewell) send(nodeId, { kind: 'bye', sessionId: this.sessionId });
  }

  closeAll(farewell: 'bye' | null = 'bye'): void {
    for (const nodeId of new Set([...this.peers.keys(), ...this.retries.keys()])) {
      this.close(nodeId, farewell);
    }
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
 * machine on this side to fall out of step with the other. The one thing it does
 * say unprompted is `ready`, when it holds nothing: on creation, and when its
 * connection fails.
 */
export class ShareSubscriber {
  private pc: RTCPeerConnection | null = null;
  /** The epoch of the offer `pc` answered; null when there is no connection. */
  private epoch: number | null = null;
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

  /** Ask the host for an offer. Harmless when they are not sharing. */
  ready(): void {
    if (!this.sessionId || !this.hostNode) return;
    send(this.hostNode, { kind: 'ready', sessionId: this.sessionId });
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
      const epoch = frame.epoch;
      this.pc = pc;
      // An untagged offer (an older host) still needs a connection to match ICE
      // against; `sameEpoch` lets its untagged candidates through.
      this.epoch = epoch ?? 0;
      const current = () => this.pc === pc;
      pc.onicecandidate = (e) => {
        if (e.candidate && current()) {
          send(nodeId, {
            kind: 'ice',
            sessionId: this.sessionId,
            candidate: e.candidate.toJSON(),
            ...(epoch === undefined ? {} : { epoch }),
          });
        }
      };
      pc.ontrack = (e) => {
        if (current()) this.onStream(e.streams[0] ?? null);
      };
      pc.onconnectionstatechange = () => {
        if (!current() || pc.connectionState !== 'failed') return;
        // Not `onEnded`: the host is still sharing, and is about to offer again
        // (see SharePublisher.recover). Saying `ready` gets that offer sooner
        // when the host has not noticed yet — its side can sit in
        // `disconnected` for a while after ours has given up.
        this.close();
        this.ready();
      };
      try {
        await pc.setRemoteDescription({ type: 'offer', sdp: frame.sdp });
        if (!current()) return;
        const answer = await pc.createAnswer();
        if (!current()) return;
        await pc.setLocalDescription(answer);
        if (!current()) return;
        send(nodeId, {
          kind: 'answer',
          sessionId: this.sessionId,
          sdp: answer.sdp ?? '',
          ...(epoch === undefined ? {} : { epoch }),
        });
      } catch {
        // Superseded mid-negotiation by a newer offer, or an offer this engine
        // cannot answer. Either way the host's answer timeout re-offers.
        if (current()) this.close();
      }
      return;
    }

    if (frame.kind === 'ice' && this.pc && sameEpoch(frame.epoch, this.epoch)) {
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
    this.epoch = null;
    this.onStream(null);
  }
}

/** Decode a raw `share_signal` payload. Exported so callers need not import both. */
export { parseSignal };
