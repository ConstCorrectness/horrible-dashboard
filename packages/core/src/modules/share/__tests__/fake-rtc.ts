/**
 * A stand-in `RTCPeerConnection` for the mesh tests: just enough state machine
 * to tell an offer from an answer and a live connection from a dead one, and a
 * `become()` to drive connection states the way the browser would.
 */
export class FakePeer {
  static made: FakePeer[] = [];
  signalingState: RTCSignalingState = 'stable';
  connectionState: RTCPeerConnectionState = 'new';
  remote: string | null = null;
  closed = false;
  candidates: RTCIceCandidateInit[] = [];
  private senders: Array<{ track: { kind: string } }> = [];
  onicecandidate: ((e: { candidate: { toJSON(): RTCIceCandidateInit } | null }) => void) | null =
    null;
  ontrack: ((e: { streams: MediaStream[] }) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  readonly id: number;

  constructor(readonly config: RTCConfiguration) {
    this.id = FakePeer.made.length;
    FakePeer.made.push(this);
  }

  static last(): FakePeer {
    const pc = FakePeer.made.at(-1);
    if (!pc) throw new Error('no peer connection was made');
    return pc;
  }

  static live(): FakePeer[] {
    return FakePeer.made.filter((pc) => !pc.closed);
  }

  become(state: RTCPeerConnectionState): void {
    this.connectionState = state;
    this.onconnectionstatechange?.();
  }

  /** Emit a local candidate, as ICE gathering would. */
  gather(candidate = `candidate:${this.id}`): void {
    this.onicecandidate?.({ candidate: { toJSON: () => ({ candidate }) } });
  }

  addTrack(track: { kind: string }): void {
    this.senders.push({ track });
  }
  getSenders() {
    return this.senders.map((s) => ({ ...s, replaceTrack: async () => {} }));
  }
  async createOffer() {
    if (this.closed) throw new Error('InvalidStateError: closed');
    return { type: 'offer', sdp: `offer-${this.id}` };
  }
  async createAnswer() {
    if (this.closed) throw new Error('InvalidStateError: closed');
    return { type: 'answer', sdp: `answer-${this.id}` };
  }
  async setLocalDescription(d: { type: string }) {
    if (this.closed) throw new Error('InvalidStateError: closed');
    this.signalingState = d.type === 'offer' ? 'have-local-offer' : 'stable';
  }
  async setRemoteDescription(d: { type: string; sdp: string }) {
    if (this.closed) throw new Error('InvalidStateError: closed');
    this.remote = d.sdp;
    this.signalingState = d.type === 'offer' ? 'have-remote-offer' : 'stable';
  }
  async addIceCandidate(c: RTCIceCandidateInit) {
    if (this.closed) throw new Error('InvalidStateError: closed');
    this.candidates.push(c);
  }
  close(): void {
    this.closed = true;
    this.signalingState = 'closed';
  }
}

/** A stream with one video and one audio track. */
export function fakeStream(): MediaStream {
  const tracks = [{ kind: 'video' }, { kind: 'audio' }];
  return {
    getTracks: () => tracks,
    getVideoTracks: () => tracks.filter((t) => t.kind === 'video'),
  } as unknown as MediaStream;
}
