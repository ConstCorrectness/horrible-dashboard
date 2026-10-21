/**
 * The guest's pixel stream: one subscriber per joined session, held at module
 * level.
 *
 * The host half is `stream.ts`; this is its mirror image, and it lives out here
 * for the same reason that one does. It used to live inside `MirrorPanel`, and a
 * pane is the wrong owner for a connection: inactive tabs unmount, and the pane
 * is routinely opened *after* the guest joins — so the host's one-shot offer
 * landed with nobody listening, and the host's connection waited in
 * `have-local-offer` forever for an answer that was never coming. Bound once at
 * boot, the listener exists from the moment a session does, and the pane is only
 * a renderer of what it has.
 *
 * Each new subscriber also says `ready`, which covers what boot-time binding
 * cannot: a guest who reloads mid-stream comes back with no connection while the
 * host's side still thinks it has one.
 */
import { ShareSubscriber } from './rtc';
import { parseSignal } from './signal';
import { getShareSnapshot, onShareSignal, subscribeShare } from './ws';

/** What the mirror pane renders, by session id. */
export interface WatchState {
  /** The host's stream for each joined session that is receiving one. */
  streams: Record<string, MediaStream>;
}

let state: WatchState = { streams: {} };
const listeners = new Set<() => void>();

function emit(): void {
  state = { ...state };
  listeners.forEach((l) => l());
}

export function getWatchState(): WatchState {
  return state;
}

export function subscribeWatch(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The stream for `sessionId`, if one is arriving. */
export function streamFor(sessionId: string): MediaStream | null {
  return state.streams[sessionId] ?? null;
}

function setStream(sessionId: string, stream: MediaStream | null): void {
  const had = state.streams[sessionId] ?? null;
  if (had === stream) return;
  const next = { ...state.streams };
  if (stream) next[sessionId] = stream;
  else delete next[sessionId];
  state.streams = next;
  emit();
}

interface Watching {
  hostNode: string;
  sub: ShareSubscriber;
}

const watching = new Map<string, Watching>();

/**
 * Bring the subscriber set in line with the sessions this node has joined.
 *
 * Rebuilt when a session's host changes rather than reconfigured, so a stale
 * peer connection can never outlive the session that negotiated it.
 */
function reconcile(): void {
  const joined = new Map(getShareSnapshot().joined.map((s) => [s.id, s.host_node] as const));

  for (const [sessionId, w] of watching) {
    if (joined.get(sessionId) === w.hostNode) continue;
    watching.delete(sessionId);
    w.sub.close();
  }

  for (const [sessionId, hostNode] of joined) {
    if (!sessionId || !hostNode || watching.has(sessionId)) continue;
    const sub = new ShareSubscriber(
      (stream) => setStream(sessionId, stream),
      () => setStream(sessionId, null),
    );
    sub.expect(sessionId, hostNode);
    watching.set(sessionId, { hostNode, sub });
    sub.ready();
  }
}

let bound: (() => void) | null = null;

/**
 * Start listening for hosts' offers. Bound once at boot, next to
 * `bindStreamLifecycle`; calling it again returns the existing binding's teardown.
 */
export function bindGuestMedia(): () => void {
  if (bound) return bound;
  const offShare = subscribeShare(reconcile);
  const offSignal = onShareSignal((from, payload) => {
    const frame = parseSignal(payload);
    if (!frame) return;
    void watching.get(frame.sessionId)?.sub.accept(from, frame);
  });
  reconcile();

  const teardown = () => {
    offShare();
    offSignal();
    for (const w of watching.values()) w.sub.close();
    watching.clear();
    bound = null;
  };
  bound = teardown;
  return teardown;
}
