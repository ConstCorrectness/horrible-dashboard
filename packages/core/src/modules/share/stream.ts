/**
 * The host's pixel stream: capture, mix, publish, and stop.
 *
 * One place that owns the whole lifecycle, because the failure this prevents is
 * a stream that is *partly* stopped — a capture released while a peer connection
 * still claims to be live, or the reverse, leaving the UI confident about a
 * picture nobody is receiving.
 *
 * The guest half is `ShareSubscriber` in `rtc.ts`, driven from the mirror pane.
 */
import { mixer } from '../audio/engine';
import { ensureShareBus, shareBusHasSource, subscribeMixer } from '../audio/store';
import { registry } from '../../registry';
import { layoutStore } from '../../layout/store';

import { onCaptureEnded, startCapture, stopCapture, type CaptureError } from './capture';
import type { ViewShareInfo } from './mirror';
import { isClear, preflight, type Preflight } from './preflight';
import { SharePublisher } from './rtc';
import { parseSignal } from './signal';
import { getShareSnapshot, onShareSignal, subscribeShare } from './ws';
import { getLink } from './api';
import { WhipPublisher } from './whip';

function lookup(viewId: string): ViewShareInfo | undefined {
  const decl = registry.view(viewId);
  return decl ? { title: decl.title, share: decl.share } : undefined;
}

/** What the host's UI needs to render the streaming state. */
export interface StreamState {
  live: boolean;
  /** Set when the last attempt failed for a reason worth showing. */
  error: string | null;
  /** How many guests currently hold a peer connection. */
  peers: number;
  /** True when audio is being sent — i.e. something is routed to the Viewers bus. */
  audio: boolean;
  /**
   * True when the capture is also going to the public relay.
   *
   * Separate from `live`, because the two fail independently: the fabric guests
   * can be watching happily while the relay is unreachable, and a single flag
   * would have to pick one of those to lie about.
   */
  relaying: boolean;
  /** Set when the relay leg specifically failed. */
  relayError: string | null;
  /**
   * Set when there is no audio path at all, as opposed to an empty one.
   *
   * "Nothing is routed yet" and "the mixer never came up, so there is nothing to
   * route into" look identical from the outside — both are a silent stream — but
   * only the first is fixed by the advice the pane gives. Telling a host to route
   * a strip into a bus that does not exist is the failure this names.
   */
  audioFault: string | null;
}

let state: StreamState = {
  live: false,
  error: null,
  peers: 0,
  audio: false,
  audioFault: null,
  relaying: false,
  relayError: null,
};
const listeners = new Set<() => void>();

function emit(): void {
  state = { ...state };
  listeners.forEach((l) => l());
}

export function getStreamState(): StreamState {
  return state;
}

export function subscribeStream(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Check what a capture would expose, without starting one. */
export function checkPreflight(): Preflight {
  return preflight(layoutStore.getSnapshot().frame, lookup);
}

const publisher = new SharePublisher();
let capture: MediaStream | null = null;
let outgoing: MediaStream | null = null;
let stopCaptureListener: (() => void) | null = null;
let unsubscribeParticipants: (() => void) | null = null;
let unsubscribeSignal: (() => void) | null = null;
let unsubscribeMixer: (() => void) | null = null;
const whip = new WhipPublisher();

/**
 * Push the outgoing stream to the relay, if a public link is live.
 *
 * Deliberately **after** the fabric path is up and deliberately non-fatal: a
 * relay that is down or misconfigured must not stop friends on the fabric from
 * watching. The failure is reported on its own field rather than the shared
 * `error`, which the capture path already owns.
 */
async function startRelay(outgoing: MediaStream): Promise<void> {
  let ingest = '';
  try {
    ingest = (await getLink()).ingest_url;
  } catch {
    // No link minted, or the node could not answer. Neither is an error for a
    // fabric-only share, which is the default and the common case.
    return;
  }
  if (!ingest) return;
  try {
    await whip.publish(ingest, outgoing);
    state.relaying = true;
    state.relayError = null;
  } catch (err) {
    state.relaying = false;
    state.relayError = (err as Error).message;
  }
  emit();
}

/**
 * Whether guests would actually hear something.
 *
 * Two independent paths: the capture's own audio track (a Chromium-only bonus
 * when the host ticked "share tab audio") and anything the host routed to the
 * viewers' bus. Counting tracks on the outgoing stream cannot answer this — the
 * bus contributes a track whether or not it carries sound.
 */
function hasAudioPath(captured: MediaStream | null): boolean {
  return (captured?.getAudioTracks().length ?? 0) > 0 || shareBusHasSource();
}

function guestNodes(): string[] {
  const hosting = getShareSnapshot().hosting;
  if (!hosting) return [];
  return hosting.participants.filter((p) => p.role === 'guest').map((p) => p.node_id);
}

/**
 * Start sharing the screen.
 *
 * `force` is the host acknowledging the pre-flight warning. It is a separate
 * argument rather than a flag on some options object so that every call site has
 * to say, in one word, whether a human agreed to this — a default that let it
 * through would be the single worst line in this module.
 */
export async function startStream(force = false): Promise<Preflight | null> {
  const hosting = getShareSnapshot().hosting;
  if (!hosting) {
    state.error = 'Start a session first.';
    emit();
    return null;
  }

  const check = checkPreflight();
  if (!isClear(check) && !force) return check;

  try {
    capture = await startCapture();
  } catch (err) {
    const e = err as CaptureError;
    // A dismissed picker is a decision, not a fault. Reporting it as an error
    // trains people to ignore the error line.
    state.error = e.cancelled ? null : e.message;
    emit();
    return null;
  }

  // The viewers' bus carries whatever the host routed into it; the capture's own
  // audio track (Chromium only) is a bonus when the engine offers one. Both are
  // added, so a host on Firefox still has a working audio path through the mixer.
  const busId = await ensureShareBus();
  const busStream = busId ? mixer.busStream(busId) : null;
  outgoing = new MediaStream();
  for (const track of capture.getVideoTracks()) outgoing.addTrack(track);
  for (const track of capture.getAudioTracks()) outgoing.addTrack(track);
  for (const track of busStream?.getAudioTracks() ?? []) outgoing.addTrack(track);

  const audioFault = busStream
    ? null
    : 'The audio mixer did not start, so this share has no Viewers bus to carry sound.';

  stopCaptureListener = onCaptureEnded(capture, () => void stopStream());

  publisher.setStream(hosting.id, outgoing);
  publisher.syncGuests(guestNodes());

  // Guests who join later get an offer without the host doing anything.
  unsubscribeParticipants = subscribeShare(() => {
    if (!state.live) return;
    publisher.syncGuests(guestNodes());
    state.peers = publisher.guestCount;
    emit();
  });

  unsubscribeSignal = onShareSignal((from, payload) => {
    const frame = parseSignal(payload);
    if (frame) void publisher.accept(from, frame);
  });

  // The chip has to follow the matrix, not just its value at start: routing a
  // strip to the viewers' bus is exactly what a host does *after* seeing the
  // "guests hear nothing" hint, and a chip that only updates on the next share
  // would leave them unsure whether it worked.
  unsubscribeMixer = subscribeMixer(() => {
    if (!state.live) return;
    const audio = hasAudioPath(capture);
    if (audio === state.audio) return;
    state.audio = audio;
    emit();
  });

  state = {
    live: true,
    error: null,
    peers: publisher.guestCount,
    audio: hasAudioPath(capture),
    audioFault,
    relaying: false,
    relayError: null,
  };
  emit();

  // Not awaited: the fabric share is already live and usable, and the relay leg
  // involves a network round trip to another continent in the worst case.
  void startRelay(outgoing);
  return null;
}

/** Stop sharing. Safe to call when nothing is running. */
export async function stopStream(): Promise<void> {
  publisher.closeAll('bye');
  stopCaptureListener?.();
  stopCaptureListener = null;
  unsubscribeParticipants?.();
  unsubscribeParticipants = null;
  unsubscribeSignal?.();
  unsubscribeSignal = null;
  unsubscribeMixer?.();
  unsubscribeMixer = null;
  // Only the capture's own tracks are stopped. The mixer's bus track belongs to
  // the shared `AudioContext` and is reused by the next stream; stopping it would
  // leave the Viewers bus permanently silent with nothing on screen to explain it.
  stopCapture(capture);
  capture = null;
  outgoing = null;
  void whip.stop();
  state = {
    live: false,
    error: null,
    peers: 0,
    audio: false,
    audioFault: null,
    relaying: false,
    relayError: null,
  };
  emit();
}

/**
 * Stop the stream if the session ends underneath it.
 *
 * Bound once at boot next to the projector, for the same reason: a session can
 * end from another tab, from the agent, or because the host clicked Stop in the
 * session pane, and the capture must not outlive any of those.
 */
export function bindStreamLifecycle(): () => void {
  return subscribeShare(() => {
    if (state.live && !getShareSnapshot().hosting) void stopStream();
  });
}
