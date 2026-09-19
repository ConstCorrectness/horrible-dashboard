/**
 * Lobby voice: a small mesh of browser-to-browser audio connections.
 *
 * Everyone in the lobby's voice channel holds one `RTCPeerConnection` to every
 * other person in it. The audio never touches a backend — it rides DTLS-SRTP
 * between the two browsers, the same shape as `share/rtc.ts` — and the lobby host
 * only carries the handshake (`lobby_signal`), stamping each frame with the
 * member it really came from. A mesh is right for a party of a handful; it is the
 * wrong shape for sixteen, which is a limit worth knowing rather than a bug.
 *
 * **Who offers is decided, not raced.** Of each pair, the member with the smaller
 * id sends the offer and the other only ever answers. Two browsers that both
 * offered at once ("glare") would each reject the other's and connect to nobody.
 *
 * Playback goes through the mixer's `lobby-voice` strip, never
 * `ctx.destination`, so voice is routable and levelled like every other sound —
 * and the shared context is released, never closed (see `audio/engine.ts`).
 *
 * See docs/modules/hassault.mdx.
 */
import { getSetting } from '../../settings';
import { mixer } from '../audio/engine';
import { inputConstraints } from '../audio/store';
import type { StripHandle } from '../audio/types';
import type { LobbyState } from './session';

mixer.declareStrip({ id: 'lobby-voice', label: 'Lobby voice' });

/** RMS above which a voice counts as speaking, and how long it stays lit after. */
const SPEAKING_RMS = 0.02;
const SPEAKING_HOLD_MS = 350;
/** Speaking is polled on a timer, not rAF: rAF stops in a background tab, and a
 * lobby is exactly the thing you leave in the background while you wait. */
const METER_INTERVAL_MS = 100;

export interface VoiceView {
  /** We are in voice (the mic is open to the mesh, subject to `muted`). */
  active: boolean;
  /** Mic capture is being requested. */
  starting: boolean;
  muted: boolean;
  deafened: boolean;
  /** Push-to-talk mode: the mic is closed except while `talking`. */
  pushToTalk: boolean;
  /** The push-to-talk key is held. */
  talking: boolean;
  /** Member ids currently audible — `self` included when we are talking. */
  speaking: string[];
  /** Connection state per member we are connected to. */
  links: Record<string, RTCPeerConnectionState>;
  error: string;
}

/** Everyone in voice other than us, in a stable order. */
export function voicePeers(state: LobbyState | null): string[] {
  if (!state) return [];
  return state.members
    .filter((m) => m.voice && m.id !== state.you)
    .map((m) => m.id)
    .sort();
}

/** Whether we send the offer to `them`. Exactly one of each pair does. */
export function isOfferer(me: string, them: string): boolean {
  return me < them;
}

/**
 * The same semantics as `buildIceConfig` in the share module and `_ice_servers`
 * in `network/transport/webrtc.py`: STUN is a bare `host:port`, TURN a full URL,
 * and a TURN entry without credentials is dropped rather than sent (a malformed
 * entry makes the browser reject the whole configuration, STUN included). Read
 * here rather than imported, so this module does not reach into `share`.
 */
function iceConfig(): RTCConfiguration {
  const iceServers: RTCIceServer[] = [];
  const stun = (getSetting<string>('network.stunServer') ?? '').trim();
  if (stun) iceServers.push({ urls: [`stun:${stun}`] });
  const turnUrl = (getSetting<string>('network.turnUrl') ?? '').trim();
  const username = (getSetting<string>('network.turnUsername') ?? '').trim();
  const credential = (getSetting<string>('network.turnCredential') ?? '').trim();
  if (turnUrl && username && credential) {
    iceServers.push({ urls: [turnUrl], username, credential });
  }
  return { iceServers };
}

interface Link {
  pc: RTCPeerConnection;
  /** Candidates that arrived before the remote description; ICE needs it first. */
  pending: RTCIceCandidateInit[];
  gain: GainNode | null;
  analyser: AnalyserNode | null;
  /** Chrome only feeds a remote WebRTC track into Web Audio while some media
   * element is also consuming it. Muted, so this adds nothing audible. */
  sink: HTMLAudioElement | null;
}

type Signal = Record<string, unknown>;

export class LobbyVoice {
  private view: VoiceView = {
    active: false,
    starting: false,
    muted: false,
    deafened: false,
    pushToTalk: false,
    talking: false,
    speaking: [],
    links: {},
    error: '',
  };
  private readonly listeners = new Set<(view: VoiceView) => void>();
  private readonly links = new Map<string, Link>();
  private strip: StripHandle | null = null;
  private stream: MediaStream | null = null;
  private localAnalyser: AnalyserNode | null = null;
  private meter: ReturnType<typeof setInterval> | null = null;
  private lastHeard = new Map<string, number>();
  private state: LobbyState | null = null;

  /** Wired by the pane to its `MatchSession`. */
  sendSignal: (to: string, signal: Signal) => void = () => {};
  announce: (on: boolean, muted: boolean) => void = () => {};

  get current(): VoiceView {
    return this.view;
  }

  subscribe(listener: (view: VoiceView) => void): () => void {
    this.listeners.add(listener);
    listener(this.view);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Open the mic and join the lobby's voice channel. */
  async start(): Promise<void> {
    if (this.view.active || this.view.starting) return;
    this.patch({ starting: true, error: '' });
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        // The microphone chosen in the mixer, not the system default.
        audio: inputConstraints({
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        }),
      });
    } catch (err) {
      this.patch({
        starting: false,
        error:
          err instanceof Error && err.name === 'NotAllowedError'
            ? 'Microphone permission was denied.'
            : 'No microphone could be opened.',
      });
      return;
    }
    try {
      this.strip = mixer.connectStrip('lobby-voice');
      const ctx = this.strip.context;
      this.localAnalyser = ctx.createAnalyser();
      this.localAnalyser.fftSize = 512;
      ctx.createMediaStreamSource(this.stream).connect(this.localAnalyser);
    } catch (err) {
      // The mic opened but audio output could not be set up. Give the mic back
      // rather than leaving it held behind a button stuck on "Opening mic…".
      this.stop();
      this.patch({
        error: `Audio output is unavailable${err instanceof Error ? `: ${err.message}` : '.'}`,
      });
      return;
    }
    this.applyMute();
    this.meter = setInterval(() => this.measure(), METER_INTERVAL_MS);
    this.patch({ active: true, starting: false });
    this.announce(true, this.view.muted);
    this.sync(this.state);
  }

  /** Leave voice: hang up every link and give the mic back. */
  stop(): void {
    const wasActive = this.view.active;
    for (const id of [...this.links.keys()]) this.hangUp(id, true);
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
    this.localAnalyser = null;
    if (this.meter) clearInterval(this.meter);
    this.meter = null;
    this.strip?.release();
    this.strip = null;
    this.lastHeard.clear();
    this.patch({ active: false, starting: false, speaking: [], links: {} });
    if (wasActive) this.announce(false, false);
  }

  setMuted(muted: boolean): void {
    this.patch({ muted });
    this.applyMute();
    if (this.view.active) this.announce(true, muted);
  }

  /** Switch between an open mic and push-to-talk. */
  setPushToTalk(on: boolean): void {
    if (on === this.view.pushToTalk) return;
    this.patch({ pushToTalk: on });
    this.applyMute();
  }

  /** The push-to-talk key went down or up. Harmless when not in PTT mode. */
  setTalking(held: boolean): void {
    if (held === this.view.talking) return;
    this.patch({ talking: held });
    this.applyMute();
  }

  /** Whether the mic is actually reaching anyone right now. */
  get transmitting(): boolean {
    return this.view.active && !this.view.muted && (!this.view.pushToTalk || this.view.talking);
  }

  setDeafened(deafened: boolean): void {
    this.patch({ deafened });
    for (const link of this.links.values()) {
      if (link.gain) link.gain.gain.value = deafened ? 0 : 1;
    }
  }

  /**
   * Bring the mesh in line with the lobby. Called on every state push: someone
   * joining voice gets an offer (if it is ours to send), someone leaving gets
   * hung up on, and a link that failed is retried.
   */
  sync(state: LobbyState | null): void {
    this.state = state;
    if (!state) {
      if (this.view.active) this.stop();
      return;
    }
    if (!this.view.active) return;
    const me = state.you;
    const wanted = new Set(voicePeers(state));
    for (const id of [...this.links.keys()]) {
      if (!wanted.has(id)) this.hangUp(id, false);
    }
    for (const id of wanted) {
      if (!this.links.has(id) && isOfferer(me, id)) void this.offer(id);
    }
    // A reconnect or a lobby hop can leave the server thinking we are out of
    // voice while we are in it; saying so again is idempotent.
    const self = state.members.find((m) => m.id === me);
    if (self && (!self.voice || self.muted !== this.view.muted)) {
      this.announce(true, this.view.muted);
    }
  }

  /** A handshake frame from `from`, relayed by the lobby host. */
  async accept(from: string, signal: Signal): Promise<void> {
    if (!this.view.active) return;
    const kind = signal.kind;
    if (kind === 'bye') {
      this.hangUp(from, false);
      return;
    }
    if (kind === 'offer' && typeof signal.sdp === 'string') {
      // Only the smaller id offers; an offer from the other direction means the
      // two sides disagree about the ids and answering it would create glare.
      if (this.state && isOfferer(this.state.you, from)) return;
      this.hangUp(from, false);
      const link = this.link(from);
      await link.pc.setRemoteDescription({ type: 'offer', sdp: signal.sdp });
      await this.flushPending(link);
      const answer = await link.pc.createAnswer();
      await link.pc.setLocalDescription(answer);
      this.sendSignal(from, { kind: 'answer', sdp: answer.sdp ?? '' });
      return;
    }
    const link = this.links.get(from);
    if (!link) return;
    if (kind === 'answer' && typeof signal.sdp === 'string') {
      if (link.pc.signalingState !== 'have-local-offer') return;
      await link.pc.setRemoteDescription({ type: 'answer', sdp: signal.sdp });
      await this.flushPending(link);
    } else if (kind === 'ice' && signal.candidate && typeof signal.candidate === 'object') {
      const candidate = signal.candidate as RTCIceCandidateInit;
      if (!link.pc.remoteDescription) link.pending.push(candidate);
      else await this.addCandidate(link, candidate);
    }
  }

  // ---- internals -----------------------------------------------------------

  private async offer(id: string): Promise<void> {
    const link = this.link(id);
    const offer = await link.pc.createOffer();
    await link.pc.setLocalDescription(offer);
    this.sendSignal(id, { kind: 'offer', sdp: offer.sdp ?? '' });
  }

  private link(id: string): Link {
    const existing = this.links.get(id);
    if (existing) return existing;
    const pc = new RTCPeerConnection(iceConfig());
    const link: Link = { pc, pending: [], gain: null, analyser: null, sink: null };
    this.links.set(id, link);
    const local = this.stream;
    if (local) for (const track of local.getAudioTracks()) pc.addTrack(track, local);
    pc.onicecandidate = (e) => {
      if (e.candidate) this.sendSignal(id, { kind: 'ice', candidate: e.candidate.toJSON() });
    };
    pc.ontrack = (e) => this.play(link, e.streams[0] ?? new MediaStream([e.track]));
    pc.onconnectionstatechange = () => {
      this.patch({ links: { ...this.view.links, [id]: pc.connectionState } });
      // `failed` is terminal; `disconnected` usually recovers by itself. The
      // retry comes from the next `sync`, which re-offers if it is ours to.
      if (pc.connectionState === 'failed') {
        this.hangUp(id, false);
        this.sync(this.state);
      }
    };
    this.patch({ links: { ...this.view.links, [id]: pc.connectionState } });
    return link;
  }

  private play(link: Link, stream: MediaStream): void {
    if (!this.strip || link.gain) return;
    const ctx = this.strip.context;
    const sink = new Audio();
    sink.muted = true;
    sink.srcObject = stream;
    void sink.play().catch(() => {
      /* muted playback never needs a gesture; nothing to report if it balks */
    });
    const source = ctx.createMediaStreamSource(stream);
    const gain = ctx.createGain();
    gain.gain.value = this.view.deafened ? 0 : 1;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    source.connect(gain);
    gain.connect(this.strip.input);
    link.gain = gain;
    link.analyser = analyser;
    link.sink = sink;
  }

  private hangUp(id: string, farewell: boolean): void {
    const link = this.links.get(id);
    if (!link) return;
    if (farewell) this.sendSignal(id, { kind: 'bye' });
    link.pc.onconnectionstatechange = null;
    link.pc.close();
    link.gain?.disconnect();
    if (link.sink) link.sink.srcObject = null;
    this.links.delete(id);
    this.lastHeard.delete(id);
    const links = { ...this.view.links };
    delete links[id];
    this.patch({ links });
  }

  private async flushPending(link: Link): Promise<void> {
    const queued = link.pending.splice(0);
    for (const candidate of queued) await this.addCandidate(link, candidate);
  }

  private async addCandidate(link: Link, candidate: RTCIceCandidateInit): Promise<void> {
    try {
      await link.pc.addIceCandidate(candidate);
    } catch {
      /* ICE tolerates a lost candidate; one arriving late is not an error */
    }
  }

  private applyMute(): void {
    // `enabled = false` sends silence rather than renegotiating, so toggling it
    // per keypress for push-to-talk costs nothing and cannot drop a link.
    const open = !this.view.muted && (!this.view.pushToTalk || this.view.talking);
    for (const track of this.stream?.getAudioTracks() ?? []) track.enabled = open;
  }

  private measure(): void {
    const now = performance.now();
    const me = this.state?.you ?? '';
    if (me && this.localAnalyser && this.transmitting && rms(this.localAnalyser) > SPEAKING_RMS) {
      this.lastHeard.set(me, now);
    }
    for (const [id, link] of this.links) {
      if (link.analyser && rms(link.analyser) > SPEAKING_RMS) this.lastHeard.set(id, now);
    }
    const speaking = [...this.lastHeard]
      .filter(([, t]) => now - t < SPEAKING_HOLD_MS)
      .map(([id]) => id)
      .sort();
    if (speaking.join() !== this.view.speaking.join()) this.patch({ speaking });
  }

  private patch(next: Partial<VoiceView>): void {
    this.view = { ...this.view, ...next };
    for (const listener of this.listeners) listener(this.view);
  }
}

function rms(analyser: AnalyserNode): number {
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  return Math.sqrt(sum / buf.length);
}

/** One per app: voice belongs to the lobby, which outlives any one pane render. */
export const lobbyVoice = new LobbyVoice();
